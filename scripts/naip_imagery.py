"""Acquire and normalize a bounded public-domain NAIP crop; no world edits or LLM.

The served orthophoto is not a facade image or a guaranteed true orthophoto of
building roofs. Keep dates, source IDs and missing observations explicit.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from pathlib import Path
import shutil
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image
from pyproj import Transformer
import rasterio
from pyproj import CRS
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling
from shapely.geometry import Polygon
from shapely.ops import transform as transform_geometry
from appearance_adapter import rank_capture, temporal_relation

SERVICE = 'https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer'
RIGHTS = SERVICE + '/info/iteminfo?f=pjson'


def source_crs(attrs):
    zone = re.fullmatch(r'([1-9]|[1-5][0-9]|60)N', attrs.get('projection_zone', ''))
    if attrs.get('projection_name') != 'UTM' or attrs.get('datum') != 'NAD83' or zone is None:
        raise ValueError('NAIP adapter requires an explicit NAD83 northern UTM source CRS')
    code = CRS.from_dict({'proj':'utm','zone':int(zone[1]),'datum':'NAD83','units':'m'}).to_epsg()
    if code is None: raise ValueError('Source projection has no unambiguous EPSG identifier')
    return code


def request(url, output, limit):
    if output.exists(): raise FileExistsError(output)
    with urllib.request.urlopen(url, timeout=60) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit: raise ValueError('NAIP response exceeds download budget')
    output.write_bytes(raw)
    return raw, {'url': url, 'file': output.name, 'bytes': len(raw),
                 'sha256': hashlib.sha256(raw).hexdigest()}


def choose_source(records, meta, preferred_year=2022, target_capture=None):
    """Select one full-coverage source, avoiding silent cross-date mosaics."""
    size = meta['size']; west = meta['west']; north = meta['north']
    area = Polygon([(west, north), (west+size, north), (west+size, north-size), (west, north-size)])
    project = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    choices = []
    for feature in records:
        attrs = feature['attributes']
        if (attrs.get('Category') != 1 or attrs.get('band_count') != 4 or
                attrs.get('resolution_units') != 'METER' or
                not 0 < attrs.get('resolution_value', 0) <= 1): continue
        geometry = None
        for ring in feature['geometry']['rings']:
            p = Polygon(ring)
            if not p.is_valid: raise ValueError('Invalid NAIP source footprint')
            geometry = p if geometry is None else geometry.symmetric_difference(p)
        geometry = transform_geometry(project.transform, geometry)
        if geometry.covers(area):
            if target_capture is not None:
                timestamp = attrs.get('acquisition_date')
                if not isinstance(timestamp, (int,float)) or not math.isfinite(timestamp):
                    continue
                try:
                    captured = datetime.fromtimestamp(timestamp/1000,timezone.utc).isoformat()
                    time_rank = rank_capture(captured, target_capture)
                except (ValueError, OverflowError, OSError):
                    continue
            else:
                time_rank = (abs(attrs['Year']-preferred_year),0)
            choices.append((*time_rank, attrs['resolution_value'], attrs['OBJECTID'], feature))
    if not choices: raise ValueError('No single <=1m four-band NAIP source covers the requested scene')
    return min(choices, key=lambda row: row[:-1])[-1]


def spectral_masks(bands, valid):
    """Conservative spectral flags, not calibrated land-cover or material labels."""
    red, green, blue, nir = bands.astype(np.float32)
    denom = nir + red
    ndvi = np.divide(nir-red, denom, out=np.full_like(red, np.nan), where=denom > 0)
    shadow = np.maximum.reduce([red, green, blue]) < 45
    vegetation = ndvi > .3
    usable = valid & ~shadow & ~vegetation & np.isfinite(ndvi)
    return ndvi, shadow & valid, vegetation & valid, usable


def normalize_crop(path, grid, max_pixels=2_000_000):
    size=grid['size']
    if type(size) is not int or not 16<=size<=512 or size%16:
        raise ValueError('Bounded metre grid required')
    if type(max_pixels) is not int or not 1<=max_pixels<=4_000_000:
        raise ValueError('Normalization pixel budget must stay within four million pixels')
    with rasterio.open(path) as raster:
        if raster.count!=4 or raster.crs is None or any(dtype!='uint8' for dtype in raster.dtypes):
            raise ValueError('Expected georeferenced four-band U8 imagery')
        if raster.width*raster.height>max_pixels:raise ValueError('Served raster exceeds pixel bound')
        bands=np.zeros((4,size,size),np.uint8)
        transform=from_origin(grid['west'],grid['north'],1,1)
        for index in range(4):
            reproject(raster.read(index+1),bands[index],src_transform=raster.transform,src_crs=raster.crs,
                      dst_transform=transform,dst_crs=grid['crs'],resampling=Resampling.nearest)
        valid=np.zeros((size,size),np.uint8)
        reproject(raster.read_masks(1),valid,src_transform=raster.transform,src_crs=raster.crs,
                  dst_transform=transform,dst_crs=grid['crs'],resampling=Resampling.nearest)
    valid=valid>0
    ndvi,shadow,vegetation,usable=spectral_masks(bands,valid)
    return {'bands':bands,'valid':valid,'ndvi':ndvi,'shadow_candidate':shadow,
            'vegetation_candidate':vegetation,'usable_candidate':usable}


def replay(imagery, output):
    """Verify cached sources and normalized arrays without making network requests."""
    imagery,output=Path(imagery),Path(output)
    if output.exists():raise FileExistsError(output)
    report=json.loads((imagery/'imagery.json').read_text())
    for asset in report['assets']:
        name=asset['file']
        if Path(name).name!=name:raise ValueError('Source asset leaves imagery directory')
        if hashlib.sha256((imagery/name).read_bytes()).hexdigest()!=asset['sha256']:
            raise ValueError('Frozen imagery source checksum changed')
    arrays=normalize_crop(imagery/'served-crop.tif',report['source_grid'],report.get('normalization_pixel_limit',2_000_000))
    with np.load(imagery/'metric-imagery.npz',allow_pickle=False) as original:
        if set(original.files)!=set(arrays):raise ValueError('Normalized imagery schema changed')
        for key,array in arrays.items():np.testing.assert_array_equal(array,original[key])
    output.mkdir(parents=True)
    np.savez_compressed(output/'metric-imagery.npz',**arrays)
    result={'status':'offline_normalization_replay_equal','arrays_compared':sorted(arrays),
            'source':str(imagery),'network_requests':0,'world_modified':False,
            'geographic_accuracy_verified':False,'llm_used':False}
    (output/'replay.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    return result


def acquire(source, output, preferred_year=2022, target_capture=None):
    source, output = Path(source), Path(output)
    if output.exists(): raise FileExistsError(output)
    if shutil.disk_usage(source).free < 21*2**30:
        raise ValueError('Preserve 20 GiB internal reserve plus working allowance')
    meta = json.loads((source/'sources.json').read_text())
    size = meta['size']
    if type(size) is not int or not 16 <= size <= 512 or size % 16:
        raise ValueError('NAIP crop must be a 16..512 metre chunk-aligned scene')
    west, north = meta['west'], meta['north']
    project = Transformer.from_crs(meta['crs'], 4326, always_xy=True)
    xx, yy = project.transform([west, west+size, west, west+size], [north, north, north-size, north-size])
    params = {'f':'json', 'where':'Category=1', 'geometry':','.join(map(str,[min(xx),min(yy),max(xx),max(yy)])),
              'geometryType':'esriGeometryEnvelope', 'inSR':4326, 'spatialRel':'esriSpatialRelIntersects',
              'outFields':'*', 'outSR':4326, 'returnGeometry':'true', 'resultRecordCount':50}
    output.mkdir(parents=True)
    assets = []
    raw, asset = request(RIGHTS, output/'rights.json', 2**20); assets.append(asset)
    rights = json.loads(raw)
    if 'public domain' not in (rights.get('description','')+rights.get('summary','')).lower():
        raise ValueError('USGS public-domain source declaration unavailable')
    raw, asset = request(SERVICE+'?f=pjson', output/'service.json', 2**20); assets.append(asset)
    service = json.loads(raw)
    if service.get('bandCount') != 4 or service.get('pixelType') != 'U8':
        raise ValueError('Unexpected NAIP bands or pixel encoding')
    raw, asset = request(SERVICE+'/query?'+urllib.parse.urlencode(params), output/'catalog.json', 2**20)
    assets.append(asset); catalog = json.loads(raw)
    if 'error' in catalog or catalog.get('exceededTransferLimit'):
        raise ValueError('NAIP catalog query failed or truncated')
    selected = choose_source(catalog['features'], meta, preferred_year, target_capture)
    attrs = selected['attributes']
    pixel = float(attrs['resolution_value']); crs = source_crs(attrs)
    transformer = Transformer.from_crs(meta['crs'], crs, always_xy=True)
    x, y = transformer.transform([west,west+size,west,west+size],[north,north,north-size,north-size])
    left = (math.floor(min(x)/pixel)-2)*pixel; top = (math.ceil(max(y)/pixel)+2)*pixel
    width = math.ceil((max(x)-left)/pixel)+2; height = math.ceil((top-min(y))/pixel)+2
    if width*height > 2_000_000: raise ValueError('NAIP served crop exceeds two million pixels')
    bounds = [left,top-height*pixel,left+width*pixel,top]
    params = {'f':'image','bbox':','.join(map(str,bounds)), 'bboxSR':crs, 'imageSR':crs,
              'size':f'{width},{height}', 'format':'tiff', 'pixelType':'U8', 'bandIds':'0,1,2,3',
              'renderingRule':json.dumps({'rasterFunction':'None'}), 'interpolation':'RSP_NearestNeighbor',
              'mosaicRule':json.dumps({'mosaicMethod':'esriMosaicLockRaster','lockRasterIds':[attrs['OBJECTID']]})}
    raw, asset = request(SERVICE+'/exportImage?'+urllib.parse.urlencode(params), output/'served-crop.tif', 10*2**20)
    assets.append(asset)
    with rasterio.open(output/'served-crop.tif') as raster:
        if raster.count != 4 or raster.width != width or raster.height != height or raster.crs.to_epsg() != crs:
            raise ValueError('Served NAIP crop changed projection, dimensions or bands')
        if not raster.transform.almost_equals(from_origin(left,top,pixel,pixel)):
            raise ValueError('Served NAIP crop changed its grid')
    arrays=normalize_crop(output/'served-crop.tif',meta)
    bands,valid=arrays['bands'],arrays['valid']
    shadow,vegetation,usable=(arrays[key] for key in ('shadow_candidate','vegetation_candidate','usable_candidate'))
    np.savez_compressed(output/'metric-imagery.npz',**arrays)
    Image.fromarray(bands[:3].transpose(1,2,0)).save(output/'metric-rgb.png')
    report = {'status':'source_crop_acquired_not_admitted_to_world',
              'source_grid':{key:meta[key] for key in ('crs','west','north','size')},
              'selected_source':selected,'capture_date_utc':datetime.fromtimestamp(attrs['acquisition_date']/1000,timezone.utc).isoformat(),
              'retrieved_utc':datetime.now(timezone.utc).isoformat(),'preferred_year':preferred_year,
              'target_capture':target_capture,
              'temporal_relation':temporal_relation(datetime.fromtimestamp(attrs['acquisition_date']/1000,timezone.utc).isoformat(), target_capture) if target_capture else None,
              'source_pixel_resolution_m':pixel, 'served_crop_crs':crs, 'served_crop_pixel_size_m':pixel,
              'source_band_order':['red','green','blue','near_infrared'],
              'processing':'USGS locked-raster served crop, rendering None; local nearest-neighbor sample at metre-grid cell centres',
              'attribution':rights.get('accessInformation'), 'license':'Public domain per USGS NAIP service description',
              'rights_url':RIGHTS,'assets':assets,'download_bytes':sum(a['bytes'] for a in assets),
              'valid_cells':int(valid.sum()),'total_cells':size*size,
              'spectral_candidates':{'shadow':int(shadow.sum()),'vegetation':int(vegetation.sum()),'usable':int(usable.sum())},
              'independent_alignment_verified':False,'world_modified':False,'llm_used':False,
              'material_routing_admitted':False,
              'limitations':[f'{attrs["Year"]} imagery may differ from source geometry dates; no temporal fusion assumed.',
                  'Orthophoto ground registration does not prove true-ortho rooftop or facade registration.',
                  'Source pixel size and output sampling are not geographic accuracy.',
                  'NDVI/brightness thresholds are diagnostic masks, not calibrated semantic labels.',
                  'Digital image bands are not calibrated reflectance; spectral flags require independent validation.',
                  'No imagery-to-wall projection, autofill, invented windows or shadow removal.']}
    (output/'imagery.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:report[k] for k in ('status','capture_date_utc','source_pixel_resolution_m','download_bytes','valid_cells','spectral_candidates')},indent=2))
    return report


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--source',type=Path);mode.add_argument('--replay',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--preferred-year',type=int,default=2022)
    p.add_argument('--target-capture',help='Actual target capture ISO date/month/year; not dataset publication year')
    a=p.parse_args()
    if a.replay:replay(a.replay,a.output)
    else:acquire(a.source,a.output,a.preferred_year,a.target_capture)
