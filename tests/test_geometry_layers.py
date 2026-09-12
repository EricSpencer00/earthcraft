import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from geometry_layers import TOPOLOGY_SUPPORT_DEPTH, geometry_layer_masks


class GeometryLayerTests(unittest.TestCase):
    def test_topology_is_shallow_and_structures_are_above_ground(self):
        ground=np.full((16,16),80,np.int32)
        ground[0,0]=83
        topology,structures=geometry_layer_masks(ground,-64,160)
        self.assertEqual(int(topology.sum()),16*16*TOPOLOGY_SUPPORT_DEPTH)
        self.assertTrue(topology[80+64,1,1])
        self.assertTrue(topology[80-TOPOLOGY_SUPPORT_DEPTH+1+64,1,1])
        self.assertFalse(topology[80-TOPOLOGY_SUPPORT_DEPTH+64,1,1])
        self.assertTrue(structures[81+64,1,1])
        self.assertFalse(structures[80+64,1,1])

    def test_invalid_or_unbounded_layers_fail_closed(self):
        with self.assertRaises(ValueError):geometry_layer_masks(np.zeros((8,8),int),-64,160)
        with self.assertRaises(ValueError):geometry_layer_masks(np.zeros((16,16),float),-64,160)
        with self.assertRaises(ValueError):geometry_layer_masks(np.full((16,16),-64,int),-64,160)
        with self.assertRaises(ValueError):geometry_layer_masks(np.full((16,16),80,int),-64,160,1)
