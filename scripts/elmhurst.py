"""Build Elmhurst, IL in its canonical scale-bounded atlas page."""
import argparse
import json
from pathlib import Path

from city_catalog import load as load_catalog, shared_world_coordinates
from geographic_quality import audit
from global_projection import (
    ATLAS_SCHEMA,
    address_for,
    page_crs,
    page_generation_envelope,
    page_manifest,
    page_coordinates,
)
from metric_world import build
from public_map_sources import prepare
from travel_controls import install_controls
from verify_metric_world import verify


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRAME = ROOT / 'runs/chicago-adaptation-city-001/frame.json'
ELMHURST_ID = 'elmhurst-il'


def target_for_city(city, frame, y, world_offset_xz, size):
    """Return the city's exact WGS84 point in the supplied page-local frame."""
    longitude, latitude = city['wgs84']
    target = shared_world_coordinates(longitude, latitude, frame, y)
    west, north = world_offset_xz
    if not (west <= target[0] < west + size and north <= target[2] < north + size):
        raise ValueError('Elmhurst WGS84 target lies outside its generated tile')
    return target


def atlas_frame_for_city(city, vertical_frame):
    """Create the canonical local metric frame for this city's atlas page.

    Horizontal coordinates come only from the formula-addressed atlas page;
    the existing Chicago frame contributes the shared gameplay Y datum/limits,
    never the city's horizontal placement.
    """
    longitude, latitude = city['wgs84']
    address = address_for(longitude, latitude)
    manifest = page_manifest(address)
    envelope = page_generation_envelope(address)
    frame = {
        'crs': page_crs(address).to_wkt(),
        'west': envelope['west'],
        'north': envelope['north'],
        'vertical_offset_m': vertical_frame['vertical_offset_m'],
        'dimension_min_y': vertical_frame['dimension_min_y'],
        'dimension_height': vertical_frame['dimension_height'],
        'atlas_schema': ATLAS_SCHEMA,
        'atlas_page_id': address.id,
        'proj_version': manifest['proj_version'],
        'page_scale_error_ppm': manifest['projection']['maximum_sampled_scale_error_ppm'],
        'page_scale_error_budget_ppm': manifest['projection']['scale_error_budget_ppm'],
    }
    return frame, address


def build_elmhurst(output, frame_path=DEFAULT_FRAME, size=256, catalog_path=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    vertical_frame = json.loads(Path(frame_path).read_text())
    catalog = load_catalog(catalog_path) if catalog_path else load_catalog()
    city = next((entry for entry in catalog['cities'] if entry['id'] == ELMHURST_ID), None)
    if city is None:
        raise ValueError(f'{ELMHURST_ID} is missing from the city catalog')
    longitude, latitude = city['wgs84']
    frame, address = atlas_frame_for_city(city, vertical_frame)
    source = output / 'source'
    world = output / 'world'
    prepare(longitude, latitude, size, source, frame=frame)
    build(source, world, world_frame=frame)
    metadata = json.loads((world / 'earthcraft.json').read_text())
    catalog_for_world = json.loads(json.dumps(catalog))
    destination = next(entry for entry in catalog_for_world['cities'] if entry['id'] == ELMHURST_ID)
    target = target_for_city(
        city, frame, metadata['spawn'][1], metadata['world_offset_xz'], size)
    atlas_point = page_coordinates(longitude, latitude, address)
    if abs(target[0] - atlas_point['x']) > 1e-7 or abs(target[2] - atlas_point['z']) > 1e-7:
        raise ValueError('Elmhurst WGS84 target disagrees with its atlas page coordinates')
    destination['target'] = target
    destination['status'] = 'generated'
    destination['atlas_location'] = {
        'schema': ATLAS_SCHEMA,
        'page_id': address.id,
        'page_local_xz': [atlas_point['x'], atlas_point['z']],
        'proj_version': frame['proj_version'],
        'maximum_sampled_scale_error_ppm': frame['page_scale_error_ppm'],
        'scale_error_budget_ppm': frame['page_scale_error_budget_ppm'],
    }
    install_controls(world, city_catalog=catalog_for_world)
    block_report = verify(world)
    quality_report = audit(world)
    result = {
        'city': city['name'],
        'location_wgs84': city['wgs84'],
        'atlas_page': address.id,
        'atlas_frame': frame,
        'vertical_reference_frame': {
            'vertical_offset_m': frame['vertical_offset_m'],
            'dimension_min_y': frame['dimension_min_y'],
            'dimension_height': frame['dimension_height'],
        },
        'world': str(world.resolve()),
        'world_offset_xz': metadata['world_offset_xz'],
        'spawn': metadata['spawn'],
        'block_verification': block_report,
        'geographic_quality': quality_report,
        'same_page_frame_as_atlas': metadata['world_frame'] == frame,
        'chicago_horizontal_frame_used': False,
        'inference_used': False,
    }
    (output / 'city-catalog.json').write_text(json.dumps(catalog_for_world, indent=2))
    (output / 'elmhurst-build.json').write_text(json.dumps(result, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frame', type=Path, default=DEFAULT_FRAME)
    parser.add_argument('--size', type=int, default=256)
    parser.add_argument('--catalog', type=Path)
    args = parser.parse_args()
    if args.size < 16 or args.size > 512 or args.size % 16:
        parser.error('Size must be 16..512 and aligned to 16 m')
    print(json.dumps(build_elmhurst(args.output, args.frame, args.size, args.catalog), indent=2), flush=True)


if __name__ == '__main__':
    main()
