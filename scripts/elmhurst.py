"""Build Elmhurst, IL in the existing Chicago shared metric frame."""
import argparse
import json
from pathlib import Path

from city_catalog import load as load_catalog, shared_world_coordinates
from geographic_quality import audit
from metric_world import build
from public_map_sources import prepare
from travel_controls import install_controls
from verify_metric_world import verify


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRAME = ROOT / 'runs/chicago-adaptation-city-001/frame.json'
ELMHURST_ID = 'elmhurst-il'


def target_for_city(city, frame, y, world_offset_xz, size):
    """Return the city's exact shared-frame WGS84 point, not a preview spawn."""
    longitude, latitude = city['wgs84']
    target = shared_world_coordinates(longitude, latitude, frame, y)
    west, north = world_offset_xz
    if not (west <= target[0] < west + size and north <= target[2] < north + size):
        raise ValueError('Elmhurst WGS84 target lies outside its generated tile')
    return target


def build_elmhurst(output, frame_path=DEFAULT_FRAME, size=256, catalog_path=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    frame = json.loads(Path(frame_path).read_text())
    catalog = load_catalog(catalog_path) if catalog_path else load_catalog()
    city = next((entry for entry in catalog['cities'] if entry['id'] == ELMHURST_ID), None)
    if city is None:
        raise ValueError(f'{ELMHURST_ID} is missing from the city catalog')
    longitude, latitude = city['wgs84']
    source = output / 'source'
    world = output / 'world'
    prepare(longitude, latitude, size, source, frame=frame)
    build(source, world, world_frame=frame)
    metadata = json.loads((world / 'earthcraft.json').read_text())
    catalog_for_world = json.loads(json.dumps(catalog))
    destination = next(entry for entry in catalog_for_world['cities'] if entry['id'] == ELMHURST_ID)
    destination['target'] = target_for_city(
        city, frame, metadata['spawn'][1], metadata['world_offset_xz'], size)
    destination['status'] = 'generated'
    install_controls(world, city_catalog=catalog_for_world)
    block_report = verify(world)
    quality_report = audit(world)
    result = {
        'city': city['name'],
        'location_wgs84': city['wgs84'],
        'shared_frame': frame,
        'world': str(world.resolve()),
        'world_offset_xz': metadata['world_offset_xz'],
        'spawn': metadata['spawn'],
        'block_verification': block_report,
        'geographic_quality': quality_report,
        'same_frame_as_chicago': metadata['world_frame'] == frame,
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
