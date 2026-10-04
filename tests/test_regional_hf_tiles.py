import gzip
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import nbtlib as n
from region_expansion import encode, sha
from regional_hf_tiles import export
from upload_generated_tiles import validate
from upload_generated_tiles import upload


class GeneratedArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(); self.control = self.root/'control'; self.control.mkdir()
        self.bulk = self.root/'bulk'; self.world = self.bulk/'tiles/-1_0/world'; (self.world/'region').mkdir(parents=True)
        self.frame = {'west': -32, 'north': 33, 'vertical_offset_m': -116}
        (self.control/'plan.json').write_text(json.dumps({'frame': self.frame}))
        self.meta = {'world_frame': self.frame, 'world_offset_xz': [-512,0],
                     'source': {'size':512,'west':-544,'north':33,'private_field':'NEVER_UPLOAD'},
                     'player': 'NEVER_UPLOAD'}
        (self.world/'earthcraft.json').write_text(json.dumps(self.meta))
        self.tag = n.File({'xPos':n.Int(-32),'zPos':n.Int(0),'entities':n.List[n.Compound]([]),
                          'block_entities':n.List[n.Compound]([]),'sections':n.List[n.Compound]([])})
        self.evidence = self.world.parent/'geometry-receipt.json'
        self.write_region()
        db = sqlite3.connect(self.control/'jobs.sqlite')
        db.execute('CREATE TABLE jobs(tile,stage,evidence,evidence_sha256,priority,state)')
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',('-1_0',1,str(self.evidence),sha(self.evidence),0,'complete'))
        db.commit();db.close()

    def write_region(self):
        data = io.BytesIO(); self.tag.write(data); compressed = zlib.compress(data.getvalue())
        raw = encode({0:((len(compressed)+1).to_bytes(4,'big')+b'\x02'+compressed,b'\0'*4)})
        path = self.world/'region/r.-1.0.mca'; path.write_bytes(raw)
        self.evidence.write_text(json.dumps({'tile':'-1_0','quality':'LQ','result':'pass','world':str(self.world),
             'world_manifest_sha256':sha(self.world/'earthcraft.json'),'regions':{path.name:sha(path)}}))

    def refresh_receipt(self):
        with sqlite3.connect(self.control/'jobs.sqlite') as db:
            db.execute('UPDATE jobs SET evidence_sha256=?',(sha(self.evidence),))

    def pack(self):
        return export(self.control,self.bulk,self.root/'out',{},8)

    def test_verified_negative_coordinate_export_excludes_personal_metadata(self):
        result = self.pack(); archive = Path(result['archive'])
        manifest = validate(archive,result['sha256'],sha(self.control/'plan.json'))
        self.assertEqual(manifest['entries'][0]['world_offset_xz'],[-512,0])
        self.assertNotIn(b'NEVER_UPLOAD',archive.read_bytes())
        self.assertNotIn(str(self.root),json.dumps(manifest))
        self.assertEqual(manifest['entries'][0]['real_facade_photo_colors_applied'],0)
        with tarfile.open(archive) as tar:
            decoded = gzip.decompress(tar.extractfile('-1_0/r.-1.0.mca.gz').read())
        self.assertEqual(decoded,(self.world/'region/r.-1.0.mca').read_bytes())

    def test_checkpoint_skips_completed_tile(self):
        result = self.pack()
        self.assertEqual(export(self.control,self.bulk,self.root/'out',{'-1_0':sha(self.evidence)},8)['state'],'caught_up')
        self.assertEqual(self.pack()['sha256'],result['sha256'])

    def test_memory_transport_validates_bytes_and_rejects_corruption(self):
        result = self.pack()
        raw = Path(result['archive']).read_bytes()
        manifest = validate(io.BytesIO(raw), result['sha256'], sha(self.control/'plan.json'))
        self.assertEqual(manifest['entries'][0]['world_offset_xz'], [-512, 0])
        changed = io.BytesIO(bytes([raw[0] ^ 1]) + raw[1:])
        with self.assertRaisesRegex(ValueError, 'checksum'):
            validate(changed, result['sha256'], sha(self.control/'plan.json'))

    def test_entities_prevent_export(self):
        self.tag['entities'] = n.List[n.Compound]([n.Compound({'id':n.String('minecraft:player')})])
        self.write_region(); self.refresh_receipt()
        with self.assertRaisesRegex(ValueError,'Entity'): self.pack()

    def test_block_entities_prevent_export(self):
        self.tag['block_entities'] = n.List[n.Compound]([n.Compound({'id':n.String('minecraft:sign'),'text':n.String('private')})])
        self.write_region();self.refresh_receipt()
        with self.assertRaisesRegex(ValueError,'Entity'): self.pack()

    def test_changed_receipt_refuses_export(self):
        self.evidence.write_text('{}')
        with self.assertRaisesRegex(ValueError,'receipt changed'): self.pack()

    def test_changed_frame_refuses_export(self):
        (self.control/'plan.json').write_text(json.dumps({'frame':{'west':1}}))
        with self.assertRaisesRegex(ValueError,'fixed-frame'):self.pack()

    def test_original_save_path_refuses_export(self):
        receipt=json.loads(self.evidence.read_text());receipt['world']=str(self.root/'personal-save')
        self.evidence.write_text(json.dumps(receipt));self.refresh_receipt()
        with self.assertRaisesRegex(ValueError,'personal save'):self.pack()

    def test_chunk_position_mismatch_refuses_export(self):
        self.tag['xPos']=n.Int(0);self.write_region();self.refresh_receipt()
        with self.assertRaisesRegex(ValueError,'coordinates'):self.pack()

    def test_verified_cloud_generator_subdirectory_is_allowed(self):
        import shutil
        cloud=self.world.parent/'cloud/aws-scan-123/world';cloud.parent.mkdir(parents=True)
        shutil.copytree(self.world,cloud)
        receipt=json.loads(self.evidence.read_text());receipt['world']=str(cloud)
        self.evidence.write_text(json.dumps(receipt));self.refresh_receipt()
        self.assertEqual(self.pack()['tiles'],1)

    def test_added_file_refuses_upload(self):
        result=self.pack(); archive=Path(result['archive'])
        with tarfile.open(archive,'a') as tar:
            item=tarfile.TarInfo('playerdata/private.dat');item.size=1;tar.addfile(item,io.BytesIO(b'x'))
        with self.assertRaisesRegex(ValueError,'Unlisted'):validate(archive,sha(archive),sha(self.control/'plan.json'))

    def test_concurrent_remote_index_change_is_preserved(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        result=self.pack(); archive=Path(result['archive'])
        remote='generated-tiles/v1/shards/'+result['sha256'][:2]+'/'+result['sha256']+'.tar'
        entry=SimpleNamespace(rfilename=remote,lfs={'sha256':result['sha256']})
        api=Mock();api.whoami.return_value={'name':'EricSpencer00'}
        api.repo_info.side_effect=[
            SimpleNamespace(id='EricSpencer00/earthcraft-minecraft-world',private=True,sha='a',siblings=[]),
            SimpleNamespace(id='EricSpencer00/earthcraft-minecraft-world',private=True,sha='b',siblings=[entry]),
            SimpleNamespace(id='EricSpencer00/earthcraft-minecraft-world',private=True,sha='c',siblings=[entry])]
        module=SimpleNamespace(HfApi=lambda:api,hf_hub_download=Mock())
        with patch.dict(sys.modules,{'huggingface_hub':module}):
            with self.assertRaisesRegex(ValueError,'Remote index changed'):
                upload(archive,result['sha256'],sha(self.control/'plan.json'),
                       'EricSpencer00/earthcraft-minecraft-world',self.root/'proof.json')
        self.assertEqual(api.upload_file.call_count,1)
        self.assertEqual(api.upload_file.call_args.kwargs['path_in_repo'],remote)


if __name__ == '__main__':unittest.main()
