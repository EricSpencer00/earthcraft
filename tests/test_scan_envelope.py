from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from scan_envelope import admit


class ScanEnvelopeTests(unittest.TestCase):
    def test_mapped_supported_unclassified_returns_and_boundary_ownership(self):
        grid={'west':0,'north':4,'size':4}
        context={'dtm':np.full((4,4),100),'dsm':np.full((4,4),110),
                 'valid':np.ones((4,4),bool),'roof_mask':np.zeros((4,4),bool)}
        context['roof_mask'][1,1]=True
        xyz=np.array([[1.5,2.5,108],[1.5,2.5,102],[1.5,2.5,113],
                      [2.5,2.5,108],[2.5,2.5,108],[4,2.5,108],[1.5,0,108],[1.5,4,108]])
        labels=np.array([1,1,1,1,6,6,6,6])
        selected,classified,associated=admit(xyz,labels,grid,context)
        np.testing.assert_array_equal(np.flatnonzero(selected),[0,4,7])
        np.testing.assert_array_equal(np.flatnonzero(associated),[0])
        np.testing.assert_array_equal(np.flatnonzero(classified),[4,7])
        direct,_,_=admit(xyz,labels,grid)
        np.testing.assert_array_equal(np.flatnonzero(direct),[4,7])
        context['valid'][1,1]=False
        self.assertFalse(admit(xyz,labels,grid,context)[2].any())

    def test_grid_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError,'another surface grid'):
            admit(np.array([[0,1,100]]),np.array([1]),{'west':0,'north':2,'size':2},
                  {key:np.zeros((1,1)) for key in ('dtm','dsm','valid','roof_mask')})
