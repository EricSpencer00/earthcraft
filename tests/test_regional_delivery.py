import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
import zlib

import nbtlib as n
import numpy as np

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from metric_world import packed
from regional_install import geometry_signature,quality_merge,baseline_is_extrusion,install,encode,records
from region_expansion import sha
from regional_publish_unpack import unpack
from regional_store import compact,materialized,region_bytes,region_hashes
import tarfile


def chunk(palette,values,version=1,entity=False,biome='minecraft:plains'):
    entries=[]
    for name in palette:
        entry=n.Compound({'Name':n.String(name)})
        if version>1 and name=='minecraft:grass_block':entry['Properties']=n.Compound({'snowy':n.String('false')})
        entries.append(entry)
    state=n.Compound({'palette':n.List[n.Compound](entries)})
    if len(palette)>1:state['data']=packed(np.array(values),4)
    tag=n.File({'DataVersion':n.Int(version),'LastUpdate':n.Long(version*100),
        'sections':n.List[n.Compound]([n.Compound({'Y':n.Byte(5),'block_states':state,
            'biomes':n.Compound({'palette':n.List[n.String]([n.String(biome)])})})]),
        'block_entities':n.List[n.Compound]([n.Compound({'id':n.String('minecraft:chest')})] if entity else [])})
    stream=io.BytesIO();tag.write(stream);raw=zlib.compress(stream.getvalue())
    return ((len(raw)+1).to_bytes(4,'big')+bytes([2])+raw,version.to_bytes(4,'big'))


class RegionalDeliveryTests(unittest.TestCase):
    def test_fresh_merge_replica_adds_region_and_expands_border(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);world=root/'current';world.mkdir();candidate=root/'candidate';candidate.mkdir()
            (world/'session.lock').write_bytes(bytes(3));frame={'crs':'fixture','west':0,'north':0}
            (world/'city-coverage.json').write_text(json.dumps({'frame':frame,'tiles':{}}))
            n.File({'Data':n.Compound({'PlayerMarker':n.String('keep')})},gzipped=True).save(world/'level.dat')
            (candidate/'region').mkdir();incoming={0:chunk(['minecraft:stone'],[])}
            (candidate/'region/r.0.0.mca').write_bytes(encode(incoming))
            (candidate/'earthcraft.json').write_text(json.dumps({'world_frame':frame,
                'world_offset_xz':[0,0],'source':{'size':16}}))
            receipt=root/'receipt.json';receipt.write_text(json.dumps({'result':'pass','checks':{'verified':True},
                'tile':'0_0','quality':'LQ','world_manifest_sha256':sha(candidate/'earthcraft.json'),
                'regions':{'r.0.0.mca':sha(candidate/'region/r.0.0.mca')}}))
            self.assertFalse((world/'region').exists())
            result=install(world,candidate,receipt,root/'backups')
            self.assertEqual(result['added_chunks'],1)
            self.assertEqual(records((world/'region/r.0.0.mca').read_bytes()),incoming)
            level=n.load(world/'level.dat')['Data'];self.assertEqual(str(level['PlayerMarker']),'keep')
            self.assertEqual(float(level['BorderSize']),4112)
            self.assertTrue((world/'data/world_border.dat').exists())

    def test_vanilla_resave_is_eligible_but_edits_and_biomes_are_preserved(self):
        original=chunk(['minecraft:grass_block','minecraft:air'],[0]+[1]*4095)
        resaved=chunk(['minecraft:air','minecraft:grass_block'],[1]+[0]*4095,2)
        edited=chunk(['minecraft:air','minecraft:grass_block'],[1,1]+[0]*4094,2)
        different_biome=chunk(['minecraft:air','minecraft:grass_block'],[1]+[0]*4095,2,biome='minecraft:desert')
        entity=chunk(['minecraft:air','minecraft:grass_block'],[1]+[0]*4095,2,entity=True)
        self.assertEqual(geometry_signature(original),geometry_signature(resaved))
        self.assertNotEqual(geometry_signature(original),geometry_signature(edited))
        self.assertNotEqual(geometry_signature(original),geometry_signature(different_biome))
        self.assertIsNone(geometry_signature(entity))
        candidate={i:original for i in range(4)}
        merged,counts=quality_merge({0:resaved,1:edited,2:different_biome,3:entity},candidate,
            {i:[original] for i in range(4)},compare_geometry=True)
        self.assertEqual(counts['upgraded_chunks'],1)
        for slot,value in {1:edited,2:different_biome,3:entity}.items():self.assertEqual(merged[slot],value)

    def test_measured_models_are_never_extrusion_baselines(self):
        prior={'source':{'county_source':'Cook GIS','county_sha256':'known'}}
        self.assertTrue(baseline_is_extrusion(prior))
        self.assertFalse(baseline_is_extrusion(dict(prior,point_geometry_source={'verified':True})))
        self.assertFalse(baseline_is_extrusion(dict(prior,lidar_surface_source={'verified':True})))

    def test_region_storage_preserves_original_bytes_and_receipt_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            world=Path(directory);(world/'region').mkdir();p=world/'region/r.0.0.mca'
            raw=bytes(8192)+b'observed chunk'+bytes(20000);p.write_bytes(raw)
            expected=region_hashes(world);compact(world)
            self.assertFalse(p.exists());self.assertEqual(region_bytes(p),raw)
            self.assertEqual(region_hashes(world),expected)
            with materialized(world):self.assertEqual(p.read_bytes(),raw)
            self.assertFalse(p.exists())
            p.write_bytes(raw);p.with_suffix('.mca.gz').write_bytes(gzip.compress(b'changed'))
            with self.assertRaises(ValueError):compact(world)
            with self.assertRaises(ValueError):compact(world, expected={p.name:'wrong'})
            self.assertTrue(p.exists())
            compact(world, expected=expected)
            self.assertEqual(region_bytes(p), raw)
            retained=list(p.parent.glob('*.retained-*'))
            self.assertEqual(len(retained),1)
            self.assertEqual(gzip.decompress(retained[0].read_bytes()),b'changed')
            self.assertFalse(p.exists())

    def test_export_unpack_rejects_path_traversal_and_changed_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bundle=root/'bundle.tar';output=root/'output'
            def write(name,raw):
                with tarfile.open(bundle,'w') as archive:
                    info=tarfile.TarInfo(name);info.size=len(raw);archive.addfile(info,io.BytesIO(raw))
            write('../outside',b'bad')
            with self.assertRaises(ValueError):unpack(bundle,output)
            write('index.json',b'[]');self.assertEqual(unpack(bundle,output),[])
            write('index.json',b'[1]')
            with self.assertRaises(ValueError):unpack(bundle,output)


if __name__=='__main__':unittest.main()
