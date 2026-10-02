import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from orthofacade_adapter import adapt, frame_digest, project_wall

FRAME = {'crs': '+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs',
         'west': -32, 'north': 33, 'vertical_offset_m': -116,
         'dimension_min_y': -64, 'dimension_height': 1024}


def fixture(north=False):
    inverse = Transformer.from_crs(FRAME['crs'], 4326, always_xy=True)
    end = (0, 4) if north else (4, 0)
    rgba = np.zeros((2, 4, 4), dtype=np.uint8)
    rgba[:, :, :3] = [153, 91, 72]
    rgba[:, :, 3] = 255
    rgba[0, 1, 3] = 192
    rgba[1, 2, 3] = 64
    observed = np.zeros((2, 4), dtype=np.uint8)
    observed[0, 1] = 1; observed[1, 0] = 1; observed[1, 2] = 1
    record = {'key': 'wall-1', 'osm_id': 123, 'reachable': True, 'tier': 'A',
              'a_lonlat': inverse.transform(0, 0), 'b_lonlat': inverse.transform(*end),
              'length_m': 4, 'cols': 4, 'rows': 2, 'extent': {'s_l': 0, 's_r': 4},
              'observed': observed.tolist(), 'cls_grid': rgba[:, :, 3].tolist(),
              'views': [{'pano': 987}], 'png': 'wall-1.png'}
    anchor = {'world_frame_sha256': frame_digest(FRAME), 'source_sha256': 'a'*64,
              'a_world_xz': [32, 33], 'b_world_xz': [32+end[0], 33-end[1]],
              'base_world_y': 65}
    return record, anchor, rgba


class OrthofacadeAdapterTests(unittest.TestCase):
    def test_observed_mask_keeps_filled_and_unknown_pixels_out(self):
        record, anchor, rgba = fixture()
        result = project_wall(record, anchor, FRAME, rgba)
        self.assertEqual(result['candidate_samples'], 2)
        self.assertEqual(result['unknown_or_outside_samples'], 6)
        self.assertEqual(result['samples'][0]['pixel_rc'], [0, 1])
        self.assertEqual(result['samples'][0]['source_class_candidate'], 'window')
        self.assertEqual(result['samples'][0]['world_xyz_m'], [33.5, 66.5, 33])
        self.assertEqual(result['samples'][1]['world_xyz_m'], [32.5, 65.5, 33])
        self.assertEqual(result['admitted_samples'], 0)
        self.assertFalse(result['geometry_changed'])

    def test_northward_wall_decreases_minecraft_z_without_rebasing_height(self):
        record, anchor, rgba = fixture(north=True)
        result = project_wall(record, anchor, FRAME, rgba)
        self.assertEqual(result['samples'][0]['world_xyz_m'], [32, 66.5, 31.5])

    def test_shifted_anchor_frame_and_class_mask_are_rejected(self):
        record, anchor, rgba = fixture()
        shifted = copy.deepcopy(anchor); shifted['a_world_xz'][0] += .8
        with self.assertRaisesRegex(ValueError, 'existing wall'):
            project_wall(record, shifted, FRAME, rgba)
        shifted = dict(anchor, world_frame_sha256='b'*64)
        with self.assertRaisesRegex(ValueError, 'another world frame'):
            project_wall(record, shifted, FRAME, rgba)
        rgba[0, 0, 3] = 0
        with self.assertRaisesRegex(ValueError, 'mask disagree'):
            project_wall(record, anchor, FRAME, rgba)

    def test_source_extensions_do_not_stretch_or_create_geometry(self):
        record, anchor, rgba = fixture()
        record['extent'] = {'s_l': -1, 's_r': 3}
        result = project_wall(record, anchor, FRAME, rgba)
        self.assertEqual(result['candidate_samples'], 1)
        self.assertEqual(result['samples'][0]['world_xyz_m'], [32.5, 66.5, 33])
        self.assertEqual(result['admitted_samples'], 0)

    def test_offline_adapter_retains_source_hashes_and_missing_anchor_abstention(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); blocks = root/'blocks'; blocks.mkdir()
            record, anchor, rgba = fixture()
            (blocks/'wall-1.json').write_text(json.dumps(record))
            Image.fromarray(rgba).save(blocks/'wall-1.png')
            (blocks/'unknown.json').write_text(json.dumps(dict(record, key='unknown')))
            anchors = root/'anchors.json'; anchors.write_text(json.dumps({'walls': {'wall-1': anchor}}))
            coverage = root/'coverage.json'; coverage.write_text(json.dumps({'frame': FRAME}))
            result = adapt(blocks, anchors, coverage, root/'output')
            self.assertEqual(result['candidate_walls'], [{'wall': 'wall-1', 'candidate_samples': 2}])
            self.assertEqual(len(result['rejected_walls']), 1)
            wall = json.loads((root/'output/wall-1.json').read_text())
            self.assertEqual(len(wall['source_record_sha256']), 64)
            self.assertEqual(len(wall['source_png_sha256']), 64)
            self.assertFalse(result['world_modified'])

    def test_existing_elmhurst_and_water_tower_world_coordinates(self):
        project = Transformer.from_crs(4326, FRAME['crs'], always_xy=True)
        east, north = project.transform(-87.62443, 41.8972)
        self.assertAlmostEqual(east-FRAME['west'], 32, places=6)
        self.assertAlmostEqual(FRAME['north']-north, 33, places=6)
        east, north = project.transform(-87.9403, 41.8995)
        self.assertAlmostEqual(east-FRAME['west'], -26179.22285576971, places=6)
        self.assertAlmostEqual(FRAME['north']-north, -270.7152814921704, places=6)


if __name__ == '__main__':
    unittest.main()
