"""One metre-valued coordinate/height frame shared by every tile of a city."""
import math
import numpy as np
from pyproj import CRS


def validate_frame(frame):
    crs=CRS.from_user_input(frame['crs'])
    if not crs.is_projected or len(crs.axis_info)!=2 or any(abs(a.unit_conversion_factor-1)>1e-12 for a in crs.axis_info):
        raise ValueError('City coordinate system must be projected in metres, not degrees or feet')
    values=[frame[k] for k in ('west','north','vertical_offset_m')]
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
        raise ValueError('Finite city origin and vertical offset required')
    height=frame['dimension_height'];bottom=frame['dimension_min_y']
    if bottom!=-64 or type(height) is not int or not 16<=height<=2032 or height%16:
        raise ValueError('Unsupported common vertical dimension')
    return crs


def tile_layout(meta, elevation, frame, maximum_y=None):
    crs=validate_frame(frame)
    if not CRS.from_user_input(meta['crs']).equals(crs):
        raise ValueError('Tile and city CRS differ; reproject sources before building')
    size=meta['size']
    if type(size) is not int or not 16<=size<=512 or size%16:
        raise ValueError('City tiles must be 16..512 m and chunk aligned')
    dx=meta['west']-frame['west'];dz=frame['north']-meta['north']
    if any(not math.isfinite(v) or v%16 or abs(v)+size>=29_999_984 for v in (dx,dz)):
        raise ValueError('City tile must align with global Minecraft chunks and limits')
    height=frame['dimension_height'];bottom=frame['dimension_min_y']
    elevation=np.asarray(elevation)
    if elevation.shape!=(size,size) or not np.isfinite(elevation).all():
        raise ValueError('Finite complete terrain tile required')
    shifted=elevation+frame['vertical_offset_m']
    if shifted.min()<bottom+1 or shifted.max()>=bottom+height-2:
        raise ValueError('Terrain exceeds common city height; never rebase individual tiles')
    ground=np.floor(shifted).astype(np.int32)
    if maximum_y is not None and (not math.isfinite(maximum_y) or maximum_y>=bottom+height-2):
        raise ValueError('Building exceeds common city height; never compress or clip')
    return frame['vertical_offset_m'],ground,height,[int(dx),int(dz)]
