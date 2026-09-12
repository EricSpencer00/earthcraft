import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import nbtlib as n
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metric_world import check_output_capacity, packed, region_write, select_spawn_cell
from verify_metric_world import point_provenance_matches
from inspect_world import chunks
from osm_json_to_kml import convert


class MetricWorldTests(unittest.TestCase):
    def test_capacity_check_uses_destination_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'tiles' / 'tile-1'
            with patch('metric_world.shutil.disk_usage', return_value=SimpleNamespace(free=25 * 1024**3)) as usage:
                self.assertEqual(check_output_capacity(output), Path(directory))
            usage.assert_called_once_with(Path(directory))

            with patch('metric_world.shutil.disk_usage', return_value=SimpleNamespace(free=19 * 1024**3)):
                with self.assertRaisesRegex(ValueError, 'output volume'):
                    check_output_capacity(output)

    def test_packing_across_long_boundaries(self):
        for bits in (4,5,6,9,10):
            values = np.arange(4096) % (1 << bits)
            encoded = packed(values,bits)
            decoded = []
            for word in encoded:
                unsigned = int(word) & ((1<<64)-1)
                decoded.extend((unsigned >> (i*bits)) & ((1<<bits)-1) for i in range(64//bits))
            np.testing.assert_array_equal(decoded[:4096],values)

    def test_anvil_negative_coordinates_and_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'r.-1.-1.mca'
            records=[(cx,cz,n.File({'xPos':n.Int(cx),'zPos':n.Int(cz),'marker':n.String(str((cx,cz)))}))
                     for cx,cz in [(-32,-32),(-1,-32),(-32,-1),(-1,-1)]]
            region_write(path,records)
            read=list(chunks(path))
            self.assertEqual({i for i,_,_ in read},{0,31,992,1023})
            self.assertEqual({(int(t['xPos']),int(t['zPos'])) for _,t,_ in read},
                             {(x,z) for x,z,_ in records})
            with self.assertRaises(FileExistsError):
                region_write(path,records)

    def test_floor_count_does_not_become_measured_height(self):
        nodes=[{'type':'node','id':i,'lon':x,'lat':y} for i,(x,y) in enumerate([(0,0),(1,0),(1,1)],1)]
        way={'type':'way','id':10,'nodes':[1,2,3,1],'tags':{'building':'yes','building:levels':'10'}}
        _,count=convert({'elements':nodes+[way]})
        self.assertEqual(count,0)
        way['tags']['height']='31.5 m'
        _,count=convert({'elements':nodes+[way]})
        self.assertEqual(count,1)

    def test_empty_point_provenance_requires_no_hidden_point_file(self):
        with tempfile.TemporaryDirectory() as directory:
            world=Path(directory)
            self.assertTrue(point_provenance_matches(world,None))
            np.save(world/'point-voxels.npy',np.empty((0,3),dtype=int))
            self.assertFalse(point_provenance_matches(world,None))

    def test_fully_occupied_tile_spawns_above_lowest_measured_roof(self):
        ground=np.full((4,4),70,np.int32)
        footprint=np.ones((4,4),bool)
        surface=np.zeros((4,4),np.uint8)
        west=np.zeros((4,4),bool);west[:,:2]=True
        east=~west
        buildings=[{'mask':west,'high':88},{'mask':east,'high':104}]
        z,x,top,targets=select_spawn_cell(ground,footprint,surface,9,buildings)
        self.assertTrue(west[z,x])
        self.assertEqual(top,88)
        self.assertEqual(len(targets),16)


if __name__=='__main__':
    unittest.main()
