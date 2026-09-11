"""Anonymous AWS terrain tiles: bounded downloads, frozen cache, local metric output.

No AWS account, credentials, compute service, or Requester Pays bucket is used.
Pixel spacing and geographic accuracy are deliberately separate metadata.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import urllib.request
import numpy as np
from PIL import Image
import rasterio
from affine import Affine
from metric_chart import chart, geographic_centres

ROOT=Path(__file__).resolve().parents[1]
BUCKET='https://elevation-tiles-prod.s3.amazonaws.com'
REGISTRY='https://registry.opendata.aws/terrain-tiles/'
ATTRIBUTION='https://github.com/tilezen/joerd/blob/master/docs/attribution.md'
FORMAT='https://github.com/tilezen/joerd/blob/master/docs/formats.md'
SOURCE_DOC='https://github.com/tilezen/joerd/blob/master/docs/data-sources.md'


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def tile_pixels(lon,lat,zoom=15):
    if not 0<=zoom<=15 or not isinstance(zoom,int):raise ValueError('Unsupported zoom')
    lon,lat=np.asarray(lon),np.asarray(lat)
    if not np.isfinite(lon).all() or not np.isfinite(lat).all() or (abs(lat)>=85.05112878).any():
        raise ValueError('Web Mercator terrain excludes poles; no silent clipping')
    count=256*2**zoom
    x=np.floor((lon+180)/360*count).astype(np.int64)%count
    y=np.floor((1-np.arcsinh(np.tan(np.deg2rad(lat)))/np.pi)/2*count).astype(np.int64)
    return x,y


def decode(rgb):
    rgb=np.asarray(rgb)
    if rgb.ndim!=3 or rgb.shape[-1]!=3 or rgb.dtype!=np.uint8:
        raise ValueError('Terrarium requires uint8 RGB, not rendered map colours')
    channels=rgb.astype(np.float64)
    return channels[:,:,0]*256+channels[:,:,1]+channels[:,:,2]/256-32768


def fetch_tile(cache,zoom,x,y):
    cache.mkdir(parents=True,exist_ok=True)
    path=cache/f'{zoom}-{x}-{y}.png';manifest=path.with_suffix('.json')
    url=f'{BUCKET}/terrarium/{zoom}/{x}/{y}.png'
    if path.exists() or manifest.exists():
        record=json.loads(manifest.read_text())
        if record['url']!=url or digest(path)!=record['sha256']:
            raise ValueError('Frozen AWS tile cache mismatch')
        return path,record
    # Public HTTPS only; urllib does not load AWS credentials or sign requests.
    with urllib.request.urlopen(url,timeout=30) as response:
        if response.status!=200:raise ValueError('Incomplete terrain tile')
        if int(response.headers.get('Content-Length',0))>4*1024**2:
            raise ValueError('Tile exceeds 4 MiB budget')
        raw=response.read(4*1024**2+1)
        if len(raw)>4*1024**2:raise ValueError('Tile exceeds 4 MiB budget')
        headers={k:v for k,v in response.headers.items() if k.lower() in
            ('etag','last-modified','x-imagery-sources','x-amz-meta-x-imagery-sources','x-amz-meta-imagery-sources')}
    with Image.open(io.BytesIO(raw)) as image:
        if image.size!=(256,256) or image.mode!='RGB':raise ValueError('Unexpected Terrarium tile')
        image.verify()
    path.write_bytes(raw)
    record={'url':url,'sha256':digest(path),'bytes':len(raw),'headers':headers,
        'retrieved_utc':datetime.now(timezone.utc).isoformat(),'requester_pays':False,
        'aws_credentials_used':False,'registry':REGISTRY,'attribution_url':ATTRIBUTION,
        'capture_date':None,'capture_date_note':'Not supplied in tile response; HTTP modification date is not capture date.'}
    manifest.write_text(json.dumps(record,indent=2))
    return path,record


def prepare(lon,lat,size,destination,cache=None):
    destination=Path(destination);cache=Path(cache or ROOT/'runs/aws-terrain-cache')
    if destination.exists():raise FileExistsError(destination)
    if shutil.disk_usage(ROOT).free<22*1024**3:raise ValueError('Preserve 20 GiB free reserve plus working allowance')
    meta=chart(lon,lat,size);lons,lats=geographic_centres(meta)
    px,py=tile_pixels(lons,lats)
    pairs=np.unique(np.column_stack((px.ravel()//256,py.ravel()//256)),axis=0)
    if len(pairs)>32:raise ValueError('At most 32 AWS tiles per bounded run')
    elevation=np.full((size,size),np.nan);assets=[]
    for x,y in pairs:
        path,record=fetch_tile(cache,15,int(x),int(y))
        with Image.open(path) as image:tile=decode(np.asarray(image))
        mask=(px//256==x)&(py//256==y)
        elevation[mask]=tile[py[mask]%256,px[mask]%256]
        assets.append({**record,'cache_path':str(path.resolve())})
    if not np.isfinite(elevation).all() or (elevation<=-32768).any():
        raise ValueError('Missing terrain observations; refusing invented fill')
    destination.mkdir(parents=True)
    raster=destination/'elevation.tif'
    with rasterio.open(raster,'w',driver='GTiff',width=size,height=size,count=1,dtype='float32',
                       crs=meta['crs'],transform=Affine(1,0,meta['west'],0,-1,meta['north'])) as target:
        target.write(elevation.astype(np.float32),1)
    np.savez_compressed(destination/'rasters.npz',elevation=elevation.astype(np.float32),cover=np.zeros((size,size),np.uint8))
    # Explicitly empty acquired layers, not a claim that no buildings exist.
    (destination/'osm-ways.json').write_text('[]')
    (destination/'cook-buildings-2022.json').write_text('{"features":[]}')
    meta.update(elevation_raster='elevation.tif',elevation_sha256=digest(raster),
        elevation_range_m=[float(elevation.min()),float(elevation.max())],
        elevation_source='Mapzen Terrain Tiles on AWS',elevation_assets=assets,
        elevation_resolution='Zoom-15 tile sampling; upstream varies; output sampling does not establish accuracy',
        elevation_vertical_datum='Mixed upstream sources; per-tile vertical datum unverified',
        elevation_source_pixel_spacing_m_approx=float(np.cos(np.deg2rad(lat))*2*np.pi*6378137/(256*2**15)),
        elevation_source_native_resolution_m=None,
        resampling='Nearest source pixel at each metre-cell geographic centre; no smoothing or height compression',
        cover_source='Not acquired; neutral stone presentation',cover_native_resolution_m=None,
        cover_classes={'0':size*size},buildings_available=False,inference_used=False,
        attribution_url=ATTRIBUTION,source_documentation=SOURCE_DOC,format_documentation=FORMAT,
        source_accuracy_verified=False,
        missing_layers=['building observations','ground cover','facade imagery','water-surface semantics'],
        purpose='Terrain-only source proof, not a populated or complete geographic reconstruction')
    (destination/'sources.json').write_text(json.dumps(meta,indent=2))
    print(json.dumps({'source':str(destination),'tiles':len(assets),'cached_asset_bytes':sum(a['bytes'] for a in assets),
        'elevation_range_m':meta['elevation_range_m'],'sample_spacing_m_approx':meta['elevation_source_pixel_spacing_m_approx'],
        'accuracy_verified':False},indent=2))
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lon',type=float,required=True);parser.add_argument('--lat',type=float,required=True)
    parser.add_argument('--size',type=int,default=512);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();prepare(args.lon,args.lat,args.size,args.output)
