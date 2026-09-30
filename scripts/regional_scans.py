"""Frozen county LiDAR surfaces with explicit units, dates and coverage masks.

DSM observations supply roof shape, not facade colour or a building classifier.
Original service metadata and unrendered float rasters are retained for replay.
"""
import hashlib
import http.client
import fcntl
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import rasterio
from pyproj import CRS

from region_expansion import atomic, sha

ROOT = 'https://data.isgs.illinois.edu/arcgis/rest/services/Elevation'
COUNTIES = ('Cook', 'DuPage', 'DeKalb', 'Grundy', 'Kane', 'Kendall', 'Lake', 'McHenry', 'Will')
SURVEY_FOOT = 1200 / 3937
_VERIFIED_FILES = {}


def frozen_file(url, path, limit=4*2**20):
    """Acquire once and hash once per unchanged file identity in this process.

    A per-source lock serializes receipt publication across workers. Receipts
    remain the authority, including on a restart; size/inode/mtime/ctime changes
    invalidate the in-memory hash shortcut. No LAZ byte array is needed to crop
    an already indexed node.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name+'.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _frozen_file(url,path,limit)


def _frozen_file(url, path, limit):
    receipt = path.with_name(path.name+'.receipt.json')
    if receipt.exists():
        record = json.loads(receipt.read_text())
        stat=path.stat()
        identity=(stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,record['sha256'])
        if record['url'] != url or stat.st_size!=record['bytes'] or stat.st_size>limit:
            raise ValueError('Frozen scan source changed')
        if _VERIFIED_FILES.get(str(path.resolve()))!=identity:
            if sha(path)!=record['sha256']:raise ValueError('Frozen scan source changed')
            _VERIFIED_FILES[str(path.resolve())]=identity
        return record
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                raw = response.read(limit+1)
                length=getattr(response,'headers',{}).get('Content-Length')
                if length is not None and len(raw)<min(int(length),limit+1):
                    raise http.client.IncompleteRead(raw)
            break
        except (OSError,http.client.IncompleteRead) as error:
            # Retry a transient source connection, rather than throwing away a
            # whole tile/worker. Missing or forbidden sources remain explicit.
            if (isinstance(error,urllib.error.HTTPError) and error.code not in (429,500,502,503,504)) or attempt==2:
                raise
            time.sleep(2**attempt)
    if len(raw) > limit:
        raise ValueError('Scan response exceeds bounded download')
    # A stop after the payload rename but before its receipt must not strand
    # a lease. Prove the same original bytes from the requested URL before
    # completing that interrupted transition; never replace changed evidence.
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('Unreceipted scan payload differs from publisher; retain it')
    else:atomic(path, raw)
    record={'url': url, 'sha256': hashlib.sha256(raw).hexdigest(),'bytes': len(raw)}
    atomic(receipt, json.dumps(record).encode())
    return record


def frozen_get(url, path, limit=4*2**20):
    frozen_file(url,path,limit)
    return Path(path).read_bytes()


def vertical_units(metadata):
    """Require vertical CRS metadata; horizontal feet do not imply Z feet."""
    reference = metadata['extent']['spatialReference']
    if reference.get('latestVcsWkid', reference.get('vcsWkid')) in (6360, 105703):
        return SURVEY_FOOT, 'NAVD88; US survey feet'
    wkt = reference.get('wkt', '')
    if 'VERTCS' in wkt or 'VERTCRS' in wkt:
        vertical = CRS.from_wkt(wkt)
        axes = [axis for axis in vertical.axis_info if axis.direction in ('up', 'down')]
        if len(axes) == 1 and axes[0].direction == 'up':
            return axes[0].unit_conversion_factor, vertical.name
    raise ValueError('Vertical datum/unit metadata missing; scan cannot be treated as measured metre heights')


def discover(destination):
    destination = Path(destination)
    listing = json.loads(frozen_get(ROOT+'?f=json', destination/'services.json'))
    names = {item['name'].split('/')[-1] for item in listing['services']}
    county_ids = {'Cook':'17031','DuPage':'17043','DeKalb':'17037','Grundy':'17063',
        'Kane':'17089','Kendall':'17093','Lake':'17097','McHenry':'17111','Will':'17197'}
    query = urllib.parse.urlencode({'where': "GEOID IN ("+','.join("'"+value+"'" for value in county_ids.values())+")",
        'outFields':'GEOID,NAME','returnGeometry':'true','outSR':4326,'f':'geojson'})
    geography=json.loads(frozen_get('https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/1/query?'+query,
                                   destination/'county-boundaries.geojson',limit=16*2**20))
    if geography.get('exceededTransferLimit') or len(geography.get('features',[]))!=len(county_ids):
        raise ValueError('County scan ownership boundaries are missing or truncated')
    boundaries={feature['properties']['GEOID']:feature['geometry'] for feature in geography['features']}
    sources, rejected = [], []
    for county in COUNTIES:
        years = sorted({int(match[1]) for name in names
                        if (match := re.fullmatch('IL_'+county+r'_DSM_(\d{4})', name))}, reverse=True)
        for year in years:
            dtm = next((name for kind in ('DTM', 'DEM')
                        if (name := f'IL_{county}_{kind}_{year}') in names), None)
            if dtm is None:
                rejected.append({'county': county, 'year': year, 'reason': 'No paired ground survey'})
                continue
            pair = {}
            try:
                for kind, name in (('dtm', dtm), ('dsm', f'IL_{county}_DSM_{year}')):
                    url = ROOT+'/'+name+'/ImageServer'
                    metadata = json.loads(frozen_get(url+'?f=json', destination/(name+'.json')))
                    if 'error' in metadata:
                        raise ValueError(str(metadata['error']))
                    factor, datum = vertical_units(metadata)
                    pair[kind] = {'service': url, 'metres_per_vertical_unit': factor,
                        'vertical_reference': datum, 'native_pixel_size': metadata['pixelSizeX'],
                        'spatial_reference': metadata['extent']['spatialReference'],
                        'extent': metadata['extent']}
                sources.append({'county': county, 'year': year, 'boundary':boundaries[county_ids[county]], **pair})
                break
            except ValueError as error:
                rejected.append({'county': county, 'year': year, 'reason': str(error)})
    catalog = {'schema': 'earthcraft-regional-scan-surfaces-v1', 'sources': sources,
        'rejected': rejected, 'selection': 'Newest paired survey with verified vertical units',
        'raw_point_surveys': 'Catalog separately; surfaces do not prove raw points were acquired',
        'facades_measured': False, 'llm_used': False}
    atomic(destination/'catalog.json', json.dumps(catalog, indent=2).encode())
    return catalog


def sample_surface(layer, grid, destination):
    if layer.get('kind')=='original-arcgrid-zip':
        from regional_arcgrid import sample
        return sample(layer,grid,destination)
    size = grid['size']
    wkt = CRS.from_user_input(grid['crs']).to_wkt(version='WKT1_ESRI')
    reference = json.dumps({'wkt': wkt}, separators=(',', ':'))
    bounds = [grid['west'], grid['north']-size, grid['west']+size, grid['north']]
    params = {'bbox': ','.join(map(str, bounds)), 'bboxSR': reference, 'imageSR': reference,
        'size': f'{size},{size}', 'format': 'tiff', 'pixelType': 'F32', 'f': 'image',
        'renderingRule': json.dumps({'rasterFunction': 'None'}),
        'interpolation': 'RSP_NearestNeighbor', 'adjustAspectRatio': 'false'}
    url = layer['service']+'/exportImage?'+urllib.parse.urlencode(params)
    frozen_get(url, destination, limit=16*2**20)
    with rasterio.open(destination) as raster:
        expected = rasterio.transform.from_origin(grid['west'], grid['north'], 1, 1)
        if raster.count != 1 or raster.shape != (size, size) or not raster.transform.almost_equals(expected):
            raise ValueError('Scan export returned another pixel grid')
        if not CRS(raster.crs).equals(CRS(grid['crs']), ignore_axis_order=True):
            raise ValueError('Scan raster CRS differs from frozen world')
        array = raster.read(1, masked=True).filled(np.nan).astype(np.float32)
    array *= layer['metres_per_vertical_unit']
    return array


def acquire(pair, grid, destination, fallback):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    dtm = sample_surface(pair['dtm'], grid, destination/'dtm.tif')
    dsm = sample_surface(pair['dsm'], grid, destination/'dsm.tif')
    valid = np.isfinite(dtm) & np.isfinite(dsm)
    # Invalid publisher pixels are explicit gaps; do not voxelize kilometre-high
    # spikes seen in some surface services or infer buildings from tree returns.
    valid &= (dsm >= dtm-1) & (dsm-dtm <= 700) & (dtm > -100) & (dtm < 800)
    elevation = np.where(valid, dtm, fallback).astype(np.float32)
    roof = np.where(valid, dsm, elevation).astype(np.float32)
    np.savez_compressed(destination/'metric-surfaces.npz', dtm=elevation, dsm=roof, valid=valid)
    record = {'schema': 'earthcraft-regional-scan-surface-crop-v1', 'county': pair['county'],
        'capture_year': pair['year'], 'grid': {key: grid[key] for key in ('crs', 'west', 'north', 'size')},
        'source': pair, 'valid_cells': int(valid.sum()), 'missing_cells': int((~valid).sum()),
        'surfaces_sha256': sha(destination/'metric-surfaces.npz'),
        'assets': {name: sha(destination/name) for name in ('dtm.tif', 'dsm.tif')},
        'resampling': 'nearest source value; one-metre output sampling is not source accuracy',
        'roof_mask_source': 'Mapped building footprints; vegetation outside footprints is excluded',
        'walls': 'Derived exposed side shell; facade measurements unavailable',
        'raw_points_acquired': False, 'physical_accuracy_verified': False, 'llm_used': False}
    atomic(destination/'probe.json', json.dumps(record, indent=2).encode())
    return elevation, record
