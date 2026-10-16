"""Offline tests for the guarded, batched, verified Hugging Face direct store."""
import base64
from email.utils import format_datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import hf_store as hs  # noqa: E402
from restore_world_snapshot import restore  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'anvil'
OWNER = 'EricSpencer00'
REPO = 'EricSpencer00/earthcraft'
PLAYER_UUID = '12345678-9abc-4def-8123-456789abcdef'
PLAYER_NAME = 'testplayer01'


# -- NBT fixture writer -------------------------------------------------------

def named(tag, name, payload):
    raw = name.encode()
    return bytes([tag]) + len(raw).to_bytes(2, 'big') + raw + payload


def p_int(value):
    return int(value).to_bytes(4, 'big', signed=True)


def p_str(value):
    raw = value.encode()
    return len(raw).to_bytes(2, 'big') + raw


def p_compound(*children):
    return b''.join(children) + b'\0'


def p_list(element, items):
    return bytes([element]) + p_int(len(items)) + b''.join(items)


def uuid_ints(value):
    raw = bytes.fromhex(value.replace('-', ''))
    return [int.from_bytes(raw[i:i + 4], 'big', signed=True) for i in range(0, 16, 4)]


def level_dat(with_player=True):
    player = named(10, 'Player', p_compound(
        named(11, 'UUID', p_int(4) + b''.join(p_int(v) for v in uuid_ints(PLAYER_UUID))),
        named(9, 'Pos', p_list(6, [bytes(8)] * 3)),
        named(8, 'Dimension', p_str('minecraft:overworld'))))
    data = p_compound(
        named(8, 'LevelName', p_str('Earthcraft')),
        named(10, 'Version', p_compound(named(8, 'Name', p_str('1.21.10')), named(3, 'Id', p_int(4556)))),
        named(3, 'DataVersion', p_int(4556)),
        *( [player] if with_player else [] ),
        named(10, 'GameRules', p_compound(named(8, 'doDaylightCycle', p_str('false')))))
    return gzip.compress(named(10, '', p_compound(named(10, 'Data', data))), mtime=0)


def scoreboard_dat():
    scores = [p_compound(named(8, 'Objective', p_str('ec_speed')), named(8, 'Name', p_str(PLAYER_NAME)),
                         named(3, 'Score', p_int(2))),
              p_compound(named(8, 'Objective', p_str('ec_speed')), named(8, 'Name', p_str('#fake')),
                         named(3, 'Score', p_int(1)))]
    objectives = [p_compound(named(8, 'Name', p_str('ec_speed')), named(8, 'CriteriaName', p_str('trigger')))]
    root = named(10, '', p_compound(named(10, 'data', p_compound(
        named(9, 'PlayerScores', p_list(10, scores)), named(9, 'Objectives', p_list(10, objectives)))),
        named(3, 'DataVersion', p_int(4556))))
    return gzip.compress(root, mtime=0)


def region_bytes(chunks, size=None):
    """A minimal Anvil file: location entries for the given (cx & 31, cz & 31)."""
    sectors = 2 + len(chunks)
    raw = bytearray((size or sectors * 4096))
    for n, (lx, lz) in enumerate(chunks):
        index = lx + lz * 32
        raw[index * 4:index * 4 + 4] = (2 + n).to_bytes(3, 'big') + b'\x01'
        start = (2 + n) * 4096
        payload = b'\x78\x9c' + bytes(8)
        raw[start:start + 5] = (len(payload) + 1).to_bytes(4, 'big') + b'\x02'
        raw[start + 5:start + 5 + len(payload)] = payload
    return bytes(raw)


def tar_bytes(members, gz=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz' if gz else 'w', format=tarfile.PAX_FORMAT) as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


# -- Fake Hub -------------------------------------------------------------------

class FakeHTTPError(Exception):
    def __init__(self, status, headers=None, message=''):
        super().__init__(message or f'HTTP {status}')
        self.response = SimpleNamespace(status_code=status, headers=headers or {})


class FakeHub:
    def __init__(self):
        self.repos = {}
        self.commit_failures = []  # exceptions raised by the next commits (after storing, if 'after')
        self.commits = []
        self.downloads = []
        self.tamper = {}

    def add_repo(self, repo, files, private=False):
        self.repos[repo] = {'files': dict(files), 'private': private, 'sha': hashlib.sha1(repo.encode()).hexdigest()}

    @staticmethod
    def lfs(data):
        return b'\0' in data[:512] or len(data) > 10 * 2**20

    def remote(self, path, data):
        digest = hs.hash_bytes(data)
        return hs.RemoteFile(path, len(data), digest.sha256 if self.lfs(data) else None, digest.blob_id)

    def whoami(self):
        return OWNER

    def info(self, repo, revision=None):
        meta = self.repos[repo]
        return {'sha': meta['sha'], 'private': meta['private'], 'id': repo}

    def list_files(self, repo, revision=None, prefixes=None):
        files = self.repos[repo]['files']
        return {p: self.remote(p, d) for p, d in files.items()
                if not prefixes or any(p.startswith(prefix.rstrip('/') + '/') for prefix in prefixes)}

    def paths_info(self, repo, paths, revision=None, expand=True):
        files = self.repos[repo]['files']
        return {p: self.remote(p, files[p]) for p in paths if p in files}

    def commit(self, repo, items, message, revision=None):
        failure = self.commit_failures.pop(0) if self.commit_failures else None
        if failure is not None and failure[0] == 'before':
            raise failure[1]
        files = self.repos[repo]['files']
        for item in items:
            data = Path(item.local).read_bytes()
            if hs.hash_bytes(data).sha256 != item.digest.sha256:
                raise RuntimeError('local changed')
            files[item.path_in_repo] = self.tamper.get(item.path_in_repo, data)
        oid = hashlib.sha1((message + str(len(self.commits))).encode()).hexdigest()
        self.repos[repo]['sha'] = oid
        self.commits.append({'oid': oid, 'paths': [i.path_in_repo for i in items], 'message': message})
        if failure is not None:
            raise failure[1]
        return {'oid': oid, 'url': f'https://example.invalid/{oid}'}

    def download(self, repo, path, revision, local_dir):
        data = self.repos[repo]['files'][path]
        target = Path(local_dir) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        meta = Path(local_dir) / '.cache' / 'huggingface' / 'download' / (path + '.metadata')
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text('etag')
        self.downloads.append(path)
        return target


def statvfs_for(total, free, frsize=4096):
    return lambda _path: SimpleNamespace(f_blocks=total // frsize, f_bavail=free // frsize, f_frsize=frsize)


def make_ctx(root, hub, *, cap=2**30, batch_bytes=2**30, batch_files=400, floor=0):
    guard = hs.DiskGuard(root, cap, floor_bytes=floor)
    retry = hs.Retry(attempts=4, sleep=lambda _s: None, rand=lambda: 0.5)
    return hs.Context(REPO, guard, hub, retry, batch_bytes, batch_files, True, None, echo=False)


# -- Tests ---------------------------------------------------------------------------

class DiskGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'scratch'

    def tearDown(self):
        self.tmp.cleanup()

    def test_cap_refuses_before_any_byte_is_written(self):
        guard = hs.DiskGuard(self.root, 64 * 1024, floor_bytes=0)
        guard.write_bytes(guard.path('a.bin'), bytes(40 * 1024))
        with self.assertRaises(hs.DiskGuardError):
            guard.write_bytes(guard.path('b.bin'), bytes(40 * 1024))
        self.assertFalse(guard.path('b.bin').exists())
        self.assertEqual([p.name for p in self.root.iterdir()], ['a.bin'])

    def test_default_floor_is_max_of_25_gb_and_ten_percent(self):
        small = hs.DiskGuard(self.root, 1, statvfs=statvfs_for(100 * 10**9, 50 * 10**9))
        self.assertEqual(small.floor(), 25 * 10**9)
        large = hs.DiskGuard(self.root, 1, statvfs=statvfs_for(10**12, 500 * 10**9))
        self.assertEqual(large.floor(), 10**11)
        explicit = hs.DiskGuard(self.root, 1, floor_bytes=7, statvfs=statvfs_for(10**12, 1))
        self.assertEqual(explicit.floor(), 7)

    def test_floor_refuses_and_admit_aborts_after_waiting(self):
        guard = hs.DiskGuard(self.root, 10**12, statvfs=statvfs_for(10**12, 101 * 10**9))
        guard.check(10**8)
        with self.assertRaises(hs.DiskGuardError):
            guard.check(2 * 10**9)
        sleeps = []
        with self.assertRaises(hs.DiskGuardError):
            guard.admit(2 * 10**9, wait_seconds=0.01, poll_seconds=0.001, sleep=sleeps.append)
        self.assertTrue(sleeps)

    def test_writes_and_deletes_stay_inside_the_root(self):
        guard = hs.DiskGuard(self.root, 2**20, floor_bytes=0)
        with self.assertRaises(ValueError):
            guard.write_bytes(Path(self.tmp.name) / 'outside.bin', b'x')
        with self.assertRaises(ValueError):
            guard.remove(Path(self.tmp.name) / 'outside.bin')
        with self.assertRaises(ValueError):
            guard.path('..', 'escape')

    def test_watch_flags_a_breach_for_the_next_check(self):
        guard = hs.DiskGuard(self.root, 1024, floor_bytes=0)
        self.root.mkdir(parents=True)
        with guard.watch(interval=0.01):
            (self.root / 'client-cache.bin').write_bytes(bytes(8192))  # a library write we do not control
            time.sleep(0.2)
        self.assertIsNotNone(guard.breach)
        self.assertGreaterEqual(guard.peak_used, 8192)
        with self.assertRaises(hs.DiskGuardError):
            guard.check(0)

    def test_hf_environment_points_every_cache_into_scratch(self):
        guard = hs.DiskGuard(self.root, 2**30, floor_bytes=0)
        env = {'HF_HOME': '/elsewhere/hf'}
        values = hs.bound_hf_environment(guard, environ=env)
        for key in ('HF_HOME', 'HF_HUB_CACHE', 'HF_XET_CACHE', 'HF_ASSETS_CACHE', 'TMPDIR'):
            self.assertTrue(Path(values[key]).is_relative_to(self.root), key)
        self.assertEqual(env['HF_TOKEN_PATH'], '/elsewhere/hf/token')
        self.assertEqual(env['HF_XET_CHUNK_CACHE_SIZE_BYTES'], '0')
        self.assertTrue(int(env['HF_XET_SHARD_CACHE_SIZE_LIMIT']) <= 2**30)

    def test_sizes_parse_decimal_and_binary_units(self):
        self.assertEqual(hs.parse_size('3G'), 3 * 10**9)
        self.assertEqual(hs.parse_size('3GiB'), 3 * 2**30)
        self.assertEqual(hs.parse_size('512Mi'), 512 * 2**20)
        self.assertEqual(hs.parse_size('1024'), 1024)


class RetryTests(unittest.TestCase):
    def run_with(self, errors, attempts=5):
        sleeps, calls = [], []

        def fn():
            calls.append(1)
            if errors:
                raise errors.pop(0)
            return 'ok'

        retry = hs.Retry(attempts=attempts, base=2, sleep=sleeps.append, rand=lambda: 0.5)
        return retry.call(fn), sleeps, calls

    def test_retry_after_seconds_is_a_lower_bound(self):
        result, sleeps, calls = self.run_with([FakeHTTPError(429, {'Retry-After': '30'})])
        self.assertEqual((result, len(calls)), ('ok', 2))
        self.assertGreaterEqual(sleeps[0], 30)

    def test_retry_after_http_date(self):
        when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
        _, sleeps, _ = self.run_with([FakeHTTPError(503, {'Retry-After': when})])
        self.assertGreaterEqual(sleeps[0], 100)

    def test_commit_limit_message_waits_the_hour(self):
        error = FakeHTTPError(429, {}, 'You have exceeded repository commits; retry this action in about 1 hour')
        _, sleeps, _ = self.run_with([error])
        self.assertGreaterEqual(sleeps[0], 3600)

    def test_exponential_backoff_with_jitter(self):
        _, sleeps, _ = self.run_with([FakeHTTPError(502), FakeHTTPError(500), ConnectionError('reset')])
        self.assertEqual(sleeps, [1.5, 3.0, 6.0])  # half fixed + half jitter (rand=0.5)

    def test_client_errors_are_not_retried_and_attempts_are_bounded(self):
        with self.assertRaises(FakeHTTPError):
            self.run_with([FakeHTTPError(404)])
        with self.assertRaises(TimeoutError):
            self.run_with([TimeoutError()] * 3, attempts=3)


class BatchCommitterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.hub = FakeHub()
        self.hub.add_repo(REPO, {})
        self.ctx = make_ctx(self.base / 'scratch', self.hub, batch_files=2)
        self.ledger = hs.Ledger(self.ctx.guard, 'test', REPO)

    def tearDown(self):
        self.tmp.cleanup()

    def items(self, count, scratch=True):
        result = []
        for n in range(count):
            data = bytes([n]) * (100 + n)
            path = (self.ctx.guard.path('xform', f'f{n}.bin') if scratch else self.base / f'in-place-{n}.bin')
            if scratch:
                self.ctx.guard.write_bytes(path, data)
            else:
                path.write_bytes(data)
            result.append(hs.Item(f'data/f{n}.bin', path, hs.hash_bytes(data), scratch=scratch))
        return result

    def test_batches_verify_then_delete_only_scratch_copies(self):
        committer = self.ctx.committer(self.ledger, 'test')
        scratch, in_place = self.items(3), self.items(2, scratch=False)
        in_place = [hs.Item(f'keep/{i.path_in_repo}', i.local, i.digest, False) for i in in_place]
        for item in scratch + in_place:
            committer.add(item)
        committer.close()
        self.assertEqual([len(c['paths']) for c in self.hub.commits], [2, 2, 1])
        self.assertTrue(all(not i.local.exists() for i in scratch))
        self.assertTrue(all(i.local.exists() for i in in_place))
        saved = json.loads(self.ledger.path.read_text())
        self.assertEqual(len(saved['files']), 5)
        self.assertEqual(len(saved['commits']), 3)

    def test_byte_bound_splits_batches(self):
        self.ctx.batch_bytes, self.ctx.batch_files = 250, 400
        committer = self.ctx.committer(self.ledger, 'test')
        for item in self.items(4):
            committer.add(item)
        committer.close()
        self.assertEqual([len(c['paths']) for c in self.hub.commits], [2, 2])  # 100+101 | 102+103

    def test_failed_remote_verification_keeps_the_scratch_copy(self):
        committer = self.ctx.committer(self.ledger, 'test')
        item = self.items(1)[0]
        self.hub.tamper[item.path_in_repo] = b'corrupted on the server'
        committer.add(item)
        with self.assertRaises(hs.VerificationError):
            committer.close()
        self.assertTrue(item.local.exists())
        self.assertNotIn(item.path_in_repo, self.ledger.files)

    def test_ledger_resume_skips_verified_files(self):
        committer = self.ctx.committer(self.ledger, 'test')
        for item in self.items(2):
            committer.add(item)
        committer.close()
        resumed = hs.Ledger(self.ctx.guard, 'test', REPO)
        again = self.ctx.committer(resumed, 'test')
        for item in self.items(2):
            again.add(item)
        again.close()
        self.assertEqual(len(self.hub.commits), 1)
        self.assertEqual(again.stats['skipped_files'], 2)

    def test_ambiguous_commit_failure_is_not_committed_twice(self):
        committer = self.ctx.committer(self.ledger, 'test')
        self.hub.commit_failures.append(('after', ConnectionError('response lost')))
        for item in self.items(2):
            committer.add(item)
        committer.close()
        self.assertEqual(len(self.hub.commits), 1)
        self.assertEqual(committer.stats['committed_files'], 2)
        self.assertEqual(len(self.ledger.files), 2)

    def test_transient_failure_before_commit_retries(self):
        committer = self.ctx.committer(self.ledger, 'test')
        self.hub.commit_failures.append(('before', FakeHTTPError(503)))
        for item in self.items(1):
            committer.add(item)
        committer.close()
        self.assertEqual(len(self.hub.commits), 1)

    def test_simulated_plan_matches_real_batching(self):
        self.assertEqual(hs.simulate_batches([100, 101, 102, 103, 104], 2**30, 2), 3)
        self.assertEqual(hs.simulate_batches([100, 101, 102, 103], 250, 400), 2)
        self.assertEqual(hs.simulate_batches([100, 200, 100], 250, 400), 3)


class RegionIndexTests(unittest.TestCase):
    def test_bitmap_encoding_matches_the_location_table(self):
        header = bytearray(4096)
        expected = {(0, 0), (31, 0), (0, 31), (5, 7), (31, 31)}
        for lx, lz in expected:
            i = lx + lz * 32
            header[i * 4:i * 4 + 4] = b'\x00\x00\x02\x01'
        header[(3 + 3 * 32) * 4:(3 + 3 * 32) * 4 + 4] = b'\x00\x00\x02\x00'  # offset but no sectors
        header[(4 + 4 * 32) * 4:(4 + 4 * 32) * 4 + 4] = b'\x00\x00\x00\x01'  # sectors but no offset
        count, bits = hs.region_bitmap(bytes(header))
        self.assertEqual((count, len(bits)), (5, 128))
        self.assertEqual(bits[0], 0b1)  # chunk 0 -> byte 0, bit 0 (LSB first)
        self.assertEqual(bits[3], 0b1000_0000)  # chunk 31 -> byte 3, bit 7
        for lx in range(32):
            for lz in range(32):
                self.assertEqual(hs.bitmap_has(bits, lx, lz), (lx, lz) in expected)

    def test_round_trip_against_a_real_region_header(self):
        header = (FIXTURES / 'oakbrook-r.-54.8-header.bin').read_bytes()
        meta = json.loads((FIXTURES / 'oakbrook-r.-54.8-header.json').read_text())
        count, bits = hs.region_bitmap(header)
        self.assertEqual(count, meta['chunks'])
        (x0, z0), (x1, z1) = meta['chunk_bounds_inclusive']
        expected = {(cx, cz) for cx in range(x0, x1 + 1) for cz in range(z0, z1 + 1)}
        decoded = base64.b64decode(base64.b64encode(bits).decode())
        self.assertEqual(decoded, bits)
        present = set()
        for i in range(1024):
            # Decoder exactly as a browser viewer would implement it.
            if decoded[i >> 3] >> (i & 7) & 1:
                present.add((i % 32, i // 32))
            entry = header[i * 4:i * 4 + 4]
            self.assertEqual(bool(decoded[i >> 3] >> (i & 7) & 1),
                             int.from_bytes(entry[:3], 'big') > 0 and entry[3] > 0)
        self.assertEqual(present, {(cx & 31, cz & 31) for cx, cz in expected})
        for cx, cz in expected:
            self.assertTrue(hs.bitmap_has(bits, cx, cz))  # negative world chunks use the same & 31 rule

    def test_buckets_use_floor_division(self):
        for x, bx in ((0, 0), (15, 0), (16, 1), (-1, -1), (-16, -1), (-17, -2), (-54, -4)):
            self.assertEqual(hs.bucket(x), bx)
        dest = hs.world_destination('region/r.-17.3.mca')
        self.assertEqual(dest['path'], 'world/region/-2/r.-17.3.mca')
        self.assertEqual(hs.world_destination('entities/r.16.0.mca')['path'], 'world/entities/1/r.16.0.mca')
        self.assertEqual(hs.world_destination('poi/r.-1.0.mca')['path'], 'world/poi/-1/r.-1.0.mca')
        self.assertEqual(hs.world_destination('region/c.-33.5.mcc')['path'], 'world/region/-1/c.-33.5.mcc')
        self.assertEqual(hs.world_destination('DIM-1/region/r.0.0.mca')['path'],
                         'world/dimensions/DIM-1/region/0/r.0.0.mca')
        self.assertIsNone(hs.world_destination('data/raids.dat'))

    def test_regions_index_schema_is_exact(self):
        entry = hs.WorldEntry('region/r.-1.2.mca', 'world/region/-1/r.-1.2.mca', 12288, 'region',
                              region={'kind': 'region', 'x': -1, 'z': 2, 'dimension': 'overworld',
                                      'external': False, 'chunks': 1,
                                      'chunk_bitmap': base64.b64encode(b'\x01' + bytes(127)).decode()},
                              digest=hs.Digest(12288, 'a' * 64, 'b' * 40))
        index = hs.regions_index({'entries': [entry]}, captured_at='t', source_revision='r')
        self.assertEqual(set(index), {'schema', 'revision_note', 'regions'})
        self.assertEqual(index['schema'], 'earthcraft.world-regions.v1')
        self.assertEqual(index['regions'], [{'x': -1, 'z': 2, 'path': 'world/region/-1/r.-1.2.mca',
                                             'bytes': 12288, 'sha256': 'a' * 64, 'chunks': 1,
                                             'chunk_bitmap': entry.region['chunk_bitmap']}])


class NbtPrivacyTests(unittest.TestCase):
    def test_level_player_is_removed_and_everything_else_is_byte_exact(self):
        original = level_dat()
        stripped, removed = hs.strip_level_player(original)
        self.assertEqual(removed, 1)
        self.assertEqual(gzip.decompress(stripped), gzip.decompress(level_dat(with_player=False)))
        summary = hs.level_summary(stripped)
        self.assertEqual((summary['level_name'], summary['minecraft_version'], summary['data_version'],
                          summary['has_player']), ('Earthcraft', '1.21.10', 4556, False))
        self.assertTrue(hs.level_summary(original)['has_player'])

    def test_identifiers_come_from_uuid_and_scoreboard_account_names(self):
        files = {'level.dat': level_dat(), 'data/scoreboard.dat': scoreboard_dat(),
                 f'playerdata/{PLAYER_UUID}.dat': b'x'}
        ids = hs.collect_identifiers(files, files.__getitem__)
        self.assertIn(PLAYER_UUID, ids)
        self.assertIn(PLAYER_UUID.replace('-', ''), ids)
        self.assertIn(PLAYER_NAME, ids)
        self.assertNotIn('#fake', ids)
        self.assertNotIn('ec_speed', ids)
        self.assertEqual(hs.uuid_from_ints(uuid_ints(PLAYER_UUID)), PLAYER_UUID)

    def test_scoreboard_player_scores_are_emptied(self):
        stripped, removed = hs.strip_scoreboard_players(scoreboard_dat())
        self.assertEqual(removed, 2)
        raw = gzip.decompress(stripped)
        self.assertEqual(hs.nbt_get(raw, ('data', 'PlayerScores')), [])
        self.assertEqual(len(hs.nbt_get(raw, ('data', 'Objectives'))), 1)
        self.assertNotIn(PLAYER_NAME.encode(), raw)

    def test_json_receipts_lose_player_entries_only(self):
        receipt = {'hashes': {f'playerdata/{PLAYER_UUID}.dat': 'a', 'level.dat': 'b',
                              'datapacks/x/data/minecraft/advancements/y.json': 'c'},
                   'files': [{'path': f'stats/{PLAYER_UUID}.json'}, {'path': 'region/r.0.0.mca'}],
                   'note': f'seen {PLAYER_NAME} at spawn'}
        out, removed = hs.redact_json(json.dumps(receipt, indent=2).encode(), {PLAYER_UUID, PLAYER_NAME})
        result = json.loads(out)
        self.assertEqual(removed, 3)
        self.assertEqual(set(result['hashes']), {'level.dat', 'datapacks/x/data/minecraft/advancements/y.json'})
        self.assertEqual(result['files'], [{'path': 'region/r.0.0.mca'}])
        self.assertNotIn(PLAYER_NAME, out.decode())
        self.assertEqual(hs.redact_json(b'{"a": 1}', {PLAYER_UUID}), (None, 0))

    def test_account_names_match_whole_words_only(self):
        self.assertTrue(hs.contains_identifier(b'by testplayer01.', {PLAYER_NAME}))
        self.assertFalse(hs.contains_identifier(b'xtestplayer01x', {PLAYER_NAME}))
        self.assertTrue(hs.contains_identifier(gzip.compress(PLAYER_UUID.upper().encode()), {PLAYER_UUID}))

    def test_bad_nbt_is_rejected(self):
        with self.assertRaises(hs.NbtError):
            hs.strip_level_player(gzip.compress(b'\x0a\x00\x00\x08\x00'))


class WorldFixture:
    def __init__(self, root):
        self.save = root / 'saves' / 'Earthcraft'
        save = self.save
        save.mkdir(parents=True)
        (save / 'level.dat').write_bytes(level_dat())
        (save / 'level.dat_old').write_bytes(level_dat())
        (save / 'session.lock').write_bytes(bytes(3))
        for tree, suffix in (('playerdata', '.dat'), ('stats', '.json'), ('advancements', '.json')):
            (save / tree).mkdir()
            (save / tree / (PLAYER_UUID + suffix)).write_bytes(b'{"player": true}')
        (save / 'data').mkdir()
        (save / 'data' / 'scoreboard.dat').write_bytes(scoreboard_dat())
        (save / 'data' / 'raids.dat').write_bytes(gzip.compress(b'\x0a\x00\x00\x00', mtime=0))
        (save / 'data' / 'DistantHorizons.sqlite').write_bytes(b'lod cache')
        (save / 'city-coverage.json').write_text(json.dumps({'frame': {'west': -32, 'north': 33}}))
        (save / 'server-verification.json').write_text(json.dumps(
            {'hashes': {f'playerdata/{PLAYER_UUID}.dat': 'x', 'level.dat': 'y'}}, indent=2))
        (save / 'resources.zip').write_bytes(b'PK\x05\x06' + bytes(18))
        for relative, chunks in (('region/r.-1.0.mca', [(0, 0), (31, 31)]), ('region/r.-17.3.mca', [(4, 4)]),
                                 ('entities/r.-1.0.mca', [(0, 0)]), ('poi/r.0.0.mca', [(1, 1)])):
            path = save / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(region_bytes(chunks))
        (save / 'region' / 'r.5.5.mca').write_bytes(b'')

    def snapshot_files(self):
        return {p.relative_to(self.save).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.save.rglob('*') if p.is_file()}


LOCK_HOLDER = '''
import fcntl, sys, time
f = open(sys.argv[1], 'r+b')
fcntl.lockf(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
print('locked', flush=True)
sys.stdin.readline()
'''


class PushWorldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.world = WorldFixture(self.base)
        self.hub = FakeHub()
        self.hub.add_repo(REPO, {'catalog.json': hs.canonical_json({'schema': 'earthcraft.public-catalog.v1',
                                                                     'atlas': {'status': 'kept'}})})
        self.ctx = make_ctx(self.base / 'scratch', self.hub)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def hold_lock(save):
        child = subprocess.Popen([sys.executable, '-c', LOCK_HOLDER, str(save / 'session.lock')],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        assert child.stdout.readline().strip() == 'locked'
        return child

    def test_dry_run_plans_while_the_game_holds_the_lock_and_touches_nothing(self):
        before = self.world.snapshot_files()
        game = self.hold_lock(self.world.save)
        try:
            plan = hs.push_world(self.ctx, self.world.save, dry_run=True, source_revision='abc')
            with self.assertRaises(RuntimeError):  # a real sync refuses while the game is open
                hs.push_world(self.ctx, self.world.save, source_revision='abc')
        finally:
            game.communicate('\n', timeout=10)
        self.assertEqual(self.world.snapshot_files(), before)
        self.assertEqual((plan['state'], plan['lock_taken'], plan['uploads']), ('dry-run', False, 0))
        self.assertEqual(plan['by_kind']['region']['files'], 2)
        self.assertEqual(plan['by_kind']['entities']['files'], 1)
        self.assertEqual(plan['by_kind']['poi']['files'], 1)
        self.assertEqual(plan['omitted_empty_region_files'], 1)
        self.assertEqual(plan['region_chunks'], 3)
        self.assertEqual(plan['excluded']['player data'], 3)
        self.assertIn('level.dat', plan['transforms'])
        self.assertTrue(plan['level_has_player_tag'])
        self.assertEqual(self.hub.commits, [])
        self.assertNotIn(PLAYER_UUID, json.dumps(plan))
        self.assertNotIn(PLAYER_NAME, json.dumps(plan))

    def test_real_sync_uploads_in_place_strips_players_and_writes_indexes(self):
        result = hs.push_world(self.ctx, self.world.save, source_revision='abc')
        files = self.hub.repos[REPO]['files']
        self.assertEqual(result['state'], 'complete')
        self.assertIn('world/region/-1/r.-1.0.mca', files)
        self.assertIn('world/region/-2/r.-17.3.mca', files)
        self.assertIn('world/entities/-1/r.-1.0.mca', files)
        self.assertIn('world/poi/0/r.0.0.mca', files)
        self.assertNotIn('world/region/0/r.5.5.mca', files)
        self.assertEqual(files['world/region/-1/r.-1.0.mca'],
                         (self.world.save / 'region/r.-1.0.mca').read_bytes())
        forbidden = [p for p in files if any(t in p for t in ('playerdata', 'stats/', 'advancements',
                                                              'session.lock', 'level.dat_old', 'DistantHorizons'))]
        self.assertEqual(forbidden, [])
        self.assertFalse(hs.level_summary(files['world/meta/level.dat'])['has_player'])
        for path, data in files.items():
            self.assertFalse(hs.contains_identifier(data, {PLAYER_UUID, PLAYER_NAME}), path)
        regions = json.loads(files['world/index/regions.json'])
        self.assertEqual(regions['schema'], 'earthcraft.world-regions.v1')
        self.assertEqual([(r['x'], r['z'], r['chunks']) for r in regions['regions']], [(-17, 3, 1), (-1, 0, 2)])
        for region in regions['regions']:
            self.assertEqual(region['sha256'], hashlib.sha256(files[region['path']]).hexdigest())
            self.assertEqual(region['bytes'], len(files[region['path']]))
        manifest = json.loads(files['world/manifest.json'])
        self.assertEqual(manifest['frame'], {'west': -32, 'north': 33})
        self.assertEqual(manifest['source_revision'], 'abc')
        listed = {f['path']: f for f in manifest['files']}
        for path, data in files.items():
            if path.startswith('world/') and path != 'world/manifest.json':
                self.assertEqual(listed[path]['sha256'], hashlib.sha256(data).hexdigest(), path)
        catalog = json.loads(files['catalog.json'])
        self.assertEqual(catalog['atlas'], {'status': 'kept'})
        self.assertEqual(catalog['world']['manifest']['sha256'],
                         hashlib.sha256(files['world/manifest.json']).hexdigest())
        self.assertEqual(self.hub.commits[-1]['paths'][-1], 'catalog.json')  # manifest/catalog last
        self.assertEqual([p.name for p in (self.base / 'scratch').iterdir() if p.name not in
                          ('ledger', 'hf-home', 'tmp')], [])

    def test_only_changed_uploads_just_the_edited_region(self):
        hs.push_world(self.ctx, self.world.save, source_revision='abc')
        path = self.world.save / 'region' / 'r.-17.3.mca'
        path.write_bytes(region_bytes([(4, 4), (5, 5)]))
        self.ctx = make_ctx(self.base / 'scratch2', self.hub)  # fresh ledger: diff comes from the remote manifest
        before = len(self.hub.commits)
        result = hs.push_world(self.ctx, self.world.save, only_changed=True, source_revision='abd')
        uploaded = [p for c in self.hub.commits[before:] for p in c['paths']]
        self.assertEqual(sorted(uploaded), sorted(['world/region/-2/r.-17.3.mca', 'world/index/regions.json',
                                                   'world/manifest.json', 'catalog.json']))
        self.assertGreater(result['skipped_unchanged'], 5)


class MigrateFixture:
    def __init__(self, hub):
        self.release = 'e' * 64
        tile_manifest = json.dumps({'excludes': ['player state', 'credentials'], 'entries': []}).encode()
        good = tar_bytes([('manifest.json', tile_manifest), ('1_2/r.1.2.mca.gz', gzip.compress(region_bytes([(0, 0)])))])
        good2 = tar_bytes([('manifest.json', tile_manifest), ('3_4/r.3.4.mca.gz', gzip.compress(region_bytes([(1, 0)])))])
        bad = tar_bytes([('manifest.json', tile_manifest), ('playerdata/x.dat', b'player')])
        shards = {}
        source = {}
        for raw in (good, good2, bad):
            sha = hashlib.sha256(raw).hexdigest()
            path = f'generated-tiles/v1/shards/{sha[:2]}/{sha}.tar'
            shards[sha] = {'path': path, 'bytes': len(raw), 'sha256': sha}
            source[path] = raw
        self.bad_path = f'generated-tiles/v1/shards/{hashlib.sha256(bad).hexdigest()[:2]}/{hashlib.sha256(bad).hexdigest()}.tar'
        source['generated-tiles/v1/index.json'] = json.dumps({
            'schema': 'earthcraft.generated-tile-index.v1', 'plan_sha256': 'p' * 64, 'shards': shards,
            'tiles': {'1_2': {}, '3_4': {}}, 'attribution': [{'source': 'OpenStreetMap contributors'}]}).encode()
        validation = 'validations/v1/' + 'a' * 64 + '/'
        source[validation + 'manifest.json'] = json.dumps({'player_data_present': False}, indent=2).encode()
        source[validation + 'world.tar'] = tar_bytes([('world/level.dat', level_dat(with_player=False)),
                                                      ('world/region/r.-52.-1.mca', region_bytes([(2, 2)]))])
        meta_members = [('level.dat', level_dat()), ('level.dat_old', level_dat()),
                        (f'playerdata/{PLAYER_UUID}.dat', b'inventory'), (f'stats/{PLAYER_UUID}.json', b'{}'),
                        (f'advancements/{PLAYER_UUID}.json', b'{}'), ('data/scoreboard.dat', scoreboard_dat()),
                        ('server-verification.json', json.dumps({'h': {f'playerdata/{PLAYER_UUID}.dat': 'x',
                                                                       'level.dat': 'y'}}, indent=2).encode()),
                        ('resources.zip', b'PK\x05\x06' + bytes(18)), ('earthcraft.json', b'{"ok": true}')]
        metadata = tar_bytes(meta_members, gz=True)
        overworld = tar_bytes([('region/r.0.0.mca', region_bytes([(0, 0)]))], gz=True)
        restore_script = (Path(__file__).resolve().parents[1] / 'scripts' / 'restore_world_snapshot.py').read_bytes()

        def records(members):
            return [{'path': n, 'bytes': len(d), 'sha256': hashlib.sha256(d).hexdigest()} for n, d in members]

        snapshot = {'schema': 'earthcraft-world-snapshot-v1', 'snapshot_id': hs.SNAPSHOT_ID, 'state': 'complete',
                    'created_utc': '2026-10-02T04:47:11+00:00', 'minecraft_version': '1.21.10', 'region_span': 8,
                    'minecraft_chunks': 1, 'excluded': ['session.lock'],
                    'file_count': len(meta_members) + 1,
                    'source_bytes': sum(len(d) for _, d in meta_members) + len(region_bytes([(0, 0)])),
                    'compressed_bytes': len(metadata) + len(overworld),
                    'shards': [{'key': 'metadata', 'path': 'shards/metadata.tar.gz', 'bytes': len(metadata),
                                'sha256': hashlib.sha256(metadata).hexdigest(), 'files': records(meta_members)},
                               {'key': 'overworld/x0_z0', 'path': 'shards/overworld/x0_z0.tar.gz',
                                'bytes': len(overworld), 'sha256': hashlib.sha256(overworld).hexdigest(),
                                'files': records([('region/r.0.0.mca', region_bytes([(0, 0)]))])}]}
        prefix = hs.SNAPSHOT_PREFIX
        source.update({prefix + 'manifest.json': json.dumps(snapshot, indent=2).encode(),
                       prefix + 'shards/metadata.tar.gz': metadata,
                       prefix + 'shards/overworld/x0_z0.tar.gz': overworld,
                       prefix + 'restore_world_snapshot.py': restore_script,
                       prefix + 'README.md': b'private card', 'README.md': b'private card',
                       '.gitattributes': b'*.tar filter=lfs'})
        hub.add_repo(hs.SOURCE_REPO, source, private=True)
        preview = {'index.json': b'{"preview": 1}', 'ATTRIBUTION.md': b'credit', '.gitattributes': b'x'}
        native = {'index.json': b'{"native": "cross-repo links"}', 'README.md': b'native card',
                  '.gitattributes': b'x'}
        hub.add_repo('EricSpencer00/earthcraft-oakbrook-source-bound-preview', preview)
        hub.add_repo('EricSpencer00/earthcraft-oakbrook-native-chunks', native)
        release = f'releases/{self.release}/'
        self.catalog = {'schema': 'earthcraft.public-catalog.v1', 'release_id_sha256': self.release,
                        'atlas': {'summary': {'path': release + 'atlas/summary.json'}}, 'regions': [{'id': 'oakbrook'}],
                        'publication_allowed': True}
        hub.add_repo(REPO, {'catalog.json': hs.canonical_json(self.catalog), 'README.md': b'old card',
                            release + 'preview/index.json': preview['index.json'],
                            release + 'preview/ATTRIBUTION.md': preview['ATTRIBUTION.md'],
                            release + 'native/README.md': native['README.md']})
        self.snapshot = snapshot


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.hub = FakeHub()
        self.fixture = MigrateFixture(self.hub)
        self.ctx = make_ctx(self.base / 'scratch', self.hub, cap=80 * 2**20, batch_files=3)

    def tearDown(self):
        self.tmp.cleanup()

    def download_snapshot(self, files):
        package = self.base / 'package'
        for path, data in files.items():
            if path.startswith(hs.SNAPSHOT_PREFIX):
                target = package / path[len(hs.SNAPSHOT_PREFIX):]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        return package

    def test_full_migration(self):
        report = hs.migrate(self.ctx, {'tiles', 'validations', 'snapshot', 'oakbrook', 'card'})
        files = self.hub.repos[REPO]['files']
        source = self.hub.repos[hs.SOURCE_REPO]['files']
        for path, data in source.items():
            if path.startswith('generated-tiles/v1/') and path != self.fixture.bad_path:
                self.assertEqual(files['tiles/v1/' + path[len('generated-tiles/v1/'):]], data)
            if path.startswith('validations/v1/'):
                self.assertEqual(files[path], data)
        self.assertEqual(len(report['held']), 1)
        self.assertNotIn('tiles/v1/' + self.fixture.bad_path[len('generated-tiles/v1/'):], files)
        # Snapshot: geographic shard identical, metadata repacked without players.
        prefix = hs.SNAPSHOT_PREFIX
        self.assertEqual(files[prefix + 'shards/overworld/x0_z0.tar.gz'], source[prefix + 'shards/overworld/x0_z0.tar.gz'])
        with tarfile.open(fileobj=io.BytesIO(files[prefix + 'shards/metadata.tar.gz']), mode='r:gz') as archive:
            names = archive.getnames()
            level = archive.extractfile('level.dat').read()
            receipt = archive.extractfile('server-verification.json').read()
        self.assertEqual(sorted(names), ['data/scoreboard.dat', 'earthcraft.json', 'level.dat',
                                         'resources.zip', 'server-verification.json'])
        self.assertFalse(hs.level_summary(level)['has_player'])
        self.assertEqual(json.loads(receipt), {'h': {'level.dat': 'y'}})
        for path, data in files.items():
            self.assertFalse(hs.contains_identifier(data, {PLAYER_UUID, PLAYER_NAME}), path)
        manifest = json.loads(files[prefix + 'manifest.json'])
        self.assertEqual(manifest['file_count'], self.fixture.snapshot['file_count'] - 4)
        self.assertEqual(manifest['public_redaction']['removed_files'],
                         {'playerdata/': 1, 'stats/': 1, 'advancements/': 1, 'level.dat_old': 1})
        restored = restore(self.download_snapshot(files), self.base / 'restored')
        self.assertTrue(restored['complete_save'])
        self.assertFalse((self.base / 'restored' / 'playerdata').exists())
        self.assertNotIn('README.md', [p for p in files if p == 'README.md' and files[p] == b'private card'])
        # Oakbrook: identical files verified, the missing native index preserved under archive/.
        oak = report['oakbrook']['EricSpencer00/earthcraft-oakbrook-native-chunks']
        self.assertEqual(oak['archived_missing'], ['index.json'])
        self.assertEqual(oak['identical_in_releases'], 1)
        archived = [p for p in files if p.startswith('archive/')]
        self.assertEqual(len(archived), 1)
        self.assertEqual(files[archived[0]], b'{"native": "cross-repo links"}')
        self.assertEqual(report['oakbrook']['EricSpencer00/earthcraft-oakbrook-source-bound-preview']
                         ['archived_missing'], [])
        # Catalog: additive, existing descriptors untouched, every new descriptor checks out.
        catalog = json.loads(files['catalog.json'])
        for key, value in self.fixture.catalog.items():
            self.assertEqual(catalog[key], value)
        self.assertEqual(catalog['tiles']['v1']['shard_count'], 2)
        self.assertEqual(catalog['world']['status'], 'awaiting-first-sync')
        for desc in ([catalog['tiles']['v1']['index'], catalog['snapshots'][0]['manifest']] +
                     catalog['validations']['v1'][0]['files'] + catalog['archive']['files']):
            self.assertEqual(hashlib.sha256(files[desc['path']]).hexdigest(), desc['sha256'], desc['path'])
            self.assertEqual(len(files[desc['path']]), desc['bytes'])
        self.assertIn(b'No player data is published', files['README.md'])
        # Private-only files were not copied.
        self.assertNotIn('.gitattributes', [p for p in files if files[p] == b'*.tar filter=lfs'])
        self.assertNotIn('restore_world_snapshot.py', files)
        # Self-cleaning scratch: only the ledger and empty client cache dirs remain.
        leftovers = [p for p in (self.base / 'scratch').rglob('*') if p.is_file() and 'ledger' not in p.parts]
        self.assertEqual(leftovers, [])
        # verify subcommand over the ledger and over the tile index with the legacy prefix rewrite.
        ledger = self.ctx.guard.path('ledger', 'migrate.json')
        self.assertTrue(hs.verify(self.ctx, str(ledger))['passed'])
        check = hs.verify(self.ctx, 'hf:tiles/v1/index.json', rewrite=('generated-tiles/v1/', 'tiles/v1/'))
        self.assertEqual((check['checked'], check['ok']), (3, 2))
        self.assertEqual(len(check['missing']), 1)  # the held shard

    def test_subset_then_resume_is_idempotent(self):
        hs.migrate(self.ctx, {'tiles'}, limit=2)
        first = len(self.hub.commits)
        report = hs.migrate(self.ctx, {'tiles', 'validations'})
        self.assertGreaterEqual(report['already_present'], 1)
        again = hs.migrate(self.ctx, {'tiles', 'validations'})
        self.assertEqual(again['copied'], 0)
        self.assertGreater(len(self.hub.commits), first)
        self.assertEqual(again['commits'], [])

    def test_scratch_cap_is_never_exceeded(self):
        self.ctx.guard.cap = 70 * 2**20  # downloads are admitted with a 64 MiB margin
        hs.migrate(self.ctx, {'tiles', 'validations', 'snapshot'})
        self.assertLessEqual(self.ctx.guard.peak_used, self.ctx.guard.cap)

    def test_tampered_remote_fails_verification(self):
        hs.migrate(self.ctx, {'tiles'})
        files = self.hub.repos[REPO]['files']
        victim = next(p for p in files if p.startswith('tiles/v1/shards/'))
        files[victim] = files[victim][:-1] + b'!'
        result = hs.verify(self.ctx, str(self.ctx.guard.path('ledger', 'migrate.json')))
        self.assertFalse(result['passed'])
        self.assertEqual(result['hash_mismatch'], [victim])


class CliTests(unittest.TestCase):
    def test_dry_run_cli_needs_no_hub_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            world = WorldFixture(Path(tmp))
            scratch = Path(tmp) / 'scratch'
            out = io.StringIO()
            from contextlib import redirect_stdout
            with redirect_stdout(out):
                code = hs.main(['--scratch', str(scratch), '--min-free', '0', 'push-world', '--save',
                                str(world.save), '--dry-run', '--source-revision', 'abc'],
                               hub_factory=lambda: self.fail('dry-run must not create a Hub client'))
            self.assertEqual(code, 0)
            self.assertFalse(scratch.exists())
            result = json.loads(out.getvalue().strip().splitlines()[-1])
            self.assertEqual(result['state'], 'dry-run')

    def test_push_files_uploads_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'src'
            (src / 'a').mkdir(parents=True)
            (src / 'a' / 'one.json').write_text('{}')
            (src / 'skip.log').write_text('x')
            hub = FakeHub()
            hub.add_repo(REPO, {})
            ctx = make_ctx(Path(tmp) / 'scratch', hub)
            result = hs.push_files(ctx, src, 'archive/mini-worker-20260929', exclude=['*.log'])
            self.assertEqual(result['files'], 1)
            self.assertEqual(hub.repos[REPO]['files'], {'archive/mini-worker-20260929/a/one.json': b'{}'})
            self.assertTrue((src / 'a' / 'one.json').exists())


if __name__ == '__main__':
    unittest.main()
