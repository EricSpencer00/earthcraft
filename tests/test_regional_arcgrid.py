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
from regional_arcgrid import grid_layout,tile_members,publisher_reference,original_member,BufferedRangeReader,fetch_member
from http_zip_range import RangeReader


class RegionalArcGridTests(unittest.TestCase):
    def test_buffered_ranges_preserve_zip_members_across_seeks(self):
        source=io.BytesIO()
        raw=bytes(range(256))*60000
        with zipfile.ZipFile(source,'w',zipfile.ZIP_STORED) as archive:
            archive.writestr('first.adf',raw);archive.writestr('second.adf',raw[::-1])
        original=source.getvalue();reader=BufferedRangeReader.__new__(BufferedRangeReader)
        reader.length=len(original);reader.position=0;calls=[]
        def fetch(current,size=-1):
            if size<0:size=current.length-current.position
            calls.append((current.position,size));result=original[current.position:current.position+size]
            current.position+=len(result);return result
        with patch.object(RangeReader,'read',fetch),zipfile.ZipFile(reader) as archive:
            with archive.open('second.adf') as member:
                self.assertEqual(member.read(2**20+117)+member.read(),raw[::-1])
            self.assertEqual(archive.read('first.adf'),raw)
        self.assertLess(len(calls),16)
        self.assertLessEqual(sum(size for _,size in calls),len(original)+2*2**20)

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

    @staticmethod
    def archive(root,raw,compression=zipfile.ZIP_DEFLATED):
        archive_path=root/'original.zip'
        with zipfile.ZipFile(archive_path,'w',compression) as archive:
            archive.writestr('grid/hdr.adf',b'header');archive.writestr('grid/w001001.adf',raw)
        with zipfile.ZipFile(archive_path) as archive:
            info=archive.getinfo('grid/w001001.adf')
            member={'name':info.filename,'bytes':info.file_size,'compressed_bytes':info.compress_size,
                    'crc32':info.CRC,'offset':info.header_offset,'compression':info.compress_type}
        data=archive_path.read_bytes()
        layer={'members':{'w001001.adf':member},'cache':str(root/'cache'),
               'archive_url':'https://publisher.example/original.zip','archive_etag':'verified-original',
               'archive_bytes':len(data)}
        calls=[]
        def get_range(url,start,end,etag,length,**_):
            assert etag=='verified-original' and length==len(data) and end-start<16*2**20
            calls.append((start,end));return data[start:end+1]
        return layer,member,get_range,calls

    def test_original_member_version_crc_and_cache_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);raw=b'original scan grid bytes'*500
            layer,_,get_range,_=self.archive(root,raw)
            with patch('source_http.get_range',get_range):
                path,receipt=original_member(layer,'w001001.adf',reserve_bytes=0)
                self.assertEqual(path.read_bytes(),raw);self.assertTrue(receipt['zip_crc32_verified'])
                path.write_bytes(raw[:-1]+b'!')
                with self.assertRaisesRegex(ValueError,'bytes changed'):original_member(layer,'w001001.adf',reserve_bytes=0)
                (root/'cache'/'w001001.adf.receipt.json').unlink()  # half-evicted unit: fetched again
                path,_=original_member(layer,'w001001.adf',reserve_bytes=0)
                self.assertEqual(path.read_bytes(),raw)

    def test_parallel_ranges_are_consumed_in_order_and_crc_checked(self):
        import random
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);raw=random.Random(7).randbytes(300_000)*3
            for compression in (zipfile.ZIP_DEFLATED,zipfile.ZIP_STORED):
                layer,member,get_range,calls=self.archive(root,raw,compression)
                with patch('source_http.get_range',get_range):
                    output=io.BytesIO()
                    digest,count,_=fetch_member(layer,member,output,chunk=4096,connections=3,log=lambda **_:None)
                    self.assertEqual(output.getvalue(),raw);self.assertEqual(count,len(raw))
                    self.assertGreater(len(calls),20)
                    bad=dict(member,crc32=member['crc32']^1)
                    with self.assertRaisesRegex(ValueError,'CRC'):
                        fetch_member(layer,bad,io.BytesIO(),chunk=65536,connections=2,log=lambda **_:None)
                    moved=dict(member,offset=member['offset']+1)
                    with self.assertRaisesRegex(ValueError,'local header'):
                        fetch_member(layer,moved,io.BytesIO(),log=lambda **_:None)


if __name__=='__main__':unittest.main()
