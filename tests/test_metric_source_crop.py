import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from metric_source_crop import crop


class MetricSourceCropTests(unittest.TestCase):
    def test_vector_observations_are_limited_to_child(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);parent=root/'parent';parent.mkdir()
            profile={'driver':'GTiff','height':32,'width':32,'count':1,
                     'dtype':'float32','crs':'EPSG:3857','transform':from_origin(0,32,1,1)}
            with rasterio.open(parent/'usgs-elevation.tif','w',**profile) as target:
                target.write(np.ones((1,32,32),np.float32))
            np.savez_compressed(parent/'rasters.npz',elevation=np.ones((32,32),np.float32),
                                cover=np.full((32,32),10,np.uint8))
            near=[[0.00001,0.00001],[0.00002,0.00001],[0.00002,0.00002],[0.00001,0.00001]]
            far=[[1,1],[1.1,1],[1.1,1.1],[1,1]]
            (parent/'osm-ways.json').write_text(json.dumps([
                {'id':1,'tags':{},'coordinates':near,'closed':True},
                {'id':2,'tags':{},'coordinates':far,'closed':True}]))
            def feature(identifier,ring):
                return {'attributes':{'OBJECTID':identifier},'geometry':{'rings':[ring]}}
            (parent/'cook-buildings-2022.json').write_text(json.dumps(
                {'features':[feature(1,near),feature(2,far)]}))
            (parent/'sources.json').write_text(json.dumps({
                'crs':'EPSG:3857','west':0,'north':32,'size':32,
                'elevation_sha256':'old','osm_subset_sha256':'old','county_sha256':'old'}))
            child=root/'child';crop(parent,child,0,16,16)
            ways=json.loads((child/'osm-ways.json').read_text())
            county=json.loads((child/'cook-buildings-2022.json').read_text())
            self.assertEqual([way['id'] for way in ways],[1])
            self.assertEqual([f['attributes']['OBJECTID'] for f in county['features']],[1])
            manifest=json.loads((child/'sources.json').read_text())
            self.assertEqual(manifest['derived_crop']['county_feature_count'],1)


if __name__=='__main__':unittest.main()
