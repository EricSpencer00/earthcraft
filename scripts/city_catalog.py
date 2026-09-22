"""Validated WGS84 city destinations shared by build and travel controls."""
import json
import math
from pathlib import Path

from pyproj import CRS, Transformer


SCHEMA = 'earthcraft-city-catalog-v1'
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / 'configs/cities.json'


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return float(value)


def validate(catalog):
    if catalog.get('schema') != SCHEMA:
        raise ValueError('Unsupported city catalog schema')
    cities = catalog.get('cities')
    if not isinstance(cities, list) or not cities:
        raise ValueError('City catalog must contain at least one city')
    seen = set()
    for city in cities:
        identifier = city.get('id')
        if not isinstance(identifier, str) or not identifier or identifier in seen or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in identifier):
            raise ValueError('City ids must be unique lowercase slugs')
        seen.add(identifier)
        coordinates = city.get('wgs84')
        if not isinstance(coordinates, list) or len(coordinates) != 2:
            raise ValueError(f'{identifier} requires [longitude, latitude]')
        longitude = _number(coordinates[0], f'{identifier} longitude')
        latitude = _number(coordinates[1], f'{identifier} latitude')
        if not -180 <= longitude < 180 or not -85 < latitude < 85:
            raise ValueError(f'{identifier} is outside the supported WGS84 travel range')
        if 'target' in city:
            target = city['target']
            if not isinstance(target, list) or len(target) != 3 or any(not math.isfinite(float(v)) for v in target):
                raise ValueError(f'{identifier} target must be [x, y, z]')
        if not isinstance(city.get('world'), str) or not city['world']:
            raise ValueError(f'{identifier} requires a world name')
    return catalog


def load(path=DEFAULT_PATH):
    if isinstance(path, dict):
        return validate(path)
    return validate(json.loads(Path(path).read_text()))


def materialized(catalog):
    """Return destinations that have an in-world target, in catalog order."""
    return [city for city in validate(catalog)['cities'] if city.get('target') is not None]


def shared_world_coordinates(longitude, latitude, frame, y):
    """Transpose WGS84 into the Chicago-compatible east/+X, south/+Z frame."""
    longitude = _number(longitude, 'longitude')
    latitude = _number(latitude, 'latitude')
    y = _number(y, 'y')
    crs = CRS.from_user_input(frame['crs'])
    easting, northing = Transformer.from_crs(4326, crs, always_xy=True).transform(longitude, latitude)
    return [easting - frame['west'], y, frame['north'] - northing]
