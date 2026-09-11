"""Paint only measured building roof tops from a frozen orthophoto crop.

This is a bounded, deterministic presentation stage for a closed generated
tile.  It never changes occupancy, wall faces, player saves, or unobserved
pixels.  The input image remains colour evidence rather than a claim of a
surveyed material.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import nbtlib as n
import numpy as np

from building_layer import photo_protection
from city_save_update import read_region
from cook_city_cache import sha
from ground_color import nearest_colors
from material_router import texture_colors
from metric_world import packed, palette_indices, region_write
from verify_metric_world import unpack, verify


def roof_mask(owner, valid, shadow, vegetation):
    """Return pixels with both a known image sample and a shell-owned roof."""
    owner = np.asarray(owner)
    valid, shadow, vegetation = map(lambda value: np.asarray(value, bool),
                                    (valid, shadow, vegetation))
    if owner.ndim != 2 or any(value.shape != owner.shape for value in (valid, shadow, vegetation)):
        raise ValueError('Roof ownership and imagery masks must share one 2D metre grid')
    return (owner > 0) & valid & ~shadow & ~vegetation


def matching_grid(image, source):
    expected = {key: source[key] for key in ('crs', 'west', 'north', 'size')}
    if image.get('source_grid') != expected:
        raise ValueError('Orthophoto grid does not exactly match the generated tile')


def protected_roof_cells(world, selected, top, offset):
    """Keep accepted photo anchors untouched if one happens to be a roof cell."""
    anchors, _, _ = photo_protection(world)
    result = selected.copy()
    ox, oz = offset
    for x, y, z in anchors:
        x -= ox; z -= oz
        if 0 <= x < result.shape[1] and 0 <= z < result.shape[0] and top[z, x] == y:
            result[z, x] = False
    return result, len(anchors)


def apply(source_world, imagery, destination):
    source_world, imagery, destination = map(Path, (source_world, imagery, destination))
    if destination.exists():
        raise FileExistsError(destination)
    if not (source_world/'building-layer.json').is_file():
        raise ValueError('Roof colour requires a verified styled-shell source world')
    image = json.loads((imagery/'imagery.json').read_text())
    report = json.loads((source_world/'earthcraft.json').read_text())
    source = report['source']; matching_grid(image, source)
    image_file = imagery/'metric-imagery.npz'
    if image.get('normalized_sha256') and sha(image_file) != image['normalized_sha256']:
        raise ValueError('Normalized orthophoto changed')
    with np.load(source_world/'building-layer.npz', allow_pickle=False) as model:
        owner = model['geometry_owner'] if 'geometry_owner' in model else model['owner']
        top = model['top'].astype(np.int32)
    with np.load(image_file, allow_pickle=False) as data:
        bands = data['bands']
        if bands.shape != (4, source['size'], source['size']) or bands.dtype != np.uint8:
            raise ValueError('Expected four U8 orthophoto bands on the metre tile grid')
        selected = roof_mask(owner, data['valid'], data['shadow_candidate'], data['vegetation_candidate'])
    offset = tuple(report.get('world_offset_xz', [0, 0]))
    selected, protected_anchor_count = protected_roof_cells(source_world, selected, top, offset)
    colours = texture_colors()
    names = sorted(name for name in colours if name.endswith('_concrete'))
    choices = nearest_colors(bands[:3].transpose(1, 2, 0),
                             np.asarray([colours[name] for name in names]))
    source_regions = {path.name: sha(path) for path in sorted((source_world/'region').glob('r.*.*.mca'))}
    shutil.copytree(source_world, destination, ignore=shutil.ignore_patterns('._*'))
    changed = retained = 0
    height, bottom = report['dimension_height'], report['dimension_min_y']
    ox, oz = offset
    for name in source_regions:
        records = []
        for (wx, wz), tag in read_region(source_world/'region'/name).items():
            cx, cz = wx-ox//16, wz-oz//16
            zs, xs = slice(cz*16, cz*16+16), slice(cx*16, cx*16+16)
            local = selected[zs, xs]
            for section in tag['sections']:
                state = section['block_states']; palette = state['palette']
                if any(block.get('Properties') for block in palette):
                    raise ValueError('Roof painter requires default-state generated blocks')
                values = (np.zeros(4096, int) if len(palette) == 1 else
                          unpack(state['data'], max(4, (len(palette)-1).bit_length()), 4096))
                for z, x in np.argwhere(local):
                    y = int(top[cz*16+z, cx*16+x])
                    if y//16 != int(section['Y']) or not bottom <= y < bottom+height:
                        continue
                    slot = (y % 16)*256 + z*16 + x
                    if str(palette[int(values[slot])]['Name']) == 'minecraft:air':
                        raise ValueError('Selected roof top is not occupied')
                    block = 'minecraft:'+names[int(choices[cz*16+z, cx*16+x])]
                    identifier = next((i for i, entry in enumerate(palette)
                                       if str(entry['Name']) == block), None)
                    if identifier is None:
                        identifier = len(palette); palette.append(n.Compound({'Name': n.String(block)}))
                    if int(values[slot]) == identifier:
                        retained += 1
                    else:
                        values[slot] = identifier; changed += 1
                if len(palette) > 1:
                    state['data'] = packed(values, max(4, (len(palette)-1).bit_length()))
            records.append((wx, wz, tag))
        path = destination/'region'/name; staged = path.with_suffix('.roof')
        region_write(staged, records); staged.replace(path)
    if source_regions != {name: sha(source_world/'region'/name) for name in source_regions}:
        raise ValueError('Source shell changed while roof appearance was staging')
    result = {
        'schema': 'earthcraft.roof-colour-v1',
        'source_world': str(source_world.resolve()),
        'source_regions': source_regions,
        'imagery': str(imagery.resolve()),
        'imagery_manifest_sha256': sha(imagery/'imagery.json'),
        'capture_interval': image.get('capture_interval') or image.get('capture_date_utc'),
        'capture_precision': image.get('capture_precision', 'exact timestamp only when supplied'),
        'rights_scope': image.get('rights_scope') or image.get('license'),
        'selected_roof_cells': int(selected.sum()),
        'recolored_roof_cells': changed,
        'same_palette_roof_cells': retained,
        'protected_photo_anchor_count': protected_anchor_count,
        'palette': names,
        'appearance_role': 'observed_image_colour_on_shell_roof_top_only',
        'geometry_changed': False,
        'walls_painted': False,
        'llm_used': False,
        'independent_accuracy_verified': False,
        'limitations': [
            'Orthophoto pixels are colour observations, not calibrated facade or roof material measurements.',
            'Only shell-owned horizontal roof-top cells with valid non-shadow/non-vegetation samples are recoloured.',
            'The source date precision and any cross-epoch disagreement remain explicit; no temporal fusion is implied.',
        ],
    }
    (destination/'roof-colour.json').write_text(json.dumps(result, indent=2))
    report['roof_appearance'] = {'schema': result['schema'], 'recolored_roof_cells': changed,
                                 'llm_used': False, 'walls_painted': False}
    (destination/'earthcraft.json').write_text(json.dumps(report, indent=2))
    verify(destination)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--world', type=Path, required=True)
    parser.add_argument('--imagery', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(apply(args.world, args.imagery, args.output), indent=2))
