import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from stereo_match import match_pair
class StereoTest(unittest.TestCase):
 def test_known_horizontal_and_vertical_shift(self):
  rng=np.random.default_rng(7);left=rng.integers(0,255,(256,320),dtype=np.uint8)
  for axis in [0,1]:
   right=np.roll(left,-12,axis=1-axis)
   d,_=match_pair([left,right],axis,0,32,{'blockSize':3,'P1':72,'P2':288,'uniquenessRatio':10})
   self.assertAlmostEqual(float(np.median(d[64:-64,64:-64])),12,delta=.15)
