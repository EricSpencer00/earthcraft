import json
from pathlib import Path
import sys
import tempfile
import unittest

import nbtlib as n

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from city_catalog import load, materialized, shared_world_coordinates
from travel_controls import install_controls


class CityNavigationTests(unittest.TestCase):
    def test_catalog_uses_real_wgs84_city_identity(self):
        catalog = load()
        elmhurst = next(city for city in catalog['cities'] if city['id'] == 'elmhurst-il')
        self.assertEqual(elmhurst['wgs84'], [-87.9403418, 41.8994745])
        self.assertEqual({city['id'] for city in materialized(catalog)}, {'chicago-il','elmhurst-il'})

    def test_shared_world_coordinates_keep_east_and_south_axes(self):
        frame = {'crs': 'EPSG:3857', 'west': -32, 'north': 33}
        point = shared_world_coordinates(-87.9403418, 41.8994745, frame, 80)
        self.assertEqual(point[1], 80)
        self.assertLess(point[0], 0)

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
                             'dialog show @s earthcraft:cities')


if __name__ == '__main__':
    unittest.main()
