"""Exact integer-window crop of frozen metric sources, keeping the same chart."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import numpy as np
import rasterio
from rasterio.windows import Window


def crop(parent,destination,col,row,size):
    meta=json.loads((parent/'sources.json').read_text())
    if any(not isinstance(v,int) or v%16 for v in (col,row,size)) or size<16:
        raise ValueError('Crop must align with metre chunks')
    if min(col,row)<0 or max(col+size,row+size)>meta['size']:
        raise ValueError('Crop must be wholly inside acquired raster')
    if destination.exists():raise FileExistsError(destination)
    window=Window(col,row,size,size)
    destination.mkdir(parents=True)
    raster_name=meta.get('elevation_raster','usgs-elevation.tif')
    with rasterio.open(parent/raster_name) as source:
        profile=source.profile.copy();profile.update(width=size,height=size,transform=source.window_transform(window))
        with rasterio.open(destination/raster_name,'w',**profile) as target:target.write(source.read(window=window))
    with np.load(parent/'rasters.npz') as source:
        arrays={key:source[key][row:row+size,col:col+size] for key in source.files}
    np.savez_compressed(destination/'rasters.npz',**arrays)
    for name in ('osm-ways.json','cook-buildings-2022.json'):shutil.copyfile(parent/name,destination/name)
    meta.update(size=size,west=meta['west']+col,north=meta['north']-row)
    codes,counts=np.unique(arrays['cover'],return_counts=True)
    meta['cover_classes']=dict(zip(map(str,codes),map(int,counts)))
    meta['elevation_range_m']=[float(arrays['elevation'].min()),float(arrays['elevation'].max())]
    meta['elevation_sha256']=hashlib.sha256((destination/raster_name).read_bytes()).hexdigest()
    meta['derived_crop']={'parent':str(parent.resolve()),'window_xywh':[col,row,size,size],
        'resampling':'none','same_projection_origin':True,
        'parent_manifest_sha256':hashlib.sha256((parent/'sources.json').read_bytes()).hexdigest()}
    meta['cell_ground_distances_scope']='Inherited conservative parent-chart corner checks; crop is wholly contained'
    (destination/'sources.json').write_text(json.dumps(meta,indent=2))
    return destination


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--col',type=int,required=True);p.add_argument('--row',type=int,required=True);p.add_argument('--size',type=int,required=True)
    a=p.parse_args();print(crop(a.source,a.output,a.col,a.row,a.size))
