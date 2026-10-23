"""Regional LiDAR trees: tile grid and the layer that lands in the delivered world."""
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from regional_trees import layer_for, tile_grid

FRAME={'crs':'EPSG:26916','west':400000,'north':4640000}


class RegionalTreeTests(unittest.TestCase):
    def test_tile_grid_is_the_tiles_region(self):
        grid=tile_grid({'west':400000+3*512,'north':4640000+2*512,'size':512},FRAME)
        self.assertEqual((grid.x0,grid.z0,grid.size),(1536,-1024,512))
        with self.assertRaisesRegex(ValueError,'one region'):
            tile_grid({'west':400010,'north':4640000,'size':512},FRAME)

    def test_crowns_never_enter_built_columns_and_sources_split_observed_from_estimated(self):
        grid=tile_grid({'west':400000,'north':4640000,'size':512},FRAME)
        labels=np.zeros((512,512),int);labels[8:13,8:13]=2
        chm=np.zeros((512,512),np.float32);chm[8:13,8:13]=8;chm[10,10]=9
        built=np.zeros((512,512),bool);built[8,8:13]=True  # a wall along the crown's north edge
        surface={'built':built,'ground_y':np.full((512,512),70,np.int32)}
        tree={'id':'tree-10-10','label':2,'top':(10,10),'trunk':(10,10),'height':9.0,'radius':2.5,
              'crown_base':3.0,'points':120,'shape':'oak'}
        layer=layer_for([tree],labels,chm,surface,grid,FRAME,'USGS_TEST_2020')
        cells=list(layer.edits)
        self.assertTrue(cells)
        self.assertFalse(any(z==8 for _,_,z in cells))  # nothing in the built row
        self.assertTrue(all(y>70 for _,y,_ in cells))
        self.assertIn('minecraft:oak_log[axis=y]',layer.keys())
        kinds=layer.counts_by_kind()
        self.assertEqual(set(kinds),{'tree_canopy','tree_structure'})
        self.assertEqual({s['evidence'] for s in layer.sources},{'observed','estimated'})


class SparseChunkTests(unittest.TestCase):
    def chunk(self):
        import nbtlib as n
        from detail_anvil import AIR,encode_states
        biomes=n.Compound({'palette':n.List[n.String]([n.String('minecraft:plains')])})
        section=n.Compound({'Y':n.Byte(5),'block_states':encode_states(np.full(4096,AIR,dtype=object)),'biomes':biomes})
        return n.Compound({'sections':n.List[n.Compound]([section]),'yPos':n.Int(-4)})

    def test_absent_section_is_refused_unless_the_world_grows_within_its_height(self):
        from detail_anvil import ChunkVolume
        with self.assertRaisesRegex(ValueError,'absent'):ChunkVolume(self.chunk()).set(0,100,0,'minecraft:oak_log[axis=y]')
        volume=ChunkVolume(self.chunk(),height=1024,grow=True)
        volume.set(0,100,0,'minecraft:oak_log[axis=y]')
        self.assertEqual(volume.get(0,100,0),'minecraft:oak_log[axis=y]')
        self.assertTrue(volume.flush())
        self.assertEqual([int(s['Y']) for s in volume.tag['sections']],[5,6])
        self.assertEqual(str(volume.tag['sections'][1]['biomes']['palette'][0]),'minecraft:plains')
        with self.assertRaisesRegex(ValueError,'absent'):volume.set(0,1000,0,'minecraft:stone')  # above the dimension


if __name__=='__main__':unittest.main()
