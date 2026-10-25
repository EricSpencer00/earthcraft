"""Build dashboard/places.json: named places in the plan area for the map's search box.

One Overpass query (OpenStreetMap, keyless) at build time. The site searches this file in the
browser, so no third-party geocoder ever sees a visitor's query.

  python scripts/build_places.py [--plan runs/regional/control/plan.json] [--out dashboard/places.json]
"""
import argparse
import json
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
OVERPASS = 'https://overpass-api.de/api/interpreter'
LICENSE = 'ODbL 1.0 (c) OpenStreetMap contributors'
MAX_BYTES = 2 * 2**20
# kind -> Overpass filters; order is search rank (earlier wins a tie)
KINDS = {
    'city': ['node[place~"^(city|town)$"]'],
    'university': ['nwr[amenity~"^(university|college)$"]'],
    'landmark': ['nwr[tourism~"^(museum|zoo|theme_park|aquarium)$"]', 'nwr[tourism=attraction][wikidata]',
                 'nwr[leisure=stadium]',
                 'nwr[aeroway=aerodrome][iata]'],
    'neighbourhood': ['node[place~"^(village|suburb|neighbourhood|quarter)$"]'],
    'hospital': ['nwr[amenity=hospital]'],
    'station': ['node[railway=station]'],
    'park': ['nwr[leisure~"^(park|nature_reserve|golf_course)$"]', 'nwr[natural=beach]'],
}


def plan_bbox(plan_path, frame_path):
    plan = json.loads(Path(plan_path).read_text())
    frame = json.loads(Path(frame_path).read_text())
    west = min(t['west'] for t in plan['tiles'])
    east = max(t['west'] + t['size'] for t in plan['tiles'])
    north = max(t['north'] for t in plan['tiles'])
    south = min(t['north'] - t['size'] for t in plan['tiles'])
    lon, lat = Transformer.from_crs(frame['crs'], 4326, always_xy=True).transform([west, east, west, east],
                                                                                 [north, north, south, south])
    return min(lat), min(lon), max(lat), max(lon)


def query(bbox):
    box = ','.join(f'{v:.4f}' for v in bbox)
    parts = ''.join(f'{f}["name"]({box});' for filters in KINDS.values() for f in filters)
    return f'[out:json][timeout:240];({parts});out center tags;'


def kind_of(tags):
    for kind, filters in KINDS.items():
        for f in filters:
            for clause in f.split('[')[1:]:
                key, op, value = clause.rstrip(']').partition('~' if '~' in clause else '=')
                actual = tags.get(key)
                if actual is None:
                    break
                if op == '~' and actual not in value.strip('"^$()').split('|'):
                    break
                if op == '=' and actual != value:
                    break
            else:
                return kind
    return None


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).casefold()


def build(elements):
    rank = {kind: i for i, kind in enumerate(KINDS)}
    best = {}
    for element in elements:
        tags = element.get('tags', {})
        name = tags.get('name', '').strip()
        point = element.get('center') or element
        if not name or 'lat' not in point:
            continue
        kind = kind_of(tags)
        if kind is None:
            continue
        lon, lat = round(point['lon'], 5), round(point['lat'], 5)
        key = (fold(name), round(lon, 2), round(lat, 2))  # one entry per name per ~1 km
        if key not in best or rank[kind] < rank[best[key][3]]:
            best[key] = [name, lon, lat, kind]
    return sorted(best.values(), key=lambda p: (rank[p[3]], fold(p[0])))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--plan', type=Path, default=ROOT / 'runs/regional/control/plan.json')
    parser.add_argument('--frame', type=Path, default=ROOT / 'runs/chicago-adaptation-city-001/frame.json')
    parser.add_argument('--out', type=Path, default=ROOT / 'dashboard/places.json')
    args = parser.parse_args()
    bbox = plan_bbox(args.plan, args.frame)
    request = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({'data': query(bbox)}).encode(),
                                     headers={'User-Agent': 'earthcraft-build-places (github.com/EricSpencer00/earthcraft)'})
    with urllib.request.urlopen(request, timeout=300) as response:
        elements = json.load(response)['elements']
    places = build(elements)
    result = {'schema': 'earthcraft.places.v1', 'built_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'source': 'OpenStreetMap via Overpass API', 'license': LICENSE,
              'bbox_lat_lon': [round(v, 4) for v in bbox], 'fields': ['name', 'lon', 'lat', 'kind'], 'places': places}
    text = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
    if len(text.encode()) > MAX_BYTES:
        raise SystemExit(f'places.json would be {len(text.encode())} bytes, over the {MAX_BYTES} cap')
    args.out.write_text(text + '\n')
    counts = {}
    for p in places:
        counts[p[3]] = counts.get(p[3], 0) + 1
    print(json.dumps({'places': len(places), 'bytes': len(text.encode()), 'kinds': counts}))


if __name__ == '__main__':
    main()
