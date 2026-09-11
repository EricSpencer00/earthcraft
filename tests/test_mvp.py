import copy, io, sys, tempfile, unittest, zlib
from pathlib import Path
import nbtlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from inspect_world import chunks, compact_region
from tower_overlay import apply

class MVPTests(unittest.TestCase):
    def test_overlay_preserves_source_and_unrelated_feature(self):
        source={'elements':[{'type':'way','id':130147025,'tags':{'building':'water_tower','height':'55.4'}},
                            {'type':'way','id':1283005804,'nodes':[1,2,3,1],'tags':{'building:part':'yes','building:material':'stone'}},
                            {'type':'way','id':9,'tags':{'building':'retail'}},
                            {'type':'relation','id':17594944,'members':[{'ref':130147025,'role':''},{'ref':1283005804,'role':''}]}]}
        frozen=copy.deepcopy(source)
        result=apply(source)
        self.assertEqual(source,frozen)
        self.assertEqual(result['elements'][2],source['elements'][2])
        self.assertEqual(result['elements'][1]['nodes'],source['elements'][1]['nodes'])
        self.assertEqual([m['role'] for m in result['elements'][3]['members']],['outline','part'])
        self.assertEqual(apply(result),result)

    def test_conflicting_duplicate_observations_fail(self):
        with self.assertRaises(ValueError):
            apply({'elements':[{'type':'way','id':1,'tags':{'height':'10'}},
                               {'type':'way','id':1,'tags':{'height':'20'}}]})

    def test_compaction_keeps_boundary_chunks_and_exact_payload(self):
        header=bytearray(8192);body=bytearray()
        for index,(x,z) in enumerate([(0,0),(3,3),(4,0),(-1,0)]):
            tag=nbtlib.File({'xPos':nbtlib.Int(x),'zPos':nbtlib.Int(z),'marker':nbtlib.String(f'{x},{z}')})
            stream=io.BytesIO();tag.write(stream)
            data=zlib.compress(stream.getvalue())
            sector=(len(data)+1).to_bytes(4,'big')+b'\x02'+data
            sector+=b'\0'*((-len(sector))%4096)
            header[index*4:index*4+3]=(2+len(body)//4096).to_bytes(3,'big')
            header[index*4+3]=len(sector)//4096
            body.extend(sector)
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'source.mca';dst=Path(folder)/'copy.mca'
            src.write_bytes(header+body)
            self.assertEqual(compact_region(src,dst,64),2)
            original=list(chunks(src));output=list(chunks(dst))
            self.assertEqual([(int(t['xPos']),int(t['zPos'])) for _,t,_ in output],[(0,0),(3,3)])
            self.assertEqual(output[0][2],original[0][2])
            self.assertEqual(output[1][2],original[1][2])
            self.assertEqual(src.read_bytes(),header+body)

if __name__=='__main__':unittest.main()
