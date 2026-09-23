import json
from pathlib import Path
import sys
import tempfile
import unittest

import nbtlib as n

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from city_catalog import load, destinations, shared_world_coordinates
from elmhurst import target_for_city
from elmhurst import atlas_frame_for_city
from global_projection import page_coordinates, MAX_PAGE_SCALE_ERROR_PPM
from metric_chart import chart_in_frame
from pyproj import CRS
from travel_controls import install_controls


class CityNavigationTests(unittest.TestCase):
    def test_catalog_uses_real_wgs84_city_identity(self):
        catalog = load()
        elmhurst = next(city for city in catalog['cities'] if city['id'] == 'elmhurst-il')
        self.assertEqual(elmhurst['wgs84'], [-87.9403418, 41.8994745])
        self.assertEqual({city['id'] for city in destinations(catalog)}, {'chicago-il','elmhurst-il'})
        self.assertEqual(next(city for city in catalog['cities'] if city['id'] == 'elmhurst-il')['status'],
                         'registered')

    def test_shared_world_coordinates_keep_east_and_south_axes(self):
        frame = {'crs': 'EPSG:3857', 'west': -32, 'north': 33}
        point = shared_world_coordinates(-87.9403418, 41.8994745, frame, 80)
        self.assertEqual(point[1], 80)
        self.assertLess(point[0], 0)

    def test_elmhurst_city_target_is_the_exact_wgs84_point_in_shared_frame(self):
        frame = {
            'crs': '+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 '
                   '+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs',
            'west': -32, 'north': 33,
        }
        elmhurst = next(city for city in load()['cities'] if city['id'] == 'elmhurst-il')
        target = target_for_city(elmhurst, frame, 95, [-26368, -512], 256)
        self.assertAlmostEqual(target[0], -26182.701896609742, places=6)
        self.assertEqual(target[1], 95)
        self.assertAlmostEqual(target[2], -267.8957291646485, places=6)
        self.assertEqual(elmhurst['target'], target)

    def test_elmhurst_target_must_be_inside_generated_tile(self):
        frame = {
            'crs': '+proj=tmerc +lat_0=41.8972 +lon_0=-87.62443 +k=1 '
                   '+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs',
            'west': -32, 'north': 33,
        }
        elmhurst = next(city for city in load()['cities'] if city['id'] == 'elmhurst-il')
        with self.assertRaisesRegex(ValueError, 'outside its generated tile'):
            target_for_city(elmhurst, frame, 95, [0, 0], 256)

    def test_elmhurst_builder_uses_its_scale_bounded_atlas_page(self):
        elmhurst = next(city for city in load()['cities'] if city['id'] == 'elmhurst-il')
        vertical = {'vertical_offset_m': -116, 'dimension_min_y': -64,
                    'dimension_height': 1024}
        frame, address = atlas_frame_for_city(elmhurst, vertical)
        self.assertEqual(address.id, 'ec1/b0893/c0466')
        self.assertEqual(frame['atlas_page_id'], address.id)
        self.assertLessEqual(frame['page_scale_error_ppm'], MAX_PAGE_SCALE_ERROR_PPM)
        point = page_coordinates(*elmhurst['wgs84'], address)
        size = 256
        tile = chart_in_frame(*elmhurst['wgs84'], size, frame)
        world_offset = [tile['west'] - frame['west'], frame['north'] - tile['north']]
        target = target_for_city(elmhurst, frame, 95, world_offset, size)
        self.assertAlmostEqual(target[0], point['x'], places=7)
        self.assertAlmostEqual(target[2], point['z'], places=7)
        chicago_frame = json.loads((Path(__file__).resolve().parents[1] /
                                    'runs/chicago-adaptation-city-001/frame.json').read_text())
        self.assertFalse(CRS.from_user_input(frame['crs']).equals(
            CRS.from_user_input(chicago_frame['crs'])))

    def test_travel_pack_contains_city_and_coordinate_dialogs(self):
        with tempfile.TemporaryDirectory() as path:
            world = Path(path)
            (world / 'earthcraft.json').write_text(json.dumps({'spawn': [1.5, 65, 1.5]}))
            n.File({'Data': n.Compound({'DataPacks': n.Compound({'Enabled': n.List[n.String](['vanilla'])})})},
                   gzipped=True).save(world / 'level.dat')
            result = install_controls(world)
            pack = world / 'datapacks/earthcraft_travel'
            cities = json.loads((pack / 'data/earthcraft/dialog/cities.json').read_text())
            coordinates = json.loads((pack / 'data/earthcraft/dialog/coordinates.json').read_text())
            self.assertEqual(result['city_count'], 2)
            self.assertEqual(cities['actions'][0]['label'], 'Chicago, IL')
            self.assertEqual(len(coordinates['inputs']), 3)
            coordinate_commands = [action['action'].get('command', action['action'].get('template', ''))
                                   for action in coordinates['actions']]
            self.assertIn('trigger ec_coord_ready set 1', coordinate_commands)
            self.assertEqual((pack / 'data/earthcraft/function/travel/city_menu.mcfunction').read_text().splitlines()[0],
                             'function earthcraft:travel/cities')
            elmhurst_function = (pack / 'data/earthcraft/function/travel/city_2.mcfunction').read_text()
            self.assertIn('tp @s -26182.701896609742 95.0 -267.8957291646485', elmhurst_function)
            self.assertIn('terrain in this save is not confirmed', elmhurst_function)
            travel = json.loads((pack / 'data/earthcraft/dialog/travel.json').read_text())
            self.assertIn('Global page transposition is not yet active', travel['body'][0]['contents'])
            self.assertIn('dialog show @s earthcraft:travel',
                          (pack / 'data/earthcraft/function/travel/open.mcfunction').read_text())
            teleporter = (pack / 'data/earthcraft/function/travel/give_teleporter.mcfunction').read_text()
            self.assertIn('minecraft:carrot_on_a_stick', teleporter)
            self.assertIn('earthcraft_teleporter:1b', teleporter)
            load_lines = (pack / 'data/earthcraft/function/travel/load.mcfunction').read_text()
            self.assertIn('minecraft.used:minecraft.carrot_on_a_stick', load_lines)
            tick_lines = (pack / 'data/earthcraft/function/travel/tick.mcfunction').read_text()
            self.assertIn('ec_tp_use', tick_lines)
            self.assertIn('tag=!earthcraft_teleporter_checked', tick_lines)
            first_join = (pack / 'data/earthcraft/function/travel/first_join.mcfunction').read_text()
            self.assertIn('unless items entity @s inventory.*', first_join)
            self.assertIn('earthcraft_teleporter_checked', first_join)
            self.assertEqual(result['teleporter_commands'][0], '/function earthcraft:travel/give_teleporter')


if __name__ == '__main__':
    unittest.main()
