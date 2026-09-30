import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from regional_arcgrid import grid_layout,tile_members,publisher_reference,original_member


class RegionalArcGridTests(unittest.TestCase):
    def test_original_layout_selects_files_across_native_boundaries(self):
        header=bytearray(308);header[:8]=b'GRID1.2\0'
        header[256:272]=struct.pack('>2d',1,1)
        header[288:308]=struct.pack('>5i',8,512,256,1,4)
        layout=grid_layout(header,struct.pack('>4d',0,0,8192,8192))
        self.assertEqual(tile_members(layout,(2000,6100,2100,6200)),
                         ['w001001.adf','w001001x.adf','w001000.adf','w001000x.adf',
                          'w002001.adf','w002001x.adf','w002000.adf','w002000x.adf'])
        self.assertEqual(tile_members(layout,(4096,100,4200,200)),['z003002.adf','z003002x.adf'])
        self.assertEqual(tile_members(layout,(9000,0,9010,10)),[])
        with self.assertRaises(ValueError):grid_layout(header[:-1],struct.pack('>4d',0,0,1,1))

    def test_vertical_reference_is_explicit_and_separate_from_horizontal(self):
        def xml(vcs):
            return ('<metadata><peXml>&lt;ProjectedCoordinateSystem&gt;'+
                '&lt;LatestWKID&gt;6455&lt;/LatestWKID&gt;'+
                f'&lt;LatestVCSWKID&gt;{vcs}&lt;/LatestVCSWKID&gt;'+
                '&lt;WKT&gt;PROJCS[Foot_US],VERTCS[NAVD88,Foot_US]&lt;/WKT&gt;'+
                '&lt;/ProjectedCoordinateSystem&gt;</peXml></metadata>').encode()
        self.assertAlmostEqual(publisher_reference(xml(6360))[2],1200/3937)
        with self.assertRaises(ValueError):publisher_reference(xml(0))

    def test_original_member_version_crc_and_cache_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive_path=root/'original.zip'
            raw=b'original scan grid bytes'*500
            with zipfile.ZipFile(archive_path,'w',zipfile.ZIP_DEFLATED) as archive:archive.writestr('grid/w001001.adf',raw)
            class Reader(io.BytesIO):
                def __init__(self,*args,**kwargs):
                    super().__init__(archive_path.read_bytes());self.length=len(self.getvalue());self.etag='verified-original'
            with zipfile.ZipFile(archive_path) as archive:
                info=archive.getinfo('grid/w001001.adf')
                member={'name':info.filename,'bytes':info.file_size,'compressed_bytes':info.compress_size,
                        'crc32':info.CRC,'offset':info.header_offset,'compression':info.compress_type}
            layer={'members':{'w001001.adf':member},'cache':str(root/'cache'),
                   'archive_url':'https://publisher.example/original.zip','archive_etag':'verified-original',
                   'archive_bytes':archive_path.stat().st_size}
            with patch('regional_arcgrid.RangeReader',Reader):
                path,receipt=original_member(layer,'w001001.adf',reserve_bytes=0)
                self.assertEqual(path.read_bytes(),raw);self.assertTrue(receipt['zip_crc32_verified'])
                path.write_bytes(raw[:-1]+b'!')
                with self.assertRaisesRegex(ValueError,'bytes changed'):original_member(layer,'w001001.adf',reserve_bytes=0)


if __name__=='__main__':unittest.main()
