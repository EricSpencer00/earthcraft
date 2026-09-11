"""Publish a verified staged building update through the bounded live inbox.

Takes the existing producer lock, so pause/stop the ordinary producer first.
Requires the installed, running game to advertise the new capability. No game
save is written; receipts remain provisional until normal save/readback.
"""
import argparse
import fcntl
import gzip
import json
from pathlib import Path
import shutil
import time

from live_city import ROOT, archive_receipted, atomic, publish, sha, validate


def run(stage,exchange,timeout=240):
    stage,exchange=Path(stage),Path(exchange)
    manifest=json.loads((stage/'manifest.json').read_text());binding=json.loads((exchange/'binding.json').read_text())
    if manifest['schema']!='building-delta-stage-v1' or manifest['frame']!=binding['frame'] or manifest['llm_used'] is not False:
        raise ValueError('Wrong staged update or coordinate frame')
    if sha(Path(manifest['candidate_world'])/'building-layer.json')!=manifest['candidate_manifest_sha256']:
        raise ValueError('Staged building candidate changed')
    patches={}
    for record in manifest['patches']:
        file=stage/'inbox'/(record['patch']+'.json.gz')
        if sha(file)!=record['patch']:raise ValueError('Changed staged patch')
        patch=validate(json.loads(gzip.decompress(file.read_bytes())))
        if patch['mode']!='building_delta' or patch['frame']!=binding['frame'] or patch['cells']!=record['cells']:
            raise ValueError('Staged patch metadata mismatch')
        photo=patch['provenance'].get('protected_photo_resource_sha256')
        if photo and sha(Path(binding['world'])/'resources.zip')!=photo:raise ValueError('Accepted photo resources changed')
        patches[record['patch']]=patch
    with (exchange/'publisher.lock').open('a+') as lock:
        fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        status=json.loads((exchange/'status.json').read_text())
        if 'building_delta' not in status.get('modes',[]):raise ValueError('Running importer has not advertised building_delta')
        if (exchange/'pause').exists():raise ValueError('Live importer is paused')
        deadline=time.monotonic()+timeout;submitted=set();completed={}
        while len(completed)<len(patches):
            if shutil.disk_usage(ROOT).free<20*2**30:raise RuntimeError('Internal 20 GiB reserve reached')
            archive_receipted(exchange)
            room=max(0,128-len(list((exchange/'inbox').glob('*.json.gz'))))
            for identity,patch in patches.items():
                receipt=exchange/'receipts'/(identity+'.json')
                if receipt.exists():
                    value=json.loads(receipt.read_text())
                    if value['result']!='applied_in_memory':raise ValueError(value)
                    completed[identity]=value
                elif identity not in submitted and room:
                    if publish(exchange,patch)!=identity:raise ValueError('Canonical patch identity changed')
                    submitted.add(identity);room-=1
            result={'stage_manifest_sha256':sha(stage/'manifest.json'),'submitted':len(submitted),
                    'completed':len(completed),'total':len(patches),
                    'written':sum(r['written'] for r in completed.values()),
                    'conflicts_preserved':sum(r.get('conflicts',0) for r in completed.values()),
                    'already_target':sum(r.get('already_target',0) for r in completed.values()),
                    'saved_and_reloaded_verified':False,'receipts':list(completed)}
            atomic(stage/'live-publication.json',json.dumps(result,indent=2).encode())
            if len(completed)==len(patches):return result
            if time.monotonic()>deadline:raise TimeoutError('Awaiting running game or deferred chunks; resume the same stage, not a duplicate')
            time.sleep(.5)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',type=Path,required=True)
    p.add_argument('--exchange',type=Path,required=True);a=p.parse_args()
    print(json.dumps(run(a.stage,a.exchange),indent=2))
