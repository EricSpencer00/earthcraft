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
import sqlite3
import subprocess
import sys
import time

from region_expansion import atomic


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
        workers={'base-1':'base','base-2':'base','scans-1':'scans'}
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).parent))
        common=['--control',str(control),'--bulk',str(args.bulk),'--frame',str(args.frame),
            '--illinois',str(args.illinois),'--reserve-gib',str(args.reserve_gib)]
        started=time.time()
        deadline_path=control/'supervisor-deadline.json'
        deadline=json.loads(deadline_path.read_text()) if deadline_path.exists() else started+14*86400
        if not deadline_path.exists():atomic(deadline_path,json.dumps(deadline).encode())
        try:
            while not stopping and time.time() < deadline:
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
                        else:
                            failures[owner]=failures.get(owner,0)+1
                            next_start[owner]=time.time()+min(600,30*2**(failures[owner]-1))
                    if owner in completed or failures.get(owner,0)>=10 or time.time()<next_start.get(owner,0):continue
                    if owner not in children and (control/'indexes.json').exists() and (control/'point-surveys/catalog.json').exists():
                        command=[sys.executable,'-u',str(Path(__file__).with_name('regional_generate.py')),
                            *common,'--lane',lane,'--worker-id',owner]
                        handle=(control/(owner+'.log')).open('a',buffering=1)
                        child=subprocess.Popen(command,env=env,stdout=handle,stderr=subprocess.STDOUT)
                        children[owner]=child;handles[owner]=handle
                atomic(control/'supervisor-status.json',json.dumps({'time':time.time(),'supervisor_pid':os.getpid(),
                    'workers':{owner:child.pid for owner,child in children.items()},
                    'failed_restarts':failures,'completed_workers':sorted(completed),
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
    args=parser.parse_args()
    if not 100<=args.reserve_gib<=500:parser.error('Keep at least 100 GiB of LaCie free')
    run(args)
