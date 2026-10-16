"""Archive only verified generated tiles with bounded IO and fair deferral."""
import argparse
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
import tarfile
import time
import zlib
from regional_hf_io import acquire_lock, checked_read, durable_json, run_child, ChildNotReaped, ReadDeadline

MAX_BYTES = 64*2**20
TOTAL_SECONDS = 220
CHILD_SECONDS = 12
COOLDOWN_SECONDS = 600
sha_bytes = lambda raw: hashlib.sha256(raw).hexdigest()


def bounded_decompress(raw, mode, bound):
    if mode == 2:
        decoder = zlib.decompressobj()
        result = decoder.decompress(raw, bound+1)
        if len(result)>bound or not decoder.eof or decoder.unused_data:
            raise ValueError('Compressed generated data exceeds bound or is incomplete')
    elif mode == 1:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            result = stream.read(bound+1)
    else:
        result = raw
    if len(result)>bound:
        raise ValueError('Generated data exceeds decompression bound')
    return result

SCHEMA = 'earthcraft.generated-tile-shard.v1'
CHUNK_KEYS = {'Status', 'InhabitedTime', 'DataVersion', 'zPos', 'isLightOn',
              'Heightmaps', 'structures', 'yPos', 'xPos', 'entities',
              'block_entities', 'PostProcessing', 'LastUpdate', 'sections',
              'block_ticks', 'fluid_ticks'}
ATTRIBUTION = [
    {'source': 'OpenStreetMap contributors', 'license': 'ODbL-1.0',
     'url': 'https://www.openstreetmap.org/copyright'},
    {'source': 'USGS 3DEP', 'license': 'US Government Public Domain',
     'url': 'https://registry.opendata.aws/usgs-lidar/'},
    {'source': 'Illinois Height Modernization / county surveys',
     'url': 'https://clearinghouse.isgs.illinois.edu/node/495',
     'license': 'Source metadata retained locally; attribution required'},
    {'source': 'Terrain Tiles',
     'url': 'https://github.com/tilezen/joerd/blob/master/docs/attribution.md',
     'license': 'Per upstream terrain source'},
    {'source': 'USDA NAIP', 'license': 'US Government Public Domain',
     'url': 'https://www.fsa.usda.gov/resources/programs/naip-imagery'}]


def audit_region(raw, rx, rz):
    import nbtlib
    from region_expansion import records
    chunks = records(raw)
    if len(chunks) != 1024:
        raise ValueError('Complete 1024-chunk 512 metre generated region required')
    for slot, (record, _) in chunks.items():
        payload = record[5:]
        decoded = bounded_decompress(payload, record[4], 4*2**20)
        tag = nbtlib.File.parse(io.BytesIO(decoded))
        if set(tag) - CHUNK_KEYS:
            raise ValueError('Unexpected generated chunk fields; no export')
        if any(tag.get(k) for k in ('entities', 'block_entities', 'structures', 'block_ticks', 'fluid_ticks')):
            # Empty structures dictionaries with empty Starts/References are normal.
            if (any(tag.get(k) for k in ('entities', 'block_entities', 'block_ticks', 'fluid_ticks')) or
                    any(tag.get('structures', {}).values())):
                raise ValueError('Entity or non-generator content; no export')
        if (int(tag['xPos']), int(tag['zPos'])) != (rx * 32 + slot % 32, rz * 32 + slot // 32):
            raise ValueError('Generated region coordinates differ')
        for section in tag.get('sections', []):
            if set(section) - {'Y', 'block_states', 'biomes', 'BlockLight', 'SkyLight'}:
                raise ValueError('Unexpected section data')
            for item in section.get('block_states', {}).get('palette', []):
                if set(item) - {'Name', 'Properties'} or not re.fullmatch(r'minecraft:[a-z0-9_]+', str(item['Name'])):
                    raise ValueError('Unexpected block palette')
    return len(chunks)


def public_tile(job, bulk, frame, fallback=None, known_geometry=None):
    tile, stage, evidence, expected = job
    if not re.fullmatch(r'-?\d+_-?\d+', tile) or stage not in (1,2) or not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise ValueError('Invalid completed job identity')
    rx, rz = map(int, tile.split('_'))
    root = Path(bulk)/'tiles'/tile
    evidence = Path(evidence)
    try:
        evidence.relative_to(root)
    except ValueError:
        raise ValueError('Receipt must belong to the exact generated tile')
    if evidence.name not in ('geometry-receipt.json','scan-receipt.json'):
        raise ValueError('Unexpected generated receipt name')
    receipt = checked_read(evidence, 2**20)
    if sha_bytes(receipt) != expected:
        raise ValueError('Completed receipt changed')
    proof = json.loads(receipt)
    if stage == 2 and proof.get('quality') == 'LQ':
        if (proof.get('tile') != tile or proof.get('stage') != 'appearance' or
            proof.get('result') != 'pass' or proof.get('installed') is not False or
            proof.get('scan_status') != 'No usable paired county scan; retain LQ' or
            'world' in proof or fallback is None or fallback[:2] != [tile,1]):
            raise ValueError('Verified explicit no-upgrade appearance and geometry required')
        note={'tile':tile,'appearance_receipt_sha256':expected,'geometry_receipt_sha256':fallback[3],
              'status':'No usable paired county scan; retained LQ geometry'}
        if known_geometry == fallback[3]:
            return {'already_archived':True,'no_upgrade':note}
        result=public_tile(fallback,bulk,frame)
        if result['entry']['quality'] != 'LQ':
            raise ValueError('No-upgrade fallback requires LQ geometry')
        result['no_upgrade']=note
        return result
    world = Path(proof['world'])
    try:
        relative = world.resolve().relative_to(root.resolve())
    except ValueError:
        relative = Path('outside')
    cloud = (len(relative.parts) == 3 and relative.parts[0] == 'cloud' and
             re.fullmatch(r'aws-scan-\d+', relative.parts[1]) and relative.parts[2] == 'world')
    if not world.is_absolute() or world.is_symlink() or not (world.resolve().parent == root.resolve() or cloud):
        raise ValueError('Export only task-generated tiles, never a personal save')
    meta_raw = checked_read(world/'earthcraft.json',4*2**20)
    meta = json.loads(meta_raw)
    if (proof.get('tile') != tile or proof.get('result') != 'pass' or
        proof.get('quality') not in ('LQ','scan-roof','scan-points-and-roof') or
        proof.get('world_manifest_sha256') != sha_bytes(meta_raw) or
        meta.get('world_frame') != frame or meta.get('world_offset_xz') != [rx*512,rz*512] or
        meta.get('source',{}).get('size') != 512):
        raise ValueError('Verified fixed-frame generated tile required')
    name = f'r.{rx}.{rz}.mca'
    if set(proof['regions']) != {name}:
        raise ValueError('One exact 512 metre region per tile required')
    path = world/'region'/name
    try:
        raw = checked_read(path,MAX_BYTES)
    except FileNotFoundError:
        raw = bounded_decompress(checked_read(path.with_suffix('.mca.gz'),MAX_BYTES),1,MAX_BYTES)
    checksum = sha_bytes(raw)
    if proof['regions'][name] != checksum:
        raise ValueError('Generated region hash changed')
    chunks = audit_region(raw,rx,rz)
    source,scan = meta['source'],proof.get('scan_source') or {}
    record = {'tile':tile,'quality':proof['quality'],'world_offset_xz':[rx*512,rz*512],
        'size_m':512,'metres_per_block':1,'receipt_sha256':expected,'region_sha256':checksum,
        'region_file':name,'chunks':chunks,'source_grid':{k:source[k] for k in ('west','north','size')},
        'scan':{k:scan[k] for k in ('county','year') if k in scan},
        'roof_source_cells':int(proof.get('roof_source_cells',0)),
        'real_facade_photo_colors_applied':0,'client_visual_verified':False,
        'privacy_audit':'Generator-only region; no entities, block entities, players or local metadata'}
    data = gzip.compress(raw,compresslevel=1,mtime=0)
    # Leave headroom for base64 in the bounded worker protocol.
    if len(data)>48*2**20:
        raise ValueError('Generated region exceeds worker transfer bound')
    return {'entry':record,'data':base64.b64encode(data).decode()}


def output_archive(request):
    output = Path(request['output'])
    bulk = Path(request['bulk'])
    if output != bulk/'hf-generated':
        raise ValueError('Exact generated transport directory required')
    for part in [output,*output.parents]:
        if part.is_symlink():
            raise ValueError('Generated output symlink forbidden')
    raw = base64.b64decode(request['data'],validate=True)
    record = request['record']
    if len(raw)>MAX_BYTES or len(raw)!=record['bytes'] or sha_bytes(raw)!=record['sha256']:
        raise ValueError('Generated archive bytes differ')
    output.mkdir(parents=True,exist_ok=True)
    archive = output/(record['sha256']+'.tar')
    try:
        prior = checked_read(archive,MAX_BYTES)
    except FileNotFoundError:
        prior = None
    if prior is not None and prior != raw:
        raise ValueError('Immutable shard differs')
    if prior is None:
        tmp = archive.with_name(archive.name+'.partial.'+str(os.getpid()))
        fd = os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'wb',closefd=False) as stream:
                stream.write(raw);stream.flush();os.fsync(fd)
            os.replace(tmp,archive)
        finally:
            os.close(fd)
    durable_json(archive.with_suffix('.local.json'),record)
    return record


def verify_output(request):
    record=request['record'];bulk=Path(request['bulk'])
    if (not re.fullmatch(r'[a-f0-9]{64}',record['sha256']) or
        record['archive'] != str(bulk/'hf-generated'/(record['sha256']+'.tar')) or
        not isinstance(record['bytes'],int) or not 1<=record['bytes']<=MAX_BYTES):
        raise ValueError('Cached archive bounds differ')
    try:
        raw=checked_read(Path(record['archive']),MAX_BYTES)
    except FileNotFoundError:
        return {'state':'missing'}
    if len(raw)!=record['bytes'] or sha_bytes(raw)!=record['sha256']:
        raise ValueError('Cached immutable archive differs')
    return {'state':'present'}


def archive_bytes(entries,payloads,frame,plan_sha):
    manifest={'schema':SCHEMA,'frame':frame,'plan_sha256':plan_sha,'entries':entries,'attribution':ATTRIBUTION,
        'excludes':['player state','personal save edits','entity regions','resource packs','source photos','credentials'],
        'note':'Generated tiles only; incomplete coverage and incomplete facades. Restore separately and preserve existing edits.'}
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as tar:
        for name,data in [('manifest.json',json.dumps(manifest,sort_keys=True).encode()),*payloads]:
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o644;tar.addfile(item,io.BytesIO(data))
    raw=out.getvalue()
    if len(raw)>MAX_BYTES:raise ValueError('Public shard exceeds bounded transfer budget')
    return raw


def worker():
    try:
        request = json.loads(sys.stdin.buffer.read(96*2**20+1))
        if request['operation']=='tile':
            result = public_tile(request['job'],request['bulk'],request['frame'],request.get('fallback'),request.get('known_geometry'))
        elif request['operation']=='output':
            result = output_archive(request)
        elif request['operation']=='verify_output':
            result = verify_output(request)
        elif request['operation']=='storage':
            import shutil
            mounted=os.path.ismount(request['volume'])
            free=shutil.disk_usage(request['volume']).free
            if (request.get('require_mounted',True) and not mounted) or free<request['reserve_bytes']:
                raise ValueError('LaCie generation reserve reached or volume not mounted')
            result={'state':'storage_ready','mounted':mounted,'free':free}
        else:
            raise ValueError('Unexpected generated IO operation')
        sys.stdout.write(json.dumps(result));sys.stdout.flush()
    except Exception as error:
        # No source contents, traceback, argv, credentials or unrelated paths.
        sys.stderr.write(json.dumps({'error':str(error)[:500]}));sys.stderr.flush()
        raise SystemExit(1)


def _export(control,bulk,output,known,limit=8,storage_preflight=False,expected_inode=None):
    if not 1<=limit<=32:
        raise ValueError('Bounded export batch required')
    control,bulk,output = map(lambda p:Path(p).absolute(),(control,bulk,output))
    started = time.monotonic()
    try:
        lock = acquire_lock(control,expected_inode)
    except BlockingIOError:
        return {'state':'busy','tiles':0,'inspected':0,'deferred':[],'reason':'Existing exporter or unresolved IO child holds the persistent lock'}
    try:
        plan_raw = checked_read(control/'plan.json',32*2**20)
        plan = json.loads(plan_raw);plan_sha = sha_bytes(plan_raw)
        command = [sys.executable,str(Path(__file__).resolve()),'--io-worker']
        def child(request,seconds=CHILD_SECONDS):
            remaining=TOTAL_SECONDS-(time.monotonic()-started)
            if remaining<=0:raise ReadDeadline('Exporter dispatch budget reached')
            return run_child(command,request,lock,min(seconds,remaining))
        if storage_preflight:
            try:
                child({'operation':'storage','volume':'/Volumes/LaCie','reserve_bytes':150*2**30})
            except (ReadDeadline,ChildNotReaped,ValueError,OSError) as error:
                return {'state':'busy','tiles':0,'reason':str(error),'inspected':0,'deferred':[]}
        ready_path=control/'hf-export-ready.local.json'
        try:
            cached=json.loads(checked_read(ready_path,256*2**10))
        except FileNotFoundError:
            cached=None
        if cached:
            record=cached['record']
            if (record['state']!='ready' or record['plan_sha256']!=plan_sha or
                not 1<=record['tiles']<=limit or len(record['entries'])!=record['tiles'] or
                len(cached['jobs'])!=record['tiles']):
                raise ValueError('Cached ready identities differ')
            if all(known.get(entry['tile'])==entry['receipt_sha256'] for entry in record['entries']):
                ready_path.unlink()
                directory=os.open(control,os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
            else:
                try:
                    verified=child({'operation':'verify_output','record':record,'bulk':str(bulk)})
                    if verified['state']=='missing':
                        payloads=[];entries=[]
                        for request in cached['jobs']:
                            result=child({**request,'operation':'tile','bulk':str(bulk),'frame':plan['frame']})
                            entry=result['entry'];entries.append(entry);payloads.append((entry['tile']+'/'+entry['region_file']+'.gz',base64.b64decode(result['data'],validate=True)))
                        raw=archive_bytes(entries,payloads,plan['frame'],plan_sha)
                        if entries!=record['entries'] or len(raw)!=record['bytes'] or sha_bytes(raw)!=record['sha256']:
                            raise ValueError('Interrupted ready archive cannot be rebuilt identically')
                        return child({'operation':'output','output':str(output),'bulk':str(bulk),'record':record,'data':base64.b64encode(raw).decode()},20)
                    if verified['state']!='present':raise ValueError('Cached archive verification failed')
                    return record
                except ChildNotReaped as error:
                    return {'state':'busy','tiles':0,'reason':str(error),'lock_retained':True,'inspected':0,'deferred':[]}
        with sqlite3.connect('file:'+str(control/'jobs.sqlite')+'?mode=ro',uri=True,timeout=2) as db:
            rows = db.execute("SELECT tile,stage,evidence,evidence_sha256 FROM jobs WHERE state='complete' AND stage IN (1,2) ORDER BY priority,tile,stage DESC").fetchall()
        highest,bases={},{}
        for row in rows:
            if row[1]==1:bases[row[0]]=list(row)
            if row[0] not in highest or row[1]>highest[row[0]][1]:highest[row[0]]=row
        closure_path=control/'hf-export-no-upgrade.sqlite'
        if closure_path.is_symlink() or (closure_path.exists() and closure_path.stat().st_size>32*2**20):
            raise ValueError('No-upgrade closure storage exceeds bound or is a symlink')
        with sqlite3.connect(closure_path,timeout=2) as closure:
            closure.execute('PRAGMA synchronous=FULL')
            closure.execute('PRAGMA max_page_count=8192')
            closure.execute('CREATE TABLE IF NOT EXISTS no_upgrade(tile TEXT PRIMARY KEY, appearance_sha TEXT NOT NULL, geometry_sha TEXT NOT NULL)')
            closures={tile:(appearance,geometry) for tile,appearance,geometry in closure.execute('SELECT tile,appearance_sha,geometry_sha FROM no_upgrade')}
        eligible,seen = [],set()
        for row in rows:
            tile,stage,evidence,checksum = row
            if tile in seen or row!=highest[tile]:continue
            seen.add(tile)
            if stage==2 and tile in bases and closures.get(tile)==(checksum,bases[tile][3]) and known.get(tile)==bases[tile][3]:
                continue
            if known.get(tile)!=checksum:eligible.append(list(row))
        if not eligible:return {'state':'caught_up','tiles':0,'eligible_unarchived':0,'inspected':0,'scan_complete':True,'deferred':[]}
        schedule_path = control/'hf-export-schedule.local.json'
        try:
            schedule = json.loads(checked_read(schedule_path,256*2**10))
        except FileNotFoundError:
            schedule = {'after':None,'cooldowns':{}}
        keys = [json.dumps([r[0],r[1],r[3]],separators=(',',':')) for r in eligible]
        cooldowns = {k:v for k,v in schedule.get('cooldowns',{}).items() if k in set(keys) and isinstance(v,dict)}
        after = schedule.get('after')
        begin = (keys.index(after)+1)%len(keys) if after in keys else 0
        entries,payloads,deferred,no_upgrades,selected_jobs = [],[],[],[],[]
        effective_eligible=len(eligible)
        inspected,visited,last = 0,0,after
        try:
            for offset in range(len(eligible)):
                index = (begin+offset)%len(eligible);job,key = eligible[index],keys[index]
                visited+=1
                cool = cooldowns.get(key,{})
                if cool.get('not_before',0)>time.time():
                    deferred.append({'tile':job[0],'stage':job[1],'receipt_sha256':job[3],'reason':cool.get('reason','IO cooldown')});continue
                if inspected>=2*limit or time.monotonic()-started>=TOTAL_SECONDS-22:
                    visited-=1;break
                inspected+=1;last=key
                try:
                    request={'job':job,'fallback':bases.get(job[0])}
                    result = child({**request,'operation':'tile','bulk':str(bulk),'frame':plan['frame'],'known_geometry':known.get(job[0])},min(CHILD_SECONDS,TOTAL_SECONDS-(time.monotonic()-started)-22))
                    expected=job[3]
                    if 'no_upgrade' in result:
                        note=result['no_upgrade']
                        if note['tile']!=job[0] or note['appearance_receipt_sha256']!=job[3] or not request['fallback'] or note['geometry_receipt_sha256']!=request['fallback'][3]:
                            raise ValueError('Owned no-upgrade identity differs')
                        no_upgrades.append(note);expected=request['fallback'][3]
                        with sqlite3.connect(closure_path,timeout=2) as closure:
                            closure.execute('PRAGMA synchronous=FULL')
                            closure.execute('PRAGMA max_page_count=8192')
                            closure.execute('INSERT OR REPLACE INTO no_upgrade VALUES(?,?,?)',(job[0],job[3],expected))
                        if result.get('already_archived'):
                            if known.get(job[0])!=expected:raise ValueError('Geometry acknowledgment differs')
                            effective_eligible-=1;cooldowns.pop(key,None);continue
                    entry = result['entry'];data = base64.b64decode(result['data'],validate=True)
                    if entry['tile']!=job[0] or entry['receipt_sha256']!=expected or entry['chunks']!=1024 or len(data)>48*2**20:
                        raise ValueError('Owned worker identity or bytes differ')
                    if sum(len(p) for _,p in payloads)+len(data)>MAX_BYTES-2**20:
                        raise ValueError('Public shard exceeds bounded transfer budget')
                    cooldowns.pop(key,None);entries.append(entry);payloads.append((job[0]+'/'+entry['region_file']+'.gz',data));selected_jobs.append(request)
                except (ReadDeadline,ValueError,OSError) as error:
                    reason=str(error)[:500]
                    cooldowns[key]={'not_before':time.time()+COOLDOWN_SECONDS,'reason':reason}
                    deferred.append({'tile':job[0],'stage':job[1],'receipt_sha256':job[3],'reason':reason})
                if len(entries)==limit:break
        except ChildNotReaped as error:
            # This candidate remains unarchived. Preserve its checksum-bound
            # retry and cursor before yielding to the inherited child lock;
            # the next invocation may rotate only after that inode is free.
            reason=str(error)[:500]
            cooldowns[key]={'not_before':time.time()+COOLDOWN_SECONDS,'reason':reason}
            deferred.append({'tile':job[0],'stage':job[1],'receipt_sha256':job[3],'reason':reason})
            cooldowns=dict(list(cooldowns.items())[-128:])
            durable_json(schedule_path,{'after':last,'cooldowns':cooldowns})
            return {'state':'busy','tiles':0,'reason':reason,'lock_retained':True,'inspected':inspected,'deferred':deferred[:2*limit]}
        # Trim scheduling metadata, never receipt/source data or the archived ledger.
        cooldowns=dict(list(cooldowns.items())[-128:])
        durable_json(schedule_path,{'after':last,'cooldowns':cooldowns})
        info={'inspected':inspected,'eligible_unarchived':effective_eligible,'deferred':deferred[:2*limit],'no_upgrades':no_upgrades[:2*limit],'scan_complete':visited==len(eligible)}
        if not entries and visited==len(eligible) and effective_eligible==0:
            return {'state':'caught_up','tiles':0,**info}
        if not entries:return {'state':'deferred' if visited==len(eligible) else 'scan_incomplete','tiles':0,**info}
        raw=archive_bytes(entries,payloads,plan['frame'],plan_sha);checksum=sha_bytes(raw)
        record={'schema':SCHEMA,'state':'ready','archive':str(output/(checksum+'.tar')),'sha256':checksum,'bytes':len(raw),
            'tiles':len(entries),'entries':entries,'plan_sha256':plan_sha,**info}
        try:
            # Persist intent before any tar write. Lost replies replay this exact
            # shard until the caller's durable known identities acknowledge it.
            durable_json(ready_path,{'record':record,'jobs':selected_jobs})
            return child({'operation':'output','output':str(output),'bulk':str(bulk),'record':record,'data':base64.b64encode(raw).decode()},20)
        except ChildNotReaped as error:
            return {'state':'busy','tiles':0,'reason':str(error),'lock_retained':True,**info}
    finally:
        # Closing alone leaves an unresolved child's inherited lock intact.
        os.close(lock)


def export(control,bulk,output,known,limit=8,storage_preflight=False,expected_inode=None):
    # This lifetime guard is best effort during kernel IO. An unresolved child
    # keeps the persistent lock, even if the OS cannot honor timely termination.
    previous_handler=signal.getsignal(signal.SIGALRM)
    previous_timer=signal.getitimer(signal.ITIMER_REAL)
    started=time.monotonic()
    def expired(signum,frame):
        raise ReadDeadline('Best-effort exporter lifetime guard exceeded')
    signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,min(TOTAL_SECONDS,previous_timer[0]) if previous_timer[0]>0 else TOTAL_SECONDS)
    try:
        return _export(control,bulk,output,known,limit,storage_preflight,expected_inode)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,previous_handler)
        if previous_timer[0]>0:
            signal.setitimer(signal.ITIMER_REAL,max(.001,previous_timer[0]-(time.monotonic()-started)),previous_timer[1])


if __name__=='__main__':
    if sys.argv[1:]==['--io-worker']:
        worker()
    else:
        parser=argparse.ArgumentParser(description=__doc__)
        for name in ('control','bulk','output'):parser.add_argument('--'+name,type=Path,required=True)
        parser.add_argument('--limit',type=int,default=8)
        parser.add_argument('--lock-inode',type=int,required=True)
        args=parser.parse_args()
        if args.lock_inode<1:parser.error('Positive existing lock inode required')
        print(json.dumps(export(args.control,args.bulk,args.output,json.loads(sys.stdin.readline()),args.limit,storage_preflight=True,expected_inode=args.lock_inode)))
