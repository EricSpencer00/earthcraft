"""Deterministic whole-Earth atlas addressing and local metre projections.

The atlas is an index, not a single map projection.  WGS84 is the durable
identity of a place.  Formula-selected pages provide bounded metric charts for
generation and page-local Minecraft coordinates for playback.
"""
import argparse
from dataclasses import dataclass
from functools import lru_cache
import json
import math

from pyproj import CRS, Geod, Proj, Transformer


ATLAS_SCHEMA = "earthcraft-atlas-v1"
PAGE_TARGET_SPAN_M = 16_384
TILE_SIZE_M = 256
CHUNK_SIZE_M = 16
HALO_M = 512
BOUNDARY_SEGMENTS = 16

_GEOD = Geod(ellps="WGS84")
_MERIDIAN_LENGTH_M = _GEOD.inv(0, -90, 0, 90)[2]
BAND_COUNT = math.ceil(_MERIDIAN_LENGTH_M / PAGE_TARGET_SPAN_M)
BAND_HEIGHT_M = _MERIDIAN_LENGTH_M / BAND_COUNT


@dataclass(frozen=True, order=True)
class PageAddress:
    band: int
    column: int

    @property
    def id(self):
        return f"ec1/b{self.band:04d}/c{self.column:04d}"


def _latitude_at_meridian_distance(distance_m):
    distance_m = min(_MERIDIAN_LENGTH_M, max(0.0, distance_m))
    return _GEOD.fwd(0, -90, 0, distance_m)[1]


@lru_cache(maxsize=None)
def band_latitudes(band):
    if not isinstance(band, int) or not 0 <= band < BAND_COUNT:
        raise ValueError("Atlas band outside range")
    south = _latitude_at_meridian_distance(band * BAND_HEIGHT_M)
    north = _latitude_at_meridian_distance((band + 1) * BAND_HEIGHT_M)
    center = _latitude_at_meridian_distance((band + 0.5) * BAND_HEIGHT_M)
    return south, center, north


def _parallel_circumference(latitude):
    radians = math.radians(latitude)
    semi_major = _GEOD.a
    eccentricity_squared = _GEOD.es
    radius = semi_major * math.cos(radians) / math.sqrt(
        1 - eccentricity_squared * math.sin(radians) ** 2
    )
    return 2 * math.pi * max(0.0, radius)


@lru_cache(maxsize=None)
def columns_in_band(band):
    _, center, _ = band_latitudes(band)
    return max(1, math.ceil(_parallel_circumference(center) / PAGE_TARGET_SPAN_M))


WORLD_PAGE_COUNT = sum(columns_in_band(band) for band in range(BAND_COUNT))


def atlas_summary():
    return {
        "schema": ATLAS_SCHEMA,
        "inference_used": False,
        "materialization": "sparse-on-request",
        "target_page_span_m": PAGE_TARGET_SPAN_M,
        "band_count": BAND_COUNT,
        "page_count": WORLD_PAGE_COUNT,
        "tile_size_m": TILE_SIZE_M,
        "chunk_size_m": CHUNK_SIZE_M,
        "source_halo_m": HALO_M,
    }


def _canonical_longitude(longitude, latitude):
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise ValueError("Finite WGS84 coordinates required")
    if not -90 <= latitude <= 90:
        raise ValueError("Latitude outside WGS84 range")
    if abs(latitude) == 90:
        return 0.0
    return (longitude + 180) % 360 - 180


def address_for(longitude, latitude):
    """Select exactly one atlas page from a WGS84 coordinate."""
    longitude = _canonical_longitude(longitude, latitude)
    distance = _GEOD.inv(0, -90, 0, latitude)[2]
    band = min(BAND_COUNT - 1, int(distance / BAND_HEIGHT_M))
    columns = columns_in_band(band)
    column = min(columns - 1, int((longitude + 180) / 360 * columns))
    return PageAddress(band, column)


@lru_cache(maxsize=None)
def page_geographic_bounds(address):
    columns = columns_in_band(address.band)
    if not 0 <= address.column < columns:
        raise ValueError("Atlas column outside band range")
    south, center_latitude, north = band_latitudes(address.band)
    west = -180 + 360 * address.column / columns
    east = -180 + 360 * (address.column + 1) / columns
    return {
        "west": west,
        "south": south,
        "east": east,
        "north": north,
        "center_longitude": (west + east) / 2,
        "center_latitude": center_latitude,
    }


@lru_cache(maxsize=None)
def page_crs(address):
    bounds = page_geographic_bounds(address)
    projection = "aeqd" if address.band in (0, BAND_COUNT - 1) else "tmerc"
    return CRS.from_proj4(
        f"+proj={projection} +lat_0={bounds['center_latitude']:.12f} "
        f"+lon_0={bounds['center_longitude']:.12f} +datum=WGS84 +units=m +no_defs"
    )


def _boundary_points(bounds):
    points = []
    for index in range(BOUNDARY_SEGMENTS + 1):
        fraction = index / BOUNDARY_SEGMENTS
        longitude = bounds["west"] + (bounds["east"] - bounds["west"]) * fraction
        latitude = bounds["south"] + (bounds["north"] - bounds["south"]) * fraction
        points.extend(
            [
                (longitude, bounds["south"]),
                (longitude, bounds["north"]),
                (bounds["west"], latitude),
                (bounds["east"], latitude),
            ]
        )
    return points


def _floor_to(value, quantum):
    return math.floor(value / quantum) * quantum


def _ceil_to(value, quantum):
    return math.ceil(value / quantum) * quantum


@lru_cache(maxsize=None)
def page_generation_envelope(address):
    """Chunk-aligned local-projection envelope, including a source halo."""
    bounds = page_geographic_bounds(address)
    forward = Transformer.from_crs(4326, page_crs(address), always_xy=True)
    projected = [forward.transform(*point) for point in _boundary_points(bounds)]
    eastings = [point[0] for point in projected]
    northings = [point[1] for point in projected]
    return {
        "west": _floor_to(min(eastings), TILE_SIZE_M) - HALO_M,
        "south": _floor_to(min(northings), TILE_SIZE_M) - HALO_M,
        "east": _ceil_to(max(eastings), TILE_SIZE_M) + HALO_M,
        "north": _ceil_to(max(northings), TILE_SIZE_M) + HALO_M,
    }


def _projection_error_ppm(address):
    bounds = page_geographic_bounds(address)
    projection = Proj(page_crs(address))
    errors = []
    for longitude, latitude in _boundary_points(bounds):
        latitude = min(89.999999999, max(-89.999999999, latitude))
        factors = projection.get_factors(longitude, latitude)
        errors.extend(
            [abs(factors.meridional_scale - 1), abs(factors.parallel_scale - 1)]
        )
    return max(errors) * 1_000_000


def page_manifest(address):
    """Materialize the versioned, JSON-safe build contract for one page."""
    bounds = page_geographic_bounds(address)
    envelope = page_generation_envelope(address)
    columns = int((envelope["east"] - envelope["west"]) / TILE_SIZE_M)
    rows = int((envelope["north"] - envelope["south"]) / TILE_SIZE_M)
    projection = page_crs(address)
    return {
        "schema": ATLAS_SCHEMA,
        "page_id": address.id,
        "selection": "fixed WGS84 meridian-distance band and longitude column formula",
        "inference_used": False,
        "geographic_bounds": bounds,
        "projection": {
            "method": projection.coordinate_operation.method_name,
            "wkt": projection.to_wkt(),
            "maximum_sampled_scale_error_ppm": _projection_error_ppm(address),
        },
        "generation": {
            "tile_size_m": TILE_SIZE_M,
            "chunk_size_m": CHUNK_SIZE_M,
            "source_halo_m": HALO_M,
            "envelope": envelope,
            "columns": columns,
            "rows": rows,
            "axes": "east +X, south +Z, elevation +Y",
        },
    }


def page_coordinates(longitude, latitude, address=None):
    """Map WGS84 into a page's non-negative X/Z generation frame."""
    address = address or address_for(longitude, latitude)
    easting, northing = Transformer.from_crs(
        4326, page_crs(address), always_xy=True
    ).transform(longitude, latitude)
    envelope = page_generation_envelope(address)
    return {
        "page": address,
        "x": easting - envelope["west"],
        "z": envelope["north"] - northing,
    }


def geographic_coordinates(address, x, z):
    """Recover WGS84 from page-local X/Z for deterministic page rebasing."""
    envelope = page_generation_envelope(address)
    longitude, latitude = Transformer.from_crs(
        page_crs(address), 4326, always_xy=True
    ).transform(envelope["west"] + x, envelope["north"] - z)
    return longitude, latitude


def rebase_coordinates(source, x, z, destination=None):
    """Translate page-local coordinates through WGS84 into another page."""
    longitude, latitude = geographic_coordinates(source, x, z)
    destination = destination or address_for(longitude, latitude)
    return page_coordinates(longitude, latitude, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--summary", action="store_true")
    target.add_argument("--point", nargs=2, type=float, metavar=("LON", "LAT"))
    target.add_argument("--page", nargs=2, type=int, metavar=("BAND", "COLUMN"))
    arguments = parser.parse_args()
    if arguments.summary:
        result = atlas_summary()
    elif arguments.point:
        result = page_manifest(address_for(*arguments.point))
    else:
        result = page_manifest(PageAddress(*arguments.page))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
