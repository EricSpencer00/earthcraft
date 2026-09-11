"""Deterministic geographic appearance routing; no geometry completion or LLM."""
import io
import json
import re
import zipfile
from functools import lru_cache
from pathlib import Path
import numpy as np
from PIL import Image, ImageColor
from affine import Affine
from pyproj import Transformer
from rasterio.features import rasterize
from shapely.geometry import Polygon
from shapely import make_valid
from osm_json_to_kml import metres,building_tag

COLORS = ('white','orange','magenta','light_blue','yellow','lime','pink','gray',
          'light_gray','cyan','purple','blue','brown','green','red','black')
APPEARANCE_BLOCKS = [f'{c}_concrete' for c in COLORS] + [f'{c}_stained_glass' for c in COLORS] + ['iron_block']
MATERIALS = {'brick':'bricks', 'bricks':'bricks', 'stone':'stone_bricks',
             'limestone':'sandstone', 'sandstone':'sandstone', 'concrete':'light_gray_concrete',
             'metal':'iron_block', 'steel':'iron_block', 'glass':'light_gray_stained_glass'}

# OSM's generic tags occur often on simple buildings; the namespaced forms
# matter when a building has a distinct facade or roof.  The tuple order is
# deliberate evidence precedence, not a popularity heuristic.
SURFACE_KEYS = {
    'facade': {
        'material': ('facade:material', 'building:material', 'material'),
        'colour': ('facade:colour', 'facade:color', 'building:colour',
                   'building:color', 'colour', 'color'),
    },
    'roof': {
        'material': ('roof:material', 'building:material', 'material'),
        'colour': ('roof:colour', 'roof:color', 'building:colour',
                   'building:color', 'colour', 'color'),
    },
}


@lru_cache(maxsize=1)
def texture_colors():
    """Read the installed palette once per worker, not once per tile."""
    jar = Path.home() / 'Library/Application Support/minecraft/versions/1.21.10/1.21.10.jar'
    result = {}
    with zipfile.ZipFile(jar) as archive:
        for block in APPEARANCE_BLOCKS:
            image = Image.open(io.BytesIO(archive.read(f'assets/minecraft/textures/block/{block}.png'))).convert('RGB')
            result[block] = np.asarray(image, dtype=float).mean(axis=(0,1))
    return result


def _tag_value(tags, keys):
    """Return the first supported OSM tag without guessing compound values."""
    for key in keys:
        value = str(tags.get(key, '')).strip()
        if value:
            # Semicolon values mean multiple stated materials/colours.  Pick
            # the first value deterministically; preserve the original tag in
            # the receipt so a later richer source can supersede it.
            return key, re.split(r'\s*[;,]\s*', value, maxsplit=1)[0].strip()
    return None, None


def _normalize_colour(value):
    # PIL recognises US spelling.  This changes only the parser spelling, not
    # the declared source value retained in a manifest.
    return value.strip().lower().replace('grey', 'gray')


def choose_with_evidence(tags, surface, palette):
    """Select one explicit facade/roof appearance and return its lineage.

    This is intentionally a router, not a semantic model: a missing or
    unusable tag returns an abstention.  Callers may then use an explicitly
    labelled deterministic house style, but cannot label that fallback as a
    measured material.
    """
    if surface == 'building':
        surface = 'facade'
    if surface not in SURFACE_KEYS:
        raise ValueError(f'Unknown surface: {surface}')
    material_key, material = _tag_value(tags, SURFACE_KEYS[surface]['material'])
    colour_key, color = _tag_value(tags, SURFACE_KEYS[surface]['colour'])
    material = material.lower() if material else None
    if color:
        try:
            rgb = np.array(ImageColor.getrgb(_normalize_colour(color)), dtype=float)
        except ValueError:
            rgb = None
        if rgb is not None and rgb.shape == (3,):
            suffix = '_stained_glass' if material == 'glass' else '_concrete'
            candidates = [b for b in palette if b.endswith(suffix)]
            if candidates:
                return (min(candidates, key=lambda b: float(np.sum((palette[b]-rgb)**2))),
                        {'role': 'tagged', 'surface': surface, 'decision': 'colour',
                         'source_key': colour_key, 'source_value': color,
                         'material_key': material_key, 'material_value': material})
    block = MATERIALS.get(material)
    if block:
        return (block, {'role': 'tagged', 'surface': surface, 'decision': 'material',
                        'source_key': material_key, 'source_value': material,
                        'colour_key': colour_key, 'colour_value': color})
    return (None, {'role': 'abstain', 'surface': surface,
                   'reason': 'no supported explicit material or colour tag',
                   'material_key': material_key, 'material_value': material,
                   'colour_key': colour_key, 'colour_value': color})


def choose(tags, surface, palette):
    """Compatibility wrapper for callers that need only a palette block."""
    return choose_with_evidence(tags, surface, palette)[0]


def route(source, meta, elevation, offset, block_ids, ways=None):
    """Sparse 2D masks plus vertical ranges; material assignment only, never blocks."""
    palette = texture_colors()
    transform = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    layers, records = [], []
    if ways is None:
        ways = json.loads((Path(source)/'osm-ways.json').read_text())
    for way in ways:
        tags = way['tags']
        if not way['closed'] or not building_tag(tags):
            continue
        wall, wall_evidence = choose_with_evidence(tags, 'facade', palette)
        roof, roof_evidence = choose_with_evidence(tags, 'roof', palette)
        if wall is None and roof is None:
            continue
        xs, ys = transform.transform(*zip(*way['coordinates']))
        polygon = make_valid(Polygon(zip(np.asarray(xs)-meta['west'], meta['north']-np.asarray(ys))))
        if polygon.is_empty:
            continue
        mask = rasterize([(polygon,1)], out_shape=elevation.shape, transform=Affine.identity()).astype(bool)
        if not mask.any():
            continue
        ground = float(np.median(elevation[mask])) + offset
        minimum = metres(tags.get('min_height','')) if 'min_height' in tags else 0
        height = metres(tags.get('height',''))
        if minimum is None or (height is not None and height<=minimum):
            records.append({'osm_way':way['id'],'decision':'abstain','reason':'invalid explicit vertical extent'})
            continue
        low = int(np.floor(ground + minimum))
        high = int(np.ceil(ground + height)-1) if height is not None else None
        if 'building:part' in tags and height is None:
            records.append({'osm_way':way['id'], 'decision':'abstain', 'reason':'part lacks vertical extent'})
            continue
        layer = {'mask':mask, 'low':low, 'high':high, 'area':polygon.area,
                 'wall':block_ids[wall] if wall else None, 'roof':block_ids[roof] if roof else None}
        layers.append(layer)
        source_keys = set(SURFACE_KEYS['facade']['material'] + SURFACE_KEYS['facade']['colour'] +
                          SURFACE_KEYS['roof']['material'] + SURFACE_KEYS['roof']['colour'] +
                          ('min_height', 'height'))
        records.append({'osm_way':way['id'], 'decision':'route', 'wall_block':wall, 'roof_block':roof,
                        'wall_evidence':wall_evidence, 'roof_evidence':roof_evidence,
                        'source_tags':{k:v for k,v in tags.items() if k in source_keys},
                        'cells':int(mask.sum()), 'vertical_range_y':[low,high]})
    layers.sort(key=lambda layer: -layer['area'])
    return layers, {'method':'explicit OSM material/colour → nearest installed texture colour; smaller parts take precedence',
                    'routes':records, 'llm_used':False, 'geometry_changed':False,
                    'photo_detail':'Not applied: no georegistered Chicago photo reconstruction supplied.',
                    'limitations':['Map tags may be outdated; no independent facade validation.',
                                   'Colour-to-block mapping is artistic; photographic or mapped colour is not exact reflectance.',
                                   'No synthetic windows, doors, floor bands or unseen details.']}
