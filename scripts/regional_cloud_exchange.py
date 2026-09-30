"""Fenced scan batches for a private, disposable AWS accelerator.

The mini retains the queue, original base worlds and save installer. The cloud
receives frozen source crops/frame/catalogs, emits a complete checked scan tile,
and never sees the live save. Heartbeats renew only matching unexpired leases;
an expired/reclaimed worker cannot install or acknowledge cloud output.
"""
import argparse
import fcntl
import io
import json
from pathlib import Path,PurePosixPath
import re
import shutil
import sqlite3
import tarfile
import time

from chicago_tiles import Journal,digest
from region_expansion import atomic,sha
from regional_store import materialized,region_hashes
from verify_metric_world import verify


def extract(archive,destination,max_bytes=4*2**30):
    destination=Path(destination)
    with tarfile.open(archive) as stream:
        members=stream.getmembers()
        if len(members)>10000 or sum(item.size for item in members)>max_bytes:
            raise ValueError('Cloud artifact exceeds task bounds')
        for item in members:
            name=PurePosixPath(item.name)
            if name.is_absolute() or '..' in name.parts or not (item.isfile() or item.isdir()):
                raise ValueError('Unsafe cloud artifact member')
        stream.extractall(destination,members=members,filter='data')


def prepare(control,bulk,output,limit=24):
    if not 1<=limit<=128:raise ValueError('Bounded cloud scan batch required')
    plan=json.loads((control/'plan.json').read_text());tiles={tile['id']:tile for tile in plan['tiles']}
    index=control/'regional-footprints.sqlite'
    if index.exists():
        record=json.loads(index.with_suffix('.json').read_text())
        if record['sha256']!=sha(index) or record['crs']!=plan['frame']['crs']:
            raise ValueError('Frozen regional footprint index changed')
    owner='aws-scan-'+str(time.time_ns());journal=Journal(control/'jobs.sqlite',plan);jobs=[]
    footprints=control/'cloud-inputs'/owner
    try:
        for _ in range(limit):
            job=journal.claim('appearance',owner,lease_seconds=3600)
            if job is None:break
            source=bulk/'tiles'/job['tile']/'sources'
            files={p.name:sha(p) for p in source.iterdir() if p.is_file() and not p.name.startswith('._')}
            entry={'job':job,'tile':tiles[job['tile']],'sources':files}
            if index.exists():
                from regional_footprints import crop
                path=footprints/(job['tile']+'.geojson');path.parent.mkdir(parents=True,exist_ok=True)
                entry['footprints']=crop(index,json.loads((source/'sources.json').read_text()),path)
            jobs.append(entry)
    finally:journal.close()
    request={'schema':'earthcraft-private-cloud-scans-v1','owner':owner,'jobs':jobs,
             'frame':plan['frame'],'plan_sha256':digest(plan),'created':time.time()}
    output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output,'w:gz',compresslevel=1) as stream:
        raw=json.dumps(request).encode();info=tarfile.TarInfo('request.json');info.size=len(raw);stream.addfile(info,io.BytesIO(raw))
        for entry in jobs:
            tile=entry['job']['tile']
            for name in entry['sources']:
                stream.add(bulk/'tiles'/tile/'sources'/name,arcname='tiles/'+tile+'/sources/'+name,recursive=False)
            if 'footprints' in entry:
                stream.add(footprints/(tile+'.geojson'),arcname='tiles/'+tile+'/footprints/scan-footprints.geojson',recursive=False)
    atomic(output.with_suffix('.json'),json.dumps(request).encode())
    return {'request':str(output.with_suffix('.json')),'archive':str(output),'sha256':sha(output),'jobs':len(jobs)}


def renew(control,request):
    now=time.time();count=0
    with sqlite3.connect(control/'jobs.sqlite',timeout=20) as db:
        for entry in request['jobs']:
            job=entry['job']
            count+=db.execute("UPDATE jobs SET expires=? WHERE tile=? AND stage=2 AND state='running' AND owner=? AND token=? AND expires>?",
                (now+3600,job['tile'],request['owner'],job['token'],now)).rowcount
    return {'renewed':count,'jobs':len(request['jobs'])}


def accept(control,bulk,request,archive,tile):
    locks=control/'cloud-imports';locks.mkdir(parents=True,exist_ok=True)
    if not re.fullmatch(r'-?\d+_-?\d+',tile):raise ValueError('Invalid cloud tile')
    with (locks/(tile+'.lock')).open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _accept(control,bulk,request,archive,tile)


def _accept(control,bulk,request,archive,tile):
    if not re.fullmatch(r'-?\d+_-?\d+',tile):raise ValueError('Invalid cloud tile')
    entry=next(e for e in request['jobs'] if e['job']['tile']==tile);job=entry['job']
    plan=json.loads((control/'plan.json').read_text())
    if digest(plan)!=request['plan_sha256'] or plan['frame']!=request['frame']:
        raise ValueError('Cloud result uses another regional frame/plan')
    with sqlite3.connect(control/'jobs.sqlite') as db:
        done=db.execute('SELECT state,owner,evidence,evidence_sha256 FROM jobs WHERE tile=? AND stage=2',(tile,)).fetchone()
    if done and done[:2]==('complete',request['owner']):
        proof=Path(done[2]);receipt=json.loads(proof.read_text())
        if sha(proof)!=done[3] or receipt.get('cloud_execution',{}).get('job')!=job:
            raise ValueError('Acknowledged cloud proof changed')
        return {'tile':tile,'quality':receipt['quality'],'verified':True,'already_acknowledged':True,'installed':False}
    staging=control/'cloud-imports'/request['owner']/tile
    if staging.exists():shutil.rmtree(staging)
    staging.mkdir(parents=True);extract(archive,staging)
    receipt=json.loads((staging/'scan-receipt.json').read_text());world=staging/'world'
    result=json.loads((staging/'cloud-result.json').read_text())
    if (result['job']!=job or result['frame']!=request['frame'] or result['sources']!=entry['sources'] or
            result.get('footprints')!=entry.get('footprints') or
            receipt['tile']!=tile or receipt['stage']!='appearance' or receipt['result']!='pass'):
        raise ValueError('Cloud result identities differ')
    if sha(world/'earthcraft.json')!=receipt['world_manifest_sha256'] or region_hashes(world)!=receipt['regions']:
        raise ValueError('Cloud world payload changed')
    destination=bulk/'tiles'/tile/'cloud'/request['owner']
    # Recheck the leased token before writing this immutable candidate. The
    # receipt transition after validation is still protected by Journal.finish.
    with sqlite3.connect(control/'jobs.sqlite') as db:
        row=db.execute('SELECT state,owner,token,expires FROM jobs WHERE tile=? AND stage=2',(tile,)).fetchone()
    if row is None or row[:3]!=('running',request['owner'],job['token']) or row[3]<=time.time():
        raise ValueError('Cloud lease expired or was reclaimed')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        # Recover a stop after candidate copy/DEM relocation but before queue
        # acknowledgement, only when every original candidate byte is intact.
        if json.loads((destination/'cloud-result.json').read_text())!=result or region_hashes(destination/'world')!=receipt['regions']:
            raise ValueError('Prior cloud import candidate changed')
        actual=json.loads((destination/'world/earthcraft.json').read_text());original=json.loads((staging/'world/earthcraft.json').read_text())
        actual['dem_path']=original['dem_path']
        if actual!=original:raise ValueError('Prior cloud import metadata changed')
        if sha(destination/'source/elevation.tif')!=sha(staging/'source/elevation.tif'):
            raise ValueError('Prior cloud import ground changed')
    else:shutil.copytree(staging,destination)
    world=destination/'world';meta=json.loads((world/'earthcraft.json').read_text())
    original_manifest_sha256=receipt['world_manifest_sha256']
    # Point/roof arrays and Anvil bytes stay identical. Only the relocated DEM
    # reference changes; retain the original cloud manifest hash in the receipt.
    meta['dem_path']=str((destination/'source'/'elevation.tif').resolve())
    atomic(world/'earthcraft.json',json.dumps(meta,indent=2).encode())
    with materialized(world):checks=verify(world)
    expected=entry['tile']['world_offset_xz']
    if meta['world_offset_xz']!=expected or meta['vertical_offset_m']!=request['frame']['vertical_offset_m']:
        raise ValueError('Cloud world position or vertical translation differs')
    receipt.update(world=str(world.resolve()),world_manifest_sha256=sha(world/'earthcraft.json'),checks=checks,
        cloud_original_manifest_sha256=original_manifest_sha256,cloud_execution=result,installed=False)
    proof=destination/'scan-receipt.json';atomic(proof,json.dumps(receipt).encode())
    journal=Journal(control/'jobs.sqlite',plan)
    try:journal.finish(job,proof)
    finally:journal.close()
    shutil.rmtree(staging)
    return {'tile':tile,'quality':receipt['quality'],'verified':True,'installed':False}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('prepare','renew','accept'))
    p.add_argument('--control',type=Path,required=True);p.add_argument('--bulk',type=Path)
    p.add_argument('--output',type=Path);p.add_argument('--request',type=Path)
    p.add_argument('--archive',type=Path);p.add_argument('--tile');p.add_argument('--limit',type=int,default=24)
    a=p.parse_args();request=json.loads(a.request.read_text()) if a.request else None
    if a.mode=='prepare':result=prepare(a.control,a.bulk,a.output,a.limit)
    elif a.mode=='renew':result=renew(a.control,request)
    else:result=accept(a.control,a.bulk,request,a.archive,a.tile)
    print(json.dumps(result),flush=True)
