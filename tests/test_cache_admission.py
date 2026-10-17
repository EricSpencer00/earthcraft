"""Several workers sharing the source cache: the strip-pinning livelock and its admission-control fix.

Mirrors specs/CacheLeases.tla (same 3-worker, 3-member-cap model): pinning
members one by one live-locks; reserving the whole member set first does not.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import regional_cache as rc  # noqa: E402

KiB = 1024
MEMBER = 256 * KiB
UNIT = MEMBER + 4 * KiB          # member + receipt (one block)
CAP = 3 * UNIT + 96 * KiB        # room for three strip members plus the bookkeeping database


class SimWorker:
    """One regional_generate worker, stepped one cache action at a time."""

    def __init__(self, name, root, clock, tiles, namespace, reserve):
        self.name, self.root, self.clock = name, root, clock
        self.cache = rc.SourceCache(root, CAP, clock=lambda: clock[0])
        self.cache.owner = name
        self.tiles, self.namespace, self.reserve = list(tiles), namespace, reserve
        self.state, self.done, self.capacity, self.paused = 'idle', [], [], 0

    def path(self, member):
        return self.root / self.namespace / 'county' / 'dsm' / (member + '.adf')

    def need(self):
        return {self.path(m): UNIT for m in self.tiles[0]}

    def step(self):
        self.clock[0] += 1
        if not self.tiles:
            return
        if self.state == 'idle':
            if self.reserve:
                self.cache.enqueue(self.need())
                self.state = 'waiting'
            else:
                self.cache.begin_tile()
                self.state = 'fetch'
            return
        if self.state == 'waiting':
            try:
                if self.cache.try_grant():
                    self.cache.begin_tile()
                    self.state = 'fetch'
            except rc.CacheCapacityError:
                self.capacity.append(self.tiles.pop(0))
                self.state = 'idle'
            return
        missing = [m for m in self.tiles[0] if not self.path(m).exists()]
        if not missing:
            for m in self.tiles[0]:
                self.cache.touch(self.path(m))
            self.cache.end_tile()
            self.done.append(self.tiles.pop(0))
            self.state = 'idle'
            return
        for m in self.tiles[0]:  # hits are touched (pinned) as the tile reads them
            if self.path(m).exists():
                self.cache.touch(self.path(m))
        path = self.path(missing[0])
        try:
            self.cache.admit(path, UNIT)
        except rc.CacheCapacityError:
            if self.reserve:
                raise AssertionError(f'{self.name}: a reserved member was refused')
            if self.cache.others_active():  # old behaviour: tile_paused_cache, the tile is retried
                self.paused += 1
                self.cache.end_tile()
                self.cache.begin_tile()     # a worker claims its next tile right away
                return
            self.capacity.append(self.tiles.pop(0))
            self.cache.end_tile()
            self.state = 'idle'
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(os.urandom(MEMBER))
        path.with_name(path.name + '.receipt.json').write_text('{}')


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'cache'
        self.clock = [1000.0]

    def tearDown(self):
        for worker in getattr(self, 'workers', []):
            worker.cache.close()
        self.tmp.cleanup()

    def run_workers(self, jobs, namespace, reserve, rounds=200):
        self.workers = [SimWorker(name, self.root, self.clock, tiles, namespace, reserve)
                        for name, tiles in jobs.items()]
        for _ in range(rounds):
            for worker in self.workers:
                worker.step()
                self.assertLessEqual(rc.entry_bytes(self.root) - rc.entry_bytes(self.root / '.lru.sqlite'),
                                     CAP + 64 * KiB)
            if all(not worker.tiles for worker in self.workers):
                break
        return {w.name: w for w in self.workers}

    def test_pinning_members_one_by_one_live_locks(self):
        """The pre-fix rule: every worker holds one strip, the cap refuses the next, all pause, forever."""
        jobs = {'w1': [['a', 'b']], 'w2': [['c', 'd']], 'w3': [['e', 'f']]}
        result = self.run_workers(jobs, 'legacy', reserve=False, rounds=200)
        self.assertEqual(sum(len(w.done) for w in result.values()), 0)
        self.assertTrue(all(w.paused > 20 for w in result.values()))

    def test_reserving_the_whole_member_set_completes_every_tile(self):
        jobs = {'w1': [['a', 'b'], ['b', 'c']], 'w2': [['c', 'd']], 'w3': [['e', 'f'], ['a', 'b']]}
        result = self.run_workers(jobs, rc.GRID_NAMESPACE, reserve=True)
        self.assertEqual({n: len(w.done) for n, w in result.items()}, {'w1': 2, 'w2': 1, 'w3': 2})
        self.assertEqual(sum(len(w.capacity) for w in result.values()), 0)

    def test_a_set_larger_than_the_cap_fails_with_a_capacity_receipt(self):
        jobs = {'w1': [['a', 'b']], 'w2': [['a', 'b', 'c', 'd'], ['c']]}
        result = self.run_workers(jobs, rc.GRID_NAMESPACE, reserve=True)
        self.assertEqual(result['w2'].capacity, [['a', 'b', 'c', 'd']])
        self.assertEqual(result['w2'].done, [['c']])
        self.assertEqual(result['w1'].done, [['a', 'b']])

    def test_shared_strips_count_once_and_grants_are_fifo(self):
        a, b, big, small = (rc.SourceCache(self.root, CAP, clock=lambda: self.clock[0]) for _ in range(4))
        self.workers = []
        try:
            path = lambda m: self.root / rc.GRID_NAMESPACE / 'x' / 'dsm' / (m + '.adf')  # noqa: E731
            a.enqueue({path('a'): UNIT, path('b'): UNIT})
            self.assertTrue(a.try_grant())
            b.enqueue({path('b'): UNIT, path('c'): UNIT})  # union {a,b,c} fits: the shared strip counts once
            self.assertTrue(b.try_grant())
            self.assertEqual(a.reserved_units(), {f'{rc.GRID_NAMESPACE}/x/dsm/{m}' for m in 'abc'})
            b.end_tile()
            big.enqueue({path('c'): UNIT, path('d'): UNIT})  # {a,b,c,d} does not fit: waits, holds nothing
            self.assertFalse(big.try_grant())
            small.enqueue({path('a'): UNIT})                 # would fit, but the older request goes first
            self.assertFalse(small.try_grant())
            a.end_tile()
            self.assertTrue(big.try_grant())
            self.assertTrue(small.try_grant())               # {c,d} + {a} = three
        finally:
            for cache in (a, b, big, small):
                cache.close()

    def test_unreserved_strips_are_evictable_even_while_tiles_run(self):
        cache = rc.SourceCache(self.root, CAP, clock=lambda: self.clock[0])
        self.workers = []
        try:
            old = self.root / rc.GRID_NAMESPACE / 'x' / 'dsm' / 'z1.adf'
            for name in ('z1', 'z2', 'z3'):
                p = old.with_name(name + '.adf')
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(os.urandom(MEMBER))
            cache.begin_tile()
            self.clock[0] += 5
            cache.touch(old)  # used during a running tile, but not reserved: no longer pinned
            cache.enqueue({old.with_name('z2.adf'): UNIT})
            self.assertTrue(cache.try_grant())
            cache.admit(old.with_name('z4.adf'), 2 * UNIT)
            self.assertTrue(old.with_name('z2.adf').exists())  # reserved: kept
            self.assertFalse(old.exists() and old.with_name('z3.adf').exists())
        finally:
            cache.close()


if __name__ == '__main__':
    unittest.main()
