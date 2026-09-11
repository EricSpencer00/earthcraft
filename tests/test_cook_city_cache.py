import gzip
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import cook_city_cache as cache


class CacheTests(unittest.TestCase):
    def test_original_deflate_rewrapped_losslessly_and_cache_tamper_rejected(self):
        original=b'LASF'+bytes(range(256))*20000
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as writer:
            writer.writestr('cook-las5/17759050.las',original)
            info=writer.getinfo('cook-las5/17759050.las')
        payload=buffer.getvalue()
        asset={'id':'17759050','member':info.filename,'url':cache.BASE+'cook-las5.zip',
            'archive_etag':'test-version','compressed_bytes':info.compress_size,'uncompressed_bytes':info.file_size,
            'crc32':f'{info.CRC:08x}','header_offset':info.header_offset,'compression':8}
        class Remote(io.BytesIO):
            def __init__(self,*args,**kwargs):
                super().__init__(payload);self.etag='test-version';self.transferred=0
            def read(self,n=-1):
                value=super().read(n);self.transferred+=len(value);return value
        with tempfile.TemporaryDirectory() as folder,patch.object(cache,'RangeReader',Remote), \
                patch.object(Path,'is_mount',return_value=True),patch.object(Path,'is_relative_to',return_value=True):
            root=Path(folder)
            path,record=cache.acquire(asset,{'capture_interval':['2022-04-05','2022-06-29']},root,reserve_bytes=0)
            self.assertEqual(gzip.decompress(path.read_bytes()),original)
            self.assertEqual(path.read_bytes()[10:-8],payload[30+len(info.filename):30+len(info.filename)+info.compress_size])
            self.assertEqual(record['bytes'],len(original))
            self.assertEqual(cache.acquire(asset,{},root,reserve_bytes=0),(path,record))
            with path.open('ab') as stream:stream.write(b'changed')
            with self.assertRaisesRegex(ValueError,'checksum'):cache.acquire(asset,{},root,reserve_bytes=0)

    def test_only_catalog_provider_and_bounded_deflate_members(self):
        with self.assertRaises(ValueError):cache.validate_asset({'id':'../../x'})
        with self.assertRaises(ValueError):cache.validate_asset({'id':'17759050','url':'https://other.invalid/file'})


if __name__=='__main__':unittest.main()
