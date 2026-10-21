"""Fetch the open inputs ``detail_build`` reads into ``<out>/``, for one tile plus halo.

    PYTHONPATH=scripts python scripts/detail_sources.py --frame configs/world-frame.json \
        --tile -256 -256 512 --halo 24 --out sources/

Writes, each once and through ``WriteGuard``:

* ``osm_halo.json``: Overpass ``out body geom`` for every node, way and relation
  in the box (OpenStreetMap, ODbL 1.0);
* ``chicago_buildings_syp8-uezg.geojson``: City of Chicago building footprints;
* ``chicago_bikeracks_hgdw-64h3.geojson`` and ``chicago_divvy_bbyy-e7gq.geojson``:
  City bike racks and Divvy stations (City of Chicago Data Portal terms);
* ``sources.json``: URL, licence, bytes and SHA-256 of each file.

The City streetlight inventory has no stated licence and is not fetched.
"""
import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from pyproj import Transformer

from detail_guard import WriteGuard

OVERPASS = 'https://overpass-api.de/api/interpreter'
SOCRATA = 'https://data.cityofchicago.org/resource/{id}.geojson'
CITY = 'City of Chicago Data Portal Terms of Use'
CITY_DATASETS = (
    ('chicago_buildings_syp8-uezg.geojson', 'syp8-uezg', 'the_geom'),
    ('chicago_bikeracks_hgdw-64h3.geojson', 'hgdw-64h3', 'the_geom'),
    ('chicago_divvy_bbyy-e7gq.geojson', 'bbyy-e7gq', 'location'),
)
MAX_BYTES = 64 * 2**20


def bbox(frame, x0, z0, size):
    """(south, west, north, east) in WGS84 covering the block square."""
    inverse = Transformer.from_crs(frame['crs'], 'EPSG:4326', always_xy=True)
    corners = [inverse.transform(frame['west'] + x, frame['north'] - z)
               for x in (x0, x0 + size) for z in (z0, z0 + size)]
    lons, lats = zip(*corners)
    return min(lats), min(lons), max(lats), max(lons)


def get(url, data=None):
    request = urllib.request.Request(url, data=data, headers={'User-Agent': 'earthcraft-detail-sources'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError(f'{url} exceeds {MAX_BYTES} bytes')
            return raw
        except OSError:
            if attempt == 3:
                raise
            time.sleep(5 * 2 ** attempt)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--frame', type=Path, required=True)
    p.add_argument('--tile', type=int, nargs=3, metavar=('X0', 'Z0', 'SIZE'), required=True)
    p.add_argument('--halo', type=int, default=24)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--max-bytes', type=int, default=200 * 2**20)
    p.add_argument('--free-floor', type=int, default=5 * 2**30)
    a = p.parse_args()
    frame = json.loads(a.frame.read_text())
    x0, z0, size = a.tile[0] - a.halo, a.tile[1] - a.halo, a.tile[2] + 2 * a.halo
    south, west, north, east = bbox(frame, x0, z0, size)
    guard = WriteGuard(a.out, a.max_bytes, a.free_floor)
    receipt = {'tile': a.tile, 'halo': a.halo, 'bbox_swne': [south, west, north, east], 'files': {}}

    def keep(name, raw, url, licence):
        guard.write_bytes(a.out / name, raw)
        receipt['files'][name] = {'url': url, 'licence': licence, 'bytes': len(raw),
                                  'sha256': hashlib.sha256(raw).hexdigest()}
        print(f'{name}: {len(raw)} bytes')

    query = f'[out:json][timeout:120];nwr({south},{west},{north},{east});out body geom;'
    keep('osm_halo.json', get(OVERPASS, urllib.parse.urlencode({'data': query}).encode()),
         OVERPASS + ' ' + query, 'ODbL 1.0 (c) OpenStreetMap contributors')
    for name, dataset, column in CITY_DATASETS:
        where = f'within_box({column}, {north}, {west}, {south}, {east})'
        url = SOCRATA.format(id=dataset) + '?' + urllib.parse.urlencode({'$where': where, '$limit': 50000})
        keep(name, get(url), url, CITY)
    receipt['fetched_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    guard.write_text(a.out / 'sources.json', json.dumps(receipt, indent=1))


if __name__ == '__main__':
    main()
