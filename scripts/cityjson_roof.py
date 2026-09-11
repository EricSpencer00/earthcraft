"""Strict, small CityJSONSeq RoofSurface-to-metre-grid adapter."""
import hashlib
import json
from pathlib import Path

import numpy as np
import shapely
from shapely import intersects_xy
from shapely.geometry import Polygon


EMPTY=np.iinfo(np.int32).min


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rings(boundaries):
    """Roofer LoD2.2 Solid has exactly one shell; return its face rings."""
    if not isinstance(boundaries,list) or len(boundaries)!=1:raise ValueError('Expected one Solid shell')
    return boundaries[0]


def _plane(points,tolerance):
    if not np.isfinite(points).all():raise ValueError('Non-finite RoofSurface')
    origin=points.mean(axis=0);_,singular,vectors=np.linalg.svd(points-origin,full_matrices=False)
    if singular[1]<1e-10:raise ValueError('Collinear RoofSurface')
    normal=vectors[-1]
    if abs(normal[2])<1e-10:raise ValueError('Vertical RoofSurface')
    distance=np.dot(points-origin,normal)
    if np.abs(distance).max()>tolerance:raise ValueError('Non-planar RoofSurface')
    return -normal[0]/normal[2],-normal[1]/normal[2],origin


def _rasterize_roof(cityjsonseq,feature_id,meta,vertical_offset):
    """Return (top:int32[z,x], supported:bool, report) for one Roofer feature.

    Empty cells are ``EMPTY``.  ``meta`` must supply west, north, size, crs;
    caller-provided CRS equality is deliberate—this adapter never reprojects.
    """
    path=Path(cityjsonseq);lines=[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    headers=[x for x in lines if x.get('type')=='CityJSON']
    features=[x for x in lines if x.get('type')=='CityJSONFeature' and str(x.get('id'))==str(feature_id)]
    if len(headers)!=1 or len(features)!=1:raise ValueError('Missing or ambiguous CityJSON header/feature')
    header,feature=headers[0],features[0]
    report={'source':str(path),'source_sha256':sha(path),'feature_id':str(feature_id),'roof_faces':0,'raster_cells':0,
            'crs':meta['crs'],'vertical_offset':vertical_offset,
            'adapter_sha256':sha(Path(__file__)),'numpy_version':np.__version__,'shapely_version':shapely.__version__}
    top=np.full((meta['size'],meta['size']),EMPTY,np.int32)
    unsupported=lambda reason:(top,np.zeros_like(top,dtype=bool),{**report,'reason':reason})
    if not header or not feature:return unsupported('Missing CityJSON header or requested feature')
    reference=header.get('metadata',{}).get('referenceSystem')
    if reference is not None and reference!=meta['crs']:return unsupported('CityJSON CRS mismatch')
    transform=header.get('transform',{});scale=np.asarray(transform.get('scale',[]),float);translate=np.asarray(transform.get('translate',[]),float)
    if scale.shape!=(3,) or translate.shape!=(3,) or not np.isfinite(scale).all() or not np.isfinite(translate).all() or (scale<=0).any():
        return unsupported('Missing or non-finite CityJSON transform')
    report['planarity_tolerance_m']=float(np.max(np.abs(scale)))
    vertices=np.asarray(feature.get('vertices',[]),float)*scale+translate
    if vertices.ndim!=2 or vertices.shape[1]!=3 or not np.isfinite(vertices).all():
        raise ValueError('Invalid CityJSON vertices')
    candidates=[]
    for obj in feature.get('CityObjects',{}).values():
        for geometry in obj.get('geometry',[]):
            if geometry.get('type')!='Solid' or str(geometry.get('lod'))!='2.2':continue
            semantics=geometry.get('semantics',{});surfaces=semantics.get('surfaces',[]);values=semantics.get('values',[])
            if len(values)!=1:raise ValueError('Ambiguous RoofSurface semantic groups')
            faces=_rings(geometry.get('boundaries'))
            if len(values[0])!=len(faces):raise ValueError('RoofSurface semantic count mismatch')
            roof=[]
            for rings,value in zip(faces,values[0]):
                if type(value) is not int or not 0<=value<len(surfaces):raise ValueError('Invalid RoofSurface semantic index')
                if surfaces[value].get('type')!='RoofSurface':continue
                if not rings or any(not ring for ring in rings):raise ValueError('Empty RoofSurface ring')
                if any(type(index) is not int or not 0<=index<len(vertices) for ring in rings for index in ring):
                    raise ValueError('Invalid RoofSurface vertex index')
                try:coords=[vertices[np.asarray(ring,int)] for ring in rings]
                except (IndexError,TypeError):raise ValueError('Invalid RoofSurface vertex index')
                polygon=Polygon(coords[0][:,:2],holes=[ring[:,:2] for ring in coords[1:]])
                if not polygon.is_valid or polygon.area<=0:raise ValueError('Invalid/self-intersecting RoofSurface')
                slope_x,slope_y,origin=_plane(np.vstack(coords),report['planarity_tolerance_m'])
                roof.append((polygon,slope_x,slope_y,origin))
            if roof:candidates.append(roof)
    if len(candidates)!=1:return unsupported('Expected exactly one LoD2.2 Solid with RoofSurface semantics')
    xx,zz=np.meshgrid(meta['west']+np.arange(meta['size'])+.5,meta['north']-np.arange(meta['size'])-.5)
    for polygon,sx,sy,origin in candidates[0]:
        mask=intersects_xy(polygon,xx,zz)
        height=origin[2]+sx*(xx-origin[0])+sy*(zz-origin[1])+vertical_offset
        selected=height[mask]
        if not np.isfinite(selected).all() or (selected<EMPTY+1).any() or (selected>np.iinfo(np.int32).max).any():
            raise ValueError('RoofSurface height cannot be represented as int32')
        values=np.floor(selected).astype(np.int32)
        top[mask]=np.maximum(top[mask],values);report['roof_faces']+=1
    report['raster_cells']=int((top!=EMPTY).sum())
    return top,top!=EMPTY,report


def rasterize_roof(cityjsonseq,feature_id,meta,vertical_offset):
    """Strict public adapter returning ``(top, supported, evidence)``.

    Unsupported semantics and malformed geometry return an all-false support
    mask so an optional caller override cannot accidentally become a fallback.
    """
    try:return _rasterize_roof(cityjsonseq,feature_id,meta,vertical_offset)
    except ValueError as error:
        top=np.full((meta['size'],meta['size']),EMPTY,np.int32)
        return top,np.zeros_like(top,dtype=bool),{'source':str(cityjsonseq),'source_sha256':sha(cityjsonseq),'feature_id':str(feature_id),
            'crs':meta['crs'],'vertical_offset':vertical_offset,'adapter_sha256':sha(Path(__file__)),
            'numpy_version':np.__version__,'shapely_version':shapely.__version__,'reason':str(error)}


rasterize=rasterize_roof
