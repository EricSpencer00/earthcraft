"""Keyless ground cover for every delivered tile: paving, bare soil, fields, prairie and wetland.

The generated ground is one grass block everywhere roads and water are not. Per 1 m cell this pass
reads the tile's NAIP crop (public domain, the same imagery roof colour uses: red, green, blue and
near-infrared), ESA WorldCover 2021 (10 m classes, CC BY 4.0) and OSM areas (amenity=parking,
natural=wetland), and changes only cells whose top block is still the generic grass:

* not vegetated (NDVI <= 0.12), bright and neutral grey: concrete (sidewalks, driveways, patios);
  darker neutral grey: asphalt (parking, driveways); clearly redder than green (NDVI <= 0.08): bare
  soil, so dry August grass stays grass ('observed' colour);
* WorldCover cropland: a planted field (farmland with a crop on it); grassland: prairie grass on
  top; herbaceous wetland or OSM wetland: moss, or mud where nothing grows;
* OSM parking that the image does not show as vegetated: asphalt ('tagged').

Shadowed or invalid imagery keeps the grass. Only the ground block and air above it change, so
buildings, roads, water and trees are untouched. Small specks are removed before edits are made, and
small holes in paving that are much less green than the tile's lawns (paint, cars) become paving.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
from affine import Affine
from pyproj import Transformer
from rasterio.features import rasterize
from scipy import ndimage
from shapely import make_valid
from shapely.geometry import Polygon

from detail_layer import LayerBuilder

WORLDCOVER = ('https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/'
              'ESA_WorldCover_10m_2021_v200_{tile}_Map.tif')
WORLDCOVER_LICENSE = 'CC BY 4.0, ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021)'
NAIP_LICENSE = 'Public domain (USDA NAIP via USGS The National Map)'
OSM_LICENSE = 'ODbL 1.0 (c) OpenStreetMap contributors'
CROPLAND, GRASSLAND, WETLAND = 40, 30, 90
GRASS = 'minecraft:grass_block'

# class id -> (ground block, block above or None, kind)
KEEP, CONCRETE, ASPHALT, SOIL, FIELD, PRAIRIE, MARSH, MUD = range(8)
CLASSES = {
    CONCRETE: ('minecraft:light_gray_concrete', None, 'ground_concrete'),
    ASPHALT: ('minecraft:gray_concrete', None, 'ground_asphalt'),
    SOIL: ('minecraft:coarse_dirt', None, 'ground_soil'),
    FIELD: ('minecraft:farmland[moisture=7]', 'minecraft:wheat[age=7]', 'ground_field'),
    PRAIRIE: (None, 'minecraft:short_grass', 'ground_prairie'),
    MARSH: ('minecraft:moss_block', None, 'ground_wetland'),
    MUD: ('minecraft:mud', None, 'ground_wetland'),
}
MIN_REGION = {CONCRETE: 3, ASPHALT: 12, SOIL: 16, FIELD: 64, PRAIRIE: 64, MARSH: 16, MUD: 16}  # cells
MAX_HOLE, HOLE_NDVI_GAP = 128, 0.2  # holes in paving smaller than this, this much less green than lawn, are paving


def stable(*values):
    return int.from_bytes(hashlib.sha256('|'.join(map(str, values)).encode()).digest()[:2], 'big') / 65536


def worldcover(meta):
    """WorldCover classes on the tile's 1 m grid (nearest), or None when unreachable."""
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject
    size = meta['size']
    lon, lat = Transformer.from_crs(meta['crs'], 4326, always_xy=True).transform(
        [meta['west'], meta['west'] + size] * 2, [meta['north']] * 2 + [meta['north'] - size] * 2)
    tiles = {(int(np.floor(la / 3) * 3), int(np.floor(lo / 3) * 3)) for lo, la in zip(lon, lat)}
    out = np.zeros((size, size), np.uint8)
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', GDAL_HTTP_MAX_RETRY='3'):
            for la, lo in sorted(tiles):
                name = f"{'N' if la >= 0 else 'S'}{abs(la):02d}{'E' if lo >= 0 else 'W'}{abs(lo):03d}"
                with rasterio.open('/vsicurl/' + WORLDCOVER.format(tile=name)) as source:
                    part = np.zeros((size, size), np.uint8)
                    reproject(rasterio.band(source, 1), part, dst_transform=from_origin(meta['west'], meta['north'], 1, 1),
                              dst_crs=meta['crs'], resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
                    out = np.where(out == 0, part, out)
    except Exception:  # noqa: BLE001 - classes are context only; NAIP still decides paving and soil
        return None
    return out


def osm_areas(sources, meta):
    """(parking, wetland) masks from closed OSM ways on the tile grid."""
    size, west, north = meta['size'], meta['west'], meta['north']
    project = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    parking, wetland = [], []
    for way in json.loads((Path(sources) / 'osm-ways.json').read_text()):
        tags = way['tags']
        is_parking = tags.get('amenity') == 'parking' and tags.get('parking') not in ('underground', 'multi-storey')
        is_wetland = tags.get('natural') == 'wetland'
        if not (is_parking or is_wetland) or not way.get('closed') or len(way['coordinates']) < 4:
            continue
        x, y = project.transform(*zip(*way['coordinates']))
        geom = make_valid(Polygon(zip(np.asarray(x) - west, north - np.asarray(y))))
        if not geom.is_empty:
            (parking if is_parking else wetland).append((geom, 1))

    def mask(shapes):
        return (rasterize(shapes, out_shape=(size, size), transform=Affine.identity(), fill=0, dtype='uint8') > 0
                if shapes else np.zeros((size, size), bool))
    return mask(parking), mask(wetland)


def classify(bands, valid, ndvi, shadow, cover, parking, wetland, editable):
    """Class id per cell (KEEP where nothing changes) and a per-class evidence note."""
    red, green, blue, nir = bands.astype(np.float32)
    rgb = np.stack([red, green, blue])
    bright, spread = rgb.mean(axis=0), rgb.max(axis=0) - rgb.min(axis=0)
    seen = valid & ~shadow & np.isfinite(ndvi)
    bare = seen & (ndvi <= 0.12)
    lush = seen & (ndvi > 0.3)
    cover = np.zeros(bright.shape, np.uint8) if cover is None else cover
    cls = np.full(bright.shape, KEEP, np.uint8)
    grey = bare & (spread < 22)  # neutral grey: pavement, not dark soil or shade
    cls[grey & (bright >= 140)] = CONCRETE
    cls[grey & (bright < 140)] = ASPHALT
    soil = seen & (ndvi <= 0.08) & ~grey & (red > green + 6) & (red > blue + 18)  # redder than green: soil, not dry grass
    cls[soil] = SOIL
    cls[parking & ~lush & seen] = ASPHALT
    fieldish = (cover == CROPLAND) & ~parking
    cls[fieldish & (seen & ~bare)] = FIELD
    cls[(cover == GRASSLAND) & lush & ~parking] = PRAIRIE
    wet = (cover == WETLAND) | wetland
    cls[wet & lush] = MARSH
    cls[wet & soil] = MUD
    cls[~editable] = KEEP
    for value, minimum in MIN_REGION.items():  # drop specks: a single grey pixel is noise, not a sidewalk
        labels, count = ndimage.label(cls == value)
        if count:
            sizes = np.bincount(labels.ravel())
            cls[(labels > 0) & (sizes[labels] < minimum)] = KEEP
    return fill_paving_holes(cls, ndvi, seen, editable)


def fill_paving_holes(cls, ndvi, seen, editable):
    """Small grass holes inside paving that are far less green than the tile's lawns (paint, cars,
    oil stains) take the surrounding paving; a green island in a parking lot stays grass."""
    paving = (cls == CONCRETE) | (cls == ASPHALT)
    lawn = (cls == KEEP) & editable & seen
    if not paving.any() or not lawn.any():
        return cls
    holes = ndimage.binary_fill_holes(paving | ~editable) & (cls == KEEP) & editable
    labels, count = ndimage.label(holes)
    if not count:
        return cls
    index = np.arange(1, count + 1)
    sizes = ndimage.sum(holes, labels, index)
    greenness = ndimage.median(np.where(np.isfinite(ndvi), ndvi, np.nan), labels, index)
    lawn_ndvi = np.median(ndvi[lawn & ~holes]) if (lawn & ~holes).any() else np.median(ndvi[lawn])
    fill = np.zeros(count + 1, bool)
    lit = ndimage.mean(seen, labels, index)  # a shadow beside a house may be lawn: unknown, keep it
    fill[1:] = (sizes < MAX_HOLE) & (greenness < lawn_ndvi - HOLE_NDVI_GAP) & (lit > 0.5)
    target = fill[labels]
    if target.any():
        _, (rows, cols) = ndimage.distance_transform_edt(~paving, return_indices=True)
        cls[target] = cls[rows[target], cols[target]]
    return cls


def ground_layer(cls, surface, grid, frame, capture, cover_seen):
    """Edits: the top ground block (expects the grass it replaces) and plants into the air above."""
    layer = LayerBuilder('ground', frame, 'Keyless ground cover: paving, soil, fields, prairie, wetland')
    sources = {}
    for value, (_, _, kind) in CLASSES.items():
        if value in (FIELD, PRAIRIE, MARSH, MUD) and cover_seen:
            dataset, licence, evidence = f'NAIP {capture} + ESA WorldCover 2021 + OSM', \
                f'{NAIP_LICENSE}; {WORLDCOVER_LICENSE}; {OSM_LICENSE}', 'derived'
        else:
            dataset, licence, evidence = f'NAIP {capture} + OSM', f'{NAIP_LICENSE}; {OSM_LICENSE}', 'observed'
        sources[value] = layer.source(kind, dataset, licence, evidence,
                                      note='class from imagery spectra and land-cover context; block choice estimated')
    ground, keys = surface['ground_y'], surface['surface_key']
    rows, cols = np.nonzero(cls != KEEP)
    counts = {}
    for r, c in zip(rows, cols):
        value = int(cls[r, c])
        block, above, kind = CLASSES[value]
        x, z, y = grid.x0 + int(c), grid.z0 + int(r), int(ground[r, c])
        if block:
            layer.add(x, y, z, block, sources[value], expect=str(keys[r, c]), priority=1)
        if above and (value != PRAIRIE or stable(x, z) < 0.45):
            layer.add(x, y + 1, z, above, sources[value], priority=1)
        counts[kind] = counts.get(kind, 0) + 1
    return layer, counts


def cover(tile, frame, world, sources, imagery, work, surface):
    """Ground-cover layer for world (written to work/'layer'); returns the report with '_layer'."""
    from detail_guard import WriteGuard
    from regional_trees import tile_grid
    world, sources, imagery, work = Path(world), Path(sources), Path(imagery), Path(work)
    meta = json.loads((sources / 'sources.json').read_text())
    if not (imagery / 'imagery.json').exists():
        return {'result': 'no imagery for the tile', 'cells': 0}
    image = json.loads((imagery / 'imagery.json').read_text())
    with np.load(imagery / 'metric-imagery.npz', allow_pickle=False) as arrays:
        bands, valid, ndvi, shadow = (arrays[k] for k in ('bands', 'valid', 'ndvi', 'shadow_candidate'))
    if bands.shape != (4, meta['size'], meta['size']):
        return {'result': 'imagery is on another grid', 'cells': 0}
    grid = tile_grid(tile, frame)
    keys = np.vectorize(lambda key: key.split('[', 1)[0])(surface['surface_key'])
    editable = (keys == GRASS) & ~surface['built']
    classes = worldcover(meta)
    parking, wetland = osm_areas(sources, meta)
    cls = classify(bands, valid, ndvi, shadow, classes, parking, wetland, editable)
    capture = (image.get('capture_date_utc') or image.get('capture_interval') or 'capture date unrecorded')[:10]
    report = {'result': 'pass', 'imagery': image.get('source_id') or image.get('service'), 'capture': capture,
              'worldcover': classes is not None, 'osm_parking_cells': int(parking.sum()),
              'osm_wetland_cells': int(wetland.sum()),
              'license': f'{NAIP_LICENSE}; {WORLDCOVER_LICENSE}; {OSM_LICENSE}'}
    if not (cls != KEEP).any():
        report['cells'] = {}
        return report
    layer, counts = ground_layer(cls, surface, grid, frame, capture, classes is not None)
    layer.write(work / 'layer', WriteGuard(work, 300 * 2**20, 4 * 2**30), {'tile': tile['id']})
    report.update(cells=counts, _layer=str(work / 'layer'))
    return report


def record(world, report):
    path = Path(world) / 'earthcraft.json'
    manifest = json.loads(path.read_text())
    manifest['ground'] = report
    if report.get('cells'):
        manifest.setdefault('limitations', []).append(
            'Ground cover: classes from NAIP spectra, ESA WorldCover and OSM areas; the block chosen for a class is a '
            'presentation, and shadowed or tree-covered ground stays grass.')
    path.write_text(json.dumps(manifest, indent=2))
