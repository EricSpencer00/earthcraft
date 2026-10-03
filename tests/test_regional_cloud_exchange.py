import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from chicago_tiles import Journal
from regional_cloud_exchange import accept,extract,renew,prepare
from unittest.mock import patch
from region_expansion import sha


class CloudExchangeTests(unittest.TestCase):
    @patch('regional_cloud_exchange.require_mini_bulk')
    def test_cloud_input_freezes_footprint_crop_without_changing_base_source(self,_storage):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);control=root/'control';control.mkdir();bulk=root/'bulk'
            source=bulk/'tiles/0_0/sources';source.mkdir(parents=True)
            original=json.dumps({'crs':'EPSG:26916','west':0,'north':16,'size':16}).encode()
            (source/'sources.json').write_bytes(original)
            plan={'frame':{'crs':'EPSG:26916'},'tiles':[{'id':'0_0','priority':0}]}
            (control/'plan.json').write_text(json.dumps(plan))
            index=control/'regional-footprints.sqlite';index.write_bytes(b'frozen index')
            index.with_suffix('.json').write_text(json.dumps({'sha256':sha(index),'crs':plan['frame']['crs']}))
            journal=Journal(control/'jobs.sqlite',plan)
            for stage in (0,1):
                proof=control/(str(stage)+'.json');proof.write_text('{"result":"pass"}')
                journal.db.execute("UPDATE jobs SET state='complete',evidence=?,evidence_sha256=? WHERE stage=?",
                    (str(proof),sha(proof),stage))
            journal.close()
            def crop(index,grid,path):
                path.write_text(json.dumps({'grid':grid,'features':[]}))
                return {'count':0,'sha256':sha(path),'geometry_measured':False}
            output=control/'batch.tar.gz'
            with patch('regional_footprints.crop',side_effect=crop):prepare(control,bulk,output,limit=1)
            restored=root/'restored';extract(output,restored)
            request=json.loads((restored/'request.json').read_text());entry=request['jobs'][0]
            self.assertEqual(entry['footprints']['sha256'],sha(restored/'tiles/0_0/footprints/scan-footprints.geojson'))
            self.assertEqual((source/'sources.json').read_bytes(),original)
            self.assertEqual((restored/'tiles/0_0/sources/sources.json').read_bytes(),original)

    def test_missing_lacie_rejects_before_queue_claim_or_import_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);control=root/'control';bulk=root/'bulk'
            with patch('regional_cloud_exchange.Path.is_mount',return_value=False):
                with self.assertRaisesRegex(ValueError,'mounted LaCie'):
                    prepare(control,bulk,root/'batch.tar.gz')
                with self.assertRaisesRegex(ValueError,'mounted LaCie'):
                    accept(control,bulk,{},root/'batch.tar.gz','0_0')
            self.assertFalse(control.exists());self.assertFalse(bulk.exists())

    def test_heartbeat_cannot_revive_expired_or_reclaimed_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            control=Path(directory);plan={'tiles':[{'id':'0_0','priority':0}]}
            journal=Journal(control/'jobs.sqlite',plan);now=time.time()
            for stage in (0,1):
                proof=control/(str(stage)+'.json');proof.write_text(json.dumps({'result':'pass'}))
                journal.db.execute("UPDATE jobs SET state='complete',evidence=?,evidence_sha256=? WHERE stage=?",
                    (str(proof),sha(proof),stage))
            job=journal.claim('appearance','aws-test',lease_seconds=300)
            request={'owner':'aws-test','jobs':[{'job':job}]}
            self.assertEqual(renew(control,request)['renewed'],1)
            journal.db.execute('UPDATE jobs SET expires=? WHERE stage=2',(now-1,))
            self.assertEqual(renew(control,request)['renewed'],0)
            new=journal.claim('appearance','mini-new',lease_seconds=300)
            self.assertIsNotNone(new)
            self.assertEqual(renew(control,request)['renewed'],0)
            journal.close()

    def test_cloud_archive_rejects_escape_symlinks_and_size_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);archive=root/'input.tar'
            def write(name,size=3,link=False):
                with tarfile.open(archive,'w') as stream:
                    item=tarfile.TarInfo(name);item.size=size
                    if link:item.type=tarfile.SYMTYPE;item.linkname='../outside';stream.addfile(item)
                    else:stream.addfile(item,io.BytesIO(b'x'*size))
            for name,link in (('../outside',False),('/outside',False),('link',True)):
                write(name,link=link)
                with self.assertRaisesRegex(ValueError,'Unsafe'):extract(archive,root/'out')
            write('world/region/r.0.0.mca',size=10)
            with self.assertRaisesRegex(ValueError,'bounds'):extract(archive,root/'out',max_bytes=5)
            extract(archive,root/'out',max_bytes=10)
            self.assertEqual((root/'out/world/region/r.0.0.mca').read_bytes(),b'x'*10)
