import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import laspy
import numpy as np
from pyproj import Transformer
from shapely.geometry import box,mapping

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from city_point_crop import PointCache, crop_sources


class CityPointCropTests(unittest.TestCase):
    def test_temporary_storage_codec_changes_no_observation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);west,north=450000,4640000
            meta={'crs':'EPSG:26916','west':west,'north':north,'size':16}
            xx=np.array([-32,-32,48,48,1,8,15],float)+west
            yy=np.array([32,-48,32,-48,-1,-8,-15],float)+north
            x,y=Transformer.from_crs(meta['crs'],6455,always_xy=True).transform(xx,yy)
            header=laspy.LasHeader(point_format=6,version='1.4')
            header.scales=[.001,.001,.001];header.offsets=[min(x),min(y),0]
            las=laspy.LasData(header);las.x=x;las.y=y;las.z=np.arange(7)+600
            las.classification=np.array([2,2,2,2,6,11,2],np.uint8)
            las.gps_time=np.arange(7)+1000000.;las.intensity=np.arange(7)*3
            raw=root/'scan.las';las.write(raw)
            with raw.open('rb') as source,gzip.open(root/'scan.las.gz','wb') as target:target.write(source.read())
            reread=laspy.read(raw)
            geometry=mapping(box(*reread.header.mins[:2],*reread.header.maxs[:2]))
            asset={'id':'00000001','native_geometry':geometry}
            for mode,path in ((True,raw),(False,root/'scan.las.gz')):
                crop_sources([(path,{'url':'frozen-test-source'},asset)],meta,root/str(mode),compress_working=mode)
            with np.load(root/'True/points.npz') as a,np.load(root/'False/points.npz') as b:
                self.assertEqual(a.files,b.files)
                self.assertEqual(len(a['xyz']),3)
                for key in a.files:
                    self.assertEqual(a[key].dtype,b[key].dtype)
                    np.testing.assert_array_equal(a[key],b[key])
            for mode,codec in ((True,zipfile.ZIP_DEFLATED),(False,zipfile.ZIP_STORED)):
                with zipfile.ZipFile(root/str(mode)/'points.npz') as z:
                    self.assertTrue(all(i.compress_type==codec for i in z.infolist()))
                report=json.loads((root/str(mode)/'manifest.json').read_text())
                self.assertFalse(report['inference_used']);self.assertTrue(report['original_preserved'])

    def test_decoded_member_cache_reuses_exact_observations(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);west,north=450000,4640000
            meta={'crs':'EPSG:26916','west':west,'north':north,'size':16}
            xx=np.array([-32,1,48],float)+west; yy=np.array([48,-1,-48],float)+north
            x,y=Transformer.from_crs(meta['crs'],6455,always_xy=True).transform(xx,yy)
            header=laspy.LasHeader(point_format=6,version='1.4')
            header.scales=[.001,.001,.001];header.offsets=[min(x),min(y),0]
            las=laspy.LasData(header);las.x=x;las.y=y;las.z=np.arange(3)+600
            las.classification=np.array([2,6,11],np.uint8)
            las.gps_time=np.arange(3)+1000000.;las.intensity=np.arange(3)*3
            raw=root/'scan.las';las.write(raw)
            reread=laspy.read(raw)
            geometry=mapping(box(*reread.header.mins[:2],*reread.header.maxs[:2]))
            asset={'id':'00000001','native_geometry':geometry}
            cache=PointCache(max_bytes=2**20)
            crop_sources([(raw,{'url':'frozen-test-source'},asset)],meta,root/'first',point_cache=cache)
            crop_sources([(raw,{'url':'frozen-test-source'},asset)],meta,root/'second',point_cache=cache)
            self.assertEqual(cache.misses,1);self.assertEqual(cache.hits,1)
            with np.load(root/'first/points.npz') as first,np.load(root/'second/points.npz') as second:
                for key in first.files: np.testing.assert_array_equal(first[key],second[key])


if __name__=='__main__':unittest.main()
