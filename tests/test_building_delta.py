import copy
from pathlib import Path
import sys
import unittest
import nbtlib as n
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from building_delta import encode_delta, native_states
from apply_building_stage_closed import mutate_chunk
from live_city import validate
from metric_world import packed


def chunk(values):
    return n.Compound({'xPos':n.Int(-3),'zPos':n.Int(5),'sections':n.List[n.Compound]([
        n.Compound({'Y':n.Byte(4),'block_states':n.Compound({'palette':n.List[n.Compound]([
            n.Compound({'Name':n.String('minecraft:'+s)}) for s in ('air','stone','bricks')]),
            'data':packed(values,4)})})])})


class DeltaTests(unittest.TestCase):
    def test_exact_additions_recolors_negative_coordinates_and_replay(self):
        before=np.zeros(4096,int);before[1:4]=1;after=before.copy();after[:4]=2
        old,new=chunk(before),chunk(after);delta=encode_delta(old,new,'frame',{})
        self.assertEqual(delta,encode_delta(copy.deepcopy(old),copy.deepcopy(new),'frame',{}))
        self.assertEqual((delta['cx'],delta['cz']),(-3,5));self.assertEqual(delta['cells'],4)
        palette,values=native_states(old);actual=np.array(palette,object)[values]
        for start,count,expected,target in delta['runs']:
            self.assertTrue((actual[start:start+count]==delta['palette'][expected]).all())
            actual[start:start+count]=delta['palette'][target]
        palette,values=native_states(new);np.testing.assert_array_equal(actual,np.array(palette,object)[values])
        self.assertIsNone(encode_delta(new,new,'frame',{}))

    def test_no_removal_or_invalid_expectation(self):
        before=np.ones(4096,int);after=before.copy();after[3]=0
        with self.assertRaisesRegex(ValueError,'removes'):encode_delta(chunk(before),chunk(after),'f',{})
        after[3]=2;p=encode_delta(chunk(before),chunk(after),'f',{})
        for run in ([0,1,-1,0],[0,1,0,99],[0,1,0,0],[262144,1,0,1]):
            bad=copy.deepcopy(p);bad['runs']=[run];bad['cells']=1
            with self.assertRaises(ValueError):validate(bad)

    def test_conflicting_player_state_is_not_expected_state(self):
        before=np.zeros(4096,int);after=before.copy();after[0]=2
        p=encode_delta(chunk(before),chunk(after),'f',{})
        run=p['runs'][0]
        self.assertEqual(p['palette'][run[2]],'minecraft:air')
        self.assertNotEqual(p['palette'][run[2]],'minecraft:diamond_block')

    def test_accepted_photo_anchor_and_exposed_air_are_not_patched(self):
        before=np.zeros(4096,int);after=before.copy();after[:3]=2
        p=encode_delta(chunk(before),chunk(after),'f',{},[[-48,64,80],[-47,64,80]])
        self.assertEqual(p['cells'],1)
        self.assertEqual(p['runs'][0][0],(64+64)*256+2)

    def test_closed_writer_uses_same_cas_and_keeps_conflicts(self):
        before=np.zeros(4096,int);before[1]=1;after=before.copy();after[:2]=2
        old=chunk(before);p=encode_delta(old,chunk(after),'f',{})
        actual,counts=mutate_chunk(old,p)
        self.assertEqual(counts,{'written':2,'conflicts':0,'already_target':0})
        palette,values=native_states(actual)
        self.assertEqual(palette[values[8*4096]],'minecraft:bricks')
        _,replay=mutate_chunk(actual,p);self.assertEqual(replay['already_target'],2)
        changed=before.copy();changed[0]=1
        _,counts=mutate_chunk(chunk(changed),p)
        self.assertEqual(counts['written'],1);self.assertEqual(counts['conflicts'],1)


if __name__=='__main__':unittest.main()
