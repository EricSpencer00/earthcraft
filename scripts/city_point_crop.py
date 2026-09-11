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
    # Native Cook coordinates are US-survey feet.  A 1000-unit bin is smaller
    # than a city tile in the source chart, so a crop usually visits only a
    # handful of bins instead of scanning the whole member.
    INDEX_BIN = 1000.0
    DECODE_CHUNK = 2_000_000
    LAS6_DTYPE = np.dtype({'names': ('X','Y','Z','intensity','return_info','flags',
                                     'classification','scan_angle','point_source_id','gps_time'),
                           'formats': ('<i4','<i4','<i4','<u2','u1','u1','u1','<i2','<u2','<f8'),
                           'offsets': (0,4,8,12,14,15,16,18,20,22), 'itemsize': 30})

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
            if reader.header.point_format.id == 6 and reader.header.point_format.size == 30:
                # Cook's 2022 scans use fixed-size LAS 1.4 format 6 records.
                # Decode packed records in NumPy without constructing a laspy
                # point object for every return.  Bit masks match LAS 1.4
                # classification flags; source order is byte-stream order.
                scale=np.asarray(reader.header.scales,dtype=np.float64)
                offset=np.asarray(reader.header.offsets,dtype=np.float64)
                stream.seek(reader.header.offset_to_point_data)
                remaining=int(reader.header.point_count)
                while remaining:
                    count=min(PointCache.DECODE_CHUNK,remaining)
                    raw=stream.read(count*PointCache.LAS6_DTYPE.itemsize)
                    if len(raw)!=count*PointCache.LAS6_DTYPE.itemsize:
                        raise ValueError('Incomplete original point stream')
                    records=np.frombuffer(raw,dtype=PointCache.LAS6_DTYPE,count=count)
                    parts['x'].append(records['X'].astype(np.float64)*scale[0]+offset[0])
                    parts['y'].append(records['Y'].astype(np.float64)*scale[1]+offset[1])
                    parts['z'].append(records['Z'].astype(np.float64)*scale[2]+offset[2])
                    parts['classification'].append(np.array(records['classification'],copy=True))
                    parts['withheld'].append(((records['flags']>>2)&1).astype(np.uint8,copy=False))
                    parts['point_source_id'].append(np.array(records['point_source_id'],copy=True))
                    parts['intensity'].append(np.array(records['intensity'],copy=True))
                    parts['return_number'].append((records['return_info']&15).astype(np.uint8,copy=False))
                    parts['gps_time'].append(np.array(records['gps_time'],copy=True))
                    inspected+=count; remaining-=count
            else:
                for points in reader.chunk_iterator(PointCache.DECODE_CHUNK):
                    # Copy each field before the laspy chunk is released.  These
                    # arrays are immutable cache values and preserve source order.
                    for key in PointCache.FIELDS:
                        parts[key].append(np.array(getattr(points, key), copy=True))
                    inspected += len(points)
            if inspected != reader.header.point_count:
                raise ValueError('Incomplete original point stream')
        arrays = {key: np.concatenate(values) for key, values in parts.items()}
        # Build a stable native-coordinate bin index once.  The sorted order is
        # only an acceleration structure; query() sorts selected source indices
        # back into original LAS order before returning them.
        x, y = arrays['x'], arrays['y']
        bx = np.floor(x / PointCache.INDEX_BIN).astype(np.int64)
        by = np.floor(y / PointCache.INDEX_BIN).astype(np.int64)
        min_bx, min_by = int(bx.min()), int(by.min())
        span_y = int(by.max() - min_by + 1)
        keys = (bx - min_bx) * span_y + (by - min_by)
        order = np.argsort(keys, kind='stable').astype(np.int32, copy=False)
        sorted_keys = keys[order]
        unique, counts = np.unique(sorted_keys, return_counts=True)
        offsets = np.zeros(int(unique.max()) + 2, dtype=np.int64)
        offsets[unique + 1] = counts
        np.cumsum(offsets, out=offsets)
        index = {'order': order, 'offsets': offsets, 'min_bx': min_bx,
                 'min_by': min_by, 'span_y': span_y}
        size = sum(int(value.nbytes) for value in arrays.values())
        size += sum(int(value.nbytes) for value in (order, offsets))
        return arrays, size, inspected, index

    def get(self, path, geometry):
        key = str(Path(path).resolve())
        entry = self._entries.get(key)
        if entry is not None:
            arrays, size, count, _ = entry
            self._entries.move_to_end(key)
            self.hits += 1
            return arrays, {'hit': True, 'bytes': size, 'point_count': count}
        self.misses += 1
        arrays, size, count, index = self._read(path, geometry)
        # A single member larger than the budget is still usable, but is not
        # retained; this keeps the acceleration opt-in and memory bounded.
        if self.max_bytes and size <= self.max_bytes:
            while self._entries and self._bytes + size > self.max_bytes:
                _, (_, old_size, _, _) = self._entries.popitem(last=False)
                self._bytes -= old_size
                self.evictions += 1
            self._entries[key] = (arrays, size, count, index)
            self._bytes += size
        return arrays, {'hit': False, 'bytes': size, 'point_count': count}

    def query(self, path, bounds):
        """Return candidate source indices for an inclusive native XY bounds.

        Returned indices are always ascending, matching the original LAS
        stream order.  A cache entry larger than the configured budget has no
        retained index and deliberately returns None so callers use the exact
        streaming path.
        """
        key = str(Path(path).resolve())
        entry = self._entries.get(key)
        if entry is None:
            return None
        _, _, _, index = entry
        lo_x, lo_y, hi_x, hi_y = map(float, bounds)
        bx0 = int(np.floor(lo_x / self.INDEX_BIN)); bx1 = int(np.floor(hi_x / self.INDEX_BIN))
        by0 = int(np.floor(lo_y / self.INDEX_BIN)); by1 = int(np.floor(hi_y / self.INDEX_BIN))
        ix0, ix1 = bx0-index['min_bx'], bx1-index['min_bx']
        iy0, iy1 = by0-index['min_by'], by1-index['min_by']
        x_bins = (index['offsets'].size - 2) // index['span_y'] + 1
        if ix1 < 0 or iy1 < 0 or ix0 >= x_bins:
            return np.empty(0, dtype=np.int32)
        ix0=max(0,ix0); iy0=max(0,iy0)
        max_key=index['offsets'].size-2
        ix1=min(ix1, max_key//index['span_y'])
        iy1=min(iy1, index['span_y']-1)
        candidates=[]
        for bx in range(ix0,ix1+1):
            base=bx*index['span_y']
            for by in range(iy0,iy1+1):
                key_id=base+by
                if key_id>max_key: continue
                start,end=int(index['offsets'][key_id]),int(index['offsets'][key_id+1])
                if end>start: candidates.append(index['order'][start:end])
        if not candidates:
            return np.empty(0, dtype=np.int32)
        # Bin iteration is spatial order; recover original LAS order exactly.
        return np.sort(np.concatenate(candidates), kind='stable')

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
    indexed_candidates = 0
    indexed_queries = 0
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
            candidate_indices = point_cache.query(path, bounds)
            if candidate_indices is None:
                candidate_indices = np.arange(len(arrays['x']), dtype=np.int32)
            else:
                indexed_queries += 1
                indexed_candidates += len(candidate_indices)
            x, y = arrays['x'][candidate_indices], arrays['y'][candidate_indices]
            selected=(x>=bounds[0])&(x<=bounds[2])&(y>=bounds[1])&(y<=bounds[3])
            if selected.any():
                xx,yy=projection.transform(x[selected],y[selected])
                valid=(xx>=west)&(xx<west+size)&(yy>north-size)&(yy<=north)
                if valid.any():
                    source_indices=candidate_indices[np.flatnonzero(selected)[valid]]
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
        report['point_cache']['indexed_queries'] = indexed_queries
        report['point_cache']['indexed_candidates'] = indexed_candidates
    (output/'manifest.json').write_text(json.dumps(report,indent=2))
    return report
