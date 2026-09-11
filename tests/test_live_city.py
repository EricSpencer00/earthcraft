import copy
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
import nbtlib as n
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from live_city import encode_chunk, validate, publish, digest, allowed
from metric_world import packed


class LiveTests(unittest.TestCase):
    def patch(self):
        return dict(version=1,frame='fixture',cx=-2,cz=3,mode='new_chunk',
                    palette=['minecraft:stone'],runs=[[0,4,0]],cells=4)

    def test_run_bounds(self):
        for runs in ([[0,0,0]], [[0,4,0],[3,2,0]], [[262143,2,0]], [[-1,4,0]], [[0,4,2]]):
            p=self.patch();p['runs']=runs
            with self.assertRaises(ValueError):validate(p)

    def test_cell_count_and_modes(self):
        p=self.patch();p['cells']=3
        with self.assertRaises(ValueError):validate(p)
        p=self.patch();p['mode']='pavement'
        with self.assertRaises(ValueError):validate(p)

    def test_allowlist_no_code_or_entities(self):
        for name in ['minecraft:command_block','minecraft:chest','minecraft:lava','custom:stone','minecraft:white_concrete[bad]']:
            self.assertFalse(allowed(name,'new_chunk'))
        self.assertTrue(allowed('minecraft:light_gray_concrete','pavement'))
        self.assertTrue(allowed('minecraft:light_gray_stained_glass','new_chunk'))
        self.assertTrue(allowed('minecraft:iron_block','new_chunk'))
        self.assertFalse(allowed('minecraft:light_gray_stained_glass','pavement'))

    def test_native_negative_coordinates_and_cell_order(self):
        values=np.zeros(4096,int);values[[0,15,16,256,4095]]=1
        tag=n.Compound({'xPos':n.Int(-2),'zPos':n.Int(3),'sections':n.List[n.Compound]([
            n.Compound({'Y':n.Byte(-4),'block_states':n.Compound({'palette':n.List[n.Compound]([
                n.Compound({'Name':n.String('minecraft:air')}),n.Compound({'Name':n.String('minecraft:stone')})]),
                'data':packed(values,4)})})])})
        p=encode_chunk(tag,'fixture',{})
        result=np.zeros(262144,int)
        for start,count,code in p['runs']:result[start:start+count]=1
        np.testing.assert_array_equal(result[:4096],values)
        self.assertEqual(int(result.sum()),5)
        self.assertEqual((p['cx'],p['cz']),(-2,3))
        tag['sections'][0]['block_states']['palette'][1]['Properties']=n.Compound({'axis':n.String('x')})
        with self.assertRaises(ValueError):encode_chunk(tag,'fixture',{})

    def test_publication_is_replayable_and_atomic(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'inbox').mkdir();(root/'receipts').mkdir()
            first=publish(root,self.patch());second=publish(root,copy.deepcopy(self.patch()))
            self.assertEqual(first,second)
            files=list((root/'inbox').iterdir());self.assertEqual(len(files),1)
            self.assertEqual(digest(files[0].read_bytes()),first)
            self.assertEqual(json.loads(gzip.decompress(files[0].read_bytes())),self.patch())


if __name__=='__main__':unittest.main()
