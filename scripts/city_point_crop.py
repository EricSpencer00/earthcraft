"""Normalize multiple indexed Cook LAS members onto one frozen city tile."""
import gzip
import hashlib
import json
from collections import OrderedDict
from pathlib import Path

import laspy
import numpy as np
from pyproj import Transformer
from shapely.geometry import shape, box, mapping
from shapely.ops import transform, unary_union


class PointCache:
    """Bounded decoded LAS-member cache for neighboring city tiles.

    Cook LAS members are commonly ~50 million points and a 256 m city tile
    intersects several adjacent members.  Without this cache every tile
    re-decompresses an entire member from the start of the gzip stream.  Keep
    decoded, immutable numpy arrays for a bounded number of bytes and evict
    least-recently-used members when the budget is reached.  The source file
    remains the authority; cache entries are only a transient acceleration.
    """

    FIELDS = ('x', 'y', 'z', 'classification', 'withheld', 'point_source_id',
              'intensity', 'return_number', 'gps_time')

    def __init__(self, max_bytes=16 * 2**30):
        if type(max_bytes) is not int or max_bytes < 0:
            raise ValueError('Point cache budget must be a non-negative integer')
        self.max_bytes = max_bytes
        self._entries = OrderedDict()
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    @staticmethod
    def _read(path, geometry):
        path = Path(path)
        opener = gzip.open if path.suffix == '.gz' else open
        parts = {key: [] for key in PointCache.FIELDS}
        inspected = 0
        with opener(path, 'rb') as stream, laspy.open(stream, closefd=False,
                                                       read_evlrs=False) as reader:
            np.testing.assert_allclose(
                [*reader.header.mins[:2], *reader.header.maxs[:2]],
                geometry.bounds, atol=.02, rtol=0)
            for points in reader.chunk_iterator(400_000):
                # Copy each field before the laspy chunk is released.  These
                # arrays are immutable cache values and preserve source order.
                for key in PointCache.FIELDS:
                    parts[key].append(np.array(getattr(points, key), copy=True))
                inspected += len(points)
            if inspected != reader.header.point_count:
                raise ValueError('Incomplete original point stream')
        arrays = {key: np.concatenate(values) for key, values in parts.items()}
        return arrays, sum(int(value.nbytes) for value in arrays.values()), inspected

    def get(self, path, geometry):
        key = str(Path(path).resolve())
        entry = self._entries.get(key)
        if entry is not None:
            arrays, size, count = entry
            self._entries.move_to_end(key)
            self.hits += 1
            return arrays, {'hit': True, 'bytes': size, 'point_count': count}
        self.misses += 1
        arrays, size, count = self._read(path, geometry)
        # A single member larger than the budget is still usable, but is not
        # retained; this keeps the acceleration opt-in and memory bounded.
        if self.max_bytes and size <= self.max_bytes:
            while self._entries and self._bytes + size > self.max_bytes:
                _, (_, old_size, _) = self._entries.popitem(last=False)
                self._bytes -= old_size
                self.evictions += 1
            self._entries[key] = (arrays, size, count)
            self._bytes += size
        return arrays, {'hit': False, 'bytes': size, 'point_count': count}

    def snapshot(self):
        return {'hits': self.hits, 'misses': self.misses,
                'evictions': self.evictions, 'cached_bytes': self._bytes,
                'cached_members': len(self._entries)}


def crop_sources(items,meta,output,max_points=20_000_000,compress_working=True,
                 point_cache=None):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    size=meta['size'];west,north=meta['west'],meta['north']
    if type(size) is not int or not 16<=size<=256 or size%16:
        raise ValueError('Use bounded 16..256 m city point tiles')
    extent=box(west,north-size,west+size,north)
    projection=Transformer.from_crs(6455,meta['crs'],always_xy=True)
    inverse=Transformer.from_crs(meta['crs'],6455,always_xy=True)
    native_extent=transform(inverse.transform,extent.segmentize(16)).buffer(.01)
    bounds=native_extent.bounds;kept=[];coverage=[];sources=[];total=0;seen=set()
    cache_before = point_cache.snapshot() if point_cache is not None else None
    for path,record,asset in items:
        if asset['id'] in seen:raise ValueError('Duplicate source member would duplicate observations')
        seen.add(asset['id']);geometry=shape(asset['native_geometry'])
        covered=transform(projection.transform,geometry.segmentize(100)).intersection(extent)
        if covered.area<=0:continue
        path=Path(path)
        if point_cache is None:
            opener=gzip.open if path.suffix=='.gz' else open
            inspected=0
            with opener(path,'rb') as stream,laspy.open(stream,closefd=False,read_evlrs=False) as reader:
                np.testing.assert_allclose([*reader.header.mins[:2],*reader.header.maxs[:2]],geometry.bounds,atol=.02,rtol=0)
                for points in reader.chunk_iterator(400_000):
                    x,y=np.asarray(points.x),np.asarray(points.y)
                    selected=(x>=bounds[0])&(x<=bounds[2])&(y>=bounds[1])&(y<=bounds[3])
                    if selected.any():
                        xx,yy=projection.transform(x[selected],y[selected]);p=points[selected]
                        valid=(xx>=west)&(xx<west+size)&(yy>north-size)&(yy<=north)
                        if valid.any():
                            p=p[valid];total+=len(p)
                            if total>max_points:raise ValueError('Point crop memory budget exceeded; preserve sources, subdivide tile')
                            kept.append({'xyz':np.column_stack((xx[valid],yy[valid],np.asarray(p.z)*(1200/3937))),
                                'classification':np.asarray(p.classification),'withheld':np.asarray(p.withheld),
                                'point_source_id':np.asarray(p.point_source_id),'intensity':np.asarray(p.intensity),
                                'return_number':np.asarray(p.return_number),'gps_time':np.asarray(p.gps_time)})
                    inspected+=len(points)
                if inspected!=reader.header.point_count:raise ValueError('Incomplete original point stream')
        else:
            arrays, cache_info = point_cache.get(path, geometry)
            inspected = cache_info['point_count']
            x, y = arrays['x'], arrays['y']
            selected=(x>=bounds[0])&(x<=bounds[2])&(y>=bounds[1])&(y<=bounds[3])
            if selected.any():
                xx,yy=projection.transform(x[selected],y[selected])
                valid=(xx>=west)&(xx<west+size)&(yy>north-size)&(yy<=north)
                if valid.any():
                    source_indices=np.flatnonzero(selected)[valid]
                    total+=len(source_indices)
                    if total>max_points:raise ValueError('Point crop memory budget exceeded; preserve sources, subdivide tile')
                    kept.append({'xyz':np.column_stack((xx[valid],yy[valid],arrays['z'][source_indices]*(1200/3937))),
                        'classification':arrays['classification'][source_indices],
                        'withheld':arrays['withheld'][source_indices],
                        'point_source_id':arrays['point_source_id'][source_indices],
                        'intensity':arrays['intensity'][source_indices],
                        'return_number':arrays['return_number'][source_indices],
                        'gps_time':arrays['gps_time'][source_indices]})
        coverage.append(covered);sources.append(dict(record,source_las=str(path.resolve()),survey_id=asset['id']))
        print(f"{asset['id']}: {inspected:,} source returns inspected; {total:,} retained in city tile",flush=True)
    covered=unary_union(coverage)
    if covered.area/extent.area<.999999:raise ValueError('Missing acquired source coverage; no geometry fallback')
    if not kept:raise ValueError('No observed returns in tile')
    arrays={key:np.concatenate([part[key] for part in kept]) for key in kept[0]}
    output.mkdir(parents=True)
    # Short-lived single-tile working data need not pay DEFLATE's CPU cost.
    # Keep identical array dtypes/values/order; original publisher scans remain
    # compressed and preserved independently of this disposable container.
    writer=np.savez_compressed if compress_working else np.savez
    writer(output/'points.npz',**arrays)
    with (output/'points.npz').open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    labels,counts=np.unique(arrays['classification'],return_counts=True)
    report={'sources':sources,'output_horizontal_crs':meta['crs'],
        'grid':{k:meta[k] for k in ('crs','west','north','size')},'coverage_geometry':mapping(covered),
        'coverage_role':'Union of actually acquired publisher survey polygons, clipped to tile; not proof of visible surface completeness',
        'aoi_fraction_covered':covered.area/extent.area,'crop_point_count':total,
        'class_counts':dict(zip(map(str,labels),map(int,counts))),
        'native_horizontal_crs':'EPSG:6455','vertical_datum':'NAVD88 Geoid2018',
        'vertical_unit_conversion':1200/3937,'points_sha256':digest,
        'working_container_compression':'deflate' if compress_working else 'stored',
        'inference_used':False,'interpolation':False,'original_preserved':True}
    if point_cache is not None:
        after = point_cache.snapshot()
        report['point_cache'] = {
            key: after[key] - cache_before[key]
            for key in ('hits','misses','evictions')
        }
        report['point_cache']['cached_bytes'] = after['cached_bytes']
        report['point_cache']['cached_members'] = after['cached_members']
    (output/'manifest.json').write_text(json.dumps(report,indent=2))
    return report
