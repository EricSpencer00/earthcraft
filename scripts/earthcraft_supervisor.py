"""Keep the LaCie-backed Earthcraft worker and live publisher alive.

The supervisor is deliberately small and conservative: it never changes the
world, never falls back to an internal output path, and keeps the control
journal on the repository volume while all tile/source output stays on LaCie.
"""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BULK_ROOT = Path('/Volumes/LaCie/Earthcraft')
PLAN = ROOT / 'runs/chicago-adaptation-city-001'
EXCHANGE = ROOT / 'runs/chicago-live-001'
CATALOG = ROOT / 'runs/chicago-source-index-003/city-sources.json'
PRIORITY = PLAN / 'route-priorities/lake-shore-north-001.json'
LOCK_PATH = PLAN / 'generation-supervisor.lock'
LOG_PATH = PLAN / 'generation-supervisor.log'


def log(handle, message):
    handle.write(f'{time.strftime("%Y-%m-%d %H:%M:%S %z")} {message}\n')
    handle.flush()


def pending_jobs(journal):
    with sqlite3.connect(f'file:{journal}?mode=ro', uri=True) as db:
        return db.execute(
            "SELECT count(*) FROM jobs WHERE stage IN (0,1) "
            "AND state IN ('pending','running')"
        ).fetchone()[0]


def validate_lacie(bulk_root, output):
    bulk_root = Path(bulk_root).resolve()
    output = Path(output).resolve()
    mount = Path('/Volumes/LaCie')
    if not mount.is_mount():
        raise RuntimeError(f'LaCie is not mounted: {mount}')
    expected = DEFAULT_BULK_ROOT.resolve()
    if bulk_root != expected:
        raise RuntimeError(f'Bulk root must be exactly {expected}, got {bulk_root}')
    if not output.is_relative_to(bulk_root):
        raise RuntimeError(f'Output must stay under {bulk_root}: {output}')
    if not bulk_root.is_dir():
        raise RuntimeError(f'Bulk root is unavailable: {bulk_root}')


def command_environment(bulk_root):
    env = os.environ.copy()
    env['EARTHCRAFT_BULK_ROOT'] = str(Path(bulk_root).resolve())
    # The active Chicago worker and live publisher are deterministic source,
    # geometry, and provenance stages. Keep an explicit runtime marker so a
    # future child restart cannot accidentally inherit an AI-enabled setting.
    env['EARTHCRAFT_PROGRAMMATIC_ONLY'] = '1'
    env['EARTHCRAFT_LLM_INFERENCE'] = 'disabled'
    env['PYTHONPATH'] = str(ROOT / 'scripts')
    return env


def start_publisher(env, log_path):
    command = [sys.executable, str(ROOT / 'scripts/live_city.py'), str(EXCHANGE),
               '--journal', str(PLAN / 'jobs.sqlite'),
               '--priority-manifest', str(PRIORITY)]
    handle = log_path.open('a', buffering=1)
    process = subprocess.Popen(command, cwd=ROOT, env=env,
                               stdout=handle, stderr=subprocess.STDOUT,
                               start_new_session=True)
    return process, handle


def start_workers(env, output, bulk_root, log_path):
    command = [sys.executable, str(ROOT / 'scripts/chicago_worker.py'),
               '--plan', str(PLAN), '--catalog', str(CATALOG),
               '--output', str(output), '--bulk', str(Path(bulk_root) / 'chicago/lidar-2022'),
               '--source-cache', str(Path(bulk_root) / 'chicago/cache/metric-source-supertiles-v1'),
               '--way-index', str(Path(bulk_root) / 'chicago/cache/metric-ways-v1.sqlite'),
               '--lidar-mode', 'deferred', '--workers', '8', '--limit', '10000']
    handle = log_path.open('a', buffering=1)
    process = subprocess.Popen(command, cwd=ROOT, env=env,
                               stdout=handle, stderr=subprocess.STDOUT,
                               start_new_session=True)
    return process, handle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bulk-root', type=Path, default=DEFAULT_BULK_ROOT)
    parser.add_argument('--output', type=Path,
                        default=DEFAULT_BULK_ROOT / 'chicago/city-tiles-001')
    parser.add_argument('--poll-seconds', type=float, default=5.0)
    args = parser.parse_args()
    if args.poll_seconds < 1:
        parser.error('--poll-seconds must be at least one second')

    PLAN.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Earthcraft supervisor already running')
        log_handle = LOG_PATH.open('a', buffering=1)
        stopping = False

        def stop(signum, _frame):
            nonlocal stopping
            stopping = True
            log(log_handle, f'received signal {signum}; finishing supervisor shutdown')

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        publisher = publisher_log = workers = workers_log = None
        try:
            while not stopping:
                try:
                    validate_lacie(args.bulk_root, args.output)
                    env = command_environment(args.bulk_root)
                    if publisher is None or publisher.poll() is not None:
                        if publisher is not None:
                            log(log_handle, f'publisher exited rc={publisher.returncode}; restarting')
                            publisher_log.close()
                        publisher, publisher_log = start_publisher(
                            env, EXCHANGE / 'publisher-supervisor.log')
                        log(log_handle, f'started publisher pid={publisher.pid}')
                    if workers is None or workers.poll() is not None:
                        if workers is not None:
                            log(log_handle, f'worker pool exited rc={workers.returncode}; restarting')
                            workers_log.close()
                        remaining = pending_jobs(PLAN / 'jobs.sqlite')
                        if remaining:
                            workers, workers_log = start_workers(
                                env, args.output, args.bulk_root,
                                PLAN / 'worker-supervisor.log')
                            log(log_handle, f'started worker pool pid={workers.pid}; pending={remaining}')
                        else:
                            workers = workers_log = None
                            log(log_handle, 'worker queue is complete; supervisor remains alive for new work')
                except Exception as error:
                    log(log_handle, f'launch check failed: {type(error).__name__}: {error}')
                time.sleep(args.poll_seconds)
        finally:
            for process, handle in ((publisher, publisher_log), (workers, workers_log)):
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                if handle is not None:
                    handle.close()
            log(log_handle, 'supervisor stopped')
            log_handle.close()


if __name__ == '__main__':
    main()
