"""Keep the task-owned regional workers running with bounded retries.

Only task-created child PIDs are managed. Persistent input/storage failures
stop the affected lane with an explicit status; they are never marked complete.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import subprocess
import sys
import time
import psutil

from region_expansion import atomic


def bulk_storage_ready(args):
    """A missing external disk is a wait, never an internal-disk fallback."""
    mount = Path('/Volumes/LaCie')
    bulk = args.bulk.resolve()
    if not mount.is_mount() or not bulk.is_relative_to(mount/'Earthcraft'):
        return False
    try:
        return shutil.disk_usage(mount).free >= args.reserve_gib * 2**30
    except OSError:
        return False


def ensure(args):
    """Start a missing task supervisor from the authorized SSH context."""
    if not bulk_storage_ready(args):
        return {'state':'waiting_for_storage'}
    control=args.control.resolve();control.mkdir(parents=True,exist_ok=True)
    with (control/'supervisor.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'state':'running'}
        deadline=control/'supervisor-deadline.json'
        if deadline.exists() and time.time()>=json.loads(deadline.read_text()):
            return {'state':'generation_deadline'}
        status_path=control/'supervisor-status.json'
        status=json.loads(status_path.read_text()) if status_path.exists() else {}
        base_workers=getattr(args,'base_workers',2)
        if len(status.get('completed_workers',[]))==base_workers+args.scan_workers:return {'state':'complete'}
        if any(value>=10 for value in status.get('failed_restarts',{}).values()):
            return {'state':'worker_failure_boundary'}
        command=[sys.executable,'-u',str(Path(__file__).resolve()),'--control',str(control),
                 '--bulk',str(args.bulk),'--frame',str(args.frame),'--illinois',str(args.illinois),
                 '--reserve-gib',str(args.reserve_gib),'--scan-workers',str(args.scan_workers),
                 '--base-workers',str(base_workers)]
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).parent.resolve()))
        with (control/'supervisor-ssh.log').open('a') as log:
            child=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        record={'state':'starting','supervisor_pid':child.pid,'time':time.time(),'execution_route':'authorized SSH'}
        atomic(control/'supervisor-start.json',json.dumps(record).encode());return record


def run(args):
    control=args.control.resolve();control.mkdir(parents=True,exist_ok=True)
    with (control/'supervisor.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        atomic(control/'supervisor.pid',str(os.getpid()).encode())
        children={};handles={};failures={};next_start={};completed=set();stopping=False
        def stop(signum,_):
            nonlocal stopping
            stopping=True
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        workers={**{f'base-{i}':'base' for i in range(1,args.base_workers+1)},
                 **{f'scans-{i}':'scans' for i in range(1,args.scan_workers+1)}}
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).parent))
        common=['--control',str(control),'--bulk',str(args.bulk),'--frame',str(args.frame),
            '--illinois',str(args.illinois),'--reserve-gib',str(args.reserve_gib)]
        started=time.time()
        deadline_path=control/'supervisor-deadline.json'
        deadline=json.loads(deadline_path.read_text()) if deadline_path.exists() else started+45*86400
        if not deadline_path.exists():atomic(deadline_path,json.dumps(deadline).encode())
        try:
            while not stopping and time.time() < deadline:
                waiting_for_memory=[]
                storage_ready = bulk_storage_ready(args)
                for owner,lane in workers.items():
                    child=children.get(owner)
                    if child is not None and child.poll() is not None:
                        handles.pop(owner).close();children.pop(owner)
                        if child.returncode==0:
                            with sqlite3.connect(control/'jobs.sqlite') as db:
                                stages=(0,1) if lane=='base' else (2,)
                                pending=db.execute('SELECT count(*) FROM jobs WHERE stage IN ('+
                                    ','.join('?' for _ in stages)+") AND state!='complete'",stages).fetchone()[0]
                            if not pending:completed.add(owner)
                            else:next_start[owner]=time.time()+15
                        elif storage_ready:
                            failures[owner]=failures.get(owner,0)+1
                            next_start[owner]=time.time()+min(600,30*2**(failures[owner]-1))
                    if not storage_ready:
                        continue
                    if owner in completed or failures.get(owner,0)>=10 or time.time()<next_start.get(owner,0):continue
                    if owner.startswith('scans-') and owner!='scans-1' and time.time()-started<60:continue
                    if owner not in children and psutil.virtual_memory().available<2*2**30:
                        waiting_for_memory.append(owner);continue
                    if owner not in children and (control/'indexes.json').exists() and (control/'point-surveys/catalog.json').exists():
                        command=[sys.executable,'-u',str(Path(__file__).with_name('regional_generate.py')),
                            *common,'--lane',lane,'--worker-id',owner]
                        handle=(control/(owner+'.log')).open('a',buffering=1)
                        child=subprocess.Popen(command,env=env,stdout=handle,stderr=subprocess.STDOUT)
                        children[owner]=child;handles[owner]=handle
                        if owner.startswith('scans-') and owner!='scans-1':
                            for other in workers:
                                if other.startswith('scans-') and other not in children:
                                    next_start[other]=max(next_start.get(other,0),time.time()+30)
                atomic(control/'supervisor-status.json',json.dumps({'time':time.time(),'supervisor_pid':os.getpid(),
                    'state':'running' if storage_ready else 'waiting_for_storage',
                    'workers':{owner:child.pid for owner,child in children.items()},
                    'failed_restarts':failures,'completed_workers':sorted(completed),
                    'waiting_for_memory':waiting_for_memory,'maximum_scan_workers':args.scan_workers,
                    'maximum_base_workers':args.base_workers,
                    'storage_reserve_gib':args.reserve_gib,'deadline':deadline,
                    'installed':False}).encode())
                if len(completed)==len(workers) or (not children and any(value>=10 for value in failures.values())):break
                time.sleep(15)
        finally:
            for child in children.values():
                if child.poll() is None:child.terminate()
            for child in children.values():
                try:child.wait(timeout=45)
                except subprocess.TimeoutExpired:child.kill();child.wait()
            for handle in handles.values():handle.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control',type=Path,required=True)
    parser.add_argument('--bulk',type=Path,required=True)
    parser.add_argument('--frame',type=Path,required=True)
    parser.add_argument('--illinois',type=Path,required=True)
    parser.add_argument('--reserve-gib',type=int,default=150)
    parser.add_argument('--ensure',action='store_true')
    parser.add_argument('--scan-workers',type=int,default=3)
    parser.add_argument('--base-workers',type=int,default=2)
    args=parser.parse_args()
    if not 100<=args.reserve_gib<=500:parser.error('Keep at least 100 GiB of LaCie free')
    if not 1<=args.scan_workers<=3:parser.error('Use at most three scan workers on the mini')
    if not 1<=args.base_workers<=2:parser.error('Use one or two base workers')
    if args.ensure:print(json.dumps(ensure(args)))
    else:run(args)
