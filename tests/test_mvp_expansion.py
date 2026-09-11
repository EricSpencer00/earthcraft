import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
import nbtlib as n
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from geographic_quality import vertical_layout, water_components
from material_router import choose
from travel_controls import install_controls


class ExpansionTests(unittest.TestCase):
    def test_water_report_separates_disconnected_bodies(self):
        mask=np.array([[1,1,0,1],[0,0,0,1]],bool)
        heights=np.array([[4,4,0,9],[0,0,0,10]])
        result=water_components(mask,heights)
        self.assertEqual(sorted(r['height_span_blocks'] for r in result),[0,1])

    def test_high_altitude_preserves_relief(self):
        elevation=np.array([[5100.,5101.],[5199.,5200.]])
        offset,ground,height=vertical_layout(elevation)
        np.testing.assert_array_equal(np.diff(ground,axis=0),np.diff(elevation,axis=0))
        self.assertEqual(offset,-5036)
        self.assertGreater(height,int(ground.max())+64)

    def test_excessive_range_is_rejected_not_compressed(self):
        with self.assertRaises(ValueError): vertical_layout(np.array([0.,8800.]))
        with self.assertRaises(ValueError): vertical_layout(np.array([0.,np.nan]))

    def test_router_abstains_and_respects_observed_glass(self):
        p={'black_concrete':np.array([0,0,0]),'white_concrete':np.array([255,255,255]),
           'black_stained_glass':np.array([0,0,0])}
        self.assertIsNone(choose({},'building',p))
        self.assertEqual(choose({'building:colour':'black','building:material':'glass'},'building',p),'black_stained_glass')
        self.assertIsNone(choose({'building:colour':'not_a_color'},'building',p))

    def test_travel_menu_uses_low_privilege_triggers_and_does_not_edit_blocks(self):
        with tempfile.TemporaryDirectory() as path:
            world=Path(path)
            (world/'earthcraft.json').write_text(json.dumps({'spawn':[1.5,65,1.5]}))
            n.File({'Data':n.Compound({'DataPacks':n.Compound({'Enabled':n.List[n.String](['vanilla'])})})},gzipped=True).save(world/'level.dat')
            result=install_controls(world)
            self.assertFalse(result['changes_geographic_blocks'])
            pack=world/'datapacks/earthcraft_travel'
            dialog=json.loads((pack/'data/earthcraft/dialog/travel.json').read_text())
            self.assertEqual(dialog['inputs'][0]['end'],10)
            for action in dialog['actions']:
                action=action['action']
                self.assertTrue(action.get('command',action.get('template','')).startswith('trigger '))
            commands='\n'.join(p.read_text() for p in pack.rglob('*.mcfunction'))
            self.assertNotIn('setblock ',commands)
            self.assertNotIn('fill ',commands)
            self.assertIn('minecraft:scale',commands)
            with self.assertRaises(FileExistsError): install_controls(world)


if __name__=='__main__':unittest.main()
