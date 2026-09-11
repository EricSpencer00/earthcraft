"""Feed immutable, verified Chicago chunks to the in-game importer.

Never opens the installed save for writing. Atomic, bounded inbox files are the
only runtime interface. The game's receipts are not a disk save/reload proof.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import fcntl

import numpy as np
from city_save_update import read_region
from verify_metric_world import unpack

ROOT=Path(__file__).resolve().parents[1]
BASE=set('stone dirt grass_block sand water snow_block gray_concrete stone_bricks bricks sandstone clay bedrock iron_block'.split())
COLORS=set('white orange magenta light_blue yellow lime pink gray light_gray cyan purple blue brown green red black'.split())
STAINED_GLASS={f'{c}_stained_glass' for c in COLORS}


def digest(raw):return hashlib.sha256(raw).hexdigest()
def sha(path):return digest(path.read_bytes())


def atomic(path,raw):
    temp=path.with_suffix(path.suffix+'.partial')
    with temp.open('wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    temp.replace(path)


def allowed(name,mode):
    if not name.startswith('minecraft:'):return False
    local=name[10:]
    concrete=local.endswith('_concrete') and local[:-9] in COLORS
    return concrete if mode=='pavement' else concrete or local in BASE or local in STAINED_GLASS


def encode_chunk(tag,frame,provenance):
    if tag.get('block_entities'):raise ValueError('Source block entities unsupported')
    palette=[];volume=np.full(262144,-1,np.int16);seen=set()
    for s in tag['sections']:
        sy=int(s['Y'])
        if sy in seen or not -4<=sy<60:raise ValueError('Section height/duplicate')
        seen.add(sy)
        if 'block_states' not in s:continue
        bs=s['block_states'];mapping=[]
        for p in bs['palette']:
            name=str(p['Name'])
            if p.get('Properties'):raise ValueError('Non-default block states not supported by live v1')
            if name=='minecraft:air':mapping.append(-1);continue
            if not allowed(name,'new_chunk'):raise ValueError('Unsupported live block '+name)
            if name not in palette:palette.append(name)
            mapping.append(palette.index(name))
        values=np.zeros(4096,int) if len(mapping)==1 else unpack(bs['data'],max(4,(len(mapping)-1).bit_length()),4096)
        volume[(sy+4)*4096:(sy+5)*4096]=np.asarray(mapping)[values]
    starts=np.r_[0,np.flatnonzero(volume[1:]!=volume[:-1])+1]
    lengths=np.diff(np.r_[starts,len(volume)])
    runs=[[int(s),int(n),int(volume[s])] for s,n in zip(starts,lengths) if volume[s]>=0]
    result=dict(version=1,frame=frame,cx=int(tag['xPos']),cz=int(tag['zPos']),mode='new_chunk',
                palette=palette,runs=runs,cells=int((volume>=0).sum()),provenance=provenance)
    validate(result)
    return result


def validate(p):
    if p['version']!=1 or p['mode'] not in ('new_chunk','pavement'):raise ValueError('Version/mode')
    if len(p['palette'])>64 or not all(allowed(s,p['mode']) for s in p['palette']):raise ValueError('Palette')
    if any(type(p[k]) is not int or abs(p[k])>10000 for k in ('cx','cz')):raise ValueError('Chunk coordinate')
    end=0;total=0
    for r in p['runs']:
        if len(r)!=3 or not all(type(x) is int for x in r):raise ValueError('Run shape')
        start,n,v=r
        if start<end or n<1 or start+n>262144 or not 0<=v<len(p['palette']):raise ValueError('Run range')
        end=start+n;total+=n
    if total!=p['cells']:raise ValueError('Cell count')
    return p


def publish(exchange,patch):
    validate(patch)
    raw=gzip.compress(json.dumps(patch,sort_keys=True,separators=(',',':')).encode(),mtime=0)
    if len(raw)>2_000_000:raise ValueError('Compressed patch cap')
    identity=digest(raw);path=exchange/'inbox'/f'{identity}.json.gz'
    if not path.exists() and not (exchange/'receipts'/f'{identity}.json').exists():atomic(path,raw)
    return identity


def initialize(exchange,world,config):
    if exchange.exists() or config.exists():raise FileExistsError('Live binding already exists')
    coverage=json.loads((world/'city-coverage.json').read_text())
    protected=set()
    for tile in coverage['tiles'].values():
        x,z=tile['world_offset_xz'];size=tile['size_m']//16
        protected.update(f'{cx},{cz}' for cx in range(x//16,x//16+size) for cz in range(z//16,z//16+size))
    # Retain accepted original chunks even if a user cleared them entirely.
    report=json.loads((world/'earthcraft.json').read_text())
    ox,oz=report.get('world_offset_xz',[0,0]);size=report['source']['size']//16
    protected.update(f'{x},{z}' for x in range(ox//16,ox//16+size) for z in range(oz//16,oz//16+size))
    exchange.mkdir(parents=True)
    for name in ('inbox','receipts','archive'):(exchange/name).mkdir()
    value={'world':str(world.resolve()),'exchange':str(exchange.resolve()),
           'frame':coverage['world_plan_sha256'],'coordinate_frame':coverage['frame'],
           'protected_chunks':sorted(protected),'minecraft':'1.21.10',
           'initial_coverage_sha256':sha(world/'city-coverage.json')}
    atomic(exchange/'binding.json',json.dumps(value,indent=2).encode())
    atomic(config,json.dumps(value,indent=2).encode())
    return value


def paint_patches(painted,frame):
    a=json.loads((painted/'ground-appearance.json').read_text())
    if a['geometry_changed'] or a['llm_used']:raise ValueError('Invalid appearance provenance')
    report=json.loads((painted/'earthcraft.json').read_text());ox,oz=report.get('world_offset_xz',[0,0])
    codes=np.load(painted/'ground-appearance-blocks.npy');tops=np.load(painted/'top-heights.npy')
    groups={};palette=['minecraft:'+x for x in a['palette']]
    for z,x in np.argwhere(codes>0):
        code=int(codes[z,x]);name=palette[code]
        if name=='minecraft:gray_concrete':continue
        wx,wz=int(x+ox),int(z+oz);y=int(tops[z,x])
        groups.setdefault((wx//16,wz//16),[]).append([(y+64)*256+(wz%16)*16+wx%16,1,code])
    # The appearance palette includes non-paint sentinel entries; retain only used colors.
    for (cx,cz),runs in sorted(groups.items()):
        used=sorted({r[2] for r in runs});p=[palette[i] for i in used]
        for r in runs:r[2]=used.index(r[2])
        yield validate(dict(version=1,frame=frame,cx=cx,cz=cz,mode='pavement',palette=p,
                            runs=sorted(runs),cells=len(runs),provenance={'manifest':str(painted/'ground-appearance.json'),
                            'sha256':sha(painted/'ground-appearance.json'),'geometry_changed':False,'llm_used':False}))


def feed(exchange,journal,once=False):
    lock=(exchange/'publisher.lock').open('a+')
    fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    binding=json.loads((exchange/'binding.json').read_text());protected=set(binding['protected_chunks'])
    frame=binding['frame'];seen=set();checked={};processed=set()
    state=exchange/'published.json'
    if state.exists():seen=set(json.loads(state.read_text())['chunks'])
    # Archived content is bounded to 1 GiB. Originals remain at their immutable source paths.
    while True:
        if (exchange/'pause').exists():
            if once:return
            time.sleep(1);continue
        if shutil.disk_usage(ROOT).free<22*2**30:raise RuntimeError('Internal 22 GiB reserve reached')
        for receipt in (exchange/'receipts').glob('*.json'):
            source=exchange/'inbox'/(receipt.stem+'.json.gz')
            if source.exists():source.rename(exchange/'archive'/source.name)
        if sum(p.stat().st_size for p in (exchange/'archive').glob('*.gz'))>2**30:raise RuntimeError('Live archive 1 GiB cap reached')
        room=128-len(list((exchange/'inbox').glob('*.gz')))
        with sqlite3.connect(f'file:{journal}?mode=ro',uri=True) as db:
            rows=db.execute("SELECT tile,evidence,evidence_sha256 FROM jobs WHERE stage=1 AND state='complete' ORDER BY priority,tile").fetchall()
        published=0
        for tile,evidence,expected in rows:
            if room<=0:break
            if tile in processed:continue
            path=Path(evidence)
            if sha(path)!=expected:raise ValueError('Geometry receipt changed')
            r=json.loads(path.read_text());world=path.parent/'world';report_path=world/'earthcraft.json'
            if sha(report_path)!=r['world_manifest_sha256']:raise ValueError('World manifest changed')
            report=json.loads(report_path.read_text());f=binding['coordinate_frame'];ox,oz=report['world_offset_xz']
            if (report['source']['crs']!=f['crs'] or report['vertical_offset_m']!=f['vertical_offset_m'] or
                report['source']['west']!=f['west']+ox or report['source']['north']!=f['north']-oz or
                report['dimension_height']!=1024 or report['dimension_min_y']!=-64):raise ValueError('Tile frame mismatch')
            all_done=True
            for name,h in sorted(r['regions'].items()):
                region=world/'region'/name
                stamp=(region.stat().st_size,region.stat().st_mtime_ns)
                if str(region) not in checked:
                    if sha(region)!=h:raise ValueError('Verified region changed')
                    checked[str(region)]=stamp
                if checked[str(region)]!=stamp:raise ValueError('Immutable region changed since admission')
                chunks=read_region(region)
                if (region.stat().st_size,region.stat().st_mtime_ns)!=stamp:raise ValueError('Source changed during read')
                for (cx,cz),tag in chunks.items():
                    key=f'{cx},{cz}'
                    if key in protected or key in seen:continue
                    if room<=0:all_done=False;break
                    p=encode_chunk(tag,frame,{'tile':tile,'receipt':str(path),'receipt_sha256':expected,
                                            'region_sha256':h,'physical_accuracy_verified':False,'llm_used':False})
                    publish(exchange,p);seen.add(key);room-=1;published+=1
                    atomic(state,json.dumps({'chunks':sorted(seen),'time':time.time()}).encode())
                if room<=0:all_done=False;break
            if all_done:processed.add(tile)
        if published:print(json.dumps({'queued_new_chunks':published,'total_published':len(seen),'time':time.time()}),flush=True)
        if once:return
        time.sleep(5)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('exchange',type=Path)
    p.add_argument('--initialize',type=Path);p.add_argument('--config',type=Path)
    p.add_argument('--paint',type=Path);p.add_argument('--once',action='store_true')
    p.add_argument('--journal',type=Path,default=ROOT/'runs/chicago-adaptation-city-001/jobs.sqlite')
    a=p.parse_args()
    if a.initialize:print(json.dumps(initialize(a.exchange,a.initialize,a.config),indent=2));return
    if a.paint:
        frame=json.loads((a.exchange/'binding.json').read_text())['frame']
        for patch in paint_patches(a.paint,frame):print(publish(a.exchange,patch))
        return
    feed(a.exchange,a.journal,a.once)


if __name__=='__main__':main()
