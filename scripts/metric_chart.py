"""Bounded metre charts, with explicit geographic round trips and scale checks."""
import math
import numpy as np
from pyproj import CRS, Geod, Transformer


def chart(lon, lat, size):
    if not all(math.isfinite(v) for v in (lon,lat,size)):
        raise ValueError('Finite location and extent required')
    if not -180<=lon<180 or not -89<=lat<=89:
        raise ValueError('Location outside supported local-chart domain')
    if not isinstance(size,int) or size<16 or size>1024 or size%16:
        raise ValueError('Bounded chart must be 16–1024 m, aligned to 16 m chunks')
    crs=CRS.from_proj4(f'+proj=tmerc +lat_0={lat} +lon_0={lon} +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs')
    inverse=Transformer.from_crs(crs,4326,always_xy=True)
    geod=Geod(ellps='WGS84');distances=[]
    west,north=-size//2,size//2
    for x,y in ((west,north),(west+size-1,north),(west,north-size+1),(west+size-1,north-size+1)):
        a,b=inverse.transform(x,y)
        for dx,dy in ((1,0),(0,-1)):
            c,d=inverse.transform(x+dx,y+dy)
            distances.append(geod.inv(a,b,c,d)[2])
    if max(abs(d-1) for d in distances)>1e-5:
        raise ValueError('Chart exceeds numerical ground-scale tolerance')
    return {'size':size,'west':west,'north':north,'crs':crs.to_wkt(),
        'projection_origin':[lat,lon],'axes':'east +X, south +Z, elevation +Y',
        'metres_per_block':1,'cell_ground_distances_m':distances,
        'global_continuity':'Separate local metric chart; not an undistorted global plane'}


def geographic_centres(meta):
    row,col=np.mgrid[:meta['size'],:meta['size']]
    return Transformer.from_crs(meta['crs'],4326,always_xy=True).transform(
        meta['west']+col+.5,meta['north']-row-.5)
