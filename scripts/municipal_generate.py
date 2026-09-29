"""Generate an entire municipal polygon in an existing world's metric frame.

Uses one frozen local OSM extract rather than per-tile Overpass requests. The
default explicit-height profile omits missing measurements. Optional display
estimates are separate from observations and recorded in their own receipts.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import time

import osmium
from pyproj import Transformer
from shapely.geometry import LineString, Polygon, box
from shapely import make_valid

from aws_terrain import prepare as terrain
from chicago_tiles import Journal, tile_plan
from metric_world import build
from verify_metric_world import verify
from region_expansion import atomic, sha
from osm_json_to_kml import metres, building_tag


def presentation_ways(ways):
    """Keep observations untouched; annotate deterministic display estimates."""
    result, estimates = [], []
    widths = {'motorway': 12, 'trunk': 10, 'primary': 9, 'secondary': 8,
              'tertiary': 7, 'residential': 6, 'unclassified': 6,
              'service': 4, 'living_street': 4, 'footway': 2,
              'pedestrian': 3, 'path': 2, 'cycleway': 2, 'steps': 2, 'track': 3}
    for original in ways:
        way = dict(original, tags=dict(original['tags']))
        tags, derived = way['tags'], {}
        if building_tag(tags) and not metres(tags.get('height', '')):
            try:
                levels = float(tags.get('building:levels', ''))
            except ValueError:
                levels = None
            if levels is not None and math.isfinite(levels) and 0 < levels <= 100:
                height, reason = levels*3, 'mapped levels times presentation assumption of 3 m per level'
            else:
                height = 3 if tags.get('building') in ('garage', 'garages', 'shed') else 6
                reason = 'deterministic presentation height; no measured height available'
            tags['height'] = str(height)
            derived['height'] = {'metres': height, 'reason': reason, 'measured': False}
        road = tags.get('highway')
        if road in widths and not metres(tags.get('width', '')):
            tags['width'] = str(widths[road])
            derived['width'] = {'metres': widths[road], 'reason': 'road-class presentation width', 'measured': False}
        if derived:
            estimates.append({'osm_way': way['id'], 'derived_attributes': derived})
        result.append(way)
    return result, estimates


def local_ways(pbf, destination, bounds):
    """Freeze complete way observations intersecting one municipality envelope."""
    identity = {'source': str(pbf.resolve()), 'source_sha256': sha(pbf), 'bounds': list(bounds)}
    receipt = destination.with_suffix('.receipt.json')
    if receipt.exists():
        proof = json.loads(receipt.read_text())
        if proof['request'] != identity or sha(destination) != proof['ways_sha256']:
            raise ValueError('Frozen municipal OSM extract changed')
        return json.loads(destination.read_text())
    west, south, east, north = bounds
    ways = []
    class Select(osmium.SimpleHandler):
        def way(self, way):
            if any(not node.location.valid() for node in way.nodes):
                raise ValueError('Incomplete local OSM way geometry')
            coords = [(node.lon, node.lat) for node in way.nodes]
            if len(coords) < 2:
                return
            xx, yy = zip(*coords)
            if max(xx) < west or min(xx) > east or max(yy) < south or min(yy) > north:
                return
            ways.append({'id': way.id, 'tags': dict(way.tags), 'coordinates': coords,
                         'closed': way.nodes[0].ref == way.nodes[-1].ref})
    Select().apply_file(str(pbf), locations=True, idx='flex_mem')
    atomic(destination, json.dumps(ways).encode())
    atomic(receipt, json.dumps({'request': identity, 'ways_sha256': sha(destination),
        'ways': len(ways), 'license': 'ODbL 1.0', 'attribution': 'OpenStreetMap contributors',
        'source_order_preserved': True, 'multipolygon_relations_acquired': False}).encode())
    return ways


def projected_ways(ways, frame):
    project = Transformer.from_crs(4326, frame['crs'], always_xy=True)
    result = []
    for way in ways:
        xx, yy = project.transform(*zip(*way['coordinates']))
        points = list(zip(xx, yy))
        geometry = make_valid(Polygon(points)) if way['closed'] and len(points) >= 4 else LineString(points)
        result.append((way, geometry))
    return result


def generate(boundary, frame_path, pbf, plan_dir, output, city_name, presentation=False):
    frame = json.loads(frame_path.read_text())
    document = json.loads(boundary.read_text())
    plan = tile_plan(document, frame)
    plan['scope'] = f'{city_name} municipal polygon; complete intersecting tiles retain boundary context'
    plan['presentation_estimates'] = presentation
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_path = plan_dir/'plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text()) != plan:
        raise ValueError('Municipal plan changed; preserve the old run')
    atomic(plan_path, json.dumps(plan, indent=2).encode())
    output.mkdir(parents=True, exist_ok=True)
    inverse = Transformer.from_crs(frame['crs'], 4326, always_xy=True)
    west = min(t['west'] for t in plan['tiles'])-32
    east = max(t['west']+t['size'] for t in plan['tiles'])+32
    south = min(t['north']-t['size'] for t in plan['tiles'])-32
    north = max(t['north'] for t in plan['tiles'])+32
    corners = [inverse.transform(x, y) for x in (west, east) for y in (south, north)]
    bounds = [min(c[0] for c in corners), min(c[1] for c in corners),
              max(c[0] for c in corners), max(c[1] for c in corners)]
    ways = projected_ways(local_ways(pbf, plan_dir/'osm-ways.json', bounds), frame)
    journal = Journal(plan_dir/'jobs.sqlite', plan)
    owner = 'municipal-worker'
    journal.requeue_owner_leases(owner)
    tiles = {tile['id']: tile for tile in plan['tiles']}
    print(json.dumps({'city': city_name, 'tiles': len(tiles), 'area_m2': plan['city_area_m2'],
                      'osm_ways': len(ways)}), flush=True)
    try:
        while True:
            job = journal.claim('geometry', owner, lease_seconds=3600)
            if job:
                tile = tiles[job['tile']]
                root = output/tile['id']
                world = root/'world'
                started = time.monotonic()
                if not world.exists():
                    staging = root/'world.building'
                    if staging.exists():
                        # An interrupted directory is retained for inspection.
                        staging.rename(root/f'world.incomplete-{time.time_ns()}')
                    build(root/'sources', staging, world_frame=frame)
                    verify(staging)
                    staging.rename(world)
                checks = verify(world)
                proof = {'tile': tile['id'], 'stage': 'geometry', 'result': 'pass', 'checks': checks,
                    'regions': {p.name: sha(p) for p in (world/'region').glob('r.*.*.mca')},
                    'world_manifest_sha256': sha(world/'earthcraft.json'),
                    'seconds': time.monotonic()-started, 'physical_accuracy_verified': False,
                    'appearance_complete': False, 'installed': False}
                receipt = root/'geometry-receipt.json'
                atomic(receipt, json.dumps(proof, indent=2).encode())
                journal.finish(job, receipt)
                print(json.dumps({'generated': tile['id'], 'stages': journal.summary()}), flush=True)
                continue
            job = journal.claim('sources', owner, lease_seconds=3600)
            if not job:
                break
            tile = tiles[job['tile']]
            root = output/tile['id']
            root.mkdir(exist_ok=True)
            source = root/'sources'
            if not (source/'sources.json').exists():
                lon, lat = inverse.transform(tile['west']+tile['size']/2, tile['north']-tile['size']/2)
                grid = {'size': tile['size'], 'west': tile['west'], 'north': tile['north'],
                    'crs': frame['crs'], 'projection_origin': [lat, lon],
                    'axes': 'east +X, south +Z, elevation +Y', 'metres_per_block': 1,
                    'cell_ground_distances_m': [], 'shared_frame': True}
                terrain(lon, lat, tile['size'], source, cache=output/'terrain-cache', chart_meta=grid)
            extent = box(tile['west'], tile['north']-tile['size'],
                         tile['west']+tile['size'], tile['north'])
            selected = [way for way, geometry in ways if geometry.intersects(extent)]
            estimates = []
            if presentation:
                selected, estimates = presentation_ways(selected)
                atomic(source/'presentation-estimates.json', json.dumps(estimates, indent=2).encode())
            atomic(source/'osm-ways.json', json.dumps(selected).encode())
            meta = json.loads((source/'sources.json').read_text())
            meta.update(buildings_available=True,
                building_source_kind='osm-presentation' if presentation else 'osm-explicit',
                osm_subset_sha256=sha(source/'osm-ways.json'),
                osm_source_receipt_sha256=sha(plan_dir/'osm-ways.receipt.json'),
                osm_license='ODbL 1.0; OpenStreetMap contributors',
                building_height_policy='Explicit metre heights only; missing heights omitted',
                purpose='Municipal terrain and mapped geometry; source-limited base coverage',
                missing_layers=['complete building heights', 'unmapped road widths',
                    'complete ground cover', 'facade imagery', 'multipolygon relations'])
            if presentation:
                meta.update(presentation_estimates_sha256=sha(source/'presentation-estimates.json'),
                    estimated_attribute_ways=len(estimates), unclassified_ground_block='grass_block',
                    building_height_policy='Observed heights first; mapped levels and fixed display defaults are explicitly estimated',
                    unclassified_ground_policy='Grass presentation only; land cover not measured')
            atomic(source/'sources.json', json.dumps(meta, indent=2).encode())
            receipt = root/'sources-receipt.json'
            atomic(receipt, json.dumps({'tile': tile['id'], 'stage': 'sources', 'result': 'pass',
                'tile_output': str(root.resolve()), 'sources_sha256': sha(source/'sources.json')}).encode())
            journal.finish(job, receipt)
    finally:
        journal.close()
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boundary', type=Path, required=True)
    parser.add_argument('--frame', type=Path, required=True)
    parser.add_argument('--osm', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--city-name', required=True)
    parser.add_argument('--presentation', action='store_true',
                        help='Show mapped buildings and roads with explicitly labelled defaults where measurements are missing')
    args = parser.parse_args()
    generate(args.boundary, args.frame, args.osm, args.plan, args.output, args.city_name, args.presentation)


if __name__ == '__main__':
    main()
