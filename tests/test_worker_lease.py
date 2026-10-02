from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from worker_lease import renew


class WorkerLeaseTests(unittest.TestCase):
    def test_renewal_cannot_revive_expired_or_replaced_attempt(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE jobs(tile,stage,token,state,expires)')
            db.execute("INSERT INTO jobs VALUES ('0_0',2,'owned','running',100)")
            job = {'tile': '0_0', 'token': 'owned'}
            renew(db, job, 2, now=90)
            self.assertEqual(db.execute('SELECT expires FROM jobs').fetchone()[0], 3690)
            with self.assertRaisesRegex(ValueError, 'expired'):
                renew(db, job, 2, now=3690)
            db.execute("UPDATE jobs SET token='replacement',expires=5000")
            with self.assertRaisesRegex(ValueError, 'lost'):
                renew(db, job, 2, now=3700)
            self.assertEqual(db.execute('SELECT expires FROM jobs').fetchone()[0], 5000)


if __name__ == '__main__': unittest.main()
