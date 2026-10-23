"""Stream a full-density USGS 3DEP EPT crop into a compact classified point file.

Reuses the octree addressing of ``ept_buildings`` (every intersecting level is
read; no downsampling). Unlike the building crop, LAZ nodes are decoded from
memory and never written to disk: only the cropped, classified points and a
per-node receipt (URL, SHA-256, bytes, point counts) are kept. All writes go
through ``detail_guard.WriteGuard``.
"""
import argparse
import hashlib
import io
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import laspy
import numpy as np
from pyproj import Transformer

from detail_guard import WriteGuard
from ept_buildings import node_bounds, overlaps

EPT_ROOT = 'https://usgs-lidar-public.s3.amazonaws.com/'
KEEP_CLASSES = (1, 2, 3, 4, 5, 6, 9, 17)


def fetch(url, limit=96 * 2**20, attempts=3):
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError(f'{url} exceeds the {limit}-byte bound')
            return raw
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def getter(base, cache=None):
    """(relative EPT path, byte limit) -> bytes. With a cache, read through the regional LAZ
    cache ept_buildings fills (same local paths and receipts), so a node is downloaded once
    per worker whichever crop asks first; its limits are the cache's (4 MiB JSON, 64 MiB LAZ)."""
    if cache is None:
        return lambda relative, limit: fetch(f'{base}/{relative}', limit)
    from regional_scans import frozen_get
    project = Path(cache) / base.rsplit('/', 1)[-1]

    def get(relative, limit):
        local = relative.replace('ept-hierarchy/', 'hierarchy/').replace('ept-data/', 'data/')
        return frozen_get(f'{base}/{relative}', project / local, 64 * 2**20 if local.endswith('.laz') else 4 * 2**20)
    return get


def hierarchy_nodes(base, metadata, query, budget=4096, get=None):
    get = get or getter(base)
    nodes, seen = {}, set()

    def walk(key):
        if key in seen or len(seen) > 64:
            raise ValueError('EPT hierarchy loop or excessive hierarchy pages')
        seen.add(key)
        table = json.loads(get(f'ept-hierarchy/{key}.json', 16 * 2**20))
        for address, count in table.items():
            if not overlaps(node_bounds(metadata['bounds'], address), query):
                continue
            if count == -1:
                walk(address)
            elif count > 0:
                nodes[address] = count
        if len(nodes) > budget:
            raise ValueError('Crop exceeds node budget; no downsampling fallback')

    walk('0-0-0-0')
    return nodes, sorted(seen)


def crop(project, frame, west, north, size, halo, guard, out, workers=4, max_download=600 * 2**20, cache=None):
    """Crop ``[west-halo, west+size+halo) x (north-size-halo, north+halo]`` in frame metres."""
    base = EPT_ROOT + project
    get = getter(base, cache)
    metadata = json.loads(get('ept.json', 96 * 2**20))
    if metadata['dataType'] != 'laszip' or metadata['hierarchyType'] != 'json':
        raise ValueError('Only LAZ/JSON EPT is supported')
    native = metadata['srs']['wkt']
    forward = Transformer.from_crs(frame['crs'], native, always_xy=True)
    inverse = Transformer.from_crs(native, frame['crs'], always_xy=True)
    w, e, n, s = west - halo, west + size + halo, north + halo, north - size - halo
    xx, yy = forward.transform([w, e, w, e], [n, n, s, s])
    query = [min(xx) - 2, min(yy) - 2, max(xx) + 2, max(yy) + 2]
    nodes, pages = hierarchy_nodes(base, metadata, query, get=get)
    receipts, parts, downloaded = [], [], 0

    def one(item):
        key, count = item
        url = f'{base}/ept-data/{key}.laz'
        raw = get(f'ept-data/{key}.laz', 96 * 2**20)
        las = laspy.read(io.BytesIO(raw))
        if len(las.points) != count:
            raise ValueError(f'{key}: payload has {len(las.points)} points, hierarchy says {count}')
        cls = np.asarray(las.classification)
        keep = (np.asarray(las.withheld) == 0) & np.isin(cls, KEEP_CLASSES)
        x, y = inverse.transform(np.asarray(las.x)[keep], np.asarray(las.y)[keep])
        z = np.asarray(las.z)[keep]
        inside = (x >= w) & (x < e) & (y > s) & (y <= n)
        rec = {'key': key, 'url': url, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
               'points': int(count), 'cropped_points': int(inside.sum()),
               'class_counts_full_node': {str(c): int(v) for c, v in zip(*np.unique(cls, return_counts=True))}}
        part = np.column_stack((x[inside], y[inside], z[inside])).astype(np.float64)
        extra = np.column_stack((cls[keep][inside], np.asarray(las.return_number)[keep][inside],
                                 np.asarray(las.number_of_returns)[keep][inside])).astype(np.uint8)
        intensity = np.asarray(las.intensity)[keep][inside].astype(np.uint16)
        return rec, part, (extra, intensity), len(raw)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rec, part, extra, nbytes in pool.map(one, sorted(nodes.items())):
            downloaded += nbytes
            if downloaded > max_download:
                raise OSError('EPT download budget exceeded')
            receipts.append(rec)
            parts.append((part, extra))
    xyz = np.concatenate([p for p, _ in parts]) if parts else np.empty((0, 3))
    attrs = np.concatenate([a for _, (a, _) in parts]) if parts else np.empty((0, 3), np.uint8)
    intensity = np.concatenate([i for _, (_, i) in parts]) if parts else np.empty(0, np.uint16)
    buffer = io.BytesIO()
    # Centimetre integers relative to the crop corner: compact and lossless at source scale (0.01 m).
    origin = np.array([w, s, 0.0])
    ints = np.rint((xyz - origin) * 100).astype(np.int32)
    np.savez_compressed(buffer, xyz_cm=ints, origin=origin, cls=attrs[:, 0], ret=attrs[:, 1], nret=attrs[:, 2],
                        intensity=intensity)
    points_path = guard.write_bytes(Path(out) / 'points.npz', buffer.getvalue())
    classes = {str(c): int(v) for c, v in zip(*np.unique(attrs[:, 0], return_counts=True))}
    manifest = {'schema': 'earthcraft.detail-points.v1', 'project': project, 'source': base,
                'license': 'USGS 3DEP: public domain (US Government work)',
                'frame_crs_sha256': hashlib.sha256(frame['crs'].encode()).hexdigest(),
                'crop': {'west': w, 'east': e, 'north': n, 'south': s, 'halo_m': halo},
                'hierarchy_pages': pages, 'nodes': len(receipts), 'downloaded_bytes': downloaded,
                'laz_written_to_disk': False, 'downsampling': False, 'all_intersecting_levels': True,
                'cropped_points': int(len(xyz)), 'classes_in_crop': classes,
                'points_sha256': hashlib.sha256(points_path.read_bytes()).hexdigest(),
                'node_receipts': receipts}
    guard.write_text(Path(out) / 'manifest.json', json.dumps(manifest, indent=1))
    return manifest


def load(out):
    with np.load(Path(out) / 'points.npz') as data:
        xyz = data['xyz_cm'] / 100.0 + data['origin']
        intensity = data['intensity'].copy() if 'intensity' in data.files else None
        return xyz, data['cls'].copy(), data['ret'].copy(), data['nret'].copy(), intensity


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', required=True)
    p.add_argument('--frame', type=Path, required=True)
    p.add_argument('--west', type=float, required=True)
    p.add_argument('--north', type=float, required=True)
    p.add_argument('--size', type=float, default=512)
    p.add_argument('--halo', type=float, default=24)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--max-bytes', type=int, default=200 * 2**20)
    p.add_argument('--free-floor', type=int, default=15 * 2**30)
    a = p.parse_args()
    guard = WriteGuard(a.out.parent, a.max_bytes, a.free_floor)
    record = crop(a.project, json.loads(a.frame.read_text()), a.west, a.north, a.size, a.halo, guard, a.out)
    print(json.dumps({k: v for k, v in record.items() if k != 'node_receipts'}, indent=1))


if __name__ == '__main__':
    main()
