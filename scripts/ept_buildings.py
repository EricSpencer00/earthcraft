"""Crop every intersecting EPT level, retaining full-density classified returns.

The EPT octree distributes points across levels. Reading only leaves or stopping
at a visual resolution would lose observations, so neither is permitted here.
Vertical metre units are checked against the paired measured ground survey.
"""
import io
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import laspy
import numpy as np
from pyproj import Transformer

from point_geometry import voxelize
from regional_scans import frozen_get
from region_expansion import atomic, sha

_CHECKED_INDEXES = {}


def indexed_node(path, count, inverse, grid, reserve_bytes=151*2**30):
    """Decode a shared LAZ node once, then query only local 256 m bins."""
    frame_hash=hashlib.sha256(grid['crs'].encode()).hexdigest()[:16]
    root=path.parent.parent/'indexes'/frame_hash/path.stem
    root.parent.mkdir(parents=True,exist_ok=True)
    with (root.parent/(root.name+'.lock')).open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if not (root/'manifest.json').exists():
            if shutil.disk_usage(path).free<reserve_bytes:raise ValueError('Preserve regional scan/index storage reserve')
            if root.exists():root.rename(root.with_name(root.name+'.incomplete-'+str(time.time_ns())))
            root.mkdir()
            points=laspy.read(path)
            if len(points)!=count:raise ValueError('Point payload count differs from EPT hierarchy')
            labels=np.asarray(points.classification)
            selected=(np.asarray(points.withheld)==0)&np.isin(labels,[2,6])
            x,y=inverse.transform(np.asarray(points.x)[selected],np.asarray(points.y)[selected])
            xyz=np.column_stack((x,y,np.asarray(points.z)[selected]))
            labels=labels[selected]
            bins=np.floor(xyz[:,:2]/256).astype(np.int64)
            order=np.lexsort((bins[:,1],bins[:,0]))
            bins=bins[order];xyz=xyz[order];labels=labels[order]
            split=np.r_[0,np.flatnonzero(np.any(bins[1:]!=bins[:-1],axis=1))+1,len(bins)] if len(bins) else np.array([0])
            np.save(root/'coordinates.npy',xyz);np.save(root/'classification.npy',labels)
            entries={f'{int(bins[a,0])},{int(bins[a,1])}':[int(a),int(b)] for a,b in zip(split[:-1],split[1:])}
            manifest={'source_sha256':sha(path),'original_points':count,'crs':grid['crs'],
                'retained_classes':[2,6],'retained_points':len(xyz),'bins':entries,
                'files':{name:sha(root/name) for name in ('coordinates.npy','classification.npy')}}
            atomic(root/'manifest.json',json.dumps(manifest).encode())
        manifest=json.loads((root/'manifest.json').read_text())
        if manifest['original_points']!=count or manifest['crs']!=grid['crs'] or manifest['source_sha256']!=sha(path):
            raise ValueError('Shared EPT node index uses another source/frame')
        identity=tuple((name,(root/name).stat().st_size,(root/name).stat().st_mtime_ns,(root/name).stat().st_ctime_ns)
                       for name in manifest['files'])
        if _CHECKED_INDEXES.get(str(root))!=identity:
            if any(sha(root/name)!=checksum for name,checksum in manifest['files'].items()):
                raise ValueError('Shared EPT node coordinates changed')
            _CHECKED_INDEXES[str(root)]=identity
        xyz=np.load(root/'coordinates.npy',mmap_mode='r');labels=np.load(root/'classification.npy',mmap_mode='r')
        positions=[]
        for bx in range(int(np.floor(grid['west']/256)),int(np.floor((grid['west']+grid['size'])/256))+1):
            for by in range(int(np.floor((grid['north']-grid['size'])/256)),int(np.floor(grid['north']/256))+1):
                if (entry:=manifest['bins'].get(f'{bx},{by}')) is not None:positions.append(entry)
        arrays=[xyz[a:b] for a,b in positions];classes=[labels[a:b] for a,b in positions]
        return (np.concatenate(arrays) if arrays else np.empty((0,3)),
                np.concatenate(classes) if classes else np.empty(0,np.uint8))


def node_bounds(bounds, key):
    depth, x, y, z = map(int, key.split('-'))
    if not 0 <= depth <= 30 or any(v < 0 or v >= 2**depth for v in (x, y, z)):
        raise ValueError('Invalid EPT octree address')
    step = (np.asarray(bounds[3:])-bounds[:3])/2**depth
    lower = np.asarray(bounds[:3])+step*np.array([x, y, z])
    return [*lower, *(lower+step)]


def overlaps(bounds, query):
    return not (bounds[3] < query[0] or bounds[0] > query[2]
                or bounds[4] < query[1] or bounds[1] > query[3])


def crop(project, grid, destination, cache, dtm, valid, max_nodes=512,
         ground_reference='paired LiDAR DTM'):
    destination, cache = Path(destination), Path(cache)/project
    destination.mkdir(parents=True, exist_ok=True)
    base = 'https://usgs-lidar-public.s3.amazonaws.com/'+project
    metadata = json.loads(frozen_get(base+'/ept.json', cache/'ept.json'))
    if metadata['dataType'] != 'laszip' or metadata['hierarchyType'] != 'json':
        raise ValueError('Only lossless LAZ/JSON EPT is supported')
    native = metadata['srs']['wkt']
    forward = Transformer.from_crs(grid['crs'], native, always_xy=True)
    inverse = Transformer.from_crs(native, grid['crs'], always_xy=True)
    size = grid['size']
    xx, yy = forward.transform([grid['west'],grid['west']+size]*2,
        [grid['north'],grid['north'],grid['north']-size,grid['north']-size])
    query = [min(xx)-2, min(yy)-2, max(xx)+2, max(yy)+2]
    visited, nodes = set(), {}
    def hierarchy(key):
        if key in visited:
            raise ValueError('EPT hierarchy loop')
        visited.add(key)
        if len(visited) > 1024:
            raise ValueError('EPT hierarchy crop exceeds budget')
        table = json.loads(frozen_get(base+'/ept-hierarchy/'+key+'.json', cache/'hierarchy'/(key+'.json')))
        for address, count in table.items():
            if not overlaps(node_bounds(metadata['bounds'], address), query):
                continue
            if count == -1:
                hierarchy(address)
            elif count > 0:
                nodes[address] = count
                if len(nodes) > max_nodes:
                    raise ValueError('Full-density EPT crop exceeds node budget; no downsampling fallback')
    hierarchy('0-0-0-0')
    pieces, ground_residuals, assets = [], [], []
    for key, expected in sorted(nodes.items()):
        path = cache/'data'/(key+'.laz')
        raw = frozen_get(base+'/ept-data/'+key+'.laz', path, limit=64*2**20)
        local, labels = indexed_node(path,expected,inverse,grid)
        x,y,z=local.T
        keep = ((x >= grid['west']) & (x < grid['west']+size) &
                (y > grid['north']-size) & (y <= grid['north']))
        ground = np.flatnonzero(keep & (labels == 2))
        if len(ground):
            rows = np.floor(grid['north']-y[ground]).astype(int)
            cols = np.floor(x[ground]-grid['west']).astype(int)
            selected = valid[rows, cols]
            ground_residuals.append(z[ground][selected]-dtm[rows,cols][selected])
        building = keep & (labels == 6)
        pieces.append(np.column_stack((x[building], y[building], z[building])))
        assets.append({'key': key, 'sha256': sha(path), 'points': expected,
                       'building_points_in_crop': int(building.sum())})
    residual = np.concatenate(ground_residuals) if ground_residuals else np.empty(0)
    if len(residual) < 20 or np.median(np.abs(residual)) > 2:
        raise ValueError('EPT Z does not agree with the selected metre ground reference: '+ground_reference)
    xyz = np.concatenate(pieces) if pieces else np.empty((0,3))
    np.savez_compressed(destination/'points.npz', xyz=xyz)
    record = {'schema': 'earthcraft-classified-ept-building-crop-v1', 'project': project,
        'output_horizontal_crs': grid['crs'], 'grid': {k: grid[k] for k in ('crs','west','north','size')},
        'points_sha256': sha(destination/'points.npz'), 'assets': assets, 'building_points': len(xyz),
        'all_intersecting_octree_levels_acquired': True, 'downsampling': False,
        'retained_classes': [6], 'vertical_reference': 'Original EPT Z; metre interpretation checked against '+ground_reference,
        'ground_reference':ground_reference,
        'ground_comparison_points': len(residual), 'median_absolute_ground_residual_m': float(np.median(np.abs(residual))),
        'independent_accuracy_verified': False, 'facade_colour_measured': False}
    atomic(destination/'manifest.json', json.dumps(record, indent=2).encode())
    return record


def load(source, meta, ground, offset):
    manifest = json.loads((source/'manifest.json').read_text())
    if manifest['schema'] not in ('earthcraft-classified-ept-building-crop-v1','earthcraft-classified-las-building-crop-v1') or manifest['grid'] != {
            key: meta[key] for key in ('crs','west','north','size')}:
        raise ValueError('Classified scan uses another world grid')
    if sha(source/'points.npz') != manifest['points_sha256']:
        raise ValueError('Frozen classified scan changed')
    with np.load(source/'points.npz') as arrays:
        cells = voxelize(arrays['xyz'], meta, offset)
    cells = cells[cells[:,1] > ground[cells[:,2],cells[:,0]]]
    if len(cells) > 2_000_000 or (len(cells) and (cells[:,1].min() < -64 or cells[:,1].max() >= 960)):
        raise ValueError('Classified scan voxel extent exceeds world budget')
    return cells, {'source_manifest': manifest, 'occupied_voxels': len(cells),
        'method': 'Direct provider class-6 building returns; no unclassified point association',
        'extrusion': False, 'llm_used': False}
