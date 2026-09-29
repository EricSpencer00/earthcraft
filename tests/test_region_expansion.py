import sys
from pathlib import Path
import unittest
import zlib
import json
import sqlite3
import tempfile
import io
import nbtlib as n

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from region_expansion import encode, merge, records, expand, sha, empty_unowned_record, materialize_unowned_empty
from chicago_tiles import digest


def record(content, timestamp=1):
    payload = b'\x02'+zlib.compress(content)
    return len(payload).to_bytes(4, 'big')+payload, timestamp.to_bytes(4, 'big')


def native(block, entity=False):
    tag = n.File({'sections': n.List[n.Compound]([n.Compound({
        'Y': n.Byte(0), 'block_states': n.Compound({'palette': n.List[n.Compound]([
            n.Compound({'Name': n.String(block)})])})})]),
        'block_entities': n.List[n.Compound]([n.Compound({'id': n.String('minecraft:chest')})] if entity else [])})
    stream = io.BytesIO()
    tag.write(stream)
    return record(stream.getvalue())


class CompressedExpansionTests(unittest.TestCase):
    def test_only_unoccupied_void_records_are_eligible_for_materialization(self):
        self.assertTrue(empty_unowned_record(native('minecraft:air')))
        self.assertFalse(empty_unowned_record(native('minecraft:stone')))
        self.assertFalse(empty_unowned_record(native('minecraft:air', entity=True)))
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
    def test_void_pass_preserves_cleared_owned_chunks_and_player_blocks_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, world, plan, source = [root/name for name in ('baseline', 'save', 'plan', 'source')]
            for path in (baseline/'region', world/'region', plan, source/'world/region'):
                path.mkdir(parents=True)
            frame = {'crs': 'EPSG:32616'}
            frozen = {'frame': frame, 'tiles': [{'id': '0_0', 'size': 256}]}
            (plan/'plan.json').write_text(json.dumps(frozen))
            # Published ownership protects a cleared chunk; coverage protects a
            # separately generated chunk; nonempty blocks and entities survive.
            coverage = {'frame': frame, 'tiles': {'older': {'world_offset_xz': [32, 0], 'size_m': 16}}}
            (baseline/'city-coverage.json').write_text(json.dumps(coverage))
            ownership = root/'ownership.json'
            ownership.write_text(json.dumps({'frame': frame, 'chunks': ['0,0']}))
            old = {0: native('minecraft:air'), 1: native('minecraft:air'),
                   2: native('minecraft:air'), 3: native('minecraft:stone'),
                   4: native('minecraft:air', entity=True)}
            for save in (baseline, world):
                (save/'region/r.0.0.mca').write_bytes(encode(old))
            incoming = {slot: native('minecraft:grass_block') for slot in old}
            (source/'world/region/r.0.0.mca').write_bytes(encode(incoming))
            receipt = source/'geometry-receipt.json'
            receipt.write_text(json.dumps({'result': 'pass', 'tile': '0_0',
                'regions': {'r.0.0.mca': sha(source/'world/region/r.0.0.mca')}}))
            with sqlite3.connect(plan/'jobs.sqlite') as db:
                db.execute('CREATE TABLE jobs(tile,stage,state,evidence,evidence_sha256)')
                db.execute("INSERT INTO jobs VALUES('0_0',1,'complete',?,?)", (str(receipt), sha(receipt)))
            progress = root/'progress.json'
            progress.write_text(json.dumps({'request': {'plans': [digest(frozen)]},
                'regions': {'r.0.0.mca': {'sha256': sha(world/'region/r.0.0.mca')}}}))
            result = materialize_unowned_empty(world, baseline, [plan], ownership, [progress])
            self.assertEqual(result['materialized_unowned_empty_chunks'], 1)
            actual = records((world/'region/r.0.0.mca').read_bytes())
            self.assertEqual(actual[1], incoming[1])
            for slot in (0, 2, 3, 4):
                self.assertEqual(actual[slot], old[slot])
            self.assertEqual(json.loads(progress.read_text())['regions']['r.0.0.mca']['sha256'],
                             sha(world/'region/r.0.0.mca'))
            self.assertEqual(materialize_unowned_empty(world, baseline, [plan], ownership, [progress]), result)
            # Changing a player chunk after assembly is a hard fence.
            actual[3] = native('minecraft:diamond_block')
            (world/'region/r.0.0.mca').write_bytes(encode(actual))
            with self.assertRaisesRegex(ValueError, 'player chunk changed'):
                materialize_unowned_empty(world, baseline, [plan], ownership, [progress])

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
