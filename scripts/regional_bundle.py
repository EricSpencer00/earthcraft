"""Export bounded verified regional tiles, including immutable LQ alternatives.

The sender runs on the mini. The receiver's checkpoint arrives on stdin; no
credentials or external service is involved. Stored MCA gzip bytes are exact.
"""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import time

from region_expansion import sha
from regional_store import region_bytes
from regional_install import baseline_is_extrusion


def export(control,bulk,output,known,limit=8,legacy=None):
    def checkpoint(step):
        (control/('export-'+str(os.getpid())+'.json')).write_text(json.dumps({'time':time.time(),'step':step}))
    checkpoint('select verified jobs')
    with sqlite3.connect(control/'jobs.sqlite') as db:
        rows=db.execute("SELECT tile,stage,evidence,evidence_sha256,priority FROM jobs WHERE state='complete' AND stage IN (1,2) ORDER BY priority,tile,stage").fetchall()
    grouped={}
    for tile,stage,path,checksum,_ in rows:
        grouped.setdefault(tile,{})[stage]=(Path(path),checksum)
    selected=[]
    for tile,stages in grouped.items():
        checkpoint('select '+tile)
        path,checksum=stages.get(2,stages[1])
        if known.get(tile)==checksum:continue
        if sha(path)!=checksum:raise ValueError('Frozen generation receipt changed')
        receipt=json.loads(path.read_text())
        if receipt.get('quality')=='LQ' and 2 in stages:
            path,checksum=stages[1]
            if known.get(tile)==checksum:continue
            if sha(path)!=checksum:raise ValueError('Frozen base receipt changed')
            receipt=json.loads(path.read_text())
        selected.append((tile,path,checksum,receipt))
        if len(selected)>=limit:break
    output.parent.mkdir(parents=True,exist_ok=True)
    index=[]
    with tarfile.open(output,'w') as archive:
        def add(raw,name):
            info=tarfile.TarInfo(name);info.size=len(raw);info.mode=0o600;archive.addfile(info,io.BytesIO(raw))
        def world_files(world,prefix,receipt):
            checkpoint('manifest '+str(world))
            add((world/'earthcraft.json').read_bytes(),prefix+'/earthcraft.json')
            for name,checksum in receipt['regions'].items():
                checkpoint('region '+str(world/'region'/name))
                raw=region_bytes(world/'region'/name)
                if hashlib.sha256(raw).hexdigest()!=checksum:raise ValueError('Verified export region changed')
                add(gzip.compress(raw,compresslevel=1,mtime=0),prefix+'/region/'+name+'.gz')
        for tile,path,checksum,receipt in selected:
            prefix=tile;world=Path(receipt['world']);baselines=[]
            add(path.read_bytes(),prefix+'/receipt.json');world_files(world,prefix+'/world',receipt)
            if receipt['quality']!='LQ':
                roots=[bulk/'tiles'/tile]
                if legacy:
                    meta=json.loads((world/'earthcraft.json').read_text());ox,oz=meta['world_offset_xz'];size=meta['source']['size']
                    directories=[legacy] if isinstance(legacy,Path) else legacy
                    roots += [directory/f'{x}_{z}' for directory in directories for x in range(ox//256,(ox+size)//256) for z in range(oz//256,(oz+size)//256)]
                for root in roots:
                    proof=root/'geometry-receipt.json'
                    if not proof.exists():continue
                    record=json.loads(proof.read_text());candidate=root/'world'
                    if not (candidate/'earthcraft.json').exists():continue
                    if not baseline_is_extrusion(json.loads((candidate/'earthcraft.json').read_text())):continue
                    target=prefix+'/baselines/'+str(len(baselines));baselines.append(target+'/world')
                    add(proof.read_bytes(),target+'/geometry-receipt.json');world_files(candidate,target+'/world',record)
            index.append({'tile':tile,'receipt_sha256':checksum,'baselines':baselines,
                'regions':list(receipt['regions']),'quality':receipt['quality']})
        add(json.dumps(index).encode(),'index.json')
    return {'bundle':str(output),'bytes':output.stat().st_size,'sha256':sha(output),'tiles':len(index),'entries':index}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--control',type=Path,required=True);p.add_argument('--bulk',type=Path,required=True)
    p.add_argument('--legacy',type=Path,action='append');p.add_argument('--limit',type=int,default=8)
    a=p.parse_args()
    if not 1<=a.limit<=32:p.error('Bounded export batch required')
    output=a.control/'exports'/('batch-'+str(time.time_ns())+'.tar')
    # One newline-delimited request avoids waiting for a multiplexed SSH
    # channel's final EOF. Bound it independently from the artifact payload.
    raw=sys.stdin.readline(16*2**20+1)
    if len(raw)>16*2**20:raise ValueError('Delivery checkpoint exceeds request bound')
    print(json.dumps(export(a.control,a.bulk,output,json.loads(raw),a.limit,a.legacy)))
