from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from regional_install import quality_merge
from ept_buildings import node_bounds, overlaps


class RegionalInstallTests(unittest.TestCase):
    def test_upgrade_preserves_edits_cleared_chunks_and_timestamps(self):
        baseline = {0:(b'LQ',b'zero'),1:(b'LQ',b'zero'),2:(b'LQ',b'zero')}
        current = {0:baseline[0],1:(b'player edit',b'zero'),2:(b'LQ',b'new!'),3:(b'air',b'zero')}
        candidate = {slot:(b'scan',b'zero') for slot in range(5)}
        merged, counts = quality_merge(current,candidate,baseline)
        self.assertEqual(merged[0],candidate[0])
        self.assertEqual(merged[4],candidate[4])
        for slot in (1,2,3):self.assertEqual(merged[slot],current[slot])
        self.assertEqual(counts,{'added_chunks':1,'upgraded_chunks':1,'existing_or_edited_chunks_preserved':3})

    def test_base_never_replaces_existing_higher_quality(self):
        original={0:(b'HQ',b'zero')}
        merged, counts = quality_merge(original,{0:(b'LQ',b'zero'),1:(b'LQ',b'zero')})
        self.assertEqual(merged[0],original[0]);self.assertEqual(counts['upgraded_chunks'],0)

    def test_octree_bounds_include_full_vertical_extent(self):
        bounds = [0,0,0,8,8,8]
        self.assertEqual(node_bounds(bounds,'1-1-0-1'),[4,0,4,8,4,8])
        self.assertTrue(overlaps(node_bounds(bounds,'1-1-0-1'),[4.5,1,5,2]))
        with self.assertRaises(ValueError):node_bounds(bounds,'1-2-0-0')


if __name__ == '__main__':unittest.main()
