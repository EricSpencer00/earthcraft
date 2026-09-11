import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import nbtlib as n
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from metric_world import packed, region_write
from world_replay import canonical_section, compare_worlds, files_snapshot, digest


def section(names, values):
    palette = n.List[n.Compound]([n.Compound({'Name': n.String(name)}) for name in names])
    return n.Compound({'Y': n.Byte(-1), 'block_states': n.Compound({
        'palette': palette, 'data': packed(values, max(4, (len(names)-1).bit_length()))})})


class ReplayTests(unittest.TestCase):
    def test_palette_order_and_unused_entries_do_not_change_blocks(self):
        values = np.arange(4096) % 2
        first = section(['minecraft:air', 'minecraft:stone'], values)
        other = section(['minecraft:stone', 'minecraft:air', 'minecraft:dirt'], 1-values)
        self.assertEqual(canonical_section(first), canonical_section(other))

    def test_single_palette_matches_uniform_packed_section(self):
        first = section(['minecraft:stone'], np.zeros(4096))
        del first['block_states']['data']
        second = section(['minecraft:air', 'minecraft:stone'], np.ones(4096))
        self.assertEqual(canonical_section(first), canonical_section(second))

    def test_one_block_and_state_properties_are_detected(self):
        values = np.zeros(4096, dtype=int)
        first = section(['minecraft:air', 'minecraft:stone'], values)
        values[100] = 1
        second = section(['minecraft:air', 'minecraft:stone'], values)
        self.assertNotEqual(canonical_section(first), canonical_section(second))
        altered = copy.deepcopy(second)
        altered['block_states']['palette'][1]['Properties'] = n.Compound({'axis': n.String('x')})
        self.assertNotEqual(canonical_section(second), canonical_section(altered))

    def test_source_changes_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'observation.json'
            path.write_text('first')
            before = digest(files_snapshot(root))
            path.write_text('second')
            self.assertNotEqual(before, digest(files_snapshot(root)))
            (root / 'alias').symlink_to(path)
            with self.assertRaises(ValueError): files_snapshot(root)

    def test_world_ignores_time_but_detects_chart_and_block_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            roots = [Path(directory) / name for name in ('a', 'b')]
            meta = {'source': {'crs':'test', 'west':-16, 'north':16, 'size':16},
                    'vertical_offset_m':-100, 'dimension_min_y':-64, 'dimension_height':128}
            tags = []
            for index, root in enumerate(roots):
                (root / 'region').mkdir(parents=True)
                (root / 'earthcraft.json').write_text(json.dumps(meta))
                tag = n.File({'xPos': n.Int(-1), 'zPos': n.Int(0), 'LastUpdate': n.Long(index),
                              'sections': n.List[n.Compound]([section(['minecraft:stone'], np.zeros(4096))])})
                tags.append(tag)
                region_write(root / 'region/r.-1.0.mca', [(-1, 0, tag)])
            self.assertTrue(compare_worlds(*roots)['equal'])
            meta['world_offset_xz']=[16,0]
            (roots[1] / 'earthcraft.json').write_text(json.dumps(meta))
            self.assertFalse(compare_worlds(*roots)['equal'])
            del meta['world_offset_xz']
            meta['source']['west'] = -17
            (roots[1] / 'earthcraft.json').write_text(json.dumps(meta))
            self.assertFalse(compare_worlds(*roots)['equal'])
            meta['source']['west'] = -16
            (roots[1] / 'earthcraft.json').write_text(json.dumps(meta))
            tags[1]['sections'][0]['block_states']['palette'][0]['Name'] = n.String('minecraft:dirt')
            # A new region fixture avoids overwriting a generated world in the test.
            (roots[1] / 'region/r.-1.0.mca').unlink()
            region_write(roots[1] / 'region/r.-1.0.mca', [(-1, 0, tags[1])])
            changed = compare_worlds(*roots)
            self.assertFalse(changed['equal'])
            self.assertEqual(changed['changed_chunks'], ['-1,0'])


if __name__ == '__main__': unittest.main()
