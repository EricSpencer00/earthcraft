"""Voxel cells grouped by 16x16 chunk, so a per-chunk loop does not rescan every cell.

``cells`` is an (N, 3) array of x, y, z block coordinates. ``ChunkCells(cells).get(cx, cz)``
returns exactly ``cells[(cells[:, 0] // 16 == cx) & (cells[:, 2] // 16 == cz)]``, in the same
order (the sort is stable), in O(log N) instead of O(N). With up to ~2M lidar cells and
1,024 chunks per tile, the mask form was ~2 billion comparisons per pass.
"""
import numpy as np

_OFFSET = 1 << 20  # chunk coordinates must stay within +-1M (+-16M blocks)


class ChunkCells:
    def __init__(self, cells):
        cells = np.asarray(cells)
        cx, cz = cells[:, 0] // 16, cells[:, 2] // 16
        if len(cells) and (np.abs(cx).max() >= _OFFSET or np.abs(cz).max() >= _OFFSET):
            raise ValueError('Chunk coordinates out of range')
        keys = self._key(cz.astype(np.int64), cx.astype(np.int64))
        order = np.argsort(keys, kind='stable')
        self.cells, self.keys = cells[order], keys[order]

    @staticmethod
    def _key(cz, cx):
        return (cz + _OFFSET) * (2 * _OFFSET) + (cx + _OFFSET)

    def get(self, cx, cz):
        key = self._key(np.int64(cz), np.int64(cx))
        lo, hi = np.searchsorted(self.keys, key, 'left'), np.searchsorted(self.keys, key, 'right')
        return self.cells[lo:hi]
