"""Cook County Assessor residential characteristics as a located index, for facade materials.

Two keyless Cook County Open Data (Socrata) tables, joined on PIN:
  x54s-btds  Residential Single and Multi-Family Improvement Characteristics (one row per card)
  pabr-t5kh  Parcel Universe (parcel latitude/longitude)
The index keeps, per residential card with a location: exterior wall construction (1 frame,
2 masonry, 3 frame and masonry, 4 stucco), roof construction, residence type and apartments, at
the parcel point (WGS84 lon/lat, so the index needs no world frame), in an R-tree. A parcel point is
not a building outline: a building takes the record of the parcel point inside its footprint.

    python3 scripts/cook_assessor_index.py --year 2024 --output DIR/cook-assessor-res-2024.sqlite
"""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import time
import urllib.parse
import urllib.request

PORTAL = 'https://datacatalog.cookcountyil.gov/resource/'
CHARACTERISTICS, PARCELS = 'x54s-btds', 'pabr-t5kh'
LICENSE = 'Cook County Open Data (datacatalog.cookcountyil.gov) terms of use; Cook County Assessor\'s Office'
EXT_WALL = {'1': 'Frame', '2': 'Masonry', '3': 'Frame + Masonry', '4': 'Stucco'}  # the portal mostly returns the labels
PAGE = 50_000
MAX_BYTES = 400 * 2**20


def fetch(dataset, select, where, order, log=print, counter=None):
    """Pages (lists of dicts) of a Socrata dataset, by $offset, within a shared byte bound."""
    offset = 0
    counter = counter if counter is not None else [0]
    while True:
        query = urllib.parse.urlencode({'$select': select, '$where': where, '$order': order,
                                        '$limit': PAGE, '$offset': offset})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(f'{PORTAL}{dataset}.csv?{query}', timeout=300) as response:
                    raw = response.read(MAX_BYTES + 1)
                break
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        counter[0] += len(raw)
        if counter[0] > MAX_BYTES:
            raise ValueError(f'Downloads exceed the {MAX_BYTES}-byte bound')
        page = list(csv.DictReader(io.StringIO(raw.decode())))
        log(json.dumps({'event': 'page', 'dataset': dataset, 'offset': offset, 'rows': len(page)}))
        yield page
        if len(page) < PAGE:
            return
        offset += PAGE


def build(year, output, log=print):
    output = Path(output)
    if output.exists():
        raise ValueError('Never replace a frozen index')
    started = time.time()
    downloaded = [0]
    where = {}
    for page in fetch(PARCELS, 'pin,lat,lon', 'lat IS NOT NULL', 'pin', log, downloaded):
        where.update((row['pin'], (float(row['lon']), float(row['lat']))) for row in page if row['lat'] and row['lon'])
    staging = output.with_suffix('.building')
    db = sqlite3.connect(staging)
    db.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);'
                     'CREATE TABLE cards(rowid INTEGER PRIMARY KEY, pin TEXT, card TEXT, ext_wall TEXT, roof TEXT,'
                     ' type_resd TEXT, apts TEXT);'
                     'CREATE VIRTUAL TABLE points USING rtree(rowid, minx, maxx, miny, maxy);')
    digest, kept, total = hashlib.sha256(), 0, 0
    cards = (row for page in fetch(CHARACTERISTICS, 'pin,card,char_ext_wall,char_roof_cnst,char_type_resd,char_apts',
                                   f'year={int(year)}', 'pin,card', log, downloaded) for row in page)
    for row in cards:
        total += 1
        location = where.get(row['pin'])
        if location is None:
            continue
        x, y = location
        values = (row['pin'], row['card'], EXT_WALL.get(row['char_ext_wall'], row['char_ext_wall'] or None),
                  row['char_roof_cnst'] or None, row['char_type_resd'] or None, row['char_apts'] or None)
        digest.update(json.dumps(values).encode())
        rowid = db.execute('INSERT INTO cards(pin,card,ext_wall,roof,type_resd,apts) VALUES(?,?,?,?,?,?)', values).lastrowid
        db.execute('INSERT INTO points VALUES(?,?,?,?,?)', (rowid, x, x, y, y))
        kept += 1
    record = {'schema': 'earthcraft-cook-assessor-residential-v1', 'year': int(year), 'crs': 'EPSG:4326',
              'sources': {CHARACTERISTICS: f'{PORTAL}{CHARACTERISTICS}', PARCELS: f'{PORTAL}{PARCELS}'},
              'license': LICENSE, 'cards': total, 'located_cards': kept, 'parcels': len(where),
              'downloaded_bytes': downloaded[0], 'rows_digest': digest.hexdigest(),
              'ext_wall_codes': EXT_WALL, 'location': 'parcel point (Parcel Universe lat/lon), not a building outline',
              'seconds': round(time.time() - started, 1)}
    db.execute('INSERT INTO meta VALUES(?,?)', ('source', json.dumps(record)))
    db.commit();db.close()
    staging.rename(output)
    record['sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.json').write_text(json.dumps(record, indent=2))
    log(json.dumps({'event': 'built', **{k: record[k] for k in ('cards', 'located_cards', 'parcels', 'sha256')}}))
    return record


def walls(index, grid):
    """[(x, y, ext_wall, type_resd)] of the located cards inside the tile, in the tile's frame metres."""
    from pyproj import Transformer
    west, north, size = grid['west'], grid['north'], grid['size']
    to_lonlat = Transformer.from_crs(grid['crs'], 4326, always_xy=True)
    lon, lat = to_lonlat.transform([west, west + size, west, west + size], [north, north, north - size, north - size])
    db = sqlite3.connect(f'file:{index}?mode=ro', uri=True)
    try:
        rows = db.execute('SELECT p.minx, p.miny, c.ext_wall, c.type_resd FROM points p JOIN cards c ON c.rowid=p.rowid '
                          'WHERE p.minx>=? AND p.maxx<=? AND p.miny>=? AND p.maxy<=?',
                          (min(lon), max(lon), min(lat), max(lat))).fetchall()
    finally:
        db.close()
    if not rows:
        return []
    x, y = Transformer.from_crs(4326, grid['crs'], always_xy=True).transform([r[0] for r in rows], [r[1] for r in rows])
    return [(px, py, r[2], r[3]) for px, py, r in zip(x, y, rows)
            if west <= px < west + size and north - size < py <= north]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--year', type=int, default=2024)
    parser.add_argument('--output', type=Path, required=True)
    a = parser.parse_args()
    build(a.year, a.output)
