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
import urllib.error
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
import regional_cache

SERVICE = 'https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer'
RIGHTS = SERVICE + '/info/iteminfo?f=pjson'


def source_crs(attrs):
    zone = re.fullmatch(r'([1-9]|[1-5][0-9]|60)N', attrs.get('projection_zone', ''))
    if attrs.get('projection_name') != 'UTM' or attrs.get('datum') != 'NAD83' or zone is None:
        raise ValueError('NAIP adapter requires an explicit NAD83 northern UTM source CRS')
    code = CRS.from_dict({'proj':'utm','zone':int(zone[1]),'datum':'NAD83','units':'m'}).to_epsg()
    if code is None: raise ValueError('Source projection has no unambiguous EPSG identifier')
    return code


def request(url, output, limit, timeout=None):
    if output.exists(): raise FileExistsError(output)
    # Metadata answers in under a second when USGS is healthy; fail over to Planetary Computer fast.
    timeout = timeout or (120 if '/exportImage' in url else 20)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit: raise ValueError('NAIP response exceeds download budget')
    output.write_bytes(raw)
    return raw, {'url': url, 'file': output.name, 'bytes': len(raw),
                 'sha256': hashlib.sha256(raw).hexdigest()}


# Responses that never change between tiles (rights statement, service description) are fetched
# once per process; each tile still writes and hashes its own copy as provenance.
_STATIC = {}


def request_static(url, output, limit):
    if url not in _STATIC:
        _STATIC[url] = request(url, output, limit)
        return _STATIC[url]
    raw, asset = _STATIC[url]
    if output.exists(): raise FileExistsError(output)
    output.write_bytes(raw)
    return raw, {**asset, 'file': output.name}


# The USGS ImageServer renders each crop on request: 5-35 s for one tile, varying run to run
# (measured 2026-11-13), and it dominated tile time on the runners. The same USDA NAIP rasters
# are published as Cloud-Optimized GeoTIFFs on Microsoft Planetary Computer (keyless; an
# anonymous read token). When the capture the catalog selected is there, with the same date,
# pixel size and projection, the crop is read straight from it on the identical pixel grid.
PC_STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1/search'
PC_TOKEN = 'https://planetarycomputer.microsoft.com/api/sas/v1/token/naip'
_PC = {'token': None, 'expires': 0.0}


def pc_token():
    import time
    if not _PC['token'] or time.time() > _PC['expires']:
        with urllib.request.urlopen(PC_TOKEN, timeout=30) as response:
            _PC['token'] = json.loads(response.read(64 * 1024))['token']
        _PC['expires'] = time.time() + 40 * 60  # tokens last about an hour
    return _PC['token']


def cog_crop(attrs, crs, lonlat_bounds, left, top, width, height, pixel, output):
    """Read the selected capture's crop from its NAIP COG; returns the asset record or raises."""
    captured = datetime.fromtimestamp(attrs['acquisition_date'] / 1000, timezone.utc).date().isoformat()
    body = json.dumps({'collections': ['naip'], 'bbox': lonlat_bounds, 'limit': 20,
                       'datetime': f'{captured}T00:00:00Z/{captured}T23:59:59Z'}).encode()
    search = urllib.request.Request(PC_STAC, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(search, timeout=60) as response:
        items = json.loads(response.read(4 * 2**20))['features']
    items = [i for i in items if i['properties'].get('proj:epsg') == crs and
             abs(float(i['properties'].get('gsd', 0)) - pixel) < 1e-9]
    if not items:
        raise LookupError(f'No Planetary Computer NAIP item for {captured} at {pixel} m, EPSG:{crs}')
    href = items[0]['assets']['image']['href']
    from rasterio.windows import Window
    env = {'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR', 'GDAL_HTTP_MULTIRANGE': 'YES',
           'GDAL_HTTP_MERGE_CONSECUTIVE_RANGES': 'YES', 'GDAL_HTTP_TIMEOUT': '60'}
    signed = href + '?' + pc_token() if href.startswith('https://') else href  # tests read a local file
    with rasterio.Env(**env), rasterio.open(signed) as cog:
        t = cog.transform
        col, row = (left - t.c) / pixel, (t.f - top) / pixel
        if (cog.count != 4 or cog.crs.to_epsg() != crs or abs(t.a - pixel) > 1e-9 or abs(t.e + pixel) > 1e-9
                or abs(col - round(col)) > 1e-6 or abs(row - round(row)) > 1e-6):
            raise ValueError('COG grid differs from the selected capture grid')
        col, row = round(col), round(row)
        if col < 0 or row < 0 or col + width > cog.width or row + height > cog.height:
            raise ValueError('Crop runs past this COG (a neighbouring quarter-quad covers it)')
        data = cog.read(window=Window(col, row, width, height))
    with rasterio.open(output, 'w', driver='GTiff', width=width, height=height, count=4, dtype='uint8',
                       crs=f'EPSG:{crs}', transform=from_origin(left, top, pixel, pixel)) as out:
        out.write(data)
    raw = Path(output).read_bytes()
    return {'url': href, 'file': Path(output).name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'via': 'planetary-computer-cog', 'stac_item': items[0]['id']}


def stac_choose(meta, lonlat_bounds, preferred_year=2022, target_capture=None):
    """When the USGS service cannot answer: choose the capture from Planetary Computer's NAIP catalog
    with the same rules (one 4-band <=1 m capture covering the whole scene; closest year or
    capture), returning (attrs as choose_source would, COG href)."""
    body = json.dumps({'collections': ['naip'], 'bbox': lonlat_bounds, 'limit': 50}).encode()
    search = urllib.request.Request(PC_STAC, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(search, timeout=60) as response:
        items = json.loads(response.read(8 * 2**20))['features']
    size, west, north = meta['size'], meta['west'], meta['north']
    area = Polygon([(west, north), (west+size, north), (west+size, north-size), (west, north-size)])
    project = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    from shapely.geometry import shape
    choices = []
    for item in items:
        props = item['properties']
        gsd = float(props.get('gsd', 0))
        if not 0 < gsd <= 1 or 'image' not in item.get('assets', {}):
            continue
        if (size / gsd + 6) ** 2 > 2_000_000:  # 0.3 m captures exceed the crop budget for a 512 m tile
            continue
        if not transform_geometry(project.transform, shape(item['geometry'])).covers(area):
            continue
        captured = datetime.fromisoformat(props['datetime'].replace('Z', '+00:00'))
        rank = rank_capture(captured.isoformat(), target_capture) if target_capture else (abs(captured.year - preferred_year), 0)
        choices.append((*rank, gsd, item['id'], item, captured))
    if not choices:
        raise ValueError('No single <=1m four-band NAIP capture covers the requested scene')
    *_, item, captured = min(choices, key=lambda row: row[:-2])
    attrs = {'acquisition_date': int(captured.timestamp() * 1000), 'Year': captured.year, 'Category': 1,
             'resolution_value': float(item['properties']['gsd']), 'OBJECTID': None,
             'stac_item': item['id'], 'epsg': item['properties'].get('proj:epsg')}
    return attrs, item['assets']['image']['href']


def cog_grid_crop(href, crs, x, y, output, max_pixels=2_000_000):
    """Crop the scene (plus the same 2-pixel margin) on the COG's own pixel grid; (left, top, pixel, width, height, asset)."""
    from rasterio.windows import Window
    signed = href + '?' + pc_token() if href.startswith('https://') else href
    env = {'GDAL_DISABLE_READDIR_ON_OPEN': 'EMPTY_DIR', 'GDAL_HTTP_MULTIRANGE': 'YES',
           'GDAL_HTTP_MERGE_CONSECUTIVE_RANGES': 'YES', 'GDAL_HTTP_TIMEOUT': '60'}
    with rasterio.Env(**env), rasterio.open(signed) as cog:
        t, pixel = cog.transform, cog.transform.a
        if cog.count != 4 or cog.crs.to_epsg() != crs or abs(t.e + pixel) > 1e-9:
            raise ValueError('NAIP COG is not a north-up four-band raster in the expected projection')
        col = math.floor((min(x) - t.c) / pixel) - 2; row = math.floor((t.f - max(y)) / pixel) - 2
        width = math.ceil((max(x) - t.c) / pixel) + 2 - col; height = math.ceil((t.f - min(y)) / pixel) + 2 - row
        if width * height > max_pixels: raise ValueError('NAIP crop exceeds two million pixels')
        if col < 0 or row < 0 or col + width > cog.width or row + height > cog.height:
            raise ValueError('Crop runs past this COG (a neighbouring quarter-quad covers it)')
        data = cog.read(window=Window(col, row, width, height))
    left, top = t.c + col * pixel, t.f - row * pixel
    with rasterio.open(output, 'w', driver='GTiff', width=width, height=height, count=4, dtype='uint8',
                       crs=f'EPSG:{crs}', transform=from_origin(left, top, pixel, pixel)) as out:
        out.write(data)
    raw = Path(output).read_bytes()
    return left, top, pixel, width, height, {'url': href, 'file': Path(output).name, 'bytes': len(raw),
                                             'sha256': hashlib.sha256(raw).hexdigest(), 'via': 'planetary-computer-cog'}


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
    regional_cache.require_free(source, 21*2**30, 'Preserve 20 GiB internal reserve plus working allowance')
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
    usgs_error = None
    try:
        raw, asset = request_static(RIGHTS, output/'rights.json', 2**20); assets.append(asset)
        rights = json.loads(raw)
        if 'public domain' not in (rights.get('description','')+rights.get('summary','')).lower():
            raise ValueError('USGS public-domain source declaration unavailable')
        try:  # a sanity check only: the crop itself is verified to be four U8 bands below
            raw, asset = request_static(SERVICE+'?f=pjson', output/'service.json', 2**20); assets.append(asset)
            service = json.loads(raw)
            if service.get('bandCount') != 4 or service.get('pixelType') != 'U8':
                raise ValueError('Unexpected NAIP bands or pixel encoding')
        except (urllib.error.URLError, TimeoutError) as error:  # this endpoint alone has returned 504 for hours
            (output/'service.json').unlink(missing_ok=True)
            assets.append({'url': SERVICE+'?f=pjson', 'unavailable': f'{type(error).__name__}: {error}'[:200], 'bytes': 0})
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
        try:
            asset = cog_crop(attrs, crs, [min(xx), min(yy), max(xx), max(yy)], left, top, width, height, pixel,
                             output/'served-crop.tif')
            via = 'Planetary Computer COG of the selected capture, same pixel grid'
        except Exception as error:  # noqa: BLE001 - the ImageServer serves every capture; it is only slower
            (output/'served-crop.tif').unlink(missing_ok=True)
            raw, asset = request(SERVICE+'/exportImage?'+urllib.parse.urlencode(params), output/'served-crop.tif', 10*2**20)
            asset['cog_unavailable'] = f'{type(error).__name__}: {error}'[:300]
            via = 'USGS locked-raster served crop, rendering None'
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:  # USGS down (502/504 seen for hours)
        usgs_error = f'{type(error).__name__}: {error}'[:300]
        for name in ('rights.json', 'service.json', 'catalog.json', 'served-crop.tif'):
            (output/name).unlink(missing_ok=True)
        assets = [{'url': RIGHTS, 'unavailable': usgs_error, 'bytes': 0}]
        rights = {'accessInformation': 'USDA Farm Service Agency, National Agriculture Imagery Program (NAIP)',
                  'description': 'NAIP imagery is in the public domain (USDA FSA).'}
        attrs, href = stac_choose(meta, [min(xx), min(yy), max(xx), max(yy)], preferred_year, target_capture)
        selected = {'attributes': attrs}
        crs = int(attrs['epsg'])
        transformer = Transformer.from_crs(meta['crs'], crs, always_xy=True)
        x, y = transformer.transform([west,west+size,west,west+size],[north,north,north-size,north-size])
        left, top, pixel, width, height, asset = cog_grid_crop(href, crs, x, y, output/'served-crop.tif')
        asset['stac_item'] = attrs['stac_item']
        via = 'Planetary Computer COG (USGS service unavailable), capture chosen from its catalog'
    assets.append(asset)
    with rasterio.open(output/'served-crop.tif') as raster:
        if raster.dtypes[0] != 'uint8':
            raise ValueError('Served NAIP crop is not 8-bit')
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
              'processing':via+'; local nearest-neighbor sample at metre-grid cell centres',
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
