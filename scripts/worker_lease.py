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
