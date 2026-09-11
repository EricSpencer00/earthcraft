"""Compile sparse scans and mapped footprints into a deterministic styled shell.

This is an explicit adaptation: roofs/walls between returns and window rhythms
are derived, not claimed as observed architecture. Original point occupancy,
ground, photo anchors and their exposed faces are retained. Works on closed,
immutable generated tiles; never on a running player's save.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import time
import zipfile

import nbtlib as n
import numpy as np
from affine import Affine
from PIL import Image
from rasterio.features import rasterize
from scipy.ndimage import distance_transform_edt, maximum_filter, median_filter
from pyproj import Transformer
from shapely import make_valid, STRtree
from shapely.geometry import Polygon
from shapely.ops import transform as transform_geometry

from building_audit import county_objects
from city_save_update import read_region
from ground_color import nearest_colors
from material_router import choose_with_evidence, route, texture_colors
from osm_json_to_kml import building_tag
from metric_world import BLOCK, PALETTE, packed, palette_indices, region_write, roof_shell_floor
from photo_layer import NORMALS
from verify_metric_world import unpack, verify

VERSION = 'styled-shell-v1'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def envelope(cells, mask, ground, provider_top):
    """Nearest sampled column envelope with a fixed one-metre closing radius.

    Only footprint interiors are filled, so courtyards remain open. Sampled
    column maxima are retained exactly: spreading a peak into already sampled
    lower columns would blunt spires and move measured setback edges.
    No cross-building point neighborhoods are used.
    Empty footprints use the explicitly recorded provider-height adaptation.
    """
    mask = np.asarray(mask, bool)
    top = np.full(mask.shape, -32768, np.int32)
    cells = np.asarray(cells, np.int32)
    if len(cells):
        keep = mask[cells[:, 2], cells[:, 0]]
        local = cells[keep]
        np.maximum.at(top, (local[:, 2], local[:, 0]), local[:, 1])
    observed = mask & (top > ground)
    if observed.any():
        # Nearest-column interpolation is deterministic; ties use scipy's fixed
        # raster traversal. The native library version is part of the recipe.
        indices = distance_transform_edt(~observed, return_distances=False, return_indices=True)
        filled = top[tuple(indices)]
        smoothed = median_filter(filled, size=3, mode='nearest')
        # Filters propose values for missing columns only. They must not dilate
        # a measured ridge over an adjacent measured lower roof.
        closed = maximum_filter(np.where(observed, top, -32768), size=3, mode='constant', cval=-32768)
        top = np.maximum(smoothed, closed)
        top[observed] = filled[observed]
    else:
        top[:] = provider_top
    top = np.maximum(top, ground + 1)
    return top, roof_shell_floor(top, mask, ground), int(observed.sum())


def photo_protection(world):
    """Preserve the exact accepted photo anchors and the air in front of them."""
    if not (world/'photo-skin.json').exists():
        return [], [], None
    with zipfile.ZipFile(world/'resources.zip') as archive:
        records = json.loads(archive.read('earthcraft-skin-manifest.json'))
        colors = []
        for r in records:
            raw = archive.read(f'assets/earthcraft_skin/textures/block/face_{r["id"]}.png')
            if hashlib.sha256(raw).hexdigest() != r['texture_sha256']:
                raise ValueError('Changed photo texture')
            rgba = np.asarray(Image.open(io.BytesIO(raw)))
            colors.append(rgba[rgba[:, :, 3] == 255, :3])
    anchors = [r['cell'] for r in records]
    air = [(np.array(r['cell']) + NORMALS[r['face']]).tolist() for r in records]
    return anchors, air, np.median(np.concatenate(colors), axis=0) if colors else None


def shell_for_chunk(layer, cx, cz, height, min_y, world_offset=(0, 0)):
    zs, xs = slice(cz*16, cz*16+16), slice(cx*16, cx*16+16)
    yy = np.arange(min_y, min_y+height)[:, None, None]
    geometry_owner = layer.get('geometry_owner', layer['owner'])
    shell = ((geometry_owner[zs, xs] > 0) & (yy >= layer['floor'][zs, xs]) &
             (yy <= layer['top'][zs, xs]))
    for x, y, z in layer['protected_air']:
        x -= world_offset[0]; z -= world_offset[1]
        if x//16 == cx and z//16 == cz and min_y <= y < min_y+height:
            shell[y-min_y, z%16, x%16] = False
    return shell


def checked_roof_override(top, supported, mask, provider_top):
    """Candidate consistency gates, not an independent accuracy certificate."""
    top, supported, mask = np.asarray(top), np.asarray(supported, bool), np.asarray(mask, bool)
    if top.shape != mask.shape or supported.shape != mask.shape or not mask.any():
        raise ValueError('Roof model and source grid shapes differ or footprint is empty')
    selected = mask & supported
    coverage = float(selected.sum()/mask.sum())
    outside = float((supported & ~mask).sum()/max(1, supported.sum()))
    if coverage < .9 or outside > .1:
        raise ValueError('Roof model disagrees with the mapped footprint')
    if not np.isfinite(top[selected]).all() or np.any(top[selected] != np.floor(top[selected])):
        raise ValueError('Roof cells must have finite integral heights')
    residual = float(top[selected].max()-provider_top)
    if abs(residual) > 2:
        raise ValueError('Roof model maximum disagrees with provider height by more than 2 m')
    return selected, {'footprint_coverage': coverage, 'outside_footprint_fraction': outside,
                      'provider_maximum_difference_blocks': residual,
                      'independent_accuracy_verified': False}


def compile_layer(source_world, source, destination, roof_models=None):
    source_world, source, destination = map(Path, (source_world, source, destination))
    if destination.exists():
        raise FileExistsError(destination)
    if (source_world/'building-layer.json').exists():
        raise ValueError('Use the immutable scan tile as input, not a styled result')
    start = time.monotonic()
    report = json.loads((source_world/'earthcraft.json').read_text())
    meta = json.loads((source/'sources.json').read_text())
    if any(meta[k] != report['source'][k] for k in ('crs', 'west', 'north', 'size')):
        raise ValueError('Source and world metre grids differ')
    size = meta['size']; height = report['dimension_height']; min_y = report['dimension_min_y']
    if not 16 <= size <= 512 or size % 16 or height > 1024:
        raise ValueError('Bounded chunk-aligned tile required')
    with np.load(source/'rasters.npz') as data:
        elevation = data['elevation'].copy()
    ground = np.floor(elevation + report['vertical_offset_m']).astype(np.int32)
    cells = np.load(source_world/'point-voxels.npy', allow_pickle=False)
    anchors, protected_air, photo_rgb = photo_protection(source_world)
    layer = {'owner': np.zeros((size, size), np.int32),
             'geometry_owner': np.zeros((size, size), np.int32), 'paint_top': ground.copy(), 'floor': ground+1,
             'top': ground.copy(), 'base': ground.copy(),
             'protected_air': np.asarray(protected_air, dtype=np.int32).reshape(-1, 3)}
    ox, oz = report.get('world_offset_xz', [0, 0])
    objects = sorted(county_objects(source, meta), key=lambda b: (-b['geometry'].area, b['id']))
    roof_models = dict(roof_models or {})
    if set(roof_models)-{b['id'] for b in objects}:
        raise ValueError('Roof override building ID absent from the source catalog')
    applied_models = set()
    ways = json.loads((source/'osm-ways.json').read_text())
    project = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    alternatives = [(w, transform_geometry(project.transform, make_valid(Polygon(w['coordinates']))))
                    for w in ways if w['closed'] and building_tag(w['tags'])]
    tree = STRtree([geom for _, geom in alternatives])
    building_records = []; wall_blocks = [BLOCK['light_gray_concrete']]
    # A permitted photo provides a style swatch only for the building that owns
    # its anchor. Applying that swatch elsewhere on that building is disclosed.
    swatch_colors = texture_colors().copy()
    jar = Path.home()/'Library/Application Support/minecraft/versions/1.21.10/1.21.10.jar'
    with zipfile.ZipFile(jar) as archive:
        for block in ('sandstone', 'bricks', 'stone_bricks'):
            texture = 'sandstone' if block == 'sandstone' else block
            raw = archive.read(f'assets/minecraft/textures/block/{texture}.png')
            swatch_colors[block] = np.asarray(Image.open(io.BytesIO(raw)).convert('RGB'), float).mean((0, 1))
    swatches = sorted(k for k in swatch_colors if not k.endswith('stained_glass'))
    transform = Affine(1, 0, meta['west'], 0, -1, meta['north'])
    for obj in objects:
        mask = rasterize([(obj['geometry'], 1)], out_shape=(size, size), transform=transform).astype(bool)
        if not mask.any() or not obj['height_m'] or obj['height_m'] <= 0:
            continue
        provider_top = int(np.ceil(obj['ground_m']+obj['height_m']+report['vertical_offset_m'])-1)
        top, floor, sampled = envelope(cells, mask, ground, provider_top)
        roof_evidence = None
        if obj['id'] in roof_models:
            from cityjson_roof import rasterize_roof
            model_top, supported, roof_evidence = rasterize_roof(
                Path(roof_models[obj['id']]), str(obj['source_id']), meta, report['vertical_offset_m'])
            selected, gates = checked_roof_override(model_top, supported, mask, provider_top)
            top[selected] = np.maximum(model_top[selected], ground[selected]+1)
            # The CityJSON model is derived, and must not remove source returns.
            # Higher observed returns remain explicitly in the combined shell.
            local = cells[mask[cells[:, 2], cells[:, 0]]]
            if len(local):
                np.maximum.at(top, (local[:, 2], local[:, 0]), local[:, 1])
            floor = roof_shell_floor(top, selected, ground)
            # An explicit model's courtyard holes and uncovered cells must not
            # be replaced by generic roof fill. Original points remain separate.
            floor[mask & ~selected] = top[mask & ~selected]+1
            roof_evidence['source_consistency'] = gates
            roof_evidence['observed_maxima_retained'] = True
            roof_evidence['unsupported_cells_are_not_filled'] = True
            applied_models.add(obj['id'])
        if top[mask].max() >= min_y+height:
            raise ValueError('Styled roof exceeds the shared world height')
        identifier = len(building_records)+1
        # A wall-return voxel can intersect the footprint even when its centre
        # is outside it. Paint those observed boundary voxels too, without
        # expanding the geometry mask or adding any new outer wall column.
        paint_mask = rasterize([(obj['geometry'], 1)], out_shape=(size, size), transform=transform,
                               all_touched=True).astype(bool)
        layer['owner'][paint_mask] = identifier
        layer['geometry_owner'][mask] = identifier
        layer['top'][mask] = top[mask]; layer['floor'][mask] = floor[mask]
        nearest = distance_transform_edt(~mask, return_distances=False, return_indices=True)
        layer['paint_top'][paint_mask] = top[tuple(nearest)][paint_mask]
        base = int(np.floor(obj['ground_m']+report['vertical_offset_m']))
        layer['base'][paint_mask] = base
        owns_photo = any(0 <= x-ox < size and 0 <= z-oz < size and mask[z-oz, x-ox] for x, _, z in anchors)
        matches = []
        for index in tree.query(obj['geometry'], predicate='intersects'):
            way, geom = alternatives[index]
            overlap = obj['geometry'].intersection(geom).area / obj['geometry'].union(geom).area
            if overlap >= .5:
                matches.append((overlap, str(way['id']), way))
        match = sorted(matches, key=lambda item: (-item[0], item[1]))[0][2] if matches else None
        tags = match['tags'] if match else {}
        landmark = bool(tags.get('historic')) or tags.get('building') in ('water_tower', 'church', 'cathedral', 'castle')
        wall = 'bricks' if obj['height_m'] < 18 else 'light_gray_concrete'
        if landmark:
            wall = 'stone_bricks'
        color_source = 'Fixed height-class style palette; not observed material'
        palette_role = 'derived_style'
        wall_evidence = {'role': 'derived_style', 'reason': 'no admitted facade observation or explicit map style applied'}
        if owns_photo and photo_rgb is not None:
            wall = swatches[int(nearest_colors(photo_rgb, np.array([swatch_colors[k] for k in swatches])))]
            color_source = 'Median opaque accepted photo texels extended as a building style swatch; experimental registration'
            palette_role = 'observed'
            wall_evidence = {'role': 'observed', 'source': 'accepted_photo_anchor',
                             'scope': 'experimental building style swatch, not a facade-wide material measurement'}
        else:
            tagged_wall, wall_evidence = choose_with_evidence(tags, 'facade', swatch_colors)
            if tagged_wall is not None:
                wall = tagged_wall
                color_source = f"Explicit OSM {wall_evidence['decision']} tag {wall_evidence['source_key']}"
                palette_role = 'tagged'
        tagged_roof, roof_tag_evidence = choose_with_evidence(tags, 'roof', swatch_colors)
        wall_blocks.append(BLOCK[wall])
        building_records.append({'id': obj['id'], 'provider_height_m': obj['height_m'],
            'footprint_cells': int(mask.sum()), 'sampled_columns': sampled,
            'appearance_cells': int(paint_mask.sum()),
            'appearance_ownership': 'Observed voxels whose cells intersect the footprint; does not expand shell geometry',
            'roof_method': 'Exact sampled maxima / nearest columns / gap-only fixed 3x3 filters' if sampled else 'Provider-height shell fallback',
            'roof_model': roof_evidence,
            'wall_block': wall, 'color_source': color_source,
            'wall_palette_role': palette_role, 'wall_evidence': wall_evidence,
            'roof_evidence_route': roof_tag_evidence,
            'matched_osm_way': match['id'] if match else None,
            'style_class': 'landmark' if landmark or owns_photo else 'regular_building',
            'photo_style_rgb': photo_rgb.tolist() if owns_photo and photo_rgb is not None else None,
            'synthetic_window_rhythm': not (owns_photo or landmark)})
        if roof_evidence is not None:
            building_records[-1]['roof_method'] = 'Explicit CityJSON planar roof candidate plus retained observed maxima; no new fill outside model support'
    if applied_models != set(roof_models):
        raise ValueError('A requested roof model could not be applied to a usable footprint')
    appearance, routing = route(source, meta, elevation, report['vertical_offset_m'], BLOCK, ways=ways)
    source_regions = {p.name: sha(p) for p in sorted((source_world/'region').glob('r.*.*.mca'))}
    source_hashes = {name: sha(source/name) for name in ('sources.json', 'rasters.npz', 'cook-buildings-2022.json', 'osm-ways.json')}
    shutil.copytree(source_world, destination, ignore=shutil.ignore_patterns('._*'))
    prior = destination/'pre-style-evidence'; prior.mkdir()
    for name in ('server-verification.json', 'block-verification.json', 'build-receipt.json', 'photo-layer-verification.json', 'city-assembly.json'):
        if (destination/name).exists():
            (destination/name).rename(prior/name)
    added = painted = 0
    for filename in source_regions:
        records = []
        for (wx, wz), tag in read_region(source_world/'region'/filename).items():
            cx, cz = wx-ox//16, wz-oz//16
            zs, xs = slice(cz*16, cz*16+16), slice(cx*16, cx*16+16)
            volume = np.zeros((height, 16, 16), np.uint8)
            for section in tag['sections']:
                state = section['block_states']; palette = state['palette']
                if any(p.get('Properties') for p in palette):
                    raise ValueError('Styled input must be a generated default-state block world')
                mapping = np.array([BLOCK[str(p['Name']).removeprefix('minecraft:')] for p in palette], np.uint8)
                values = np.zeros(4096, int) if len(palette) == 1 else unpack(state['data'], max(4, (len(palette)-1).bit_length()), 4096)
                y0 = int(section['Y'])*16-min_y
                volume[y0:y0+16] = mapping[values].reshape(16, 16, 16)
            before = volume.copy()
            shell = shell_for_chunk(layer, cx, cz, height, min_y, (ox, oz))
            yy = np.arange(min_y, min_y+height)[:, None, None]
            owner = layer['owner'][zs, xs]
            paint_top = layer['paint_top'][zs, xs]
            structure = shell | ((volume != BLOCK['air']) & (yy > ground[zs, xs]) & (owner > 0))
            colors = np.broadcast_to(np.asarray(wall_blocks, np.uint8)[owner], volume.shape).copy()
            levels = yy-layer['base'][zs, xs]
            zz, xx = np.mgrid[wz*16:wz*16+16, wx*16:wx*16+16]
            # Global metre coordinates lock phase across tile boundaries.
            windows = ((levels % 4 == 1) | (levels % 4 == 2)) & ((xx % 4 < 2) | (zz % 4 < 2))
            no_windows = np.array([True]+[not b['synthetic_window_rhythm'] for b in building_records])[owner]
            windows &= (levels > 3) & (yy < paint_top-1) & ~no_windows
            colors[windows] = BLOCK['gray_stained_glass']
            colors[(yy == paint_top) & ~no_windows] = BLOCK['gray_concrete']
            for r in appearance:
                take = structure & r['mask'][zs, xs] & (yy >= r['low'])
                if r['high'] is not None:
                    take &= yy <= r['high']
                if r['wall'] is not None:
                    colors[take & ~windows] = r['wall']
                if r['roof'] is not None:
                    colors[take & (yy == paint_top)] = r['roof']
            volume[structure] = colors[structure]
            for x, y, z in anchors:
                if x//16 == wx and z//16 == wz:
                    volume[y-min_y, z%16, x%16] = before[y-min_y, z%16, x%16]
            if np.any((before != BLOCK['air']) & (volume == BLOCK['air'])):
                raise AssertionError('Adaptation removed an observed cell')
            added += int(((before == BLOCK['air']) & (volume != BLOCK['air'])).sum())
            painted += int(((before != BLOCK['air']) & (before != volume)).sum())
            for section in tag['sections']:
                y0 = int(section['Y'])*16-min_y
                ids, inverse = palette_indices(volume[y0:y0+16])
                state = n.Compound({'palette': n.List[n.Compound]([n.Compound({'Name': n.String('minecraft:'+PALETTE[i])}) for i in ids])})
                if len(ids) > 1:
                    state['data'] = packed(inverse, max(4, (len(ids)-1).bit_length()))
                section['block_states'] = state
                section.pop('BlockLight', None); section.pop('SkyLight', None)
            tag['isLightOn'] = n.Byte(0)
            top = height-np.argmax((volume != BLOCK['air'])[::-1], axis=0)
            tag['Heightmaps'] = n.Compound({key: packed(top, height.bit_length()) for key in tag['Heightmaps']})
            records.append((wx, wz, tag))
        path = destination/'region'/filename; temp = path.with_suffix('.styled')
        region_write(temp, records); temp.replace(path)
    if source_regions != {name: sha(source_world/'region'/name) for name in source_regions}:
        raise ValueError('Source world changed while adapting')
    np.savez_compressed(destination/'building-layer.npz', **layer)
    import scipy
    result = {'schema': VERSION, 'source_world': str(source_world.resolve()),
        'source_regions': source_regions, 'source_hashes': source_hashes,
        'source_points_sha256': sha(source_world/'point-voxels.npy'),
        'model_sha256': sha(destination/'building-layer.npz'),
        'buildings': building_records, 'mapped_material_routes': routing,
        'derived_added_cells': added, 'recolored_existing_cells': painted,
        'photo_anchors_preserved': len(anchors), 'photo_resource_sha256': sha(source_world/'resources.zip') if anchors else None,
        'recipe': {'metres_per_block': 1, 'implementation_sha256': sha(Path(__file__)),
                   'roof_revision': 'gap-only-v2', 'numpy': np.__version__, 'scipy': scipy.__version__,
                   'floor_spacing_m': 4, 'window_bay_m': 4, 'randomness': None},
        'geometry_changed': bool(added), 'llm_used': False, 'independent_accuracy_verified': False,
        'limitations': ['Gap-filled surfaces and window patterns are stylized derived architecture, not measured detail.',
            'Footprints and point heights retain survey-date and spatial-association uncertainty.',
            'Column-envelope roofs approximate overhangs, archways and complex roof forms.',
            'Photo colors include captured lighting; extended swatches do not establish facade materials.',
            'Tile-edge roofs need shared halo observations before seam equality can be certified.']}
    (destination/'building-layer.json').write_text(json.dumps(result, indent=2))
    report['building_geometry'] = 'Deterministic styled shells from point-column envelopes and mapped footprints'
    report['building_adaptation'] = {'schema': VERSION, 'derived_added_cells': added, 'llm_used': False}
    report['limitations'][0] = result['limitations'][0]
    (destination/'earthcraft.json').write_text(json.dumps(report, indent=2))
    if (destination/'photo-skin.json').exists():
        photo = json.loads((destination/'photo-skin.json').read_text())
        photo['pre_building_layer_region_sha256'] = photo['region_sha256']
        photo['region_sha256'] = {name: sha(destination/'region'/name) for name in source_regions}
        photo['subsequent_building_adaptation'] = 'Styled shell; exact original photo anchors and exposed face air preserved'
        (destination/'photo-skin.json').write_text(json.dumps(photo, indent=2))
    verify(destination)
    print(json.dumps({'buildings': len(building_records), 'added_cells': added, 'painted_cells': painted,
                      'seconds': time.monotonic()-start, 'world': str(destination)}, indent=2), flush=True)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--world', type=Path, required=True); p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--roof-model', nargs=2, action='append', default=[], metavar=('COUNTY_BUILDING_ID', 'CITYJSON_PATH'),
                   help='Explicit same-CRS candidate roof override; repeat for another stable building ID')
    a = p.parse_args()
    if len(dict(a.roof_model)) != len(a.roof_model):
        p.error('Only one roof model per building ID')
    compile_layer(a.world, a.source, a.output, dict(a.roof_model))
