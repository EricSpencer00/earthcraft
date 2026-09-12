"""Deterministic topology/building separation for Minecraft geometry.

Terrain only needs a shallow collision/support slab.  Every non-air cell above
the measured ground belongs to the sparse structure layer.  Keeping the two
masks formulaic avoids material or shape inference.
"""
import numpy as np


TOPOLOGY_SUPPORT_DEPTH = 2


def geometry_layer_masks(ground, min_y, height, support_depth=TOPOLOGY_SUPPORT_DEPTH):
    ground = np.asarray(ground)
    if ground.shape != (16, 16) or not np.issubdtype(ground.dtype, np.integer):
        raise ValueError('Ground layer must be a 16x16 integer grid')
    if type(min_y) is not int or type(height) is not int or height < 1:
        raise ValueError('Invalid vertical geometry envelope')
    if type(support_depth) is not int or support_depth < 2 or support_depth > 16:
        raise ValueError('Topology support depth must be 2..16 blocks')
    if ground.min() < min_y + support_depth - 1 or ground.max() >= min_y + height:
        raise ValueError('Ground layer exceeds the vertical geometry envelope')
    y = np.arange(min_y, min_y + height)[:, None, None]
    topology = (y <= ground) & (y > ground - support_depth)
    structures = y > ground
    return topology, structures
