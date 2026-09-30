"""Mapped building association for unclassified returns, never inferred LiDAR.

Footprints constrain class-1 association; a paired measured DSM/DTM constrains
height. Provider-classified buildings are retained even outside these masks.
Trees or clutter can still occur inside mapped footprints; provenance says so.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from affine import Affine
from pyproj import Transformer
from rasterio.features import rasterize
from shapely import make_valid
from shapely.geometry import Polygon
from shapely.ops import transform

from osm_json_to_kml import building_tag


def building_mask(source,grid):
    source=Path(source);project=Transformer.from_crs(4326,grid['crs'],always_xy=True)
    def local(lon,lat,z=None):
        x,y=project.transform(lon,lat)
        return np.asarray(x)-grid['west'],grid['north']-np.asarray(y)
    geometries=[]
    for way in json.loads((source/'osm-ways.json').read_text()):
        if way['closed'] and building_tag(way['tags']) and len(way['coordinates'])>=4:
            geometries.append(transform(local,make_valid(Polygon(way['coordinates']))))
    for feature in json.loads((source/'cook-buildings-2022.json').read_text())['features']:
        geometry=None
        for ring in feature['geometry']['rings']:
            polygon=make_valid(Polygon(ring))
            geometry=polygon if geometry is None else geometry.symmetric_difference(polygon)
        if geometry is not None:geometries.append(transform(local,geometry))
    from regional_footprints import load_shapes
    def metric_local(x,y,z=None):return np.asarray(x)-grid['west'],grid['north']-np.asarray(y)
    geometries.extend(transform(metric_local,g) for _,g in load_shapes(source,grid))
    geometries=[geometry for geometry in geometries if not geometry.is_empty]
    size=grid['size']
    return (rasterize([(g,1) for g in geometries],out_shape=(size,size),transform=Affine.identity()).astype(bool)
            if geometries else np.zeros((size,size),bool))


def context(source,grid,surfaces):
    result={key:np.array(surfaces[key],copy=True) for key in ('dtm','dsm','valid')}
    result['roof_mask']=building_mask(source,grid)&result['valid']&(result['dsm']>result['dtm']+2)
    return result


def identity(context):
    return {key:hashlib.sha256(np.ascontiguousarray(context[key]).tobytes()).hexdigest()
            for key in ('dtm','dsm','valid','roof_mask')}


def admit(xyz,labels,grid,context=None):
    xyz=np.asarray(xyz);labels=np.asarray(labels)
    if xyz.shape!=(len(labels),3) or not np.isfinite(xyz).all():raise ValueError('Finite labeled Nx3 points required')
    west,north,size=grid['west'],grid['north'],grid['size']
    inside=(xyz[:,0]>=west)&(xyz[:,0]<west+size)&(xyz[:,1]>north-size)&(xyz[:,1]<=north)
    classified=inside&(labels==6);associated=np.zeros(len(xyz),bool)
    if context is not None:
        if any(np.shape(context[key])!=(size,size) for key in ('dtm','dsm','valid','roof_mask')):
            raise ValueError('Building association uses another surface grid')
        selected=np.flatnonzero(inside&(labels==1))
        rows=np.floor(north-xyz[selected,1]).astype(int);cols=np.floor(xyz[selected,0]-west).astype(int)
        supported=(context['valid'][rows,cols]&context['roof_mask'][rows,cols]&
            (xyz[selected,2]>context['dtm'][rows,cols]+2)&(xyz[selected,2]<=context['dsm'][rows,cols]+2))
        associated[selected[supported]]=True
    return classified|associated,classified,associated
