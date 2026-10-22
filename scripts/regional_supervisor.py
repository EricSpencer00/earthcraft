"""Keep the task-owned one-pass regional workers running at low priority.

Workers are regional_generate.py processes (see its docstring): each tile is
generated in guarded scratch and committed straight to the Hugging Face
dataset. The supervisor checks the disk footprint guard (cache + scratch +
control <= cap, free space >= floor) before every start; when it refuses, the
state is 'waiting_for_storage' and nothing is spawned. Workers run at low
priority so the machine stays usable, Minecraft included: nice +10 and, on
macOS, the 'utility' QoS clamp (taskpolicy -c utility), which yields CPU and
disk to interactive work. '--qos background' (taskpolicy -b) yields more but
also throttles networking: measured on the MacBook it cut Hub downloads from
about 20 MB/s to 0.3 MB/s, so a 3.4 GB way index took hours.
Only task-created child PIDs are managed.
A worker exiting with 75 is waiting for capacity or indexes, not failing.

  regional_supervisor.py --frame FRAME [--lane base] [--workers 1] [--ensure]
"""
import argparse
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import psutil

from chicago_tiles import parse_shard, tile_shard
from hf_store import DiskGuardError, GiB, parse_size
from region_expansion import atomic
from regional_cache import Footprint

ROOT = Path(__file__).resolve().parents[1]
EX_TEMPFAIL = 75
LOG_LIMIT = 16 * 2**20


def footprint(args):
    scratch = args.scratch.resolve()
    return Footprint({'cache': scratch / 'cache', 'gen': scratch / 'gen', 'control': args.control.resolve()},
                     args.footprint_cap, args.min_free, volume=scratch)


def storage_ready(args):
    """The guard's answer for one more tile; never an external-drive or fallback check."""
    try:
        footprint(args).check(args.tile_cap, 'worker start')
        return True, None
    except (DiskGuardError, OSError) as error:
        return False, str(error)


def low_priority(command, qos='utility'):
    """nice +10, plus a macOS QoS clamp: 'utility' (default) or 'background' (taskpolicy -b)."""
    if sys.platform == 'darwin' and Path('/usr/sbin/taskpolicy').exists():
        clamp = ['-b'] if qos == 'background' else ['-c', qos]
        return ['/usr/sbin/taskpolicy', *clamp, 'nice', '-n', '10', *command]
    return ['nice', '-n', '10', *command]


def worker_command(args, owner):
    command = [sys.executable, '-u', str(Path(__file__).with_name('regional_generate.py')),
               '--control', str(args.control.resolve()), '--frame', str(args.frame.resolve()),
               '--scratch', str(args.scratch.resolve()), '--lane', args.lane, '--worker-id', owner,
               '--workers', str(args.workers), '--cache-cap', str(args.cache_cap),
               '--footprint-cap', str(args.footprint_cap), '--min-free', str(args.min_free),
               '--tile-cap', str(args.tile_cap), '--batch-tiles', str(args.batch_tiles)]
    if args.hf_python:
        command += ['--hf-python', str(args.hf_python)]
    if args.footprints:
        command.append('--footprints')
    if args.shard:
        command += ['--shard', '%d/%d' % args.shard]
    return low_priority(command + ['--nice', '0'], args.qos)


def route_locality(args, env, logs):
    """Rank pending tiles so tiles sharing source files run together (once per plan; see regional_generate)."""
    command = [sys.executable, '-u', str(Path(__file__).with_name('regional_generate.py')), '--route-locality',
               '--control', str(args.control.resolve()), '--frame', str(args.frame.resolve()),
               '--scratch', str(args.scratch.resolve()), '--nice', '0']
    with (logs / 'supervisor-route.log').open('a') as log:
        try:
            return subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=3600).returncode
        except subprocess.TimeoutExpired:
            return None


def pending_work(control, lane, shard=None):
    journal = control / 'jobs.sqlite'
    if not journal.exists():
        return True
    with closing(sqlite3.connect(journal, timeout=20)) as db:  # WAL journal: a plain reader
        db.create_function('tile_shard', 2, tile_shard, deterministic=True)
        if lane in ('base', 'lq'):
            query = "SELECT count(*) FROM jobs j WHERE stage=0 AND state IN ('pending','running')"
        elif lane == 'auto':
            return pending_work(control, 'base', shard) or pending_work(control, 'scans', shard)
        else:
            query = ("SELECT count(*) FROM jobs j WHERE stage=2 AND state IN ('pending','running') AND NOT EXISTS "
                     "(SELECT 1 FROM jobs p WHERE p.tile=j.tile AND p.stage<2 AND p.state!='complete')")
        params = ()
        if shard:
            query += ' AND tile_shard(j.tile, ?) = ?'
            params = (shard[1], shard[0])
        return db.execute(query, params).fetchone()[0] > 0


def rotate(log):
    if log.exists() and log.stat().st_size > LOG_LIMIT:
        log.replace(log.with_name(log.name + '.1'))


def ensure(args):
    """Start a missing supervisor (for an SSH or launchd 'ensure' tick)."""
    ready, reason = storage_ready(args)
    if not ready:
        return {'state': 'waiting_for_storage', 'reason': reason}
    control = args.control.resolve()
    control.mkdir(parents=True, exist_ok=True)
    with (control / 'supervisor.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'state': 'running'}
        deadline = control / 'supervisor-deadline.json'
        if deadline.exists() and time.time() >= json.loads(deadline.read_text()):
            return {'state': 'generation_deadline'}
        status_path = control / 'supervisor-status.json'
        status = json.loads(status_path.read_text()) if status_path.exists() else {}
        if len(status.get('completed_workers', [])) == args.workers:
            return {'state': 'complete'}
        if any(value >= 10 for value in status.get('failed_restarts', {}).values()):
            return {'state': 'worker_failure_boundary'}
        command = [sys.executable, '-u', str(Path(__file__).resolve()), '--control', str(control),
                   '--frame', str(args.frame), '--scratch', str(args.scratch), '--lane', args.lane,
                   '--workers', str(args.workers), '--cache-cap', str(args.cache_cap),
                   '--footprint-cap', str(args.footprint_cap), '--min-free', str(args.min_free),
                   '--tile-cap', str(args.tile_cap), '--batch-tiles', str(args.batch_tiles)]
        if args.hf_python:
            command += ['--hf-python', str(args.hf_python)]
        if args.footprints:
            command.append('--footprints')
        if args.shard:
            command += ['--shard', '%d/%d' % args.shard]
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).parent.resolve()))
        logs = control / 'logs'
        logs.mkdir(exist_ok=True)
        rotate(logs / 'supervisor.log')
        command += ['--qos', args.qos]
        with (logs / 'supervisor.log').open('a') as log:
            child = subprocess.Popen(low_priority(command, args.qos), env=env, stdout=log, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        record = {'state': 'starting', 'supervisor_pid': child.pid, 'time': time.time()}
        atomic(control / 'supervisor-start.json', json.dumps(record).encode())
        return record


def run(args):
    control = args.control.resolve()
    control.mkdir(parents=True, exist_ok=True)
    logs = control / 'logs'
    logs.mkdir(exist_ok=True)
    with (control / 'supervisor.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        atomic(control / 'supervisor.pid', str(os.getpid()).encode())
        children, handles, failures, next_start, completed, stopping = {}, {}, {}, {}, set(), False

        def stop(signum, _):
            nonlocal stopping
            stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        # Worker ids stay 'base-N' for the base, lq and auto lanes so a lane change
        # requeues the same owners' leases and keeps the same log files.
        prefix = 'scans' if args.lane == 'scans' else 'base'
        workers = [f'{prefix}-{i}' for i in range(1, args.workers + 1)]
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).parent.resolve()))
        if (control / 'plan.json').exists() and (control / 'jobs.sqlite').exists():
            route_locality(args, env, logs)
        started = time.time()
        deadline_path = control / 'supervisor-deadline.json'
        deadline = json.loads(deadline_path.read_text()) if deadline_path.exists() else started + 45 * 86400
        if not deadline_path.exists():
            atomic(deadline_path, json.dumps(deadline).encode())
        try:
            while not stopping and time.time() < deadline:
                waiting_for_memory = []
                ready, reason = storage_ready(args)
                for owner in workers:
                    child = children.get(owner)
                    if child is not None and child.poll() is not None:
                        handles.pop(owner).close()
                        children.pop(owner)
                        if child.returncode == 0:
                            if not pending_work(control, args.lane, args.shard):
                                completed.add(owner)
                            else:
                                next_start[owner] = time.time() + 15
                        elif child.returncode == EX_TEMPFAIL:
                            next_start[owner] = time.time() + 300  # waiting for capacity or indexes
                        else:
                            failures[owner] = failures.get(owner, 0) + 1
                            next_start[owner] = time.time() + min(600, 30 * 2 ** (failures[owner] - 1))
                    if not ready or owner in children or owner in completed:
                        continue
                    if failures.get(owner, 0) >= 10 or time.time() < next_start.get(owner, 0):
                        continue
                    if psutil.virtual_memory().available < args.min_memory:
                        waiting_for_memory.append(owner)
                        continue
                    if not (control / 'plan.json').exists():
                        continue
                    log = logs / f'{owner}.log'
                    rotate(log)
                    handle = log.open('a', buffering=1)
                    children[owner] = subprocess.Popen(worker_command(args, owner), env=env, stdout=handle,
                                                       stderr=subprocess.STDOUT)
                    handles[owner] = handle
                atomic(control / 'supervisor-status.json', json.dumps({
                    'time': time.time(), 'supervisor_pid': os.getpid(),
                    'state': 'running' if ready else 'waiting_for_storage', 'storage_reason': reason,
                    'workers': {owner: child.pid for owner, child in children.items()},
                    'failed_restarts': failures, 'completed_workers': sorted(completed),
                    'waiting_for_memory': waiting_for_memory, 'lane': args.lane, 'deadline': deadline,
                    'installed': False}).encode())
                if len(completed) == len(workers) or (not children and any(v >= 10 for v in failures.values())):
                    break
                time.sleep(15)
        finally:
            for child in children.values():
                if child.poll() is None:
                    child.terminate()
            for child in children.values():
                try:
                    child.wait(timeout=float(os.environ.get('EARTHCRAFT_STOP_TIMEOUT', 240)))
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            for handle in handles.values():
                handle.close()


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--control', type=Path, default=ROOT / 'runs/regional/control')
    parser.add_argument('--frame', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, default=ROOT / 'runs/scratch')
    parser.add_argument('--lane', choices=('auto', 'lq', 'base', 'scans'), default='base')
    parser.add_argument('--workers', type=int, default=int(os.environ.get('EARTHCRAFT_WORKERS') or 1),
                        help='concurrent tile workers (default EARTHCRAFT_WORKERS or 1)')
    parser.add_argument('--shard', type=parse_shard, default=parse_shard(os.environ.get('EARTHCRAFT_SHARD')),
                        help='i/N: this machine\'s deterministic share of the plan (default EARTHCRAFT_SHARD)')
    parser.add_argument('--hf-python', type=Path, default=None)
    parser.add_argument('--footprints', action='store_true')
    parser.add_argument('--cache-cap', type=parse_size, default=12 * GiB)
    parser.add_argument('--footprint-cap', type=parse_size, default=25 * GiB)
    parser.add_argument('--min-free', type=parse_size, default=40 * GiB)
    parser.add_argument('--tile-cap', type=parse_size, default=4 * GiB)
    parser.add_argument('--batch-tiles', type=int, default=6)
    parser.add_argument('--min-memory', type=parse_size, default=parse_size(os.environ.get('EARTHCRAFT_MIN_MEMORY') or '2GiB'),
                        help='available RAM needed to start a worker (default EARTHCRAFT_MIN_MEMORY or 2GiB; '
                             'a 1 GiB host sets it lower and relies on swap)')
    parser.add_argument('--qos', choices=('utility', 'background'), default='utility',
                        help='macOS QoS clamp for workers (background also throttles network)')
    parser.add_argument('--ensure', action='store_true')
    return parser


if __name__ == '__main__':
    parser = build_parser()
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error('Use between one and eight workers')
    if args.ensure:
        print(json.dumps(ensure(args)))
    else:
        run(args)
