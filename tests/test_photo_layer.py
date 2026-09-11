import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import nbtlib as n
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from metric_world import packed, region_write
from photo_layer import (apply_layer, chart_translation, layer_bounds, load_records,
                         verify_layer, verify_saved_entities, visual_payload, write_entry)


def fixture(root, translated=False, cover_face=False):
    (root / 'region').mkdir(parents=True)
    shift = np.array([16, 0, 16]) if translated else np.zeros(3, dtype=int)
    cell = np.array([1, 0, 1]) + shift
    points = [cell.tolist()]
    if cover_face: points.append((cell + [-1, 0, 0]).tolist())
    np.save(root / 'point-voxels.npy', points)
    values = np.zeros((16, 16, 16), dtype=int)
    for x, y, z in points: values[y % 16, z % 16, x % 16] = 1
    section = n.Compound({'Y': n.Byte(0), 'block_states': n.Compound({
        'palette': n.List[n.Compound]([n.Compound({'Name': n.String(name)})
                                     for name in ('minecraft:air', 'minecraft:stone')]),
        'data': packed(values, 4)})})
    cx, cz = int(cell[0] // 16), int(cell[2] // 16)
    region_write(root / 'region/r.0.0.mca', [(cx, cz, n.File({
        'xPos': n.Int(cx), 'zPos': n.Int(cz), 'sections': n.List[n.Compound]([section])}))])
    meta = {'source': {'crs': 'synthetic-metric', 'west': -int(shift[0]),
                       'north': 16+int(shift[2]), 'size': 32},
            'vertical_offset_m': 0, 'spawn': [float(cell[0]), 5., float(cell[2])],
            'dimension_min_y': 0, 'dimension_height': 16}
    (root / 'earthcraft.json').write_text(json.dumps(meta))
    n.File({'Data': n.Compound({'Player': n.Compound({
        'Pos': n.List[n.Double](meta['spawn']), 'Rotation': n.List[n.Float]([0, 0]),
        'abilities': n.Compound({'flying': n.Byte(0)})})})}, gzipped=True).save(root / 'level.dat')
    return meta


def source_layer(root):
    fixture(root)
    rgba = np.full((16, 16, 4), 255, dtype=np.uint8)
    buffer = io.BytesIO(); Image.fromarray(rgba).save(buffer, format='PNG'); texture = buffer.getvalue()
    record = {'id': 0, 'cell': [1, 0, 1], 'face': 'west', 'observed_texels': 256,
              'texture_sha256': hashlib.sha256(texture).hexdigest()}
    attribution = {'license': 'test-fixture', 'title': 'Synthetic unit fixture, not geographic evidence'}
    with zipfile.ZipFile(root / 'resources.zip', 'x') as archive:
        write_entry(archive, 'attribution.json', attribution)
        write_entry(archive, 'earthcraft-skin-manifest.json', [record])
        write_entry(archive, 'assets/earthcraft_skin/textures/block/face_0.png', texture)
    report = {'llm_used': False, 'new_or_removed_blocks': 0, 'texture_pixels_per_block_edge': 16,
              'surface_offset_m': .002, 'display_entities': 1, 'observed_texels': 256,
              'photo': attribution, 'independent_photo_validation': False,
              'initial_player_position': [8., 5., 8.], 'initial_player_yaw': 90.}
    (root / 'photo-skin.json').write_text(json.dumps(report))


class PhotoLayerTests(unittest.TestCase):
    def test_translation_preserves_enu_and_rejects_fractional_or_different_crs(self):
        a = {'source': {'crs': 'metric', 'west': -32, 'north': 33}, 'vertical_offset_m': -117}
        b = {'source': {'crs': 'metric', 'west': -192, 'north': 129}, 'vertical_offset_m': -116}
        np.testing.assert_array_equal(chart_translation(a, b), [160, 1, 96])
        c = copy.deepcopy(b); c['source']['crs'] = 'different'
        with self.assertRaises(ValueError): chart_translation(a, c)
        c = copy.deepcopy(b); c['source']['north'] += .1
        with self.assertRaises(ValueError): chart_translation(a, c)

    def test_transfer_preserves_blocks_pixels_and_replays_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); source = base / 'source'; source_layer(source)
            targets = [base / name for name in ('a', 'b')]
            for target in targets:
                fixture(target, translated=True)
                before = (target / 'region/r.0.0.mca').read_bytes()
                with self.assertRaises(ValueError): apply_layer(target, source)
                self.assertFalse((target / 'resources.zip').exists())
                result = apply_layer(target, source, allow_experimental=True, start_at_detail=True)
                self.assertEqual(result['chart_translation_blocks'], [16, 0, 16])
                self.assertEqual(result['initial_player_position'], [24., 5., 24.])
                self.assertEqual(before, (target / 'region/r.0.0.mca').read_bytes())
                self.assertTrue(verify_layer(target, source)['command_positions_verified'])
                with self.assertRaises(FileExistsError): apply_layer(target, source, allow_experimental=True)
            self.assertEqual(visual_payload(targets[0]), visual_payload(targets[1]))
            self.assertEqual((targets[0] / 'resources.zip').read_bytes(), (targets[1] / 'resources.zip').read_bytes())

    def test_city_frame_translation_and_local_observation_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source_layer(source)
            target=root/'target';meta=fixture(target,translated=True)
            # Same source origin; chunk positions are shifted by a shared city frame.
            meta['source']['west']=0;meta['source']['north']=16
            meta['world_offset_xz']=[16,16]
            (target/'earthcraft.json').write_text(json.dumps(meta))
            np.save(target/'point-voxels.npy',[[1,0,1]])
            result=apply_layer(target,source,allow_experimental=True)
            self.assertEqual(result['chart_translation_blocks'],[16,0,16])
            self.assertTrue(verify_layer(target,source)['command_positions_verified'])

    def test_missing_or_covered_anchor_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); source = base / 'source'; source_layer(source)
            target = base / 'target'; fixture(target, translated=True, cover_face=True)
            with self.assertRaises(ValueError): apply_layer(target, source, allow_experimental=True)
            self.assertFalse((target / 'resources.zip').exists())
            np.save(target / 'point-voxels.npy', [[22, 0, 22]])
            with self.assertRaises(ValueError): apply_layer(target, source, allow_experimental=True)

    def test_tampered_pixels_or_commands_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); source = base / 'source'; source_layer(source)
            target = base / 'target'; fixture(target, translated=True)
            apply_layer(target, source, allow_experimental=True)
            command = target / 'datapacks/earthcraft_photo_skin/data/earthcraft_skin/function/build.mcfunction'
            command.write_text(command.read_text().replace('17.5 0.5 17.5', '18.5 0.5 17.5'))
            with self.assertRaises(ValueError): verify_layer(target, source)
            report = json.loads((source / 'photo-skin.json').read_text())
            report['observed_texels'] = 255
            (source / 'photo-skin.json').write_text(json.dumps(report))
            with self.assertRaises(ValueError): load_records(source)

    def test_chunk_loading_is_bounded_and_uses_translated_anchors(self):
        self.assertEqual(layer_bounds([{'cell': [190, 91, 131]}, {'cell': [198, 108, 135]}]),
                         [190, 131, 198, 135])
        with self.assertRaises(ValueError): layer_bounds([{'cell': [0, 0, 0]}, {'cell': [512, 0, 512]}])

    def test_saved_entity_positions_and_model_ids_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'entities').mkdir()
            entity = n.Compound({'id': n.String('minecraft:item_display'),
                'Tags': n.List[n.String](['earthcraft_photo_skin']),
                'Pos': n.List[n.Double]([17.5, 0.5, 17.5]),
                'item': n.Compound({'components': n.Compound({
                    'minecraft:item_model': n.String('earthcraft_skin:face_0')})})})
            tag = n.File({'Entities': n.List[n.Compound]([entity])})
            region_write(root / 'entities/r.0.0.mca', [(1, 1, tag)])
            self.assertEqual(verify_saved_entities(root, [{'id': 0, 'cell': [17, 0, 17]}])['count'], 1)
            with self.assertRaises(ValueError): verify_saved_entities(root, [{'id': 0, 'cell': [18, 0, 17]}])


if __name__ == '__main__': unittest.main()
