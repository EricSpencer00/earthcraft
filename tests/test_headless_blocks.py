"""Headless actual-NBT tests; geographic observations here are synthetic fixtures."""
import copy
import gzip
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zlib

import nbtlib as n
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from headless_blocks import (analyze,analyze_region,bounded_expand,digest,read_chunk,
                             region_records,unpack,validate_bundle)
from headless_site import build_site


def packed(values,bits):
    per=64//bits;words=np.zeros((len(values)+per-1)//per,dtype=np.uint64)
    for index,value in enumerate(values):words[index//per]|=np.uint64(value)<<np.uint64(index%per*bits)
    return n.LongArray(words.view(np.int64))


def chunk(slot=0):
    return n.File({'xPos':n.Int(slot%32),'zPos':n.Int(slot//32),
        'entities':n.List[n.Compound]([]),'block_entities':n.List[n.Compound]([]),
        'sections':n.List[n.Compound]([n.Compound({'Y':n.Byte(-4),'block_states':n.Compound({
            'palette':n.List[n.Compound]([n.Compound({'Name':n.String('minecraft:stone_bricks')})])})})]),
        'Heightmaps':n.Compound({'WORLD_SURFACE':packed([16]*256,7)})})


def encoded(tag):
    out=io.BytesIO();tag.write(out);return b'\x02'+zlib.compress(out.getvalue())


def region():
    header=bytearray(8192);bodies=[]
    for slot in range(1024):
        record=encoded(chunk(slot));require_len=len(record)+4
        assert require_len<=4096
        header[slot*4:slot*4+3]=(slot+2).to_bytes(3,'big');header[slot*4+3]=1
        body=len(record).to_bytes(4,'big')+record;bodies.append(body+bytes(4096-len(body)))
    return bytes(header)+b''.join(bodies)


class HeadlessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=region();cls.frame={'crs':'EPSG:32616','west':0,'north':512,'vertical_offset_m':0,
                                  'dimension_min_y':-64,'dimension_height':64,
                                  'vertical_reference':'Unverified fixture reference'}
        cls.entry={'tile':'0_0','quality':'LQ','world_offset_xz':[0,0],'size_m':512,'metres_per_block':1,
           'receipt_sha256':'a'*64,'region_sha256':digest(cls.raw),'region_file':'r.0.0.mca','chunks':1024,
           'source_grid':{'west':0,'north':512,'size':512},'scan':{},'roof_source_cells':0,
           'real_facade_photo_colors_applied':0,'client_visual_verified':False,'privacy_audit':'fixture'}

    def test_padded_palette_words_decode_across_word_boundaries(self):
        values=np.arange(4096)%17
        np.testing.assert_array_equal(unpack(packed(values,5),5,4096),values)
        with self.assertRaisesRegex(ValueError,'length'):unpack([],5,4096)

    def test_actual_complete_blocks_and_native_heightmaps_are_counted(self):
        report,top,names,records=analyze_region(self.raw,self.entry,self.frame)
        self.assertEqual(report['actual_chunks'],1024)
        self.assertEqual(report['actual_non_air_blocks'],512*512*16)
        self.assertEqual(report['heightmap_columns_checked'],512*512)
        self.assertTrue(np.all(top==-49))
        self.assertEqual(report['occupied_columns'],512*512)

    def test_private_content_coordinates_and_heightmaps_are_rejected(self):
        tag=chunk();tag['entities']=n.List[n.Compound]([n.Compound({'id':n.String('minecraft:player')})])
        with self.assertRaisesRegex(ValueError,'Personal'):read_chunk(encoded(tag),0,'0_0',-64,64)
        tag=chunk();tag['xPos']=n.Int(99)
        with self.assertRaisesRegex(ValueError,'coordinates'):read_chunk(encoded(tag),0,'0_0',-64,64)
        changed=bytearray(self.raw);tag=chunk();tag['Heightmaps']['WORLD_SURFACE']=packed([15]*256,7)
        record=encoded(tag);body=len(record).to_bytes(4,'big')+record
        changed[8192:12288]=body+bytes(4096-len(body))
        with self.assertRaisesRegex(ValueError,'heightmap'):analyze_region(bytes(changed),self.entry,self.frame)

    def test_missing_chunks_and_overlapping_sectors_are_rejected(self):
        changed=bytearray(self.raw);changed[:4]=bytes(4)
        with self.assertRaisesRegex(ValueError,'1024'):analyze_region(bytes(changed),self.entry,self.frame)
        changed=bytearray(self.raw);changed[4:8]=changed[:4]
        with self.assertRaisesRegex(ValueError,'overlapping'):region_records(bytes(changed))

    def test_decompression_bomb_and_trailing_data_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'bound'):bounded_expand(zlib.compress(b'x'*1000),limit=100)
        with self.assertRaisesRegex(ValueError,'trailing'):bounded_expand(zlib.compress(b'hello')+b'junk')

    def test_headless_bundle_replays_and_rejects_changed_images_and_false_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);archive=root/'fixture.tar'
            manifest={'schema':'earthcraft.generated-tile-shard.v1','frame':self.frame,'plan_sha256':'b'*64,
                      'entries':[self.entry],'attribution':[{'source':'Synthetic test fixture','license':'CC0'}],
                      'excludes':[],'note':'Fixture, no production measurements'}
            with tarfile.open(archive,'w') as tar:
                for name,body in [('manifest.json',json.dumps(manifest).encode()),('0_0/r.0.0.mca.gz',gzip.compress(self.raw,mtime=0))]:
                    info=tarfile.TarInfo(name);info.size=len(body);tar.addfile(info,io.BytesIO(body))
            before=archive.read_bytes();expected=digest(before)
            first=analyze(archive,expected,root/'first','c'*40,'2026-10-10T00:00:00+00:00',32)
            second=analyze(archive,expected,root/'second','c'*40,'2026-10-10T00:00:00+00:00',32)
            self.assertEqual(first,second);self.assertEqual(archive.read_bytes(),before)
            self.assertFalse(first['minecraft_opened']);self.assertFalse(first['inference_used'])
            page=build_site(root/'second',root/'site')
            self.assertEqual(page['actual_non_air_blocks'],512*512*16)
            html=(root/'site/index.html').read_text()
            self.assertIn('4,194,304',html);self.assertIn('clipped 32-metre',html)
            self.assertIn('checks 1 existing archived tile;',html)
            self.assertIn('not a new generation or installation',html)
            self.assertEqual({p.name for p in (root/'site').iterdir()},
                             {'index.html','style.css','report.json','blocks.png','topdown.png'})
            unsafe=copy.deepcopy(second);unsafe['source']['attribution'][0]['url']='javascript:alert(1)'
            (root/'second/report.json').write_text(json.dumps(unsafe))
            with self.assertRaisesRegex(ValueError,'Unsafe attribution'):build_site(root/'second',root/'unsafe-site')
            (root/'second/report.json').write_text(json.dumps(second))
            (root/'first/blocks.png').write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError,'image hash'):validate_bundle(root/'first')
            report=copy.deepcopy(second);report['checks']['measured_color']='verified'
            (root/'second/report.json').write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'Unsupported'):validate_bundle(root/'second')

    def test_chunk_trailing_bytes_are_rejected(self):
        data=io.BytesIO();chunk().write(data)
        with self.assertRaisesRegex(ValueError,'trailing NBT'):
            read_chunk(b'\x02'+zlib.compress(data.getvalue()+b'private trailing bytes'),0,'0_0',-64,64)


if __name__=='__main__':unittest.main()
