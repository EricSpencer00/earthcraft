"""Convert a frozen OpenStreetMap Overpass JSON response to Google Earth-compatible KML.

This is a geometry bridge, not a scraper: it only reads an already saved response
and carries OSM attribution and building-height tags into KML ExtendedData.
"""
import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

KML = 'http://www.opengis.net/kml/2.2'
ET.register_namespace('', KML)


def metres(value: str) -> float | None:
    if not isinstance(value,str):return None
    match = re.match(r'\s*(\d+(?:\.\d+)?)\s*(?:m|metres?|meters?)?\s*$', value or '', re.I)
    number=float(match.group(1)) if match else None
    return number if number is not None and math.isfinite(number) else None


def building_tag(tags):
    return any(tags.get(key) not in (None,'','no','false','0') for key in ('building','building:part'))


def convert(data: dict) -> tuple[ET.Element, int]:
    nodes = {item['id']: item for item in data.get('elements', []) if item.get('type') == 'node'}
    root = ET.Element(f'{{{KML}}}kml'); document = ET.SubElement(root, f'{{{KML}}}Document')
    ET.SubElement(document, f'{{{KML}}}name').text = 'Earthcraft OSM geometry export'
    count = 0
    for way in data.get('elements', []):
        tags = way.get('tags', {})
        if way.get('type') != 'way' or not ('building' in tags or 'building:part' in tags):
            continue
        height = metres(tags.get('height', ''))
        if not height or height <= 0:
            continue
        try:
            points = [nodes[node] for node in way['nodes']]
        except KeyError:
            continue
        if len(points) < 4 or points[0]['id'] != points[-1]['id']:
            continue
        placemark = ET.SubElement(document, f'{{{KML}}}Placemark')
        ET.SubElement(placemark, f'{{{KML}}}name').text = tags.get('name', f"OSM way {way['id']}")
        extended = ET.SubElement(placemark, f'{{{KML}}}ExtendedData')
        data_tag = ET.SubElement(extended, f'{{{KML}}}Data', name='height_m')
        ET.SubElement(data_tag, f'{{{KML}}}value').text = str(height)
        source = ET.SubElement(extended, f'{{{KML}}}Data', name='source')
        ET.SubElement(source, f'{{{KML}}}value').text = f'OpenStreetMap way {way["id"]}; ODbL'
        polygon = ET.SubElement(placemark, f'{{{KML}}}Polygon')
        outer = ET.SubElement(polygon, f'{{{KML}}}outerBoundaryIs')
        ring = ET.SubElement(outer, f'{{{KML}}}LinearRing')
        ET.SubElement(ring, f'{{{KML}}}coordinates').text = ' '.join(f"{point['lon']},{point['lat']},0" for point in points)
        count += 1
    return root, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f'refusing to overwrite {args.output}')
    if not args.output.parent.is_dir():
        raise ValueError(f'output parent does not exist: {args.output.parent}')
    root, count = convert(json.loads(args.input.read_text()))
    if not count:
        raise ValueError('no closed building polygons with an explicit height in metres')
    ET.ElementTree(root).write(args.output, encoding='utf-8', xml_declaration=True)
    print(json.dumps({'output': str(args.output), 'buildings': count}, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, FileExistsError, json.JSONDecodeError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(2)
