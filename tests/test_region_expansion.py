import sys
from pathlib import Path
import unittest
import zlib
import json
import sqlite3
import tempfile
import nbtlib as n

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from region_expansion import encode, merge, records, expand, sha


def record(content, timestamp=1):
    payload = b'\x02'+zlib.compress(content)
    return len(payload).to_bytes(4, 'big')+payload, timestamp.to_bytes(4, 'big')


class CompressedExpansionTests(unittest.TestCase):
    def test_cleared_player_chunk_and_timestamp_win_over_fresh_geography(self):
        old = {0: record(b'player cleared this chunk', 100), 900: record(b'inventory')}
        new = {0: record(b'building', 200), 1: record(b'new geographic chunk')}
        merged = records(encode(merge(old, new)))
        self.assertEqual(merged[0], old[0])
        self.assertEqual(merged[900], old[900])
        self.assertEqual(merged[1], new[1])
        self.assertEqual(merge(merged, new), merged)

    def test_corrupt_and_overlapping_sectors_are_rejected(self):
        raw = bytearray(encode({0: record(b'one'), 1: record(b'two')}))
        raw[4:8] = raw[0:4]
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            records(raw)
        with self.assertRaisesRegex(ValueError, 'size'):
            records(b'incomplete')


class StagingExpansionTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        world, plan, source = root/'save', root/'plan', root/'tile'
        for path in (world/'region', plan, source/'world/region'):
            path.mkdir(parents=True)
        frame = {'crs': 'EPSG:32616', 'west': 0, 'north': 0,
                 'vertical_offset_m': 0, 'dimension_min_y': -64, 'dimension_height': 1024}
        tile = {'id': 'owned', 'world_offset_xz': [16, 0], 'west': 16, 'north': 0, 'size': 32}
        (plan/'plan.json').write_text(json.dumps({'frame': frame, 'tiles': [tile]}))
        old = {0: record(b'user building'), 1: record(b'cleared to air', 999)}
        incoming = {slot: record(b'geography') for slot in (1, 2, 33, 34)}
        (world/'region/r.0.0.mca').write_bytes(encode(old))
        (source/'world/region/r.0.0.mca').write_bytes(encode(incoming))
        (source/'world/earthcraft.json').write_text(json.dumps({
            'source': {'crs': frame['crs'], 'size': 32, 'west': 16, 'north': 0},
            'vertical_offset_m': 0, 'world_offset_xz': [16, 0],
            'dimension_height': 1024, 'dimension_min_y': -64}))
        receipt = source/'geometry-receipt.json'
        receipt.write_text(json.dumps({'tile': 'owned', 'result': 'pass',
            'world_manifest_sha256': sha(source/'world/earthcraft.json'),
            'regions': {'r.0.0.mca': sha(source/'world/region/r.0.0.mca')}}))
        with sqlite3.connect(plan/'jobs.sqlite') as db:
            db.execute('CREATE TABLE jobs (tile TEXT,stage INTEGER,state TEXT,evidence TEXT,evidence_sha256 TEXT)')
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', ('owned', 1, 'complete', str(receipt), sha(receipt)))
        (world/'earthcraft.json').write_text('{}')
        (world/'city-coverage.json').write_text(json.dumps({'frame': frame, 'tiles': {}}))
        player = n.Compound({'Pos': n.List[n.Double]([4.5, 80, 3.5]), 'Inventory': n.List[n.Compound]([])})
        n.File({'Data': n.Compound({'Player': player})}, gzipped=True).save(world/'level.dat')
        return root, world, plan, source, old, player

    def test_complete_expansion_and_resume_preserve_player_and_cleared_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root, world, plan, source, old, player = self.fixture(directory)
            report = expand(world, [plan], root/'progress.json')
            actual = records((world/'region/r.0.0.mca').read_bytes())
            self.assertEqual(report['added_chunks'], 3)
            self.assertTrue(all(actual[key] == value for key, value in old.items()))
            self.assertEqual(n.load(world/'level.dat')['Data']['Player'], player)
            self.assertEqual(expand(world, [plan], root/'progress.json'), report)

    def test_changed_source_receipt_is_rejected_without_touching_save(self):
        with tempfile.TemporaryDirectory() as directory:
            root, world, plan, source, old, player = self.fixture(directory)
            before = sha(world/'region/r.0.0.mca')
            with (source/'geometry-receipt.json').open('a') as stream:
                stream.write(' ')
            with self.assertRaisesRegex(ValueError, 'receipt changed'):
                expand(world, [plan], root/'progress.json')
            self.assertEqual(sha(world/'region/r.0.0.mca'), before)

    def test_external_chunk_reference_is_never_silently_lost(self):
        raw = bytearray(encode({0: record(b'one')}))
        raw[8196] = 130
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            records(raw)


if __name__ == '__main__':
    unittest.main()
