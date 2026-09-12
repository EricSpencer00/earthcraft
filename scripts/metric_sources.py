"""Freeze a bounded metre grid of USGS elevation, ESA cover and raw OSM ways."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import urllib.parse
import urllib.request
import zlib

import numpy as np
import osmium
from pyproj import CRS, Geod, Transformer
import rasterio
import tifffile
from local_paths import bulk_path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bounded_json(url,limit=8*1024**2):
    request=urllib.request.Request(url,headers={'User-Agent':'Earthcraft/0.1 deterministic geographic reconstruction'})
    with urllib.request.urlopen(request,timeout=60) as response:raw=response.read(limit+1)
    if len(raw)>limit:raise ValueError('Source response exceeds bounded download')
    value=json.loads(raw)
    if 'error' in value:raise ValueError('Source service returned an error')
    return value,raw


def prepare(out, size=512, lat=41.89720, lon=-87.62443, grid=None,way_index=None):
    if size % 16 or not 16 <= size <= 1024:
        raise ValueError('First gate supports 16–1024 metres in full chunks')
    out.mkdir(parents=True, exist_ok=True)
    crs = CRS.from_proj4(f'+proj=tmerc +lat_0={lat} +lon_0={lon} +k=1 +x_0=0 +y_0=0 +ellps=WGS84 +units=m +type=crs')
    service_crs = json.dumps({'wkt':crs.to_wkt('WKT1_ESRI')})
    forward = Transformer.from_crs(4326, crs, always_xy=True)
    reverse = Transformer.from_crs(crs, 4326, always_xy=True)
    east, north = forward.transform(lon, lat)
    west, top = math.floor(east - size / 2), math.ceil(north + size / 2)
    if grid is not None:
        if grid['size'] != size:
            raise ValueError('Explicit grid size differs from requested source size')
        crs = CRS.from_user_input(grid['crs'])
        if not crs.is_projected or any(abs(a.unit_conversion_factor-1)>1e-12 for a in crs.axis_info):
            raise ValueError('Source grid must be projected in metres')
        west, top = grid['west'], grid['north']
        if not all(math.isfinite(v) and v == int(v) for v in (west,top)):
            raise ValueError('Source grid origin must be integral metres')
        service_crs = json.dumps({'wkt':crs.to_wkt('WKT1_ESRI')})
        forward = Transformer.from_crs(4326, crs, always_xy=True)
        reverse = Transformer.from_crs(crs, 4326, always_xy=True)
    zz, xx = np.mgrid[:size, :size]
    lons, lats = reverse.transform(west + xx + .5, top - zz - .5)
    bounds = [west, top - size, west + size, top]
    url = 'https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage?' + urllib.parse.urlencode({
        'bbox': ','.join(map(str, bounds)), 'bboxSR': service_crs, 'imageSR': service_crs,
        'size': f'{size},{size}', 'format': 'tiff', 'pixelType': 'F32',
        'interpolation': 'RSP_BilinearInterpolation', 'f': 'image'})
    dem = out / 'usgs-elevation.tif'
    if not dem.exists():
        with urllib.request.urlopen(url, timeout=90) as response:
            raw = response.read(16 * 1024**2 + 1)
        if len(raw) > 16 * 1024**2:
            raise ValueError('DEM response exceeds bounded download')
        dem.write_bytes(raw)
    with rasterio.open(dem) as dataset:
        expected = rasterio.transform.from_origin(west, top, 1, 1)
        # ArcGIS rewrites datum names in custom WKT; compare the numeric inverse
        # transform on corners and centre rather than its human-readable name.
        returned = Transformer.from_crs(dataset.crs,4326,always_xy=True)
        controls = [(west,top),(west+size,top-size),(west+size/2,top-size/2)]
        matching = all(np.allclose(returned.transform(x,y),reverse.transform(x,y),rtol=0,atol=1e-10)
                       for x,y in controls)
        if not matching or not dataset.transform.almost_equals(expected):
            raise ValueError('Server changed requested metric grid')
        heights = dataset.read(1, masked=True)
        if np.ma.getmaskarray(heights).any() or not np.isfinite(heights).all():
            raise ValueError('Missing terrain elevation; no flat fallback')
        heights = np.asarray(heights, dtype=np.float32)

    cache = bulk_path('chicago','cache','arnis-landcover-cache')
    cover = np.zeros((size, size), dtype=np.uint8)
    assets = []
    # Decode only original cached ESA TIFF ranges. No classification smoothing.
    for south, left in set(zip((np.floor(lats / 3) * 3).astype(int).ravel(),
                               (np.floor(lons / 3) * 3).astype(int).ravel())):
        stem = f'ESA_WorldCover_10m_2021_v200_{"N" if south >= 0 else "S"}{abs(south):02}{"E" if left >= 0 else "W"}{abs(left):03}_Map'
        header = cache / f'{stem}_header.bin'
        with tifffile.TiffFile(header) as tif:
            page = tif.pages[0]
            tw, th = page.tilewidth, page.tilelength
            if int(page.compression) not in (8, 32946) or page.predictor != 1:
                raise ValueError('Unsupported cached ESA codec')
        px = np.floor((lons - left) * 12000).astype(int)
        py = np.floor((south + 3 - lats) * 12000).astype(int)
        belongs = (lats >= south) & (lats < south + 3) & (lons >= left) & (lons < left + 3)
        for tx, ty in set(zip((px[belongs] // tw).ravel(), (py[belongs] // th).ravel())):
            asset = cache / f'{stem}_tile_{tx}_{ty}.bin'
            raw = asset.read_bytes()
            try:
                decoded = zlib.decompress(raw)
            except zlib.error:
                decoded = zlib.decompress(raw, -15)
            tile = np.frombuffer(decoded, np.uint8).reshape(th, tw)
            selected = belongs & (px // tw == tx) & (py // th == ty)
            cover[selected] = tile[py[selected] % th, px[selected] % tw]
            assets.append({'path': str(asset), 'sha256': digest(asset)})
    if (cover == 0).any():
        raise ValueError('Missing land cover; no grass fallback')

    ways_path = out / 'osm-ways.json'
    source = bulk_path('chicago','sources','chicago.osm.pbf')
    if not ways_path.exists() and way_index is not None:
        ways_path.write_text(json.dumps(way_index.select(source,crs,west,top,size)))
    if not ways_path.exists():
        ways = []
        class Select(osmium.SimpleHandler):
            def way(self, w):
                coords = [(n.lon, n.lat) for n in w.nodes if n.location.valid()]
                if not coords:
                    return
                x, y = forward.transform(*zip(*coords))
                if max(x) < west or min(x) > west + size or max(y) < top - size or min(y) > top:
                    return
                ways.append({'id': w.id, 'tags': dict(w.tags), 'coordinates': coords,
                             'closed': w.nodes[0].ref == w.nodes[-1].ref})
        Select().apply_file(str(source), locations=True, idx='flex_mem')
        ways_path.write_text(json.dumps(ways))
    geod = Geod(ellps='WGS84')
    distances = []
    for x, z in ((0, 0), (size-2, 0), (0, size-2), (size-2, size-2)):
        for dx, dz in ((1, 0), (0, 1)):
            distances.append(geod.inv(lons[z,x], lats[z,x], lons[z+dz,x+dx], lats[z+dz,x+dx])[2])
    np.savez_compressed(out / 'rasters.npz', elevation=heights, cover=cover)
    county_path = out/'cook-buildings-2022.json'
    county_endpoint='https://gis.cookcountyil.gov/traditional/rest/services/buildingFootprint_2022/MapServer/0/query?'
    county_query={
        'where':'1=1','geometry':','.join(map(str,[lons.min(),lats.min(),lons.max(),lats.max()])),
        'geometryType':'esriGeometryEnvelope','inSR':4326,
        'spatialRel':'esriSpatialRelIntersects','f':'json'}
    county_url=county_endpoint+urllib.parse.urlencode({**county_query,'returnIdsOnly':'true'})
    if not county_path.exists():
        identifier_document,identifier_raw=bounded_json(county_url)
        identifiers=sorted(identifier_document.get('objectIds') or [])
        if len(identifiers)!=len(set(identifiers)) or any(type(value) is not int for value in identifiers):
            raise ValueError('County object ID inventory is invalid')
        features=[];requests=[]
        for start in range(0,len(identifiers),500):
            batch=identifiers[start:start+500]
            url=county_endpoint+urllib.parse.urlencode({'objectIds':','.join(map(str,batch)),
                'outSR':4326,'outFields':'OBJECTID,Year,Ground_Z,Max_Point,Height',
                'returnGeometry':'true','orderByFields':'OBJECTID','f':'json'})
            document,raw=bounded_json(url)
            if document.get('exceededTransferLimit') or not isinstance(document.get('features'),list):
                raise ValueError('County source page is incomplete')
            observed=[feature.get('attributes',{}).get('OBJECTID') for feature in document['features']]
            if sorted(observed)!=batch:
                raise ValueError('County source changed between ID inventory and feature pages')
            features.extend(document['features'])
            requests.append({'url':url,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        county={'objectIdFieldName':identifier_document.get('objectIdFieldName','OBJECTID'),
                'features':features}
        county_path.write_text(json.dumps(county))
        (out/'cook-buildings-request.json').write_text(json.dumps({'url':county_url,
            'id_inventory_bytes':len(identifier_raw),'id_inventory_sha256':hashlib.sha256(identifier_raw).hexdigest(),
            'object_ids':identifiers,'feature_pages':requests},indent=2))
    county_url=json.loads((out/'cook-buildings-request.json').read_text())['url']
    county=json.loads(county_path.read_text())
    if 'error' in county or county.get('exceededTransferLimit') or not isinstance(county.get('features'),list):
        raise ValueError('County source is incomplete or failed')
    report = {'size': size, 'crs': crs.to_wkt(), 'projection_origin': [lat,lon], 'west': west, 'north': top,
        'axes': 'east +X, south +Z, elevation +Y', 'metres_per_block': 1,
        'cell_ground_distances_m': distances,
        'elevation_range_m': [float(heights.min()), float(heights.max())],
        'elevation_resolution': '1 m output sampling; underlying source resolution/accuracy unverified',
        'elevation_vertical_datum': 'USGS service composite; source datum unverified',
        'elevation_url': url, 'elevation_sha256': digest(dem),
        'cover_classes': {int(c): int(n) for c,n in zip(*np.unique(cover, return_counts=True))},
        'cover_source': 'ESA WorldCover 2021 v200, nominal 10m, CC BY 4.0',
        'cover_assets': assets, 'osm_source': str(source), 'osm_subset_sha256': digest(ways_path),
        'county_source':'Cook County GIS Building Footprints 2022',
        'county_url':county_url,'county_sha256':digest(county_path),
        'county_terms':'https://www.cookcountyil.gov/terms-use',
        'osm_license': 'OpenStreetMap contributors, ODbL', 'inference_used': False}
    (out / 'sources.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'sources':str(out),'size':size,'west':west,'north':top,'buildings':len(county['features'])}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--size', type=int, default=512)
    args = parser.parse_args()
    prepare(args.output, args.size)
