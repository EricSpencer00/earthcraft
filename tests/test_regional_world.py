"""Read back a mixed measured-roof/point world with an explicit survey gap."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import rasterio
from affine import Affine
from pyproj import Transformer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metric_chart import chart
from metric_world import ROOT,build
from region_expansion import sha
from verify_metric_world import verify


class RegionalWorldTests(unittest.TestCase):
    @unittest.skipUnless((ROOT/'worlds/chicago-water-tower-64/Arnis World 1/level.dat').exists(),
                         'Local Minecraft metadata fixture required')
    def test_roofs_determine_envelope_lower_points_and_gaps_remain_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir();surfaces=root/'surfaces';surfaces.mkdir()
            points=root/'points';points.mkdir();world=root/'world'
            meta=chart(0,0,16);grid={key:meta[key] for key in ('crs','west','north','size')}
            ground=np.full((16,16),100,np.float32);valid=np.ones((16,16),bool);valid[2,2]=False
            np.savez_compressed(source/'rasters.npz',elevation=ground,cover=np.zeros((16,16),np.uint8))
            with rasterio.open(source/'elevation.tif','w',driver='GTiff',width=16,height=16,count=1,
                dtype='float32',crs=meta['crs'],transform=Affine(1,0,meta['west'],0,-1,meta['north'])) as raster:raster.write(ground,1)
            inverse=Transformer.from_crs(meta['crs'],4326,always_xy=True)
            coordinates=[inverse.transform(meta['west']+x,meta['north']-z) for x,z in ((2,2),(8,2),(8,8),(2,8),(2,2))]
            (source/'osm-ways.json').write_text(json.dumps([{'id':1,'tags':{'building':'yes','height':'5'},
                                                         'coordinates':coordinates,'closed':True}]))
            (source/'cook-buildings-2022.json').write_text('{"features":[]}')
            np.savez_compressed(surfaces/'metric-surfaces.npz',dtm=ground,
                                dsm=np.where(valid,600,ground).astype(np.float32),valid=valid)
            surface={'schema':'earthcraft-regional-scan-surface-crop-v1','grid':grid,
                     'surfaces_sha256':sha(surfaces/'metric-surfaces.npz')}
            (surfaces/'probe.json').write_text(json.dumps(surface))
            np.savez_compressed(points/'points.npz',xyz=np.array([[meta['west']+4.5,meta['north']-4.5,110]]))
            classified={'schema':'earthcraft-classified-ept-building-crop-v1','grid':grid,
                        'points_sha256':sha(points/'points.npz')}
            (points/'manifest.json').write_text(json.dumps(classified))
            meta.update(buildings_available=True,building_source_kind='osm-explicit',elevation_raster='elevation.tif',
                        scan_surface_receipt=surface,classified_point_receipt=classified)
            (source/'sources.json').write_text(json.dumps(meta))
            build(source,world,surface_source=surfaces,point_source=points)
            checks=verify(world);report=json.loads((world/'earthcraft.json').read_text())
            tops=np.load(world/'top-heights.npy');offset=report['vertical_offset_m']
            self.assertEqual(tops[4,4],int(np.ceil(600+offset)-1))
            self.assertEqual(tops[2,2],int(np.ceil(105+offset)-1))
            self.assertGreaterEqual(report['dimension_height']-64,tops.max()+1)
            self.assertEqual(report['roof_scan_missing_cells'],1)
            self.assertEqual(checks['observed_3d_voxels'],1)
            self.assertTrue((world/'missing-scan-fallback-voxels.npy').exists())


if __name__=='__main__':unittest.main()
