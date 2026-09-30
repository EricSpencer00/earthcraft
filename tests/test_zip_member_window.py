import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import laspy
import numpy as np
from pyproj import CRS,Transformer
from shapely.geometry import box,mapping

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cook_city_cache as cache
from regional_will_points import validate_asset,URL,crop
from regional_scans import SURVEY_FOOT
from zip_member_window import ZipMemberWindow,member_identity


class NestedZipTests(unittest.TestCase):
    def fixture(self,compression=zipfile.ZIP_STORED):
        original=b'LASF'+bytes(range(256))*12000
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('las/0001.las',original);info=archive.getinfo('las/0001.las')
        outer=io.BytesIO()
        with zipfile.ZipFile(outer,'w',compression) as archive:
            archive.writestr('unrelated-before',b'prefix'*10000)
            archive.writestr('will-las.zip',inner.getvalue())
            archive.writestr('unrelated-after',b'suffix'*10000)
            identity=member_identity(archive.getinfo('will-las.zip'))
        class Remote(io.BytesIO):
            def __init__(self):
                super().__init__(outer.getvalue());self.length=len(outer.getvalue())
                self.etag='original-outer-version';self.transferred=0;self.ranges=[]
            def read(self,n=-1):
                start=self.tell();raw=super().read(n);self.transferred+=len(raw);self.ranges.append((start,len(raw)))
                return raw
        return original,info,identity,Remote

    def test_nested_member_keeps_original_deflate_and_las_bytes(self):
        original,info,identity,Remote=self.fixture()
        asset={'id':'0001','member':info.filename,'url':URL,'archive_etag':'original-outer-version',
            'compressed_bytes':info.compress_size,'uncompressed_bytes':info.file_size,
            'crc32':f'{info.CRC:08x}','header_offset':info.header_offset,'compression':8}
        validate_asset(asset)
        readers=[]
        def factory(url,budget):
            self.assertEqual(url,URL);reader=Remote();readers.append(reader)
            return ZipMemberWindow(reader,'will-las.zip',identity)
        with tempfile.TemporaryDirectory() as folder,patch.object(cache,'bulk_root',return_value=Path(folder)):
            path,receipt=cache.acquire_validated_deflate(asset,{'capture_interval':['2021'],'license':'Publisher terms'},
                Path(folder),reserve_bytes=0,reader_factory=factory)
            self.assertEqual(gzip.decompress(path.read_bytes()),original)
            self.assertEqual(receipt['license'],'Publisher terms')
            with zipfile.ZipFile(factory(URL,0)) as inner:
                self.assertEqual(inner.read(info.filename),original)
            self.assertLess(readers[0].transferred,len(readers[0].getvalue()))

    def test_window_rejects_changed_identity_compression_and_outside_seeks(self):
        _,_,identity,Remote=self.fixture()
        with self.assertRaisesRegex(ValueError,'changed'):
            ZipMemberWindow(Remote(),'will-las.zip',dict(identity,crc32=identity['crc32']^1))
        window=ZipMemberWindow(Remote(),'will-las.zip',identity)
        for offset,whence in [(-1,0),(1,2),(0,3)]:
            with self.assertRaises(ValueError):window.seek(offset,whence)
        window.seek(-7,2);self.assertEqual(len(window.read(100)),7);self.assertEqual(window.read(1),b'')
        _,_,_,Compressed=self.fixture(zipfile.ZIP_DEFLATED)
        with self.assertRaisesRegex(ValueError,'stored'):ZipMemberWindow(Compressed(),'will-las.zip')

    def test_original_will_accepts_only_published_member_identity(self):
        valid={'url':URL,'id':'0001','member':'las/0001.las','compression':8,
               'compressed_bytes':10,'uncompressed_bytes':100,'crc32':'deadbeef'}
        validate_asset(valid)
        for changed in [dict(url='https://other.example/data.zip'),dict(id='../1'),
                        dict(member='other/0001.las'),dict(compression=0)]:
            with self.assertRaises(ValueError):validate_asset(dict(valid,**changed))

    def test_original_classified_crop_excludes_withheld_and_converts_z(self):
        header=laspy.LasHeader(version='1.4',point_format=6)
        header.scales=np.array([.001,.001,.001]);header.add_crs(CRS.from_epsg(6455),keep_compatibility=False)
        las=laspy.LasData(header)
        las.x=[1000000,1000005,1000010,1000015];las.y=[1800000,1799995,1799990,1799985]
        las.z=[650,655,640,660];las.classification=[6,6,2,6];las.withheld=[0,1,0,0]
        raw=io.BytesIO();las.write(raw);original=raw.getvalue();compressed=gzip.compress(original)
        metric=Transformer.from_crs(6455,26916,always_xy=True)
        x,y=metric.transform(np.asarray(las.x),np.asarray(las.y))
        grid={'crs':'EPSG:26916','west':float(np.floor(min(x))-1),'north':float(np.ceil(max(y))+1),'size':32}
        source={'etag':'version'}
        publisher={'native_crs':'EPSG:6455','metres_per_vertical_unit':SURVEY_FOOT,
                   'vertical_reference':'NAVD88 survey feet','nested_archive':source}
        asset={'id':'0001','member':'las/0001.las','url':URL,'compression':8,'crc32':'deadbeef',
               'compressed_bytes':len(compressed),'uncompressed_bytes':len(original),
               'native_geometry':mapping(box(1000000,1799985,1000015,1800000))}
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);path=root/'source.las.gz';path.write_bytes(compressed)
            catalog=root/'catalog.json';catalog.write_text(json.dumps({'publisher':publisher,'nested_archive':source,
                'assets':[asset],'unindexed_original_members':[]}))
            receipt={'publisher':publisher,'sha256':hashlib.sha256(original).hexdigest(),
                     'compressed_sha256':hashlib.sha256(compressed).hexdigest(),'bytes':len(original),
                     'url':URL,'member':asset['member']}
            with patch('regional_will_points.acquire_validated_deflate',return_value=(path,receipt)):
                result=crop(catalog,grid,root/'crop',root/'cache')
            self.assertEqual(result['building_points'],2)
            with np.load(root/'crop/points.npz') as points:
                np.testing.assert_allclose(points['xyz'],np.column_stack((x[[0,3]],y[[0,3]],np.array([650,660])*SURVEY_FOOT)))


if __name__=='__main__':unittest.main()
