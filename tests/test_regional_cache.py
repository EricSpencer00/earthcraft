"""Disk guard refusals and LRU eviction for the regional source cache."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import regional_cache as rc  # noqa: E402
from hf_store import DiskGuardError  # noqa: E402

KiB = 1024


def statvfs(free):
    return lambda _path: SimpleNamespace(f_bavail=free // 4096, f_frsize=4096, f_blocks=10**9)


def put(path, size, when):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))
    os.utime(path, (when, when))
    return path


class FootprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cap_and_floor_refuse_before_writing(self):
        gen = self.root / 'gen'
        put(gen / 'a.bin', 64 * KiB, time.time())
        footprint = rc.Footprint({'gen': gen}, cap=100 * KiB, floor=10 * 2**30, statvfs=statvfs(11 * 2**30))
        footprint.check(16 * KiB)
        with self.assertRaisesRegex(DiskGuardError, 'Footprint cap'):
            footprint.check(64 * KiB)
        self.assertGreaterEqual(footprint.peak_used, 64 * KiB)
        roomy = rc.Footprint({'gen': gen}, cap=100 * 2**30, floor=10 * 2**30, statvfs=statvfs(11 * 2**30))
        roomy.check(512 * 2**20)
        with self.assertRaisesRegex(DiskGuardError, 'Free-space floor'):
            roomy.check(2 * 2**30)

    def test_watch_flags_a_library_write_and_the_tile_cap(self):
        gen, tile = self.root / 'gen', self.root / 'gen' / '0_0'
        footprint = rc.Footprint({'gen': gen}, cap=10 * 2**20, floor=0)
        with footprint.watch(interval=0.01, extra={'tile': (tile, 8 * KiB)}):
            put(tile / 'world' / 'big.bin', 64 * KiB, time.time())
            time.sleep(0.2)
        self.assertEqual(footprint.breach_kind, 'tile')
        with self.assertRaises(DiskGuardError):
            footprint.check(0)
        (tile / 'world' / 'big.bin').unlink()
        self.assertTrue(footprint.clear_breach())
        footprint.check(0)

    def test_tracked_roots_count_without_walking(self):
        footprint = rc.Footprint({'cache': lambda: 5 * 2**30, 'gen': self.root}, cap=4 * 2**30, floor=0)
        with self.assertRaisesRegex(DiskGuardError, 'Footprint cap'):
            footprint.check(0)


class SourceCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'cache'
        self.clock = [1000.0]
        self.cache = rc.SourceCache(self.root, cap=100 * KiB, clock=lambda: self.clock[0], used_ttl=0)

    def tearDown(self):
        self.cache.close()
        rc.activate(None)
        self.tmp.cleanup()

    def test_units_group_sidecars_and_atomic_index_directories(self):
        put(self.root / 'terrain' / '15-1-2.png', 1000, 10)
        put(self.root / 'terrain' / '15-1-2.json', 100, 10)
        put(self.root / 'ept' / 'P' / 'data' / '7-1-2-3.laz', 1000, 10)
        put(self.root / 'ept' / 'P' / 'data' / '7-1-2-3.laz.receipt.json', 100, 10)
        put(self.root / 'ept' / 'P' / 'indexes' / 'f' / '7-1-2-3' / 'manifest.json', 10, 10)
        put(self.root / 'ept' / 'P' / 'indexes' / 'f' / '7-1-2-3' / 'coordinates.npy', 1000, 10)
        put(self.root / 'ept' / 'P' / 'indexes' / 'f' / '7-1-2-3.lock', 0, 10)
        put(self.root / 'cook2022-cache' / 'indexes' / '.locks' / 'x.lock', 0, 10)
        units = self.cache.units()
        self.assertEqual(sorted(units), ['ept/P/data/7-1-2-3', 'ept/P/indexes/f/7-1-2-3', 'terrain/15-1-2'])
        self.assertEqual(len(units['terrain/15-1-2']['paths']), 2)
        self.assertEqual(self.cache.unit_of(self.root / 'ept/P/indexes/f/7-1-2-3/coordinates.npy'),
                         'ept/P/indexes/f/7-1-2-3')
        self.assertEqual(self.cache.unit_of(self.root / 'ept/P/data/7-1-2-3.laz.receipt.json'), 'ept/P/data/7-1-2-3')

    def test_least_recently_used_unit_is_evicted_first_and_whole(self):
        put(self.root / 'terrain' / 'old.png', 40 * KiB, 10)
        put(self.root / 'terrain' / 'old.json', 1 * KiB, 10)
        put(self.root / 'terrain' / 'recent.png', 10 * KiB, 20)  # small enough that evicting old reaches low water
        self.cache.touch(self.root / 'terrain' / 'recent.png')  # stamped at clock 1000
        self.cache.admit(self.root / 'terrain' / 'new.png', 30 * KiB)
        self.assertFalse((self.root / 'terrain' / 'old.png').exists())
        self.assertFalse((self.root / 'terrain' / 'old.json').exists())
        self.assertTrue((self.root / 'terrain' / 'recent.png').exists())
        self.assertEqual(self.cache.evicted_units, 1)

    def test_units_used_by_a_running_tile_are_pinned(self):
        put(self.root / 'indexes' / 'illinois-ways.sqlite', 60 * KiB, 10)
        self.clock[0] = 2000.0
        self.cache.begin_tile()
        self.clock[0] = 2001.0
        self.cache.touch(self.root / 'indexes' / 'illinois-ways.sqlite')
        with self.assertRaises(rc.CacheCapacityError):
            self.cache.admit(self.root / 'ept' / 'P' / 'data' / 'n.laz', 50 * KiB)
        self.assertTrue((self.root / 'indexes' / 'illinois-ways.sqlite').exists())
        self.assertFalse((self.root / 'ept').exists())
        self.cache.end_tile()
        self.cache.admit(self.root / 'ept' / 'P' / 'data' / 'n.laz', 50 * KiB)  # unpinned now: evicted
        self.assertFalse((self.root / 'indexes' / 'illinois-ways.sqlite').exists())

    def test_admission_also_checks_the_footprint_floor(self):
        footprint = rc.Footprint({'cache': self.root}, cap=10 * 2**30, floor=10 * 2**30, statvfs=statvfs(10 * 2**30))
        self.cache.footprint = footprint
        with self.assertRaisesRegex(DiskGuardError, 'Free-space floor'):
            self.cache.admit(self.root / 'terrain' / 'x.png', 1)

    def test_hooks_use_the_cache_inside_it_and_legacy_reserves_elsewhere(self):
        rc.activate(self.cache)
        outside = Path(self.tmp.name) / 'elsewhere' / 'file.bin'
        with self.assertRaisesRegex(ValueError, 'reserve'):
            rc.admit(outside, 1, legacy_reserve=1 << 62, legacy_message='reserve')
        rc.admit(outside, 1, legacy_reserve=0)
        put(self.root / 'terrain' / 'a.png', 90 * KiB, 10)
        rc.admit(self.root / 'terrain' / 'b.png', 20 * KiB)
        self.assertFalse((self.root / 'terrain' / 'a.png').exists())
        self.assertEqual(rc.describe(self.root / 'terrain' / 'b.png'), 'cache:terrain/b.png')

    def test_eviction_respects_another_live_workers_pin(self):
        put(self.root / 'terrain' / 'shared.png', 80 * KiB, 10)
        other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            with self.cache._db_lock:
                self.cache._db.execute('INSERT INTO active(owner,pid,since) VALUES(?,?,?)', ('other', other.pid, 5.0))
            self.cache.touch(self.root / 'terrain' / 'shared.png')
            with self.assertRaises(rc.CacheCapacityError):
                self.cache.admit(self.root / 'terrain' / 'mine.png', 40 * KiB)
        finally:
            other.kill()
            other.wait()
        self.cache.admit(self.root / 'terrain' / 'mine.png', 40 * KiB)  # dead worker no longer pins
        self.assertFalse((self.root / 'terrain' / 'shared.png').exists())


class GeneratorGuardTests(unittest.TestCase):
    def test_worker_refuses_to_start_below_the_floor_without_claiming(self):
        from regional_fixtures import FRAME, plan_for
        import regional_generate as rg
        from chicago_tiles import Journal
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control = root / 'control'
            control.mkdir()
            plan = plan_for(['0_0'])
            (control / 'plan.json').write_text(json.dumps(plan))
            frame = root / 'frame.json'
            frame.write_text(json.dumps(FRAME))
            args = rg.build_parser().parse_args(['--control', str(control), '--frame', str(frame), '--scratch',
                                                 str(root / 'scratch'), '--min-free', '1000000T'])
            try:
                self.assertEqual(rg.Worker(args, store=object()).run(), rg.EX_TEMPFAIL)
            finally:
                rc.activate(None)
            self.assertFalse((control / 'jobs.sqlite').exists())
            journal = Journal(control / 'jobs.sqlite', plan)
            self.assertEqual({row['state'] for row in journal.summary()}, {'pending'})
            journal.close()

    def test_caps_that_exceed_the_footprint_are_refused(self):
        import regional_generate as rg
        args = rg.build_parser().parse_args(['--frame', 'f.json', '--cache-cap', '20GiB', '--workers', '2'])
        with self.assertRaisesRegex(SystemExit, 'exceed the footprint'):
            rg.Worker(args).budget()

    def test_supervisor_waits_without_spawning_when_the_guard_refuses(self):
        import regional_supervisor as rs
        with tempfile.TemporaryDirectory() as directory, patch('regional_supervisor.subprocess.Popen') as spawn:
            root = Path(directory)
            args = rs.build_parser().parse_args(['--control', str(root / 'control'), '--frame', str(root / 'f.json'),
                                                 '--scratch', str(root / 'scratch'), '--min-free', '1000000T'])
            result = rs.ensure(args)
            self.assertEqual(result['state'], 'waiting_for_storage')
            self.assertIn('floor', result['reason'])
            spawn.assert_not_called()
            self.assertFalse((root / 'control').exists())


class AdmitWalkTests(unittest.TestCase):
    def test_admit_walks_only_when_the_tally_reaches_the_cap(self):
        from unittest.mock import patch
        import regional_cache
        with tempfile.TemporaryDirectory() as folder:
            cache = regional_cache.SourceCache(Path(folder) / 'cache', cap=10 * 2**20)
            walks = []
            real = regional_cache.entry_bytes

            def counted(path):
                walks.append(Path(path))
                return real(path)
            with patch('regional_cache.entry_bytes', counted):
                for index in range(90):  # up to ~9 MiB of 10: the shared tally, no walk per admit
                    cache.admit(Path(folder) / 'cache' / 'ept' / f'n{index}.laz', 100 * 2**10)
                far = sum(1 for p in walks if p == cache.root)
                cache.admit(Path(folder) / 'cache' / 'ept' / 'big.laz', 2 * 2**20)  # would pass the cap: walk
                near = sum(1 for p in walks if p == cache.root)
            self.assertLessEqual(far, 1)
            self.assertEqual(near, far + 1)

    def test_an_eviction_frees_down_to_the_low_water_mark(self):
        import regional_cache
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'cache'
            cache = regional_cache.SourceCache(root, cap=10 * 2**20)
            for index in range(9):
                put(root / 'ept' / f'n{index}.laz', 2**20, 10 + index)
            cache.admit(root / 'ept' / 'new.laz', 2 * 2**20)  # 9 + 2 > 10 MiB: evict to 80% minus the newcomer
            left = sum(1 for index in range(9) if (root / 'ept' / f'n{index}.laz').exists())
            self.assertLessEqual(left, 6)
            self.assertFalse((root / 'ept' / 'n0.laz').exists())  # oldest first


if __name__ == '__main__':
    unittest.main()
