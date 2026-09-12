import copy,json
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
import nbtlib as n

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metric_world import region_write
from city_continuous import initialize,append_tile
from inspect_world import chunks


class ContinuousCityTests(unittest.TestCase):
    def test_shared_region_idempotence_and_interrupted_commit_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);frame={'crs':'EPSG:3857','west':0,'north':0,'vertical_offset_m':0,'dimension_min_y':-64,'dimension_height':1024}
            tiles=[{'id':f'{x}_0','size':16,'world_offset_xz':[x*16,0]} for x in range(2)]
            plan={'frame':frame,'tiles':tiles};worlds=[]
            for x,tile in enumerate(tiles):
                world=root/f'tile-{x}';(world/'region').mkdir(parents=True);worlds.append(world)
                (world/'earthcraft.json').write_text(json.dumps({'world_frame':frame,'world_offset_xz':tile['world_offset_xz']}))
                tag=n.File({'xPos':n.Int(x),'zPos':n.Int(0),'sections':n.List[n.Compound]([
                    n.Compound({'Y':n.Byte(0),'block_states':n.Compound({'palette':n.List[n.Compound]([
                        n.Compound({'Name':n.String('minecraft:stone' if x==0 else 'minecraft:dirt')})])})})])})
                region_write(world/'region/r.0.0.mca',[(x,0,tag)])
                n.File({'Data':n.Compound({})},gzipped=True).save(world/'level.dat')
            target=root/'continuous';initialize(target,worlds[0],plan)
            initial_state=json.loads((target/'city-coverage.json').read_text())
            initial_border=initial_state['world_border']
            append_tile(target,worlds[0],tiles[0],plan)
            append_tile(target,worlds[0],tiles[0],plan)
            with patch('world_border.n.load',side_effect=OSError('simulated interruption after region commit')):
                with self.assertRaises(OSError):append_tile(target,worlds[1],tiles[1],plan)
            self.assertTrue((target/'pending-region.json').exists())
            append_tile(target,worlds[1],tiles[1],plan)
            state=json.loads((target/'city-coverage.json').read_text())
            self.assertEqual(state['generated_tiles'],2);self.assertTrue(state['full_chicago_geometry_complete'])
            self.assertEqual(state['world_border'],initial_border)
            self.assertFalse(state['appearance_complete']);self.assertFalse((target/'pending-region.json').exists())
            self.assertEqual({int(t['xPos']) for _,t,_ in chunks(target/'region/r.0.0.mca')},{0,1})
            (target/'session.lock').write_bytes(b'opened')
            with self.assertRaisesRegex(ValueError,'opened'):append_tile(target,worlds[1],tiles[1],plan)


if __name__=='__main__':unittest.main()
