"""Deterministic geographic appearance routing; no geometry completion or LLM."""
import io
import json
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


def choose(tags, surface, palette):
    material = tags.get(f'{surface}:material', '').lower()
    color = tags.get(f'{surface}:colour')
    if color:
        try:
            rgb = np.array(ImageColor.getrgb(color), dtype=float)
        except ValueError:
            rgb = None
        if rgb is not None and rgb.shape == (3,):
            suffix = '_stained_glass' if material == 'glass' else '_concrete'
            candidates = [b for b in palette if b.endswith(suffix)]
            return min(candidates, key=lambda b: float(np.sum((palette[b]-rgb)**2)))
    return MATERIALS.get(material)


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
        wall, roof = choose(tags, 'building', palette), choose(tags, 'roof', palette)
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
        records.append({'osm_way':way['id'], 'decision':'route', 'wall_block':wall, 'roof_block':roof,
                        'source_tags':{k:v for k,v in tags.items() if k.startswith(('building:','roof:','min_height','height'))},
                        'cells':int(mask.sum()), 'vertical_range_y':[low,high]})
    layers.sort(key=lambda layer: -layer['area'])
    return layers, {'method':'explicit OSM material/colour → nearest installed texture colour; smaller parts take precedence',
                    'routes':records, 'llm_used':False, 'geometry_changed':False,
                    'photo_detail':'Not applied: no georegistered Chicago photo reconstruction supplied.',
                    'limitations':['Map tags may be outdated; no independent facade validation.',
                                   'Colour-to-block mapping is artistic; photographic or mapped colour is not exact reflectance.',
                                   'No synthetic windows, doors, floor bands or unseen details.']}
