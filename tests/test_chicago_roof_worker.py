import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from chicago_roof_worker import route_tile_ids
from chicago_tiles import digest


def fixture_plan():
    return {
        'schema':'earthcraft.chicago-city-plan-v1',
        'tiles':[
            {'id':'0_0','bounds':[-1.0,-1.0,0.0,0.0]},
            {'id':'1_0','bounds':[0.0,-1.0,1.0,0.0]},
        ],
    }


class ChicagoRoofWorkerTests(unittest.TestCase):
    def test_route_requires_matching_plan_and_unique_known_tiles(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'route.json';plan=fixture_plan();tiles=[tile['id'] for tile in plan['tiles'][:2]]
            path.write_text(json.dumps({'schema':'named-road-priority-v1','plan_sha256':digest(plan),'tiles':tiles}))
            self.assertEqual(route_tile_ids(path,plan),tuple(tiles))
            path.write_text(json.dumps({'schema':'named-road-priority-v1','plan_sha256':digest(plan),'tiles':[tiles[0],tiles[0]]}))
            with self.assertRaisesRegex(ValueError,'unique'):
                route_tile_ids(path,plan)
            path.write_text(json.dumps({'schema':'named-road-priority-v1','plan_sha256':'changed','tiles':tiles}))
            with self.assertRaisesRegex(ValueError,'frozen'):
                route_tile_ids(path,plan)


if __name__ == '__main__':
    unittest.main()
