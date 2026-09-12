import fcntl
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from earthcraft_supervisor import pending_jobs, publisher_lock_held


def hold_publisher_lock(path, ready, release):
    with Path(path).open('a+') as lock:
        fcntl.lockf(lock, fcntl.LOCK_EX)
        ready.set()
        release.wait(5)


class SupervisorTests(unittest.TestCase):
    def test_existing_publisher_is_adoptable_without_a_duplicate_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            exchange = Path(folder)
            self.assertFalse(publisher_lock_held(exchange))
            context = multiprocessing.get_context('fork')
            ready, release = context.Event(), context.Event()
            holder = context.Process(target=hold_publisher_lock,
                                     args=(exchange / 'publisher.lock', ready, release))
            holder.start()
            self.assertTrue(ready.wait(5))
            self.assertTrue(publisher_lock_held(exchange))
            release.set()
            holder.join(5)
            self.assertEqual(holder.exitcode, 0)
            self.assertFalse(publisher_lock_held(exchange))

    def test_only_actionable_source_and_geometry_jobs_keep_workers_alive(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / 'jobs.sqlite'
            import sqlite3
            with sqlite3.connect(journal) as db:
                db.execute('CREATE TABLE jobs (tile TEXT, stage INTEGER, state TEXT)')
                db.executemany('INSERT INTO jobs VALUES (?,?,?)', [
                    ('source-ready', 0, 'pending'),
                    ('geometry-ready', 0, 'complete'),
                    ('geometry-ready', 1, 'pending'),
                    ('source-rejected', 0, 'failed'),
                    ('source-rejected', 1, 'pending'),
                    ('geometry-failed', 0, 'complete'),
                    ('geometry-failed', 1, 'failed'),
                ])
            self.assertEqual(pending_jobs(journal), 2)


if __name__ == '__main__':
    unittest.main()
