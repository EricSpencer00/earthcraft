"""Local writer integration, with synthetic coordinates and independent block readback."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np
import nbtlib
import rasterio
from affine import Affine
from pyproj import Transformer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metric_chart import chart
from metric_world import ROOT,build
from verify_metric_world import verify,unpack
from geographic_quality import audit
from inspect_world import chunks
from travel_controls import install_controls


class MappedWorldTests(unittest.TestCase):
    @unittest.skipUnless((ROOT/'worlds/chicago-water-tower-64/Arnis World 1/level.dat').exists(), 'Local Minecraft metadata fixture required')
    def test_known_roof_material_no_unknown_height_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source';source.mkdir();world=Path(tmp)/'world'
            meta=chart(0,0,16)
            meta.update(buildings_available=True,building_source_kind='osm-explicit',elevation_raster='elevation.tif')
            elevation=np.full((16,16),100,dtype=np.float32)
            np.savez(source/'rasters.npz',elevation=elevation,cover=np.zeros((16,16),np.uint8))
            with rasterio.open(source/'elevation.tif','w',driver='GTiff',width=16,height=16,count=1,dtype='float32',
                               crs=meta['crs'],transform=Affine(1,0,-8,0,-1,8)) as raster:raster.write(elevation,1)
            inverse=Transformer.from_crs(meta['crs'],4326,always_xy=True)
            coords=[inverse.transform(x-8,8-z) for x,z in [(2,2),(8,2),(8,8),(2,8),(2,2)]]
            tags=[{'building':'yes','height':'5','building:material':'brick','roof:colour':'blue'},
                  {'building':'yes','height':'50','min_height':'unknown'},
                  {'building':'yes','building:levels':'20'},
                  {'building':'no','height':'70'}]
            ways=[{'id':i,'tags':tag,'coordinates':coords,'closed':True} for i,tag in enumerate(tags)]
            (source/'sources.json').write_text(json.dumps(meta))
            (source/'osm-ways.json').write_text(json.dumps(ways))
            (source/'cook-buildings-2022.json').write_text('{"features":[]}')
            build(source,world);result=verify(world);quality=audit(world)
            self.assertEqual(result['top_y_range'],[64,68])
            report=json.loads((world/'earthcraft.json').read_text())
            level=nbtlib.load(world/'level.dat')['Data']
            self.assertEqual(float(level['BorderSize']),4112)
            self.assertEqual(float(level['BorderSizeLerpTarget']),4112)
            self.assertEqual(int(level['BorderSizeLerpTime']),0)
            self.assertFalse(level['Player']['abilities']['flying'])
            np.testing.assert_allclose(level['Player']['Rotation'],report['spawn_rotation'],atol=1e-5)
            install_controls(world)
            home=(world/'datapacks/earthcraft_travel/data/earthcraft/function/travel/home.mcfunction').read_text()
            self.assertIn('tp @s '+' '.join(map(str,report['spawn']+report['spawn_rotation'])),home)
            self.assertEqual([b['osm_way'] for b in report['buildings']],[0])
            self.assertEqual({b['osm_way'] for b in report['osm_skipped_buildings']},{1,2})
            self.assertEqual(quality['coverage']['measured_roof_cells'],0)
            self.assertGreater(quality['coverage']['mapped_shell_roof_cells'],0)
            self.assertEqual(quality['water_levels']['status'],'unavailable')
            _,tag,_=next(chunks(world/'region/r.0.0.mca'))
            def block(x,y,z):
                section=next(s for s in tag['sections'] if int(s['Y'])==y//16)
                states=section['block_states'];palette=states['palette']
                index=0 if len(palette)==1 else unpack(states['data'],max(4,(len(palette)-1).bit_length()),4096)[(y%16)*256+z*16+x]
                return str(palette[int(index)]['Name'])
            self.assertEqual(block(4,68,4),'minecraft:blue_concrete')
            self.assertEqual(block(2,65,4),'minecraft:bricks')
            self.assertEqual(block(4,66,4),'minecraft:air')
