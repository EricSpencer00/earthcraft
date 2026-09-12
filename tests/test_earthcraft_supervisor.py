import fcntl
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from earthcraft_supervisor import publisher_lock_held


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


if __name__ == '__main__':
    unittest.main()
