"""Transfer a frozen observed-photo patch between aligned metric charts.

This is a renderer/input adapter, not camera recovery or facade completion.
Only existing supported block faces are eligible. No source commands are copied.
"""
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

import nbtlib as n
import numpy as np
from PIL import Image

from inspect_world import chunks
from verify_metric_world import unpack
from world_replay import digest, file_hash

NORMALS = {'west': (-1, 0, 0), 'east': (1, 0, 0),
           'north': (0, 0, -1), 'south': (0, 0, 1)}
MAX_FACES = 512


def chart_translation(source, target):
    """No rotation, scaling, CRS approximation or fractional voxel realignment."""
    a, b = source['source'], target['source']
    if a['crs'] != b['crs']:
        raise ValueError('Photo layer requires the identical metric CRS')
    delta = np.array([a['west'] - b['west'],
                      target['vertical_offset_m'] - source['vertical_offset_m'],
                      b['north'] - a['north']], dtype=float)
    source_offset=source.get('world_offset_xz',[0,0])
    target_offset=target.get('world_offset_xz',[0,0])
    delta[[0,2]]+=np.asarray(target_offset)-np.asarray(source_offset)
    if not np.isfinite(delta).all() or not np.allclose(delta, np.rint(delta), atol=1e-9, rtol=0):
        raise ValueError('Photo layer grids are not integer-metre aligned')
    return np.rint(delta).astype(np.int64)


def load_records(world):
    """Bound archive reads; retain only verified RGBA observations and attribution."""
    world = Path(world)
    report = json.loads((world / 'photo-skin.json').read_text())
    if report.get('llm_used') is not False or report.get('new_or_removed_blocks') != 0:
        raise ValueError('Layer must declare no LLM use and no geometry completion')
    if report.get('texture_pixels_per_block_edge') != 16 or report.get('surface_offset_m') != .002:
        raise ValueError('Unsupported texture resolution or face offset')
    with zipfile.ZipFile(world / 'resources.zip') as archive:
        members = archive.infolist()
        if (len(members) > 4 + MAX_FACES * 3 or
                sum(m.file_size for m in members) > 16 * 2**20 or
                any(m.file_size > 2**20 for m in members) or
                len({m.filename for m in members}) != len(members)):
            raise ValueError('Photo archive exceeds bounds or has duplicate members')
        records = json.loads(archive.read('earthcraft-skin-manifest.json'))
        attribution = json.loads(archive.read('attribution.json'))
        if not isinstance(records, list) or not 1 <= len(records) <= MAX_FACES:
            raise ValueError('Unsupported photo panel count')
        ids, faces, textures = set(), set(), {}
        texels = 0
        for record in records:
            index, face, cell = record['id'], record['face'], record['cell']
            if type(index) is not int or not 0 <= index < MAX_FACES or index in ids:
                raise ValueError('Invalid or duplicate panel identifier')
            if (face not in NORMALS or not isinstance(cell, list) or len(cell) != 3 or
                    any(type(v) is not int or abs(v) > 30_000_000 for v in cell)):
                raise ValueError('Invalid photo anchor')
            key = (tuple(cell), face)
            if key in faces:
                raise ValueError('Duplicate photo anchor')
            ids.add(index); faces.add(key)
            data = archive.read(f'assets/earthcraft_skin/textures/block/face_{index}.png')
            if hashlib.sha256(data).hexdigest() != record['texture_sha256']:
                raise ValueError('Photo texture checksum changed')
            with Image.open(io.BytesIO(data)) as image:
                if image.size != (16, 16) or image.mode != 'RGBA':
                    raise ValueError('Photo texture must be 16x16 RGBA')
                alpha = np.asarray(image)[:, :, 3]
            count = int((alpha == 255).sum())
            if not np.isin(alpha, [0, 255]).all() or count != record['observed_texels'] or count < 1:
                raise ValueError('Photo support mask disagrees with observations')
            textures[index] = data; texels += count
    if len(records) != report['display_entities'] or texels != report['observed_texels']:
        raise ValueError('Photo layer totals disagree')
    if attribution != report['photo']:
        raise ValueError('Photo attribution disagrees with source report')
    return report, records, textures, attribution


def read_blocks(world, positions):
    """Read actual destination blocks rather than trusting a point sidecar alone."""
    wanted = set(map(tuple, positions)); result = {}
    wanted_chunks = {(x // 16, z // 16) for x, _, z in wanted}
    for path in (world / 'region').glob('r.*.*.mca'):
        for _, tag, _ in chunks(path):
            cx, cz = int(tag['xPos']), int(tag['zPos'])
            if (cx, cz) not in wanted_chunks:
                continue
            sections = {int(s['Y']): s for s in tag['sections']}
            for position in wanted:
                x, y, z = position
                if (x // 16, z // 16) != (cx, cz): continue
                section = sections.get(y // 16)
                if section is None:
                    result[position] = 'minecraft:air'; continue
                state = section['block_states']; palette = state['palette']
                slot = (y % 16) * 256 + (z % 16) * 16 + x % 16
                index = 0 if len(palette) == 1 else int(unpack(
                    state['data'], max(4, (len(palette)-1).bit_length()), 4096)[slot])
                result[position] = str(palette[index]['Name'])
    if len(result) != len(wanted):
        raise ValueError('Photo anchors or preview camera lie outside populated chunks')
    return result


def validate_anchors(world, records):
    meta=json.loads((world/'earthcraft.json').read_text())
    ox,oz=meta.get('world_offset_xz',[0,0])
    occupied = set(map(tuple, np.load(world / 'point-voxels.npy', allow_pickle=False)+[ox,0,oz]))
    positions = []
    for r in records:
        cell = tuple(r['cell']); adjacent = tuple(np.array(cell) + NORMALS[r['face']])
        if cell not in occupied or adjacent in occupied:
            raise ValueError('Photo anchor is absent or its face is covered by observed geometry')
        positions.extend((cell, adjacent))
    blocks = read_blocks(world, positions)
    empty = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}
    for r in records:
        cell = tuple(r['cell']); adjacent = tuple(np.array(cell) + NORMALS[r['face']])
        if blocks[cell] in empty or blocks[adjacent] not in empty:
            raise ValueError('Actual destination blocks do not expose the photo face')


def layer_bounds(records):
    cells = np.array([r['cell'] for r in records], dtype=np.int64)
    lo, hi = cells.min(0), cells.max(0)
    if ((hi[0]//16-lo[0]//16+1)*(hi[2]//16-lo[2]//16+1)) > 16:
        raise ValueError('Photo layer spans more than sixteen chunks')
    return [int(lo[0]), int(lo[2]), int(hi[0]), int(hi[2])]


def write_entry(archive, name, value):
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode()
    archive.writestr(info, data)


def face_model(index, face):
    normal = np.array(NORMALS[face]); lo = np.zeros(3); hi = np.full(3, 16.)
    axis = int(np.flatnonzero(normal)[0]); lo[axis] = hi[axis] = -.032 if normal[axis] < 0 else 16.032
    texture = f'earthcraft_skin:block/face_{index}'
    return {'ambientocclusion': False, 'textures': {'skin': texture, 'particle': texture},
            'elements': [{'from': lo.tolist(), 'to': hi.tolist(), 'shade': False,
                          'faces': {face: {'texture': '#skin', 'uv': [0, 0, 16, 16]}}}]}


def apply_layer(world, source_world, *, allow_experimental=False, start_at_detail=False):
    world, source_world = Path(world), Path(source_world)
    if (world / 'resources.zip').exists() or (world / 'photo-skin.json').exists():
        raise FileExistsError('Photo layer would replace existing world assets')
    pack = world / 'datapacks/earthcraft_photo_skin'
    if pack.exists(): raise FileExistsError(pack)
    original, records, textures, attribution = load_records(source_world)
    if original.get('independent_photo_validation') is not True and not allow_experimental:
        raise ValueError('Photo alignment unverified; explicit experimental profile required')
    source = json.loads((source_world / 'earthcraft.json').read_text())
    target = json.loads((world / 'earthcraft.json').read_text())
    delta = chart_translation(source, target)
    mapped = [dict(r, cell=(np.array(r['cell']) + delta).tolist()) for r in records]
    validate_anchors(source_world, records)
    validate_anchors(world, mapped)
    bounds = layer_bounds(mapped)
    player_position = None
    if start_at_detail:
        player_position = np.asarray(original['initial_player_position'], float) + delta
        if not np.isfinite(player_position).all(): raise ValueError('Invalid photo preview camera')
        feet = np.floor(player_position).astype(int)
        # Initial creative flight may lack floor support, but must have body clearance.
        clearance = [tuple(feet + [x, y, z]) for x in (-1, 0, 1)
                     for y in (0, 1, 2) for z in (-1, 0, 1)]
        if any(block != 'minecraft:air' for block in read_blocks(world, clearance).values()):
            raise ValueError('Photo preview camera intersects destination geometry')
    before = {p.name: file_hash(p) for p in (world / 'region').glob('r.*.*.mca')}
    # Fresh declarative assets and a closed command vocabulary; source functions
    # and models are not executable input to this adapter.
    with zipfile.ZipFile(world / 'resources.zip', 'x') as archive:
        write_entry(archive, 'pack.mcmeta', {'pack': {'pack_format': 69, 'min_format': [69, 0],
                    'max_format': [69, 0], 'description': 'Earthcraft observed photo patch — experimental alignment'}})
        write_entry(archive, 'attribution.json', attribution)
        write_entry(archive, 'earthcraft-skin-manifest.json', mapped)
        for r in mapped:
            index = r['id']
            write_entry(archive, f'assets/earthcraft_skin/textures/block/face_{index}.png', textures[index])
            write_entry(archive, f'assets/earthcraft_skin/models/face_{index}.json', face_model(index, r['face']))
            write_entry(archive, f'assets/earthcraft_skin/items/face_{index}.json', {
                'model': {'type': 'minecraft:model', 'model': f'earthcraft_skin:face_{index}'}})
    functions = pack / 'data/earthcraft_skin/function'; functions.mkdir(parents=True)
    tags = pack / 'data/minecraft/tags/function'; tags.mkdir(parents=True)
    (pack / 'pack.mcmeta').write_text(json.dumps({'pack': {'pack_format': 88, 'min_format': [88, 0],
        'max_format': [88, 0], 'description': 'Earthcraft observed photo layer; no geometry completion'}}))
    for event in ('load', 'tick'):
        (tags / f'{event}.json').write_text(json.dumps({'values': [f'earthcraft_skin:{event}']}))
    def function(name, commands):
        (functions / f'{name}.mcfunction').write_text('\n'.join(commands) + '\n')
    rectangle = ' '.join(map(str, bounds))
    function('load', ['data remove storage earthcraft_skin:state pending'])
    function('tick', ['execute if entity @a unless data storage earthcraft_skin:state {installed:1b} unless data storage earthcraft_skin:state pending run function earthcraft_skin:prepare'])
    function('prepare', [f'forceload add {rectangle}', 'schedule function earthcraft_skin:finish 10t replace',
                         'data modify storage earthcraft_skin:state pending set value 1b'])
    # On retry, replace only this layer's entities after its chunks are loaded.
    # This prevents partial creation from accumulating duplicate panels.
    commands = ['kill @e[type=minecraft:item_display,tag=earthcraft_photo_skin]']
    for r in mapped:
        position = ' '.join(str(v + .5) for v in r['cell'])
        commands.append(f'summon minecraft:item_display {position} '+
            '{Tags:["earthcraft_photo_skin"],item:{id:"minecraft:stone",count:1,components:{'+
            f'"minecraft:item_model":"earthcraft_skin:face_{r["id"]}"'+
            '}},item_display:"none",Rotation:[180f,0f],brightness:{block:15,sky:15},view_range:4f,width:2f,height:2f}')
    commands.append('data modify storage earthcraft_skin:state installed set value 1b')
    function('build', commands)
    function('finish', ['function earthcraft_skin:build', f'forceload remove {rectangle}',
                        'data remove storage earthcraft_skin:state pending'])
    if player_position is not None:
        level = n.load(world / 'level.dat'); player = level['Data']['Player']
        player['Pos'] = n.List[n.Double](player_position.tolist())
        player['Rotation'] = n.List[n.Float]([float(original['initial_player_yaw']), 0])
        player['abilities']['flying'] = n.Byte(1)
        level.save(world / 'level.dat')
    final_player = n.load(world / 'level.dat')['Data']['Player']
    after = {p.name: file_hash(p) for p in (world / 'region').glob('r.*.*.mca')}
    if before != after: raise ValueError('Photo layer changed region geometry')
    report = dict(original, status='experimental_photo_layer_integrated_client_unverified',
                  world=str(world), source_world=str(source_world), source_photo_report_sha256=file_hash(source_world / 'photo-skin.json'),
                  source_resource_pack_sha256=file_hash(source_world / 'resources.zip'),
                  region_sha256=after, chart_translation_blocks=delta.tolist(),
                  display_bounds_xz=bounds, geometry_regions_byte_identical=True,
                  client_visual_verification=False, mapped_faces_sha256=digest(mapped),
                  initial_player_position=list(map(float, final_player['Pos'])),
                  initial_player_yaw=float(final_player['Rotation'][0]),
                  initial_player_flying=bool(final_player['abilities']['flying']))
    # Original baseline is provenance, not a region-by-region comparator for a
    # larger target chart. Retain it under a clearly named source field.
    report['source_baseline'] = report.pop('baseline', original.get('source_baseline'))
    (world / 'photo-skin.json').write_text(json.dumps(report, indent=2))
    verify_layer(world, source_world)
    return report


def verify_layer(world, source_world):
    """Read exported assets and commands back; checks placement, not photo alignment."""
    world, source_world = Path(world), Path(source_world)
    report, mapped, textures, attribution = load_records(world)
    _, originals, source_textures, source_attribution = load_records(source_world)
    source = json.loads((source_world / 'earthcraft.json').read_text())
    target = json.loads((world / 'earthcraft.json').read_text())
    delta = chart_translation(source, target)
    expected = [dict(r, cell=(np.array(r['cell']) + delta).tolist()) for r in originals]
    if mapped != expected or textures != source_textures or attribution != source_attribution:
        raise ValueError('Photo layer changed observation pixels or chart placement')
    validate_anchors(world, mapped)
    for name, sha in report['region_sha256'].items():
        if file_hash(world / 'region' / name) != sha:
            raise ValueError('Geometry changed after photo integration')
    with zipfile.ZipFile(world / 'resources.zip') as archive:
        for r in mapped:
            model = json.loads(archive.read(f'assets/earthcraft_skin/models/face_{r["id"]}.json'))
            if model != face_model(r['id'], r['face']):
                raise ValueError('Unexpected photo model geometry or orientation')
    expected_centers = {r['id']: np.asarray(r['cell'], float) + .5 for r in mapped}
    found = set()
    functions = world / 'datapacks/earthcraft_photo_skin/data/earthcraft_skin/function'
    for line in (functions / 'build.mcfunction').read_text().splitlines():
        if not line.startswith('summon '): continue
        fields = line.split(' ', 5)
        match = re.search(r'"minecraft:item_model":"earthcraft_skin:face_(\d+)"', fields[5])
        if fields[1] != 'minecraft:item_display' or match is None:
            raise ValueError('Unexpected display command')
        index = int(match[1]); center = np.array(list(map(float, fields[2:5])))
        if index in found or index not in expected_centers or not np.array_equal(center, expected_centers[index]):
            raise ValueError('Display command placement differs from photo anchors')
        found.add(index)
    if found != expected_centers.keys(): raise ValueError('Missing photo display commands')
    result = {'display_entities': len(mapped), 'observed_texels': report['observed_texels'],
              'original_texture_bytes_preserved': True, 'actual_block_anchors_verified': True,
              'chart_translation_blocks': delta.tolist(), 'region_geometry_unchanged': True,
              'command_positions_verified': True, 'independent_photo_alignment_verified': False,
              'client_render_verified': False}
    (world / 'photo-layer-verification.json').write_text(json.dumps(result, indent=2))
    return result


def visual_payload(world):
    """Deterministic layer assets/commands, excluding zip timestamps and report paths."""
    world = Path(world)
    if not (world / 'resources.zip').exists(): return None
    with zipfile.ZipFile(world / 'resources.zip') as archive:
        assets = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in sorted(archive.namelist())}
    pack = world / 'datapacks/earthcraft_photo_skin'
    functions = {p.relative_to(pack).as_posix(): file_hash(p) for p in sorted(pack.rglob('*')) if p.is_file()}
    player = n.load(world / 'level.dat')['Data']['Player']
    view = {'position': list(map(float, player['Pos'])), 'rotation': list(map(float, player['Rotation'])),
            'flying': bool(player['abilities']['flying'])}
    return {'sha256': digest({'assets': assets, 'datapack': functions, 'initial_view': view}),
            'asset_count': len(assets), 'datapack_files': len(functions),
            'scope': 'Photo textures, models, mapped anchors, attribution, creation commands and initial view; not rendered appearance'}


def verify_saved_entities(world, records):
    """Check serialized display IDs/positions after the actual server has stopped."""
    expected = {f'earthcraft_skin:face_{r["id"]}': tuple(v + .5 for v in r['cell']) for r in records}
    found = {}
    for path in (Path(world) / 'entities').glob('r.*.*.mca'):
        for _, tag, _ in chunks(path):
            for entity in tag.get('Entities', []):
                if 'earthcraft_photo_skin' not in entity.get('Tags', []): continue
                if str(entity.get('id')) != 'minecraft:item_display':
                    raise ValueError('Unexpected photo-layer entity type')
                model = str(entity['item']['components']['minecraft:item_model'])
                if model in found: raise ValueError('Duplicate saved photo panel')
                found[model] = tuple(map(float, entity['Pos']))
    if found != expected:
        raise ValueError('Saved photo entity positions/models differ from mapped observations')
    return {'count': len(found), 'positions_and_model_ids_match': True}
