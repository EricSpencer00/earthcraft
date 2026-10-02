"""Admit complete frozen terrain arrays before generation or cache reuse."""
import json
from pathlib import Path
import numpy as np
from region_expansion import sha


def validate(root, tile):
    root = Path(root)
    source = root / 'sources'
    receipt = json.loads((root / 'sources-receipt.json').read_text())
    meta = json.loads((source / 'sources.json').read_text())
    if (receipt.get('tile') != tile['id'] or receipt.get('stage') != 'sources' or
            receipt.get('result') != 'pass' or
            receipt.get('sources_sha256') != sha(source / 'sources.json')):
        raise ValueError('Frozen source receipt changed')
    if any(meta.get(key) != tile[key] for key in ('size', 'west', 'north')):
        raise ValueError('Source raster uses another tile grid')
    raster = source / 'rasters.npz'
    if meta.get('rasters_sha256') and sha(raster) != meta['rasters_sha256']:
        raise ValueError('Frozen source rasters changed')
    with np.load(raster, allow_pickle=False) as arrays:
        size = tile['size']
        if (arrays['elevation'].shape != (size, size) or
                arrays['cover'].shape != (size, size) or
                not np.isfinite(arrays['elevation']).all()):
            raise ValueError('Incomplete source raster arrays')
    return source
