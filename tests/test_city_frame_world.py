"""Compare a monolithic terrain world to independent city-frame tiles, block for block."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import nbtlib
import numpy as np
import rasterio
from affine import Affine

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metric_chart import chart
from metric_world import ROOT,build
from inspect_world import chunks
from verify_metric_world import verify


def source_fixture(root,meta,elevation):
    root.mkdir()
    meta=dict(meta,buildings_available=False,elevation_raster='elevation.tif')
    size=meta['size']
    np.savez(root/'rasters.npz',elevation=elevation,cover=np.zeros((size,size),np.uint8))
    with rasterio.open(root/'elevation.tif','w',driver='GTiff',width=size,height=size,count=1,
            dtype='float32',crs=meta['crs'],transform=Affine(1,0,meta['west'],0,-1,meta['north'])) as raster:
        raster.write(elevation.astype(np.float32),1)
    (root/'sources.json').write_text(json.dumps(meta))
    (root/'osm-ways.json').write_text('[]')
    (root/'cook-buildings-2022.json').write_text('{"features":[]}')


def blocks(world):
    result={}
    for region in (world/'region').glob('*.mca'):
        for _,tag,_ in chunks(region):
            key=(int(tag['xPos']),int(tag['zPos']))
            if key in result:raise AssertionError('Duplicated tile chunk')
            result[key]=[{ 'y':int(section['Y']),
                'palette':[str(p['Name']) for p in section['block_states']['palette']],
                'data':[int(w) for w in section['block_states'].get('data',[])]}
                for section in tag['sections']]
    return result


class CityFrameWorldTests(unittest.TestCase):
    @unittest.skipUnless((ROOT/'worlds/chicago-water-tower-64/Arnis World 1/level.dat').exists(),'Minecraft fixture required')
    def test_independent_tiles_equal_monolithic_world_with_negative_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);meta=chart(0,0,32)
            frame={'crs':meta['crs'],'west':meta['west']+16,'north':meta['north']-16,
                   'vertical_offset_m':-100,'dimension_min_y':-64,'dimension_height':1024}
            zz,xx=np.mgrid[:32,:32];elevation=(160+xx*.4+zz*.6).astype(np.float32)
            source=root/'source';source_fixture(source,meta,elevation)
            mono=root/'monolithic';build(source,mono,world_frame=frame);verify(mono)
            expected=blocks(mono);actual={};joined=np.zeros((32,32),int)
            for z in (0,16):
                for x in (0,16):
                    tile=dict(meta,size=16,west=meta['west']+x,north=meta['north']-z)
                    src=root/f'source-{x}-{z}';dst=root/f'world-{x}-{z}'
                    source_fixture(src,tile,elevation[z:z+16,x:x+16])
                    build(src,dst,world_frame=frame);verify(dst)
                    payload=blocks(dst);self.assertFalse(set(payload)&set(actual));actual.update(payload)
                    joined[z:z+16,x:x+16]=np.load(dst/'top-heights.npy')
                    report=json.loads((dst/'earthcraft.json').read_text())
                    self.assertEqual(report['world_offset_xz'],[x-16,z-16])
                    np.testing.assert_allclose(nbtlib.load(dst/'level.dat')['Data']['Player']['Pos'],report['spawn'])
            self.assertEqual(actual,expected)
            self.assertEqual(set(actual),{(-1,-1),(-1,0),(0,-1),(0,0)})
            np.testing.assert_array_equal(joined,np.load(mono/'top-heights.npy'))


if __name__=='__main__':unittest.main()
