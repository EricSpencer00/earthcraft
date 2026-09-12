"""Persistent, queryable Cook LiDAR indexes for optional detail refinement.

The original publisher LAS remains the authority. This module deterministically
retains only the provider classes used by Earthcraft, groups them into small
native-coordinate bins, and stores source order so tile crops remain stable.
"""
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

import laspy
import numpy as np
from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform, unary_union

from city_point_crop import PointCache


BIN_SIZE_NATIVE = 128.0
RETAINED_CLASSES = np.array([1, 6, 11, 15, 19], dtype=np.uint8)
CHUNK_POINTS = 2_000_000


def _source_identity(path, record):
    key = 'compressed_sha256' if Path(path).suffix == '.gz' else 'sha256'
    value = record.get(key)
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError('Verified LAS source digest required')
    return {key: value, 'bytes': record.get('bytes'), 'url': record.get('url'),
            'member': record.get('member')}


def _chunks(path):
    """Yield raw scaled-integer coordinates and provider fields in source order."""
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as stream, laspy.open(
            stream, closefd=False, read_evlrs=False) as reader:
        header = reader.header
        scale = np.asarray(header.scales, dtype=np.float64)
        offset = np.asarray(header.offsets, dtype=np.float64)
        source_start = 0
        if header.point_format.id == 6 and header.point_format.size == 30:
            stream.seek(header.offset_to_point_data)
            remaining = int(header.point_count)
            while remaining:
                count = min(CHUNK_POINTS, remaining)
                raw = stream.read(count * PointCache.LAS6_DTYPE.itemsize)
                if len(raw) != count * PointCache.LAS6_DTYPE.itemsize:
                    raise ValueError('Incomplete original point stream')
                records = np.frombuffer(raw, dtype=PointCache.LAS6_DTYPE, count=count)
                yield (records['X'], records['Y'], records['Z'],
                       records['classification'], (records['flags'] >> 2) & 1,
                       source_start, scale, offset)
                source_start += count
                remaining -= count
        else:
            for points in reader.chunk_iterator(CHUNK_POINTS):
                count = len(points)
                yield (np.asarray(points.X), np.asarray(points.Y), np.asarray(points.Z),
                       np.asarray(points.classification), np.asarray(points.withheld),
                       source_start, scale, offset)
                source_start += count
        if source_start != header.point_count:
            raise ValueError('Incomplete original point stream')


def _header(path, geometry):
    path = Path(path);opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as stream, laspy.open(
            stream, closefd=False, read_evlrs=False) as reader:
        header = reader.header
        np.testing.assert_allclose([*header.mins[:2], *header.maxs[:2]],
                                   geometry.bounds, atol=.02, rtol=0)
        return {'point_count': int(header.point_count),
                'scales': list(map(float, header.scales)),
                'offsets': list(map(float, header.offsets)),
                'mins': list(map(float, header.mins)),
                'maxs': list(map(float, header.maxs)),
                'point_format': header.point_format.id}


def _selected(x, y, classification, withheld, scale, offset):
    keep = (np.asarray(withheld) == 0) & np.isin(classification, RETAINED_CLASSES)
    indices = np.flatnonzero(keep)
    bx = np.floor((x[indices] * scale[0] + offset[0]) / BIN_SIZE_NATIVE).astype(np.int64)
    by = np.floor((y[indices] * scale[1] + offset[1]) / BIN_SIZE_NATIVE).astype(np.int64)
    return indices, bx, by


def _validate(index, identity=None):
    index = Path(index);manifest_path = index/'manifest.json'
    if not manifest_path.is_file():raise ValueError('LiDAR spatial index has no manifest')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('schema') != 'earthcraft-cook-lidar-spatial-v1':
        raise ValueError('Unsupported LiDAR spatial index')
    if identity is not None and manifest.get('source_identity') != identity:
        raise ValueError('LiDAR spatial index source changed')
    expected = {'coordinates.npy': ('int32', (manifest['retained_points'], 3)),
                'classification.npy': ('uint8', (manifest['retained_points'],)),
                'source-index.npy': ('uint32', (manifest['retained_points'],)),
                'bin-offsets.npy': ('int64', (manifest['bin_count'] + 1,))}
    for name,(dtype,shape_) in expected.items():
        path=index/name
        if not path.is_file() or path.stat().st_size != manifest['files'][name]['bytes']:
            raise ValueError(f'Incomplete LiDAR spatial index: {name}')
        value=np.load(path,mmap_mode='r',allow_pickle=False)
        if value.dtype.name!=dtype or value.shape!=shape_:
            raise ValueError(f'Invalid LiDAR spatial index array: {name}')
    return manifest


class SpatialPointIndex:
    """Build each verified LAS member once and serve exact bounded crops."""
    def __init__(self, root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'.locks').mkdir(exist_ok=True)

    def ensure(self, path, record, asset):
        path=Path(path);identifier=asset['id'];identity=_source_identity(path,record)
        destination=self.root/identifier
        with (self.root/'.locks'/f'{identifier}.lock').open('a+') as lock:
            fcntl.lockf(lock,fcntl.LOCK_EX)
            if destination.exists():
                _validate(destination,identity);return destination
            staging=self.root/f'.{identifier}.building-{os.getpid()}-{uuid.uuid4().hex}'
            try:
                self._build(path,record,asset,staging)
                _validate(staging,identity)
                staging.rename(destination)
            except Exception:
                if staging.exists():shutil.rmtree(staging,ignore_errors=True)
                raise
        return destination

    def _build(self,path,record,asset,destination):
        geometry=shape(asset['native_geometry']);header=_header(path,geometry)
        min_bx=int(np.floor(header['mins'][0]/BIN_SIZE_NATIVE))
        min_by=int(np.floor(header['mins'][1]/BIN_SIZE_NATIVE))
        max_bx=int(np.floor(header['maxs'][0]/BIN_SIZE_NATIVE))
        max_by=int(np.floor(header['maxs'][1]/BIN_SIZE_NATIVE))
        span_y=max_by-min_by+1;span_x=max_bx-min_bx+1;bin_count=span_x*span_y
        counts=np.zeros(bin_count,dtype=np.int64);inspected=retained=0
        for x,y,z,classification,withheld,start,scale,offset in _chunks(path):
            selected,bx,by=_selected(x,y,classification,withheld,scale,offset)
            keys=(bx-min_bx)*span_y+(by-min_by)
            counts+=np.bincount(keys,minlength=bin_count)
            inspected+=len(x);retained+=len(selected)
        if inspected!=header['point_count'] or retained<=0:
            raise ValueError('LiDAR index point accounting failed')
        offsets=np.empty(bin_count+1,dtype=np.int64);offsets[0]=0
        np.cumsum(counts,out=offsets[1:])
        destination.mkdir()
        coordinates=np.lib.format.open_memmap(destination/'coordinates.npy',mode='w+',dtype=np.int32,shape=(retained,3))
        classification_out=np.lib.format.open_memmap(destination/'classification.npy',mode='w+',dtype=np.uint8,shape=(retained,))
        source_indices=np.lib.format.open_memmap(destination/'source-index.npy',mode='w+',dtype=np.uint32,shape=(retained,))
        cursors=offsets[:-1].copy()
        for x,y,z,classification,withheld,start,scale,offset in _chunks(path):
            selected,bx,by=_selected(x,y,classification,withheld,scale,offset)
            keys=(bx-min_bx)*span_y+(by-min_by)
            order=np.argsort(keys,kind='stable');ordered_keys=keys[order]
            unique,starts,amounts=np.unique(ordered_keys,return_index=True,return_counts=True)
            for key,position,amount in zip(unique,starts,amounts):
                local=order[position:position+amount];source=selected[local]
                target=int(cursors[key]);end=target+len(source);cursors[key]=end
                coordinates[target:end,0]=x[source]
                coordinates[target:end,1]=y[source]
                coordinates[target:end,2]=z[source]
                classification_out[target:end]=classification[source]
                source_indices[target:end]=start+source
        if not np.array_equal(cursors,offsets[1:]):
            raise ValueError('LiDAR index bin accounting failed')
        coordinates.flush();classification_out.flush();source_indices.flush()
        del coordinates,classification_out,source_indices
        np.save(destination/'bin-offsets.npy',offsets,allow_pickle=False)
        files={name:{'bytes':(destination/name).stat().st_size}
               for name in ('coordinates.npy','classification.npy','source-index.npy','bin-offsets.npy')}
        manifest={'schema':'earthcraft-cook-lidar-spatial-v1','asset_id':asset['id'],
            'source_identity':_source_identity(path,record),'source_path':str(path.resolve()),
            'source_points':inspected,'retained_points':retained,
            'retained_classes':RETAINED_CLASSES.tolist(),'withheld_retained':False,
            'bin_size_native':BIN_SIZE_NATIVE,'min_bin_xy':[min_bx,min_by],
            'span_xy':[span_x,span_y],'bin_count':bin_count,
            'native_horizontal_crs':'EPSG:6455','coordinate_storage':'LAS scaled integers',
            'scales':header['scales'],'offsets':header['offsets'],'files':files,
            'original_preserved':True,'inference_used':False,'interpolation':False,
            'purpose':'Rebuildable spatial acceleration for optional detail refinement'}
        (destination/'manifest.json').write_text(json.dumps(manifest,indent=2))

    def query(self,index,bounds):
        index=Path(index);manifest=_validate(index)
        lo_x,lo_y,hi_x,hi_y=map(float,bounds)
        min_bx,min_by=manifest['min_bin_xy'];span_x,span_y=manifest['span_xy']
        bx0=max(0,int(np.floor(lo_x/BIN_SIZE_NATIVE))-min_bx)
        bx1=min(span_x-1,int(np.floor(hi_x/BIN_SIZE_NATIVE))-min_bx)
        by0=max(0,int(np.floor(lo_y/BIN_SIZE_NATIVE))-min_by)
        by1=min(span_y-1,int(np.floor(hi_y/BIN_SIZE_NATIVE))-min_by)
        if bx1<bx0 or by1<by0:
            return {'xyz':np.empty((0,3),np.float64),'classification':np.empty(0,np.uint8),
                    'source_index':np.empty(0,np.uint32),'candidate_points':0}
        offsets=np.load(index/'bin-offsets.npy',mmap_mode='r',allow_pickle=False)
        ranges=[]
        for bx in range(bx0,bx1+1):
            for by in range(by0,by1+1):
                key=bx*span_y+by;start,end=int(offsets[key]),int(offsets[key+1])
                if end>start:ranges.append(np.arange(start,end,dtype=np.int64))
        if not ranges:
            return {'xyz':np.empty((0,3),np.float64),'classification':np.empty(0,np.uint8),
                    'source_index':np.empty(0,np.uint32),'candidate_points':0}
        rows=np.concatenate(ranges);coordinates=np.load(index/'coordinates.npy',mmap_mode='r',allow_pickle=False)
        scale=np.asarray(manifest['scales']);offset=np.asarray(manifest['offsets'])
        xyz=np.asarray(coordinates[rows],dtype=np.float64)*scale+offset
        selected=(xyz[:,0]>=lo_x)&(xyz[:,0]<=hi_x)&(xyz[:,1]>=lo_y)&(xyz[:,1]<=hi_y)
        rows=rows[selected];xyz=xyz[selected]
        source=np.asarray(np.load(index/'source-index.npy',mmap_mode='r',allow_pickle=False)[rows])
        order=np.argsort(source,kind='stable')
        labels=np.asarray(np.load(index/'classification.npy',mmap_mode='r',allow_pickle=False)[rows])
        return {'xyz':xyz[order],'classification':labels[order],
                'source_index':source[order],'candidate_points':int(sum(len(r) for r in ranges))}


def crop_indexed_sources(indexer,items,meta,output,max_points=20_000_000):
    """Create the existing verified tile point interface from persistent bins."""
    output=Path(output);size=meta['size'];west,north=meta['west'],meta['north']
    if output.exists():raise FileExistsError(output)
    extent=box(west,north-size,west+size,north)
    projection=Transformer.from_crs(6455,meta['crs'],always_xy=True)
    inverse=Transformer.from_crs(meta['crs'],6455,always_xy=True)
    native_extent=transform(inverse.transform,extent.segmentize(16)).buffer(.01)
    kept=[];coverage=[];sources=[];total=0;candidates=0
    for index,raw_path,record,asset in items:
        geometry=shape(asset['native_geometry'])
        covered=transform(projection.transform,geometry.segmentize(100)).intersection(extent)
        if covered.area<=0:continue
        points=indexer.query(index,native_extent.bounds);candidates+=points['candidate_points']
        xyz=points['xyz'];xx,yy=projection.transform(xyz[:,0],xyz[:,1]) if len(xyz) else (np.empty(0),np.empty(0))
        valid=(xx>=west)&(xx<west+size)&(yy>north-size)&(yy<=north)
        if valid.any():
            values=np.column_stack((xx[valid],yy[valid],xyz[valid,2]*(1200/3937)))
            total+=len(values)
            if total>max_points:raise ValueError('Indexed point crop memory budget exceeded')
            kept.append({'xyz':values,'classification':points['classification'][valid],
                         'withheld':np.zeros(len(values),np.uint8)})
        coverage.append(covered);sources.append(dict(record,source_las=str(Path(raw_path).resolve()),
            spatial_index=str(Path(index).resolve()),survey_id=asset['id']))
    covered=unary_union(coverage)
    if not coverage or covered.area/extent.area<.999999:
        raise ValueError('Missing acquired source coverage; no geometry fallback')
    if not kept:raise ValueError('No retained observed returns in tile')
    arrays={key:np.concatenate([part[key] for part in kept]) for key in kept[0]}
    output.mkdir(parents=True);np.savez(output/'points.npz',**arrays)
    with (output/'points.npz').open('rb') as stream:
        digest=hashlib.file_digest(stream,'sha256').hexdigest()
    labels,counts=np.unique(arrays['classification'],return_counts=True)
    report={'sources':sources,'output_horizontal_crs':meta['crs'],
        'grid':{key:meta[key] for key in ('crs','west','north','size')},
        'coverage_geometry':mapping(covered),'aoi_fraction_covered':covered.area/extent.area,
        'coverage_role':'Union of acquired publisher survey polygons clipped to tile',
        'crop_point_count':total,'indexed_candidate_points':candidates,
        'class_counts':dict(zip(map(str,labels),map(int,counts))),
        'native_horizontal_crs':'EPSG:6455','vertical_datum':'NAVD88 Geoid2018',
        'vertical_unit_conversion':1200/3937,'points_sha256':digest,
        'working_container_compression':'stored','persistent_spatial_index':True,
        'inference_used':False,'interpolation':False,'original_preserved':True}
    (output/'manifest.json').write_text(json.dumps(report,indent=2));return report
