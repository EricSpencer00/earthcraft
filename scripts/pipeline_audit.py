"""Read-only pipeline measurements; write an explicit evidence snapshot."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import sqlite3
import statistics

from live_city import ROOT,atomic
from local_paths import bulk_path,bulk_root


def percentile(values,q):
    values=sorted(values)
    if not values:return None
    i=(len(values)-1)*q;lo=int(i);hi=min(lo+1,len(values)-1)
    return values[lo]*(hi-i)+values[hi]*(i-lo) if hi!=lo else values[lo]


def audit():
    with sqlite3.connect(f'file:{ROOT}/runs/chicago-adaptation-city-001/jobs.sqlite?mode=ro',uri=True) as db:
        stages=[{'stage':s,'state':state,'count':count} for s,state,count in db.execute('select stage,state,count(*) from jobs group by stage,state')]
        timing={}
        for stage,name in [(0,'source_acquisition_and_crop'),(1,'geometry_and_native_readback')]:
            rows=db.execute("select evidence from jobs where stage=? and state='complete' order by priority,tile",(stage,)).fetchall()
            values=[json.loads(Path(p).read_text()).get('seconds') for p, in rows]
            values=[v for v in values if v is not None]
            timing[name]={'samples':len(values),'p10_seconds':percentile(values,.1),'median_seconds':percentile(values,.5),
                          'p90_seconds':percentile(values,.9),'last_10_seconds':values[-10:],
                          'scope':'Mixed cold/warm source history, not independent end-to-end throughput'}
    coverage=json.loads(bulk_path('chicago','worlds','Earthcraft-Chicago-Continuous','city-coverage.json').read_text())
    exchange=ROOT/'runs/chicago-live-001'
    receipts=[json.loads(p.read_text()) for p in (exchange/'receipts').glob('*.json')]
    added=[p for p in receipts if p['mode']=='new_chunk' and p['result']=='applied_in_memory']
    paint=[p for p in receipts if p['mode']=='pavement' and p['result']=='applied_in_memory']
    stamps=sorted(datetime.fromisoformat(p['time']) for p in added)
    duration=(stamps[-1]-stamps[0]).total_seconds() if len(stamps)>1 else 0
    batches=[p['max_batch_ms'] for p in added]
    checkpoint_path=exchange/'saved-checkpoint.json'
    checkpoint=json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
    current_saved=(checkpoint.get('scan_chunks_saved_verified')==len(added) and
                   checkpoint.get('scan_cells_saved_verified')==sum(p['written'] for p in added) and
                   checkpoint.get('pavement_recolors_verified')==sum(p['written'] for p in paint))
    first=sorted(added,key=lambda p:p['time'])[:134]
    initial_span=(datetime.fromisoformat(first[-1]['time'])-datetime.fromisoformat(first[0]['time'])).total_seconds() if len(first)>1 else 0
    initial={'sample_chunks':len(first),'seconds':initial_span,'chunks_per_second':(len(first)-1)/initial_span if initial_span else None,
             'source':'Earliest 134 immutable new-chunk receipts from the initial client run',
             'block_writes':sum(p['written'] for p in first)}
    return {'time':datetime.now(timezone.utc).isoformat(),'stages':stages,'stage_timings':timing,
            'assembled_scan_tiles':len(coverage['tiles']),'planned_tiles':9639,
            'assembled_full_tile_area_m2':len(coverage['tiles'])*256**2,
            'initial_client_benchmark':initial,
            'live':{'new_chunks_applied_in_memory':len(added),'scan_cells_written':sum(p['written'] for p in added),
                    'pavement_cells_recolored':sum(p['written'] for p in paint),'pavement_chunks_processed':len(paint),
                    'receipt_results':dict(Counter(p['result'] for p in receipts)),
                    'window_seconds':duration,'chunks_per_second':(len(added)-1)/duration if duration else None,
                    'window_includes_game_pauses_and_deployment_downtime':True,
                    'per_chunk_peak_batch_ms_median':percentile(batches,.5),'worst_batch_ms':max(batches,default=None),
                    'all_current_client_receipts_independently_saved_reloaded_verified':current_saved,
                    'queue_files':len(list((exchange/'inbox').glob('*.gz')))},
            'free_gib':{'internal':shutil.disk_usage(ROOT).free/2**30,'bulk':shutil.disk_usage(bulk_root()).free/2**30},
            'llm_geographic_inference_used':False,'full_city_appearance_complete':False,
            'independent_geographic_accuracy_verified':False,'global_config_only_pipeline_verified':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args()
    result=audit();atomic(a.output,json.dumps(result,indent=2).encode());print(json.dumps(result,indent=2))
