"""CPU-only private AWS scan accelerator; no live-save or credential access."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

from region_expansion import atomic,sha
from regional_cloud_exchange import extract
from regional_generate import _scan_tile,select_pair
from regional_store import compact


def process(entry,request,root,control,bulk):
    started=time.time();tile=entry['tile'];path=bulk/'tiles'/tile['id']
    for name,checksum in entry['sources'].items():
        if sha(path/'sources'/name)!=checksum:raise ValueError('Cloud source crop changed')
    catalog=json.loads((control/'scans/catalog.json').read_text())
    point_catalog=json.loads((control/'point-surveys/catalog.json').read_text())
    pair=select_pair(tile,request['frame'],catalog)
    if entry.get('footprints'):
        footprint=path/'footprints/scan-footprints.geojson'
        if sha(footprint)!=entry['footprints']['sha256']:raise ValueError('Cloud footprint crop changed')
        if pair is not None:
            shutil.copyfile(footprint,path/'sources/scan-footprints.geojson')
            meta=json.loads((path/'sources/sources.json').read_text())
            meta['scan_footprint_receipt']=entry['footprints']
            atomic(path/'sources/sources.json',json.dumps(meta).encode())
    receipt=_scan_tile(tile,request['frame'],path,pair,
        point_catalog,bulk/'ept-cache',control/'cook2022/catalog.json',control/'will2021/catalog.json')
    if not receipt.get('world'):raise ValueError('Cloud batch requires a measured scan candidate')
    world=Path(receipt['world']);compact(world)
    artifact=root/'out'/tile['id'];artifact.mkdir(parents=True,exist_ok=True)
    shutil.copytree(world,artifact/'world')
    source=path/'combined-scan-sources'
    if not source.exists():source=path/'scan-sources'
    if not source.exists():source=path/'point-only-sources'
    shutil.copytree(source,artifact/'source')
    # Retain exact observed crop arrays and source receipts, not only voxels.
    for name in ('scan-surfaces','point-crops','points.combined','point-only-crops','naip-imagery'):
        if (path/name).exists():shutil.copytree(path/name,artifact/name)
    atomic(artifact/'scan-receipt.json',json.dumps(receipt).encode())
    result={'job':entry['job'],'sources':entry['sources'],'frame':request['frame'],
        'seconds':time.time()-started,'execution':'Private AWS c7i CPU; full-density originals retained',
        'installed':False}
    if entry.get('footprints'):result['footprints']=entry['footprints']
    atomic(artifact/'cloud-result.json',json.dumps(result).encode())
    output=root/'out'/(tile['id']+'.tar.gz')
    with tarfile.open(output,'w:gz',compresslevel=1) as stream:
        for item in artifact.iterdir():stream.add(item,arcname=item.name)
    shutil.rmtree(artifact)
    return {'tile':tile['id'],'archive':str(output),'sha256':sha(output),'seconds':result['seconds'],
            'quality':receipt['quality']}


def run(args):
    root=args.root.resolve();control=root/'control';bulk=root/'bulk'
    extract(args.inputs,bulk);request=json.loads((bulk/'request.json').read_text())
    if request['schema']!='earthcraft-private-cloud-scans-v1':raise ValueError('Unknown cloud request')
    bucket=args.bucket
    def upload(source,key):
        subprocess.run([shutil.which('aws') or '/usr/bin/aws','s3','cp',str(source),'s3://'+bucket+'/'+key,
            '--region','us-east-1','--sse','AES256','--only-show-errors'],check=True)
    output=root/'out';output.mkdir(exist_ok=True);records=[]
    os.environ['EARTHCRAFT_BULK_ROOT']=str(bulk)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(process,entry,request,root,control,bulk):entry for entry in request['jobs']}
        for future in as_completed(futures):
            entry=futures[future]
            try:
                record=future.result();upload(record['archive'],'results/'+record['tile']+'.tar.gz')
                records.append(record)
            except Exception as error:
                records.append({'tile':entry['job']['tile'],'result':'failed','reason':str(error)})
            atomic(output/'status.json',json.dumps({'records':records,'complete':False,'time':time.time()}).encode())
            upload(output/'status.json','status.json')
    # Preserve original compressed LAZ/LAS and their exact spatial indexes in
    # private AWS until the coordinator retrieves them to LaCie and cleans up.
    cache=root/'source-cache.tar'
    with tarfile.open(cache,'w') as stream:
        for name in ('ept-cache','cook2022-cache','will2021-cache'):
            if (bulk/name).exists():stream.add(bulk/name,arcname=name)
    upload(cache,'source-cache.tar')
    final={'records':records,'complete':True,'source_cache_bytes':cache.stat().st_size,
           'source_cache_sha256':sha(cache),'time':time.time()}
    atomic(output/'status.json',json.dumps(final).encode());upload(output/'status.json','status.json')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--inputs',type=Path,required=True)
    p.add_argument('--bucket',required=True);p.add_argument('--workers',type=int,default=4)
    a=p.parse_args()
    if not 1<=a.workers<=8:p.error('Bounded CPU scan concurrency required')
    run(a)
