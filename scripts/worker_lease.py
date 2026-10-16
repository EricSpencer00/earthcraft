"""Renew a running lease without reviving expired or replaced ownership."""
from contextlib import contextmanager
import sqlite3
import threading
import time


def renew(db, job, stage_index, lease_seconds=3600, now=None):
    now = time.time() if now is None else now
    changed = db.execute("""UPDATE jobs SET expires=?
        WHERE tile=? AND stage=? AND token=? AND state='running' AND expires>?""",
        (now + lease_seconds, job['tile'], stage_index, job['token'], now)).rowcount
    if changed != 1:
        raise ValueError('Worker lease lost or expired')


def renew_tile(db, job, lease_seconds=3600, now=None):
    """Renew every still-running stage a one-pass tile lease holds under its token."""
    now = time.time() if now is None else now
    changed = db.execute("""UPDATE jobs SET expires=?
        WHERE tile=? AND token=? AND state='running' AND expires>?""",
        (now + lease_seconds, job['tile'], job['token'], now)).rowcount
    if changed < 1:
        raise ValueError('Worker lease lost or expired')
    return changed


@contextmanager
def heartbeat(journal, job, stage_index, interval=60):
    stop = threading.Event()
    lost = []
    def keep_alive():
        try:
            with sqlite3.connect(journal, timeout=20, isolation_level=None) as db:
                while not stop.wait(interval):
                    renew(db, job, stage_index)
        except Exception as error:
            lost.append(error)
    thread = threading.Thread(target=keep_alive, name='tile-lease', daemon=True)
    thread.start()
    try:
        yield
        if lost:
            raise ValueError('Worker lease heartbeat failed') from lost[0]
    finally:
        stop.set()
        thread.join(timeout=25)


class LeaseKeeper:
    """Keep one-pass tile leases alive from claim until their batch is verified on the Hub.

    A packaged tile waits for its batch commit; its lease must not expire in
    between, or another worker could regenerate it. A lease the keeper fails to
    renew (expired or reclaimed) is reported through ``lost`` and never revived.
    """

    def __init__(self, journal, interval=60, lease_seconds=3600):
        self.journal, self.interval, self.lease_seconds = journal, interval, lease_seconds
        self.jobs, self.lost = {}, {}
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._thread = threading.Thread(target=self._run, name='tile-leases', daemon=True)
        self._thread.start()

    def add(self, job):
        with self._lock:
            self.jobs[job['tile']] = job

    def remove(self, job):
        with self._lock:
            self.jobs.pop(job['tile'], None)

    def check(self, job):
        error = self.lost.get(job['tile'])
        if error is not None:
            raise ValueError(f'Lease for tile {job["tile"]} was lost: {error}')

    def renew_all(self, db):
        with self._lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            try:
                renew_tile(db, job, self.lease_seconds)
            except Exception as error:  # noqa: BLE001 - recorded for the owner
                self.lost[job['tile']] = str(error)
                self.remove(job)

    def _run(self):
        try:
            with sqlite3.connect(self.journal, timeout=20, isolation_level=None) as db:
                while not self._stop.wait(self.interval):
                    self.renew_all(db)
        except Exception as error:  # noqa: BLE001
            for tile in list(self.jobs):
                self.lost[tile] = str(error)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=25)
