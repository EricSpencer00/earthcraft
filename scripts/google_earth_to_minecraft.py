"""Deterministically turn a Google Earth KML/KMZ *geometry export* into a Minecraft datapack.

This importer deliberately accepts KML geometry the user is entitled to reuse; it does
not fetch, scrape, trace, or infer from Google imagery.  Each source metre maps to one
Minecraft block in X/Z.  Building heights must be explicit KML metadata, a ``height=``
name fragment, or an extruded relative-to-ground altitude.  Missing heights are errors.
"""
import argparse
import hashlib
import json
import math
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

EARTH_RADIUS_M = 6_378_137.0
HEIGHT_NAMES = {"height", "height_m", "building:height", "building_height"}


@dataclass(frozen=True)
class Building:
    name: str
    ring: tuple[tuple[float, float, float], ...]  # longitude, latitude, altitude
    height_m: float
    source: str


def local_metre_coordinates(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    """Local east/north tangent-plane approximation, exact enough for bounded KML AOIs."""
    x = EARTH_RADIUS_M * math.cos(math.radians(lat0)) * math.radians(lon - lon0)
    z = -EARTH_RADIUS_M * math.radians(lat - lat0)
    return x, z


def _tag(element: ET.Element, suffix: str) -> ET.Element | None:
    return next((x for x in element.iter() if x.tag.rsplit('}', 1)[-1] == suffix), None)


def _text(element: ET.Element, suffix: str) -> str:
    found = _tag(element, suffix)
    return (found.text or '').strip() if found is not None else ''


def _number(text: str) -> float | None:
    match = re.search(r'(?<![\w.])-?\d+(?:\.\d+)?', text)
    return float(match.group()) if match else None


def _height(placemark: ET.Element, ring: list[tuple[float, float, float]]) -> tuple[float, str]:
    for data in placemark.iter():
        if data.tag.rsplit('}', 1)[-1] == 'Data' and data.attrib.get('name', '').lower() in HEIGHT_NAMES:
            value = _number(_text(data, 'value'))
            if value is not None and value > 0:
                return value, 'ExtendedData'
    named = _number(_text(placemark, 'name').lower().split('height=', 1)[-1]) if 'height=' in _text(placemark, 'name').lower() else None
    if named and named > 0:
        return named, 'name height='
    if _text(placemark, 'extrude') == '1' and _text(placemark, 'altitudeMode').lower() == 'relativetoground':
        altitude = max(point[2] for point in ring)
        if altitude > 0:
            return altitude, 'extruded relativeToGround altitude'
    raise ValueError(f"{_text(placemark, 'name') or 'unnamed placemark'}: no usable building height")


def parse_kml_bytes(raw: bytes) -> list[Building]:
    root = ET.fromstring(raw)
    buildings: list[Building] = []
    for placemark in root.iter():
        if placemark.tag.rsplit('}', 1)[-1] != 'Placemark':
            continue
        polygon = _tag(placemark, 'Polygon')
        coordinates = _text(polygon, 'coordinates') if polygon is not None else ''
        if not coordinates:
            continue
        ring = []
        for point in coordinates.replace('\n', ' ').split():
            parts = point.split(',')
            if len(parts) < 2:
                raise ValueError(f'invalid coordinate {point!r}')
            ring.append((float(parts[0]), float(parts[1]), float(parts[2]) if len(parts) > 2 else 0.0))
        if len(ring) < 4:
            raise ValueError(f"{_text(placemark, 'name') or 'unnamed placemark'}: polygon needs at least four points")
        if ring[0][:2] == ring[-1][:2]:
            ring.pop()
        if len(ring) < 3:
            raise ValueError('polygon has fewer than three distinct vertices')
        height, source = _height(placemark, ring)
        buildings.append(Building(_text(placemark, 'name') or f'building-{len(buildings)+1}', tuple(ring), height, source))
    if not buildings:
        raise ValueError('KML contains no Polygon placemarks')
    return buildings


def read_kml(path: Path) -> bytes:
    if path.suffix.lower() == '.kmz':
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith('.kml')]
            if len(names) != 1:
                raise ValueError('KMZ must contain exactly one KML document')
            return archive.read(names[0])
    return path.read_bytes()


def point_in_polygon(x: float, z: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    for (x1, z1), (x2, z2) in zip(polygon, polygon[1:] + polygon[:1]):
        if (z1 > z) != (z2 > z) and x < (x2 - x1) * (z - z1) / (z2 - z1) + x1:
            inside = not inside
    return inside


def occupied_cells(buildings: list[Building]) -> tuple[set[tuple[int, int, int]], dict]:
    lon0 = sum(p[0] for b in buildings for p in b.ring) / sum(len(b.ring) for b in buildings)
    lat0 = sum(p[1] for b in buildings for p in b.ring) / sum(len(b.ring) for b in buildings)
    cells: set[tuple[int, int, int]] = set()
    records = []
    for building in buildings:
        footprint = [local_metre_coordinates(lon, lat, lon0, lat0) for lon, lat, _ in building.ring]
        min_x, max_x = math.floor(min(p[0] for p in footprint)), math.ceil(max(p[0] for p in footprint))
        min_z, max_z = math.floor(min(p[1] for p in footprint)), math.ceil(max(p[1] for p in footprint))
        height = math.ceil(building.height_m)
        count = 0
        for x in range(min_x, max_x):
            for z in range(min_z, max_z):
                if point_in_polygon(x + .5, z + .5, footprint):
                    for y in range(height):
                        cells.add((x, y, z)); count += 1
        records.append({'name': building.name, 'height_m': building.height_m, 'height_source': building.source,
                        'footprint_vertices': len(footprint), 'filled_cells_before_overlap': count})
    return cells, {'origin_wgs84': {'longitude': lon0, 'latitude': lat0}, 'buildings': records}


def write_datapack(output: Path, cells: set[tuple[int, int, int]], manifest: dict, block: str, base_y: int = 0) -> None:
    if output.exists():
        raise FileExistsError(f'refusing to overwrite {output}')
    if not output.parent.is_dir():
        raise ValueError(f'output parent does not exist: {output.parent}')
    # Cells are vertical building columns. Merge contiguous Y cells so a city
    # does not become millions of separate command lines.
    columns: dict[tuple[int, int], list[int]] = {}
    for x, y, z in cells:
        columns.setdefault((x, z), []).append(y)
    commands = []
    for (x, z), ys in sorted(columns.items()):
        start = previous = min(ys)
        for y in sorted(ys)[1:]:
            if y != previous + 1:
                commands.append(f'fill {x} {start + base_y} {z} {x} {previous + base_y} {z} minecraft:{block}')
                start = y
            previous = y
        commands.append(f'fill {x} {start + base_y} {z} {x} {previous + base_y} {z} minecraft:{block}')
    manifest.update({'format': 'Earthcraft KML geometry datapack', 'block': f'minecraft:{block}',
                     'occupied_blocks': len(cells), 'scale': '1 Minecraft block = 1 local tangent-plane metre',
                     'minecraft_commands': len(commands),
                     'geometry': 'solid vertical extrusion of explicit KML building polygons',
                     'height_quantization': 'each explicit height is rounded up to whole one-metre blocks',
                     'base_y': base_y,
                     'unknown': 'terrain, roofs, façades, interiors, vegetation, roads, and all unexported features',
                     'inference': 'none; deterministic geometry only'})
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('pack.mcmeta', json.dumps({'pack': {'pack_format': 88, 'description': 'Earthcraft deterministic KML import'}}))
        archive.writestr('data/earthcraft/function/build.mcfunction', '\n'.join(commands) + '\n')
        archive.writestr('earthcraft-manifest.json', json.dumps(manifest, indent=2, sort_keys=True) + '\n')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='Google Earth KML or KMZ geometry export')
    parser.add_argument('--output', type=Path, required=True, help='new .zip datapack path')
    parser.add_argument('--block', default='stone_bricks', help='vanilla block used for observed geometry')
    parser.add_argument('--base-y', type=int, default=0, help='Minecraft Y coordinate for local ground')
    parser.add_argument('--max-blocks', type=int, default=2_000_000, help='safety limit before writing')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9_/]+', args.block):
        parser.error('--block must be an unqualified vanilla block id')
    raw = read_kml(args.input)
    buildings = parse_kml_bytes(raw)
    cells, manifest = occupied_cells(buildings)
    if len(cells) > args.max_blocks:
        raise ValueError(f'{len(cells)} blocks exceeds --max-blocks={args.max_blocks}')
    manifest.update({'input': str(args.input), 'input_sha256': hashlib.sha256(raw).hexdigest(),
                     'source_contract': 'user-provided KML/KMZ geometry; no Google imagery acquisition or analysis'})
    write_datapack(args.output, cells, manifest, args.block, args.base_y)
    print(json.dumps({'output': str(args.output), 'buildings': len(buildings), 'occupied_blocks': len(cells)}, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, FileExistsError, ET.ParseError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(2)
