import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

from building_rerun import selected_tiles
from chicago_tiles import digest


class BuildingRerunRouteTests(unittest.TestCase):
    def test_frozen_tile_list_preserves_declared_route_order(self):
        plan={'tiles':[{'id':'0_0','tx':0,'tz':0},{'id':'1_-1','tx':1,'tz':-1}]}
        by_id={tile['id']:tile for tile in plan['tiles']}
        with TemporaryDirectory() as folder:
            path=Path(folder)/'route.json'
            path.write_text(json.dumps({'plan_sha256':digest(plan),'tiles':['1_-1','0_0']}))
            args=SimpleNamespace(tile_list=path,min_tx=None,max_tx=None,min_tz=None,max_tz=None)
            tiles,bounds=selected_tiles(args,plan,by_id)
        self.assertEqual([tile['id'] for tile in tiles],['1_-1','0_0'])
        self.assertEqual(bounds['tiles_requested'],2)

    def test_tile_list_rejects_wrong_plan(self):
        plan={'tiles':[{'id':'0_0','tx':0,'tz':0}]};by_id={'0_0':plan['tiles'][0]}
        with TemporaryDirectory() as folder:
            path=Path(folder)/'route.json';path.write_text(json.dumps({'plan_sha256':'wrong','tiles':['0_0']}))
            args=SimpleNamespace(tile_list=path,min_tx=None,max_tx=None,min_tz=None,max_tz=None)
            with self.assertRaisesRegex(ValueError,'frozen plan'):
                selected_tiles(args,plan,by_id)


if __name__=='__main__':unittest.main()
