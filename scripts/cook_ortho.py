"""Freeze a native-resolution Cook 2022 orthophoto crop for local appearance QA.

No inference, geometry changes or facade projection. Keep the county's dated
source, exact metre chart, attribution and no-redistribution limitation explicit.
"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import urllib.parse

import numpy as np
from PIL import Image
from pyproj import CRS,Transformer
import rasterio
from rasterio.transform import from_origin
from naip_imagery import request,normalize_crop

SERVICE='https://gis.cookcountyil.gov/imagery/rest/services/CookOrtho2022/ImageServer'
OPEN_DATA='https://www.cookcountyil.gov/news/county-eliminates-charge-gis-data'
TERMS='https://www.cookcountyil.gov/terms-use'


def native_grid(meta):
    size=meta['size']
    if type(size) is not int or not 16<=size<=256 or size%16:
        raise ValueError('Native-resolution appearance jobs use 16..256 m tiles')
    crs=CRS.from_user_input(meta['crs'])
    if not crs.is_projected or any(abs(a.unit_conversion_factor-1)>1e-12 for a in crs.axis_info):
        raise ValueError('Metre-valued target chart required')
    project=Transformer.from_crs(crs,6455,always_xy=True)
    w,n=meta['west'],meta['north']
    x,y=project.transform([w,w+size,w,w+size],[n,n,n-size,n-size])
    pixel=.5
    left=(math.floor(min(x)/pixel)-2)*pixel;top=(math.ceil(max(y)/pixel)+2)*pixel
    width=math.ceil((max(x)-left)/pixel)+2;height=math.ceil((top-min(y))/pixel)+2
    if width*height>4_000_000:raise ValueError('Native crop exceeds bounded image size')
    return [left,top-height*pixel,left+width*pixel,top],width,height


def acquire(source,output):
    source,output=Path(source),Path(output)
    if output.exists():raise FileExistsError(output)
    meta=json.loads((source/'sources.json').read_text())
    bounds,width,height=native_grid(meta);output.mkdir(parents=True)
    assets=[]
    def fetch(url,name,limit):
        raw,asset=request(url,output/name,limit);assets.append(asset);return raw
    service=json.loads(fetch(SERVICE+'?f=pjson','service.json',2**20))
    item=json.loads(fetch(SERVICE+'/info/iteminfo?f=pjson','item.json',2**20))
    fetch(SERVICE+'/info/metadata','metadata.xml',2**20)
    fetch(OPEN_DATA,'open-data-policy.html',2**20)
    fetch(TERMS,'county-terms.html',2**20)
    if service.get('bandCount')!=4 or service.get('pixelType')!='U8' or service.get('pixelSizeX')!=.5 or service.get('pixelSizeY')!=.5:
        raise ValueError('Cook 2022 source bands or half-foot resolution changed')
    if 'flown in 2022' not in item.get('summary','') or '6 inch' not in item.get('summary',''):
        raise ValueError('Source capture-year declaration changed')
    source_crs=CRS.from_wkt(service['spatialReference']['wkt']).to_2d()
    if not source_crs.equals(CRS.from_epsg(6455)):
        raise ValueError('Source horizontal datum/projection changed')
    query={'where':'Category=1','geometry':','.join(map(str,bounds)),
        'geometryType':'esriGeometryEnvelope','inSR':6455,'outSR':6455,
        'spatialRel':'esriSpatialRelIntersects','outFields':'*','returnGeometry':'true','f':'json'}
    catalog=json.loads(fetch(SERVICE+'/query?'+urllib.parse.urlencode(query),'catalog.json',2**20))
    if catalog.get('error') or catalog.get('exceededTransferLimit') or not 1<=len(catalog.get('features',[]))<=4:
        raise ValueError('Imagery catalog failed, truncated, or exceeds four source tiles')
    ids=sorted(f['attributes']['OBJECTID'] for f in catalog['features'])
    params={'f':'image','bbox':','.join(map(str,bounds)),'bboxSR':6455,'imageSR':6455,
        'size':f'{width},{height}','format':'tiff','pixelType':'U8','bandIds':'0,1,2,3',
        'renderingRule':json.dumps({'rasterFunction':'None'}),'interpolation':'RSP_NearestNeighbor',
        'mosaicRule':json.dumps({'mosaicMethod':'esriMosaicLockRaster','lockRasterIds':ids})}
    fetch(SERVICE+'/exportImage?'+urllib.parse.urlencode(params),'served-crop.tif',16*2**20)
    with rasterio.open(output/'served-crop.tif') as raster:
        if raster.count!=4 or raster.width!=width or raster.height!=height or any(t!='uint8' for t in raster.dtypes):
            raise ValueError('Served image schema changed')
        if not CRS.from_user_input(raster.crs).to_2d().equals(CRS.from_epsg(6455)):
            raise ValueError('Served image horizontal CRS changed')
        if not raster.transform.almost_equals(from_origin(bounds[0],bounds[3],.5,.5)):
            raise ValueError('Served image grid changed')
    arrays=normalize_crop(output/'served-crop.tif',meta,max_pixels=4_000_000)
    if not arrays['valid'].all():raise ValueError('Missing image observations in requested crop')
    np.savez_compressed(output/'metric-imagery.npz',**arrays)
    Image.fromarray(arrays['bands'][:3].transpose(1,2,0)).save(output/'metric-rgb.png')
    report={'status':'acquired_for_local_alignment_QA_not_applied_to_world',
        'source_grid':{k:meta[k] for k in ('crs','west','north','size')},
        'source_service':SERVICE,'source_raster_ids':ids,'source_catalog':catalog,
        'capture_interval':['2022-01-01','2022-12-31'],'capture_precision':'year only; exact flight dates not supplied by service',
        'publication_date':'2023-03-01','retrieved_utc':datetime.now(timezone.utc).isoformat(),
        'native_horizontal_crs':'EPSG:6455','native_pixel_size_survey_feet':.5,
        'source_pixel_resolution_m':.5*1200/3937,'target_pixel_size_m':1,
        'normalization_pixel_limit':4_000_000,
        'source_band_order':['red','green','blue','near_infrared'],
        'band_order_evidence':'County four-band RGBN orthophoto product specification; not calibrated reflectance',
        'processing':'Locked 2022 raster IDs, no rendering function, native half-foot crop; local nearest-neighbor metre sample',
        'rights_scope':'Private local noncommercial reconstruction research under county open-GIS release; no publication or redistribution cleared',
        'rights_evidence':[OPEN_DATA,TERMS,SERVICE+'/info/iteminfo'],
        'attribution':'Cook County Board of Commissioners; Cook County 2022 Aerial Imagery (Contract No. 2050-18294)',
        'assets':assets,'download_bytes':sum(a['bytes'] for a in assets),
        'normalized_sha256':hashlib.sha256((output/'metric-imagery.npz').read_bytes()).hexdigest(),
        'llm_used':False,'world_modified':False,'independent_alignment_verified':False,
        'limitations':['Orthophotos do not supply wall-facing colors or unseen details.',
            'A source year matching LiDAR is not exact date coincidence.',
            'Rooftop relief displacement, shadows, trees and image seams require visibility/registration checks.',
            'NDVI and brightness masks are diagnostic candidates, not calibrated material truth.']}
    (output/'imagery.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:report[k] for k in ['status','download_bytes','source_raster_ids','source_pixel_resolution_m']},indent=2))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();acquire(a.source,a.output)
