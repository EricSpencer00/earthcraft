from pathlib import Path
import sys
import unittest
import numpy as np
from shapely.geometry import box
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from building_audit import point_metrics,footprint_matches


class BuildingAuditTests(unittest.TestCase):
    def test_missing_point_coverage_is_not_success(self):
        result=point_metrics(box(10,10,12,12),0,{},box(0,0,2,2))
        self.assertEqual(result['available_footprint_fraction'],0)
        self.assertFalse(result['complete_facade_observation'])

    def test_split_strips_and_partial_coverage(self):
        data={'xyz':np.array([[.1,.1,2.1],[.2,.2,2.2],[1.1,1.1,3.1],[1.2,1.2,3.2]]),
              'classification':np.array([6,6,6,6]),'withheld':np.zeros(4),
              'point_source_id':np.array([1,2,1,2])}
        result=point_metrics(box(0,0,2,2),0,data,box(0,0,1,2))
        self.assertEqual(result['available_footprint_fraction'],.5)
        self.assertEqual(result['strip_holdout']['coverage_ratio'],1)
        self.assertFalse(result['complete_facade_observation'])

    def test_footprint_agreement_is_not_independent_truth(self):
        g=box(0,0,10,10)
        match=footprint_matches(g,[{'id':1,'geometry':g,'tags':{'name':'fixture'}}])[0]
        self.assertEqual(match['intersection_over_union'],1)
        self.assertFalse(match['independent_accuracy_reference'])

    def test_tall_neighbor_ring_is_separated_from_building_core(self):
        data={'xyz':np.array([[.5,.5,10.],[2.5,.5,90.]]),'classification':np.array([6,6]),
              'withheld':np.zeros(2),'point_source_id':np.array([1,2])}
        result=point_metrics(box(0,0,2,2),0,data,box(-1,-1,4,4))
        self.assertEqual(result['core_candidate_max_height_m'],10)
        self.assertEqual(result['context_ring_candidate_max_height_m'],90)


if __name__=='__main__':unittest.main()
