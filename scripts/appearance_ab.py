"""Offline roof-colour A/B and canopy diagnostics on a fixed generated tile.

Reads frozen local data, proposes material changes in memory, and renders the
same occupied blocks. Optional canopy data is a separate in-memory candidate.
Never writes a world, acquires data, or plants trees in the accepted save.
"""
import argparse
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import time
import resource
import sys
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pyproj import CRS
from scipy.ndimage import binary_dilation, label

from ground_color import nearest_colors
import headless_blocks as headless
from inspect_world import chunks
from naip_imagery import normalize_crop
from verify_metric_world import unpack

VERSION = 'earthcraft.appearance-ab-v1'
RGBN = ['red', 'green', 'blue', 'near_infrared']
NEUTRAL = {'minecraft:stone', 'minecraft:stone_bricks',
           'minecraft:gray_concrete', 'minecraft:light_gray_concrete'}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def bounded_npz(path, expanded_limit):
    if Path(path).stat().st_size > expanded_limit:
        raise ValueError('Compressed array file exceeds preview budget')
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 32 or sum(m.file_size for m in members) > expanded_limit:
            raise ValueError('Expanded arrays exceed preview budget')


def visible_bands(bands, declared_order):
    if declared_order != RGBN or bands.ndim != 3 or bands.shape[0] != 4 or bands.dtype != np.uint8:
        raise ValueError('Explicit red/green/blue/NIR U8 bands required; NIR is not visible colour')
    return bands[:3].transpose(1, 2, 0)


def aligned_window(image_grid, world_grid):
    if not CRS(image_grid['crs']).equals(CRS(world_grid['crs'])):
        raise ValueError('Image and world coordinate systems differ')
    x, z = world_grid['west']-image_grid['west'], image_grid['north']-world_grid['north']
    size = world_grid['size']
    if x != int(x) or z != int(z) or min(x, z) < 0 or max(x+size, z+size) > image_grid['size']:
        raise ValueError('Complete, integral, aligned image coverage required')
    return slice(int(z), int(z)+size), slice(int(x), int(x)+size)


def load_image(directory, grid):
    directory = Path(directory)
    manifest = json.loads((directory/'imagery.json').read_text())
    for asset in manifest['assets']:
        name = asset['file']
        if Path(name).name != name or sha(directory/name) != asset['sha256']:
            raise ValueError('Frozen image asset changed')
    path = directory/'metric-imagery.npz'
    bounded_npz(path, 32*2**20)
    with np.load(path, allow_pickle=False) as stored:
        arrays = {key: stored[key].copy() for key in stored.files}
    visible_bands(arrays['bands'], manifest.get('source_band_order'))
    expected_shape = (manifest['source_grid']['size'],)*2
    if arrays['bands'].shape != (4, *expected_shape):
        raise ValueError('Image bands differ from the declared grid')
    for key in ('valid', 'shadow_candidate', 'vegetation_candidate'):
        if arrays[key].shape != expected_shape or arrays[key].dtype != bool:
            raise ValueError('Image masks must be boolean on the declared grid')
    if manifest.get('normalized_sha256'):
        if sha(path) != manifest['normalized_sha256']:
            raise ValueError('Normalized imagery changed')
        replay = 'stored normalized SHA-256'
    else:
        reproduced = normalize_crop(directory/'served-crop.tif', manifest['source_grid'])
        if set(reproduced) != set(arrays):
            raise ValueError('Normalized imagery schema changed')
        for key in reproduced:
            np.testing.assert_array_equal(arrays[key], reproduced[key])
        replay = 'all normalized arrays reproduced from hash-checked served raster'
    zs, xs = aligned_window(manifest['source_grid'], grid)
    crop = {key: value[:, zs, xs] if key == 'bands' else value[zs, xs]
            for key, value in arrays.items()}
    return crop, {'manifest_sha256': sha(directory/'imagery.json'),
                  'normalized_sha256': sha(path), 'verification': replay,
                  'source_band_order': manifest['source_band_order'],
                  'capture': manifest.get('capture_interval') or manifest.get('capture_date_utc'),
                  'attribution': manifest.get('attribution'),
                  'rights': manifest.get('rights_scope') or manifest.get('license'),
                  'independent_alignment_verified': manifest.get('independent_alignment_verified', False)}


def read_world(directory):
    directory = Path(directory)
    meta = json.loads((directory/'earthcraft.json').read_text())
    size, height, bottom = meta['source']['size'], meta['dimension_height'], meta['dimension_min_y']
    if type(size) is not int or not 16 <= size <= 128 or size % 16 or not 16 <= height <= 1024 or height % 16:
        raise ValueError('Preview requires a 16..128 m generated tile and bounded dimension')
    ox, oz = meta.get('world_offset_xz', [0, 0])
    if ox % 16 or oz % 16:
        raise ValueError('Chunk-aligned world offset required')
    names = ['minecraft:air']; ids = {names[0]: 0}
    volume = np.zeros((height, size, size), np.uint16); seen = set(); hashes = {}
    for path in sorted((directory/'region').glob('r.*.*.mca')):
        if path.stat().st_size > 32*2**20:
            raise ValueError('Region exceeds fixed preview byte budget')
        hashes[path.name] = sha(path)
        for _, tag, _ in chunks(path):
            cx, cz = int(tag['xPos'])-ox//16, int(tag['zPos'])-oz//16
            if not (0 <= cx < size//16 and 0 <= cz < size//16) or (cx, cz) in seen:
                raise ValueError('Chunk coverage/identity differs')
            if any(tag.get(key) for key in ('entities', 'block_entities')):
                raise ValueError('Only generated entity-free preview chunks are supported')
            seen.add((cx, cz)); local = volume[:, cz*16:cz*16+16, cx*16:cx*16+16]
            for section in tag['sections']:
                state = section.get('block_states')
                if state is None:
                    continue
                palette = state['palette']; mapping = []
                for entry in palette:
                    name = str(entry['Name'])
                    if name not in ids:
                        ids[name] = len(names); names.append(name)
                    mapping.append(ids[name])
                values = (np.zeros(4096, int) if len(palette) == 1 else
                          unpack(state['data'], max(4, (len(palette)-1).bit_length()), 4096))
                sy = int(section['Y'])*16-bottom
                if not 0 <= sy <= height-16:
                    raise ValueError('Section outside declared height')
                local[sy:sy+16] = np.asarray(mapping, np.uint16)[values].reshape(16, 16, 16)
            actual = height-np.argmax((local != 0)[::-1], axis=0)
            np.testing.assert_array_equal(actual, unpack(tag['Heightmaps']['WORLD_SURFACE'], height.bit_length(), 256).reshape(16, 16))
    if len(seen) != (size//16)**2:
        raise ValueError('Complete fixed tile required')
    tops = height-np.argmax((volume != 0)[::-1], axis=0)+bottom-1
    np.testing.assert_array_equal(tops, np.load(directory/'top-heights.npy', allow_pickle=False))
    if (directory/'building-layer.npz').exists():
        with np.load(directory/'building-layer.npz', allow_pickle=False) as model:
            owner = model['geometry_owner'] if 'geometry_owner' in model else model['owner']
            owner = np.where(model['top'] == tops, owner, 0).astype(np.int32)
            ground = model['base'].astype(np.int32)
    elif (directory/'roof-observations.npz').exists():
        with np.load(directory/'roof-observations.npz', allow_pickle=False) as model:
            owner = label(model['mask'] & (model['top_y'] == tops))[0]
        import rasterio
        with rasterio.open(meta['dem_path']) as dem:
            ground = np.floor(dem.read(1)+meta['vertical_offset_m']).astype(np.int32)
    else:
        raise ValueError('Explicit roof ownership evidence required')
    if owner.shape != (size, size) or ground.shape != owner.shape:
        raise ValueError('World evidence grids differ')
    owner = np.where(tops > ground, owner, 0)
    protected = np.zeros_like(owner, bool)
    if (directory/'photo-skin.json').exists():
        from building_layer import photo_protection
        for x, y, z in photo_protection(directory)[0]:
            x -= ox; z -= oz
            if 0 <= x < size and 0 <= z < size:
                protected[z, x] = True  # Preserve the entire anchored column.
    return meta, volume, names, tops, owner, ground, protected, hashes


def palette():
    jar = Path(os.environ.get('EARTHCRAFT_CLIENT_JAR',
               str(Path.home()/'Library/Application Support/minecraft/versions/1.21.10/1.21.10.jar')))
    blocks = ['minecraft:'+c+'_concrete' for c in
              ('white', 'orange', 'magenta', 'light_blue', 'yellow', 'lime', 'pink', 'gray',
               'light_gray', 'cyan', 'purple', 'blue', 'brown', 'green', 'red', 'black')]
    colours = {}
    with zipfile.ZipFile(jar) as archive:
        for name in set(blocks) | NEUTRAL:
            raw = archive.read('assets/minecraft/textures/block/'+name.split(':')[1]+'.png')
            colours[name] = np.asarray(Image.open(io.BytesIO(raw)).convert('RGB'), float).mean(axis=(0, 1))
    return blocks, colours, {'role': 'mean installed vanilla texture RGB; no texture assets redistributed',
                            'client_jar_sha256': sha(jar)}


def colour_error(rgb, swatch):
    def linear(value):
        value = np.asarray(value)/255
        return np.where(value <= .04045, value/12.92, ((value+.055)/1.055)**2.4)
    return float(np.sum((linear(rgb)-linear(swatch))**2 * [.2126, .7152, .0722]))


def capture_relation(image_capture, geometry_capture):
    def interval(value):
        if isinstance(value, str):
            value = [value, value]
        if not isinstance(value, list) or len(value) != 2:
            return None
        try:
            return [datetime.fromisoformat(x.replace('Z', '+00:00')).date() for x in value]
        except (TypeError, ValueError):
            return None
    image, geometry = interval(image_capture), interval(geometry_capture)
    if image is None or geometry is None:
        return 'unknown'
    return 'overlap' if max(image[0], geometry[0]) <= min(image[1], geometry[1]) else 'disjoint'


def proposals(rgb, valid, shadow, vegetation, owner, top, current, protected,
              blocks, colours, minimum_neighbors=5, max_spread=35, max_chroma=80):
    shape = owner.shape
    if rgb.shape != (*shape, 3) or any(np.shape(x) != shape for x in (valid, shadow, vegetation, top, current, protected)):
        raise ValueError('Colour proposal grids differ')
    usable = valid & ~shadow & ~vegetation & (owner > 0) & ~protected
    selected = {}; reasons = {'edge_or_sparse': 0, 'mixed': 0, 'high_chroma': 0,
                            'existing_material_retained': 0, 'no_error_improvement': 0}
    for z, x in np.argwhere(usable):
        before = str(current[z, x])
        if before not in NEUTRAL:
            reasons['existing_material_retained'] += 1; continue
        zs, xs = slice(max(0, z-1), min(shape[0], z+2)), slice(max(0, x-1), min(shape[1], x+2))
        same = usable[zs, xs] & (owner[zs, xs] == owner[z, x]) & (np.abs(top[zs, xs]-top[z, x]) <= 1)
        samples = rgb[zs, xs][same].astype(float)
        if len(samples) < minimum_neighbors:
            reasons['edge_or_sparse'] += 1; continue
        median = np.median(samples, axis=0)
        if float(np.median(np.max(np.abs(samples-median), axis=1))) > max_spread:
            reasons['mixed'] += 1; continue
        if np.ptp(median) > max_chroma:
            reasons['high_chroma'] += 1; continue
        choice = blocks[int(nearest_colors(median, np.asarray([colours[b] for b in blocks])))]
        old, new = colour_error(rgb[z, x], colours[before]), colour_error(rgb[z, x], colours[choice])
        if choice == before or new >= old:
            reasons['no_error_improvement'] += 1; continue
        selected[int(z), int(x)] = {'block': choice, 'before': before, 'rgb_error_before': old,
                                   'rgb_error_candidate': new, 'neighbors': len(samples)}
    return selected, {'usable_roof_cells': int(usable.sum()), 'abstentions': reasons}


def canopy(points_path, manifest_path, meta, ground, owner):
    manifest = json.loads(Path(manifest_path).read_text())
    bounded_npz(points_path, 128*2**20)
    if sha(points_path) != manifest['points_sha256'] or not CRS(manifest['output_horizontal_crs']).equals(CRS(meta['source']['crs'])):
        raise ValueError('Point checksum or coordinate chart differs')
    with np.load(points_path, allow_pickle=False) as data:
        xyz, classes, withheld = data['xyz'], data['classification'], data['withheld']
    if xyz.shape != (len(classes), 3) or withheld.shape != classes.shape or not np.isfinite(xyz).all():
        raise ValueError('Finite point/class/withheld arrays required')
    if len(classes) > 2_000_000:
        raise ValueError('Point crop exceeds two-million-point preview bound')
    ids, counts = np.unique(classes, return_counts=True)
    histogram = {str(int(k)): int(v) for k, v in zip(ids, counts)}
    if histogram != manifest['class_counts']:
        raise ValueError('Actual class histogram differs from retained manifest')
    grid = meta['source']; size = grid['size']
    inside = ((xyz[:, 0] >= grid['west']) & (xyz[:, 0] < grid['west']+size) &
              (xyz[:, 1] <= grid['north']) & (xyz[:, 1] > grid['north']-size))
    chosen = np.flatnonzero(inside & (withheld == 0) & np.isin(classes, [3, 4, 5]))
    x = np.floor(xyz[chosen, 0]-grid['west']).astype(int); z = np.floor(grid['north']-xyz[chosen, 1]).astype(int)
    height = xyz[chosen, 2]+meta['vertical_offset_m']-ground[z, x]
    admit = (height >= 2) & (height <= 60)
    mask = np.zeros((size, size), bool); mask[z[admit], x[admit]] = True
    high = chosen[admit & (classes[chosen] == 5)]
    cells = np.unique(np.floor(np.column_stack((xyz[high, 0]-grid['west'],
                      xyz[high, 2]+meta['vertical_offset_m'], grid['north']-xyz[high, 1]))).astype(np.int32), axis=0)
    return mask, cells, {'actual_class_histogram': histogram, 'crop_points': len(classes),
                  'withheld_points': int(np.count_nonzero(withheld)),
                  'height_supported_vegetation_points_in_tile': int(admit.sum()),
                  'canopy_columns_in_tile': int(mask.sum()),
                  'canopy_columns_over_roof_owner': int((mask & (owner > 0)).sum()),
                  'capture_interval': manifest['capture_interval'], 'source_points_sha256': manifest['points_sha256'],
                  'tree_instances_or_species_inferred': False, 'trees_placed': 0,
                  'role': 'classified vegetation/height evidence only; not validated individual trees'}


def add_observed_canopy(volume, cells, ground, exclusion, minimum_y, material_id):
    """Add admitted high-vegetation returns only to air; never infer a trunk."""
    result = volume.copy(); cells = np.asarray(cells)
    if cells.ndim != 2 or cells.shape[1] != 3 or not np.issubdtype(cells.dtype, np.integer):
        raise ValueError('Integral observed canopy voxels required')
    if ground.shape != volume.shape[1:] or exclusion.shape != ground.shape:
        raise ValueError('Canopy admission grid differs')
    if not 0 < material_id <= np.iinfo(volume.dtype).max:
        raise ValueError('Canopy display material must be a non-air integer ID')
    added = []; rejected = 0
    for x, y, z in cells:
        sy = int(y)-minimum_y
        if not (0 <= x < volume.shape[2] and 0 <= z < volume.shape[1] and 0 <= sy < volume.shape[0]):
            rejected += 1; continue
        if exclusion[z, x] or y <= ground[z, x]+2 or result[sy, z, x] != 0:
            rejected += 1; continue
        result[sy, z, x] = material_id; added.append([int(x), int(y), int(z)])
    np.testing.assert_array_equal(result[volume != 0], volume[volume != 0])
    return result, {'added_observed_voxels': len(added), 'rejected_voxels': rejected,
                    'original_blocks_unchanged': True, 'new_voxels': added,
                    'trunks_inferred': False, 'species_inferred': False}


def render_fixed_camera(volume, names, target, caption):
    """Use the common array bounds so added canopy cannot change the camera."""
    height, depth, width = volume.shape; occupied = volume != 0; faces = []
    corners = np.asarray([[x, y, z] for x in (0, width) for y in (0, height) for z in (0, depth)], float)
    extent = np.column_stack((corners[:, 0]-corners[:, 2], .5*(corners[:, 0]+corners[:, 2])-corners[:, 1]))
    low, high = extent.min(axis=0), extent.max(axis=0)
    scale = min(1120/max(1, high[0]-low[0]), 680/max(1, high[1]-low[1])); offset = np.array([640, 445])-(low+high)*.5*scale
    specs = [(0, 1., [(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)]),
             (2, .76, [(1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)]),
             (1, .87, [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)])]
    for axis, shade, shape in specs:
        neighbor = np.zeros_like(occupied); a = [slice(None)]*3; b = [slice(None)]*3
        a[axis] = slice(0, -1); b[axis] = slice(1, None); neighbor[tuple(a)] = occupied[tuple(b)]
        visible = occupied & ~neighbor
        if len(faces)+int(visible.sum()) > 250000:
            raise ValueError('Canopy render exceeds 250000-face preview bound')
        for y, z, x in np.argwhere(visible):
            vertices = np.asarray(shape, float)+[x, y, z]
            xy = np.column_stack((vertices[:, 0]-vertices[:, 2], .5*(vertices[:, 0]+vertices[:, 2])-vertices[:, 1]))
            colour = headless.COLORS.get(names[int(volume[y, z, x])], (150, 140, 159))
            faces.append((float(vertices.mean(axis=0).sum()), axis, xy, tuple(round(c*shade) for c in colour)))
    image = Image.new('RGB', (1280, 960), (233, 239, 240)); draw = ImageDraw.Draw(image)
    for _, _, polygon, colour in sorted(faces, key=lambda f: (f[0], f[1])):
        draw.polygon([tuple(np.round(p*scale+offset).astype(int)) for p in polygon],
                     fill=colour, outline=tuple(max(0, c-14) for c in colour))
    draw.text((40, 28), caption, fill=(32, 54, 58), font=ImageFont.load_default(size=23))
    draw.text((40, 870), 'Same fixed camera. Observed voxel shapes; illustrative green, no species inference.',
              fill=(32, 54, 58), font=ImageFont.load_default(size=17))
    draw.text((40, 908), 'In-memory candidate only. Original world is unchanged; no game client or new downloads.',
              fill=(52, 71, 74), font=ImageFont.load_default(size=16))
    image.save(target)
    return {'scale': float(scale), 'offset': offset.tolist(), 'visible_faces': len(faces), 'shape': list(volume.shape)}


def run(world, imagery, output, points=None, point_manifest=None, canopy_preview=False):
    start = time.monotonic(); output = Path(output)
    if output.exists():
        raise FileExistsError(output)
    if (points is None) != (point_manifest is None):
        raise ValueError('Points and point manifest must be supplied together')
    evidence_names = ('earthcraft.json', 'top-heights.npy', 'building-layer.npz',
                      'building-layer.json', 'roof-observations.npz', 'photo-skin.json',
                      'resources.zip', 'point-voxels.npy', 'classified-road-mask.npy')
    evidence_hashes = {name: sha(Path(world)/name) for name in evidence_names
                       if (Path(world)/name).is_file()}
    meta, before, names, tops, owner, ground, protected, hashes = read_world(world)
    image, image_evidence = load_image(imagery, meta['source'])
    rgb = visible_bands(image['bands'], RGBN); blocks, colours, palette_evidence = palette()
    top_values = before[tops-meta['dimension_min_y'], np.arange(len(tops))[:, None], np.arange(len(tops))[None, :]]
    current = np.asarray(names, object)[top_values]
    chosen, selection = proposals(rgb, image['valid'], image['shadow_candidate'], image['vegetation_candidate'],
                                  owner, tops, current, protected, blocks, colours)
    after = before.copy(); ids = {name: i for i, name in enumerate(names)}; names = list(names)
    for (z, x), value in chosen.items():
        name = value['block']
        if name not in ids:
            ids[name] = len(names); names.append(name)
        after[int(tops[z, x])-meta['dimension_min_y'], z, x] = ids[name]
    np.testing.assert_array_equal(before != 0, after != 0)
    changed = np.argwhere(before != after)
    expected_changes = {(int(tops[z, x])-meta['dimension_min_y'], z, x) for z, x in chosen}
    if {tuple(map(int, p)) for p in changed} != expected_changes or any(protected[z, x] for z, x in chosen):
        raise ValueError('Changes must remain at selected roof tops outside photo columns')
    size, height = meta['source']['size'], meta['dimension_height']
    np.testing.assert_array_equal(height-np.argmax((after != 0)[::-1], axis=0)+meta['dimension_min_y']-1, tops)
    if points is not None:
        if point_manifest is None:
            raise ValueError('Point manifest required')
        tree_mask, canopy_cells, trees = canopy(points, point_manifest, meta, ground, owner)
    else:
        tree_mask = np.zeros_like(owner, bool); canopy_cells = np.empty((0, 3), np.int32)
        trees = {'trees_placed': 0, 'reason': 'no verified vegetation point input supplied'}
    canopy_volume = None; canopy_candidate = None
    if canopy_preview:
        if points is None:
            raise ValueError('Observed canopy preview requires verified point input')
        from scan_envelope import building_mask
        source = Path(meta['dem_path']).parent
        footprint = building_mask(source, meta['source'])
        road = np.load(Path(world)/'classified-road-mask.npy', allow_pickle=False)
        exclusion = binary_dilation(footprint | (owner > 0), iterations=2) | binary_dilation(road, iterations=1) | protected
        vegetation_name = 'minecraft:oak_leaves'
        if vegetation_name not in ids:
            ids[vegetation_name] = len(names); names.append(vegetation_name)
        canopy_volume, canopy_candidate = add_observed_canopy(before, canopy_cells, ground, exclusion,
                                                            meta['dimension_min_y'], ids[vegetation_name])
        canopy_candidate.update({'material_role': 'oak_leaves/green cubes are a display proxy, not an observed species',
                                 'classification': 5, 'footprint_buffer_m': 2, 'road_buffer_m': 1,
                                 'gap_filling': False, 'candidate_only': True, 'world_written': False,
                                 'promotion_allowed': False})
    if hashes != {name: sha(Path(world)/'region'/name) for name in hashes}:
        raise ValueError('Source world changed during preview')
    if evidence_hashes != {name: sha(Path(world)/name) for name in evidence_hashes}:
        raise ValueError('Source evidence changed during preview')
    output.mkdir(parents=True)
    headless.COLORS.update({name: tuple(map(int, rgb)) for name, rgb in colours.items()})
    headless.COLORS['minecraft:oak_leaves'] = (58, 116, 52)
    floor = max(meta['dimension_min_y'], int(ground.min())-2); ceiling = int(tops.max())+1
    renders = []
    for tag, volume in [('baseline', before), ('candidate', after)]:
        renders.append(headless.render(volume[floor-meta['dimension_min_y']:ceiling-meta['dimension_min_y']],
                                      names, output/(tag+'.png'), 'fixed '+str(size)+' m tile', tag+' roof palette'))
        if tag == 'candidate':
            with Image.open(output/'candidate.png') as rendered:
                drawing = ImageDraw.Draw(rendered)
                drawing.rectangle((25, 20, 1200, 60), fill=(233, 239, 240))
                drawing.text((40, 28), 'EARTHCRAFT / PROPOSED MATERIALS ON FIXED BLOCKS',
                             fill=(32, 54, 58), font=ImageFont.load_default(size=24))
                rendered.save(output/'candidate.png')
    comparison = Image.new('RGB', (2560, 1030), 'white'); draw = ImageDraw.Draw(comparison)
    for i, tag in enumerate(['baseline', 'candidate']):
        with Image.open(output/(tag+'.png')) as image_file:
            comparison.paste(image_file, (i*1280, 0))
    draw.text((35, 970), 'Same geometry and camera. Candidate colours only; no world edited, no accuracy improvement certified.',
              fill=(35, 55, 55), font=ImageFont.load_default(size=22))
    comparison.save(output/'comparison.png')
    if canopy_volume is not None:
        canopy_top = height-np.argmax((canopy_volume != 0)[::-1], axis=0)+meta['dimension_min_y']-1
        canopy_ceiling = max(ceiling, int(canopy_top.max())+1)
        cameras = []
        for tag, volume in [('canopy-baseline', before), ('canopy-candidate', canopy_volume)]:
            caption = 'EARTHCRAFT / '+('BASELINE BLOCKS' if tag == 'canopy-baseline' else 'OBSERVED HIGH-VEGETATION CANDIDATE')
            cameras.append(render_fixed_camera(volume[floor-meta['dimension_min_y']:canopy_ceiling-meta['dimension_min_y']],
                                               names, output/(tag+'.png'), caption))
        if any(cameras[0][key] != cameras[1][key] for key in ('scale', 'offset', 'shape')):
            raise ValueError('Matched canopy cameras differ')
        canopy_candidate['render_cameras'] = cameras
        pair = Image.new('RGB', (2560, 1030), 'white'); drawing = ImageDraw.Draw(pair)
        for i, tag in enumerate(['canopy-baseline', 'canopy-candidate']):
            with Image.open(output/(tag+'.png')) as rendered:
                pair.paste(rendered, (i*1280, 0))
        drawing.text((35, 970), 'Observed class-5 voxels in air only, outside buffered buildings/roads. No trunks, species or world edits.',
                     fill=(35, 55, 55), font=ImageFont.load_default(size=21))
        pair.save(output/'canopy-comparison.png')
    overlay = rgb.copy(); overlay[tree_mask] = (45, 125, 65)
    overlay[(owner > 0) & tree_mask] = (215, 110, 35)
    canopy_image = Image.new('RGB', (max(600, size*6), size*6+110), 'white')
    canopy_image.paste(Image.fromarray(overlay).resize((size*6, size*6), Image.Resampling.NEAREST), (0, 0))
    drawing = ImageDraw.Draw(canopy_image)
    for i, line in enumerate(['Green: classified vegetation at 2..60 m above ground.',
                              'Orange: vegetation evidence overlaps roof ownership.',
                              'Different capture dates may disagree. No trees placed.']):
        drawing.text((10, size*6+12+i*28), line, fill=(35, 55, 55), font=ImageFont.load_default(size=17))
    canopy_image.save(output/'canopy-evidence.png')
    np.savez_compressed(output/'proposal.npz', owner=owner, vegetation_evidence=tree_mask,
                        changed_roof_cells=np.asarray(list(chosen), dtype=np.int32).reshape(-1, 2))
    geometry_capture = meta.get('point_geometry_source', {}).get('source_manifest', {}).get('capture_interval')
    relation = capture_relation(image_evidence['capture'], geometry_capture)
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    result = {'schema': VERSION, 'source_world': str(Path(world).resolve()),
              'source_manifest_sha256': sha(Path(world)/'earthcraft.json'), 'source_regions': hashes,
              'source_evidence_sha256': evidence_hashes,
              'source_grid': meta['source'], 'image_evidence': image_evidence, 'palette_evidence': palette_evidence,
              'actual_chunks': (size//16)**2, 'roof_cells': int((owner > 0).sum()),
              'protected_photo_columns': int(protected.sum()), 'changed_roof_cells': len(chosen),
              'selection': selection, 'changes': [{'local_zx': list(p), **v} for p, v in chosen.items()],
              'canopy': trees, 'regression_gates': {'source_regions_unchanged': True, 'occupancy_equal': True,
                  'heightmaps_equal': True, 'only_selected_roof_blocks_changed': True,
                  'blocks_below_roofs_and_road_ground_unchanged': True, 'photo_columns_preserved': True},
              'renders': renders, 'elapsed_seconds': time.monotonic()-start,
              'maximum_resident_bytes': int(max_rss if sys.platform == 'darwin' else max_rss*1024),
              'temporal_relation_to_geometry': relation, 'geometry_capture': geometry_capture,
              'promotion_gates': {'independent_roof_registration_verified': image_evidence['independent_alignment_verified'],
                  'capture_intervals_overlap': relation == 'overlap', 'heldout_accuracy_check_passed': False},
              'promotion_allowed': False,
              'observed_canopy_candidate': canopy_candidate,
              'roof_colour_geometry_added_or_removed_blocks': 0,
              'accepted_world_geometry_added_or_removed_blocks': 0,
              'canopy_candidate_geometry_added_blocks': 0 if canopy_candidate is None else canopy_candidate['added_observed_voxels'],
              'world_written': False, 'trees_placed': 0,
              'network_requests': 0, 'model_calls': 0, 'candidate_only': True, 'promoted_to_live_pipeline': False,
              'independent_accuracy_verified': False,
              'limitations': ['Fixed local fixture is not proven to be the screenshot location.',
                  'Georeference is not independently validated roof registration; tall roofs can exhibit parallax.',
                  'Image colours include illumination, seasonal conditions and acquisition date differences.',
                  'Local consensus and lower pixel/palette error are presentation diagnostics, not physical accuracy proof.',
                  'Canopy labels/height support do not identify individual stems/species or validate tree placement.',
                  'Headless renders use cubes and mean block colours, not Minecraft blockstate meshes or a game screenshot.']}
    (output/'report.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({key: result[key] for key in ['actual_chunks', 'changed_roof_cells', 'canopy', 'regression_gates', 'elapsed_seconds', 'world_written']}, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', required=True, type=Path)
    parser.add_argument('--imagery', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--points', type=Path)
    parser.add_argument('--point-manifest', type=Path)
    parser.add_argument('--canopy-preview', action='store_true', help='Separate observed class-5 candidate; no world export')
    args = parser.parse_args()
    run(args.world, args.imagery, args.output, args.points, args.point_manifest, args.canopy_preview)
