"""Compare full-density cached EPT crops against a frozen prior implementation."""
import argparse
import faulthandler
import importlib.util
import json
from pathlib import Path
import time
import signal
import numpy as np

from ept_buildings import crop
from region_expansion import atomic


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result);return result


def run(args):
    old=module('baseline_ept',args.baseline_ept);sources=module('baseline_scans',args.baseline_sources)
    old.frozen_get=sources.frozen_get
    grid=json.loads((args.tile/'sources/sources.json').read_text())
    manifests=sorted((args.tile/'point-crops').glob('*/manifest.json'))
    measured=next(json.loads(p.read_text()) for p in manifests
        if json.loads(p.read_text()).get('schema')=='earthcraft-classified-ept-building-crop-v1')
    project=measured['project'];args.output.mkdir(parents=True,exist_ok=False)
    with np.load(args.tile/'scan-surfaces/metric-surfaces.npz') as arrays:
        times=[];records=[]
        for name,function in [('before',old.crop),('after',crop),('after-warm',crop)]:
            start=time.perf_counter()
            records.append(function(project,grid,args.output/name,args.cache,arrays['dtm'],arrays['valid']))
            times.append(time.perf_counter()-start)
    with np.load(args.output/'before/points.npz') as before:
        for name in ('after','after-warm'):
            with np.load(args.output/name/'points.npz') as after:np.testing.assert_array_equal(before['xyz'],after['xyz'])
    source=lambda r:[(a['key'],a['sha256'],a['points']) for a in r['assets']]
    if any(source(r)!=source(records[0]) for r in records[1:]):raise ValueError('Acquired source nodes differ')
    result={'project':project,'tile':args.tile.name,'full_density_points':records[0]['building_points'],
        'original_nodes':len(records[0]['assets']),'seconds':dict(zip(('before','after','after_warm'),times)),
        'speedup':times[0]/times[1],'warm_speedup':times[0]/times[2],
        'point_arrays_identical':True,'source_nodes_identical':True,'downsampling':False,
        'scope':'Cached EPT crop only; not an end-to-end region throughput measurement'}
    atomic(args.output/'benchmark.json',json.dumps(result,indent=2).encode());print(json.dumps(result),flush=True)


if __name__=='__main__':
    faulthandler.enable()
    if hasattr(signal,'SIGUSR1'):faulthandler.register(signal.SIGUSR1,all_threads=True)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tile',type=Path,required=True);p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--baseline-ept',type=Path,required=True);p.add_argument('--baseline-sources',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);run(p.parse_args())
