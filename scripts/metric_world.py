"""Populate a fresh Java world from frozen metric sources, without in-game building commands.

Uses nbtlib and the same Anvil sector layout as inspect_world, plus an existing
Arnis level/chunk metadata template. Only block states derived here are retained.
"""
import argparse
import copy
import io
import json
import math
from pathlib import Path
import shutil
import zlib

import nbtlib as n
import numpy as np
from pyproj import Transformer
from rasterio.features import rasterize
from affine import Affine
from shapely.geometry import Polygon, LineString
from shapely import make_valid
from shapely.ops import transform as transform_geometry

from inspect_world import chunks
from osm_json_to_kml import metres,building_tag
from material_router import APPEARANCE_BLOCKS, route
from geographic_quality import vertical_layout
from point_geometry import load_tower_points
from batch_point_geometry import load_batch_points
from world_templates import height_template
from point_ground_materials import load as load_ground_materials
from metric_frame import tile_layout

ROOT = Path(__file__).resolve().parents[1]
PALETTE = ['air', 'bedrock', 'stone', 'dirt', 'grass_block', 'sand', 'water',
           'snow_block', 'gray_concrete', 'stone_bricks', 'bricks', 'sandstone', 'clay']
PALETTE += [block for block in APPEARANCE_BLOCKS if block not in PALETTE]
BLOCK = {name: i for i, name in enumerate(PALETTE)}


def packed(values, bits):
    per_long = 64 // bits
    values = np.asarray(values, dtype=np.uint64).ravel()
    padded = np.zeros(math.ceil(len(values) / per_long) * per_long, dtype=np.uint64)
    padded[:len(values)] = values
    matrix = padded.reshape(-1, per_long)
    words = np.bitwise_or.reduce(matrix << (np.arange(per_long, dtype=np.uint64) * bits), axis=1)
    return n.LongArray(words.view(np.int64))


def palette_indices(values):
    """Stable small-integer unique/inverse without an argsort per section."""
    flat = np.asarray(values, dtype=np.uint8).ravel()
    ids = np.flatnonzero(np.bincount(flat, minlength=len(PALETTE)))
    lookup = np.full(len(PALETTE), -1, dtype=np.int16)
    lookup[ids] = np.arange(len(ids), dtype=np.int16)
    return ids, lookup[flat].reshape(np.asarray(values).shape)


def region_write(path, records):
    if path.exists():
        raise FileExistsError(path)
    header = bytearray(8192)
    body = bytearray()
    for cx, cz, tag in records:
        stream = io.BytesIO()
        tag.write(stream)
        raw = zlib.compress(stream.getvalue())
        sector = (len(raw)+1).to_bytes(4, 'big') + b'\x02' + raw
        sector += b'\0' * (-len(sector) % 4096)
        count = len(sector)//4096
        if count > 255:
            raise ValueError('Oversized chunk')
        slot = (cx % 32) + (cz % 32)*32
        header[slot*4:slot*4+3] = (2+len(body)//4096).to_bytes(3,'big')
        header[slot*4+3] = count
        body.extend(sector)
    path.write_bytes(header+body)


def roof_shell_floor(tops, mask, ground):
    """Lowest exposed side cell of a sampled heightfield; interiors remain empty."""
    heights = np.where(mask, tops, ground)
    padded = np.pad(heights, 1, mode='edge')
    neighbors = np.minimum.reduce([padded[:-2,1:-1], padded[2:,1:-1],
                                   padded[1:-1,:-2], padded[1:-1,2:]])
    return np.maximum(ground + 1, np.minimum(tops, neighbors + 1))


def build(source, destination, surface_source=None, point_source=None, world_frame=None):
    if destination.exists():
        raise FileExistsError(destination)
    if shutil.disk_usage(ROOT).free < 20*1024**3:
        raise ValueError('Preserve 20 GiB free-space reserve')
    meta = json.loads((source/'sources.json').read_text())
    data = np.load(source/'rasters.npz')
    elevation, cover = data['elevation'], data['cover']
    size = meta['size']
    if elevation.shape != (size,size) or cover.shape != (size,size):
        raise ValueError('Raster extent mismatch')
    # One constant vertical translation, preserving all relative elevations.
    world_offset=[0,0]
    if world_frame is None:
        offset, ground, _ = vertical_layout(elevation)
    else:
        offset,ground,_,world_offset=tile_layout(meta,elevation,world_frame)
    surface = np.full((size,size), BLOCK['stone'], np.uint8)
    for code, material in {10:'grass_block',20:'grass_block',30:'grass_block',40:'grass_block',
            50:'gray_concrete',60:'sand',70:'snow_block',80:'stone',90:'clay',95:'clay',100:'grass_block'}.items():
        surface[cover==code] = BLOCK[material]
    transformer = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    buildings, skipped = [], []
    ways = json.loads((source/'osm-ways.json').read_text())
    polygons = []
    for way in ways:
        tags = way['tags']
        x, y = transformer.transform(*zip(*way['coordinates']))
        coords = list(zip(np.asarray(x)-meta['west'], meta['north']-np.asarray(y)))
        if way['closed'] and len(coords)>=4:
            geom = make_valid(Polygon(coords))
            if geom.is_empty:
                continue
            polygons.append((way, geom))
            material = None
            if tags.get('landuse') in ('grass','meadow') or tags.get('leisure') in ('garden','park') or tags.get('natural') in ('grassland','wood'):
                material = 'grass_block'
            if tags.get('natural')=='water' or tags.get('water'):
                material = 'water'
            if material:
                mask = rasterize([(geom,1)], out_shape=(size,size), transform=Affine.identity()).astype(bool)
                surface[mask] = BLOCK[material]
        # Only mapped widths: a centreline alone supplies no measured pavement width.
        elif 'highway' in tags and tags.get('bridge') in (None,'no','false','0') and tags.get('tunnel') in (None,'no','false','0'):
            width = metres(tags.get('width',''))
            if width and len(coords)>1:
                road = LineString(coords).buffer(width/2, cap_style='flat')
                mask = rasterize([(road,1)], out_shape=(size,size), transform=Affine.identity()).astype(bool)
                surface[mask] = BLOCK['gray_concrete']
    parts = [geom for way,geom in polygons if building_tag({'building:part':way['tags'].get('building:part')})]
    footprint_union = np.zeros((size,size),bool)
    for way,geom in polygons:
        tags = way['tags']
        if not building_tag(tags):
            continue
        mask = rasterize([(geom,1)], out_shape=(size,size), transform=Affine.identity()).astype(bool)
        footprint_union |= mask
        reason = None
        height = metres(tags.get('height',''))
        min_height = metres(tags.get('min_height','')) if 'min_height' in tags else 0
        if height is None or height<=0:
            reason = 'no explicit height in metres'
        elif min_height is None or min_height>=height:
            reason = 'invalid explicit min_height; no substituted vertical extent'
        elif 'building:part' not in tags and any(geom.covers(p.representative_point()) for p in parts):
            reason = 'parent contains mapped parts; parent total height is not base height'
        if reason:
            skipped.append({'osm_way':way['id'],'reason':reason})
            continue
        if not mask.any():
            continue
        bottom = float(np.median(elevation[mask])) + offset
        low, high = math.floor(bottom+min_height), math.ceil(bottom+height)-1
        interior = rasterize([(geom.buffer(-1),1)], out_shape=(size,size), transform=Affine.identity()).astype(bool) if not geom.buffer(-1).is_empty else np.zeros_like(mask)
        wall = mask & ~interior
        material = tags.get('building:material','stone_bricks')
        material = {'brick':'bricks','concrete':'gray_concrete'}.get(material,material)
        if material not in BLOCK:
            material = 'stone_bricks'
        buildings.append({'id':way['id'],'mask':mask,'wall':wall,'low':low,'high':high,'block':BLOCK[material],
                          'height':height,'ground_assumption':'median sampled terrain over footprint'})
    # The county's measured footprint/height source replaces the incomplete OSM
    # extrusion layer, rather than overlaying incompatible building volumes.
    county=json.loads((source/'cook-buildings-2022.json').read_text())
    county_buildings=[]; county_ground_errors=[]; county_skipped=[]
    for feature in county['features']:
        attrs=feature['attributes']; geom=None
        for ring in feature['geometry']['rings']:
            polygon=make_valid(Polygon(ring))
            geom=polygon if geom is None else geom.symmetric_difference(polygon)
        def project(lon,lat,z=None):
            x,y=transformer.transform(lon,lat)
            return np.asarray(x)-meta['west'],meta['north']-np.asarray(y)
        geom=transform_geometry(project,geom)
        mask=rasterize([(geom,1)],out_shape=(size,size),transform=Affine.identity()).astype(bool)
        if not mask.any():continue
        footprint_union |= mask
        if not attrs.get('Height') or attrs['Height']<=0:
            county_skipped.append(attrs['OBJECTID']);continue
        # Native county layer is NAD83(2011) Illinois East US-survey-feet.
        # Validate vertical attribute units independently against USGS terrain.
        feet_to_m=1200/3937
        ground_m=attrs['Ground_Z']*feet_to_m
        error=ground_m-float(np.median(elevation[mask]))
        county_ground_errors.append(error)
        height=attrs['Height']*feet_to_m
        if abs((attrs['Max_Point']-attrs['Ground_Z'])-attrs['Height'])>.05:
            raise ValueError('County height is inconsistent with its elevations')
        low=math.floor(ground_m+offset);high=math.ceil(ground_m+offset+height)-1
        inner=geom.buffer(-1)
        inside=rasterize([(inner,1)],out_shape=(size,size),transform=Affine.identity()).astype(bool) if not inner.is_empty else np.zeros_like(mask)
        county_buildings.append({'id':attrs['OBJECTID'],'mask':mask,'wall':mask & ~inside,
            'low':low,'high':high,'block':BLOCK['stone_bricks'],'height':height})
        # Measured building footprint takes precedence over a coarse water class.
        surface[mask & (surface==BLOCK['water'])]=BLOCK['gray_concrete']
    osm_geometry = meta.get('building_source_kind')=='osm-explicit'
    # A tile with no admitted county/OSM geometry and no 3D observation is a
    # terrain-only result even when older source manifests lack the explicit
    # buildings_available flag.  This mirrors the worker's LiDAR skip policy
    # and makes the world receipt unambiguous for later appearance stages.
    terrain_only = (meta.get('buildings_available') is False or
                    (not county['features'] and not osm_geometry and point_source is None))
    if osm_geometry and (county_buildings or point_source is not None or surface_source is not None):
        raise ValueError('OSM geometry profile cannot silently mix county or scan geometry')
    if terrain_only and county_buildings:
        raise ValueError('Terrain-only metadata conflicts with supplied building geometry')
    if not terrain_only and not osm_geometry and county['features'] and (not county_ground_errors or np.median(np.abs(county_ground_errors))>3):
        raise ValueError('County ground elevations fail independent terrain/unit check')
    if not osm_geometry:buildings=county_buildings
    point_cells = point_report = None
    if point_source is not None:
        if surface_source is not None:
            raise ValueError('A/B experiment: choose 3D points or DSM, never blend silently')
        point_manifest=json.loads((point_source/'manifest.json').read_text())
        if 'coverage_geometry' in point_manifest:
            point_cells, point_report = load_batch_points(point_source, source, meta, ground, offset)
        else:
            if {b['id'] for b in buildings} != {833197}:
                raise ValueError('Legacy point crop is Water Tower only; use explicit batch acquisition coverage')
            point_cells, point_report = load_tower_points(point_source, source, meta, ground, offset)
    appearance, appearance_report = route(source, meta, elevation, offset, BLOCK, ways=ways)
    road_report=None
    if point_source is not None:
        road_mask,road_report=load_ground_materials(point_source,meta,elevation,footprint_union)
        if road_mask is not None:surface[road_mask]=BLOCK['gray_concrete']
    lidar_report = None
    if surface_source is not None:
        lidar_report = json.loads((surface_source/'probe.json').read_text())
        if lidar_report['grid'] != {key: meta[key] for key in ('crs','west','north','size')}:
            raise ValueError('LiDAR surface grid does not match world')
        dsm = np.load(surface_source/'metric-surfaces.npz')['dsm']
        if dsm.shape != ground.shape or not np.isfinite(dsm).all():
            raise ValueError('Missing or mismatched LiDAR surface; no flat roof fallback')
        roof_top = np.ceil(dsm + offset).astype(np.int32) - 1
        roof_mask = np.zeros_like(footprint_union)
        for b in buildings:
            roof_mask |= b['mask']
        roof_mask &= roof_top > ground
        roof_floor = roof_shell_floor(roof_top, roof_mask, ground)
    max_y = max([int(ground.max()), *[b['high'] for b in buildings]])
    if lidar_report is not None:
        max_y = max(int(ground.max()), int(roof_top[roof_mask].max()))
    if point_cells is not None and len(point_cells):
        max_y = max(int(ground.max()), int(point_cells[:,1].max()))
    if world_frame is None:
        _, _, world_height = vertical_layout(elevation, max_y)
    else:
        _,_,world_height,_=tile_layout(meta,elevation,world_frame,max_y)
    min_y = -64
    destination.mkdir(parents=True)
    (destination/'region').mkdir()
    template = ROOT/'worlds/chicago-water-tower-64/Arnis World 1'
    _, sample, _ = next(chunks(template/'region/r.0.0.mca'))
    # The sample tag is immutable after this point.  A shallow per-chunk copy
    # is sufficient because every mutable field we change below is replaced by
    # a newly allocated value; deep-copying the full template 256 times was a
    # measurable serialization hotspot.
    sample_template = copy.deepcopy(sample)
    sections_n = world_height//16
    level = n.load(template/'level.dat')
    d = level['Data']
    d['LevelName'] = n.String(destination.name)
    d['GameType'] = n.Int(1)
    d['allowCommands'] = n.Byte(1)
    d['WorldGenSettings']['generate_features'] = n.Byte(0)
    settings = d['WorldGenSettings']['dimensions']['minecraft:overworld']['generator']['settings']
    settings['layers'] = n.List[n.Compound]([n.Compound({'height':n.Int(1),'block':n.String('minecraft:air')})])
    settings['structure_overrides'] = n.List[n.String]([])
    free = np.argwhere(~footprint_union & (surface != BLOCK['water']))
    target_cells = np.argwhere(footprint_union)
    target = target_cells.mean(axis=0) if len(target_cells) else np.array([size/2,size/2])
    # Prefer an exterior viewing distance, not the closest doorway to tile centre.
    # Keep a margin from the data edge and exclude observed elevated blocks.
    viewing = free[(free[:,0]>=3)&(free[:,0]<size-3)&(free[:,1]>=3)&(free[:,1]<size-3)]
    if point_cells is not None:
        occupied = np.zeros_like(footprint_union)
        occupied[point_cells[:,2],point_cells[:,0]] = True
        viewing = viewing[~occupied[viewing[:,0],viewing[:,1]]]
    candidates = viewing if len(viewing) else free
    distance = min(20.0,size/3)
    spawn_z, spawn_x = min(candidates, key=lambda p:(abs(float(np.linalg.norm(p-target))-distance),int(p[0]),int(p[1])))
    spawn = [int(spawn_x)+.5,int(ground[spawn_z,spawn_x])+1,int(spawn_z)+.5]
    player = d['Player']
    # Face the observed structure from the safe terrain spawn, without moving blocks.
    yaw = 0.0
    if len(target_cells):
        target_z, target_x = target_cells.mean(axis=0) + .5
        yaw = math.degrees(math.atan2(-(target_x-spawn[0]), target_z-spawn[2]))
    spawn[0]+=world_offset[0];spawn[2]+=world_offset[1]
    player['Pos'] = n.List[n.Double](spawn)
    player['Rotation'] = n.List[n.Float]([yaw,0])
    player['Inventory'] = n.List[n.Compound]([])
    player['abilities']['flying'] = n.Byte(0)
    player['abilities']['flySpeed'] = n.Float(.2)
    player['abilities']['walkSpeed'] = n.Float(.1)
    d['SpawnX'],d['SpawnY'],d['SpawnZ'] = [n.Int(math.floor(p)) for p in spawn]
    d['BorderCenterX']=n.Double(size/2+world_offset[0]); d['BorderCenterZ']=n.Double(size/2+world_offset[1])
    # Coverage is represented by the actual data edge, not an intrusive blue wall.
    # The surrounding generator remains void: no geography is invented here.
    d['BorderSize']=n.Double(size+2048); d['BorderSizeLerpTarget']=n.Double(size+2048)
    d['BorderSizeLerpTime']=n.Long(0)
    tall = destination/'datapacks/earthcraft_height'
    types = tall/'data/minecraft/dimension_type'
    types.mkdir(parents=True)
    old = height_template(ROOT)
    shutil.copy2(old/'pack.mcmeta',tall/'pack.mcmeta')
    # Preserve upstream version overlays, with one consistent dimension envelope.
    for file in old.rglob('overworld.json'):
        target=tall/file.relative_to(old); target.parent.mkdir(parents=True,exist_ok=True)
        dimension=json.loads(file.read_text()); dimension.update(min_y=min_y,height=world_height,logical_height=world_height)
        target.write_text(json.dumps(dimension))
    d['DataPacks']=n.Compound({'Enabled':n.List[n.String](['vanilla','file/earthcraft_height']), 'Disabled':n.List[n.String]([])})
    d['ScheduledEvents']=n.List[n.Compound]([])
    level.save(destination/'level.dat')
    records = {}
    count = 0
    for cz in range(size//16):
        for cx in range(size//16):
            zs,xs = slice(cz*16,cz*16+16),slice(cx*16,cx*16+16)
            g = ground[zs,xs]
            volume = np.zeros((world_height,16,16),np.uint8)
            yy = np.arange(min_y,min_y+world_height)[:,None,None]
            volume[:] = np.where(yy<=g, BLOCK['stone'],BLOCK['air'])
            volume[0] = BLOCK['bedrock']
            zz,xx=np.mgrid[:16,:16]
            volume[g-min_y,zz,xx] = surface[zs,xs]
            volume[g-min_y-1,zz,xx] = BLOCK['dirt']
            if point_cells is not None:
                local = point_cells[(point_cells[:,0]//16 == cx) & (point_cells[:,2]//16 == cz)]
                volume[local[:,1]-min_y, local[:,2]%16, local[:,0]%16] = BLOCK['stone_bricks']
            elif lidar_report is not None:
                exposed = (roof_mask[zs,xs] & (yy >= roof_floor[zs,xs]) &
                           (yy <= roof_top[zs,xs]))
                volume[exposed] = BLOCK['stone_bricks']
            else:
                for b in buildings:
                    mask,wall=b['mask'][zs,xs],b['wall'][zs,xs]
                    if mask.any():
                        volume[b['low']-min_y:b['high']-min_y+1,wall]=b['block']
                        volume[b['high']-min_y,mask]=b['block']
            # Route appearance only onto occupied building cells; never fill gaps.
            structure = ((volume != BLOCK['air']) & (yy > g)) if osm_geometry else volume == BLOCK['stone_bricks']
            for layer in appearance:
                selected = structure & layer['mask'][zs,xs] & (yy >= layer['low'])
                if layer['high'] is not None:
                    selected &= yy <= layer['high']
                if layer['wall'] is not None:
                    volume[selected] = layer['wall']
                if lidar_report is not None and layer['roof'] is not None:
                    cap = selected & (yy == roof_top[zs,xs])
                    volume[cap] = layer['roof']
                elif osm_geometry and layer['roof'] is not None and layer['high'] is not None:
                    volume[selected & (yy == layer['high'])] = layer['roof']
            tag=copy.copy(sample_template)
            for name in ('entities','block_entities','block_ticks','fluid_ticks'):
                tag[name]=n.List[n.Compound]([])
            tag['structures']=n.Compound({'starts':n.Compound({}),'References':n.Compound({})})
            world_cx=cx+world_offset[0]//16;world_cz=cz+world_offset[1]//16
            tag['xPos']=n.Int(world_cx);tag['zPos']=n.Int(world_cz);tag['yPos']=n.Int(min_y//16)
            tag['isLightOn']=n.Byte(0)
            sections=[]
            for si in range(sections_n):
                values,inverse=palette_indices(volume[si*16:si*16+16])
                states=n.Compound({'palette':n.List[n.Compound]([n.Compound({'Name':n.String('minecraft:'+PALETTE[v])}) for v in values])})
                if len(values)>1:
                    states['data']=packed(inverse,max(4,(len(values)-1).bit_length()))
                sections.append(n.Compound({'Y':n.Byte(si+min_y//16),'block_states':states,
                    'biomes':n.Compound({'palette':n.List[n.String](['minecraft:plains'])})}))
            tag['sections']=n.List[n.Compound](sections)
            top=world_height-np.argmax((volume!=0)[::-1],axis=0)
            heightmap=packed(top,(world_height).bit_length())
            tag['Heightmaps']=n.Compound({name:heightmap for name in ('WORLD_SURFACE','MOTION_BLOCKING','MOTION_BLOCKING_NO_LEAVES','OCEAN_FLOOR')})
            tag['PostProcessing']=n.List[n.List[n.Short]]([n.List[n.Short]([]) for _ in sections])
            records.setdefault((world_cx//32,world_cz//32),[]).append((world_cx,world_cz,tag))
            count += 1
        if cz%8==0:print(f'{count} chunks populated',flush=True)
    for (rx,rz),values in records.items():
        region_write(destination/'region'/f'r.{rx}.{rz}.mca',values)
    report={'status':'populated_not_game_verified','source':meta,'chunks':count,'spawn':spawn,
            'world_offset_xz':world_offset,'world_frame':world_frame,
            'observation_coordinate_frame':'tile-local X/Z, shared absolute Minecraft Y',
            'spawn_rotation':[yaw,0], 'coverage_edge':'Unscanned surroundings are void; use Human at spawn to return.',
        'dem_path':str((source/meta.get('elevation_raster','usgs-elevation.tif')).resolve()),
        'vertical_offset_m':offset,'dimension_min_y':min_y,'dimension_height':world_height,
        'buildings':[{'county_objectid':b['id'],'height_m':b['height'],'base_y':b['low'],'roof_y':b['high']} for b in buildings],
        'skipped_county_buildings':county_skipped,'osm_incomplete_extrusions_replaced':len(skipped),
        'county_ground_vs_usgs_median_abs_error_m':float(np.median(np.abs(county_ground_errors))) if county_ground_errors else None,
        'county_height_units':'not used' if terrain_only else 'US survey feet; numeric ground elevation cross-check against USGS passed; attribute unit metadata not explicit',
        'inference_used':False,'user_build_commands_required':False,
        'limitations':['Extruded shells approximate mapped buildings; roof forms and facades unverified.',
            'Subsurface stone, soil depth, material palette and plains biome are gameplay representations.',
            'ESA tree cover does not supply individual tree geometry; no invented trees placed.',
            'ESA water pixels are ambiguous in this urban area and shown as neutral stone unless OSM confirms water; no invented lakes.',
            'Confirmed water uses observed terrain surface; bathymetry unknown.',
            'Roads without mapped width remain represented only by coarse land cover.',
            'Projection ground-scale error is measured in source report; no globally exact flat projection.'],
        'travel':'Creative flight initial speed 4x vanilla; double-tap jump. Configurable controls still pending.'}
    (destination/'earthcraft.json').write_text(json.dumps(report,indent=2))
    if osm_geometry:
        report['buildings']=[{'osm_way':b['id'],'height_m':b['height'],'base_y':b['low'],'roof_y':b['high']} for b in buildings]
        report['building_geometry']='Mapped explicit-height OSM shells; not scanned building surfaces'
        report['county_height_units']='not used'
        report['osm_skipped_buildings']=skipped
        report['osm_incomplete_extrusions_replaced']=0
        mapped_top=ground.copy();mapped_mask=np.zeros_like(ground,dtype=bool)
        for b in buildings:
            mapped_top[b['mask']]=np.maximum(mapped_top[b['mask']],b['high'])
            mapped_mask|=b['mask']
        np.savez_compressed(destination/'mapped-building-tops.npz',top_y=mapped_top,mask=mapped_mask)
        (destination/'earthcraft.json').write_text(json.dumps(report,indent=2))
    if terrain_only:
        report['building_geometry']='unavailable; terrain-only source proof'
        report['limitations']=['Terrain-only export: buildings, land cover, water semantics and facades were not acquired.',
            'Neutral stone and substrate are presentation, not observed land cover.',
            'AWS tile spacing is not upstream resolution or geographic accuracy.',
            'Vertical datum and independent source accuracy remain unverified.',
            'This is a separate bounded metric chart, not a continuous undistorted Earth.']
        (destination/'earthcraft.json').write_text(json.dumps(report,indent=2))
    (destination/'appearance-routing.json').write_text(json.dumps(appearance_report,indent=2))
    if road_report is not None:
        (destination/'ground-material-routing.json').write_text(json.dumps(road_report,indent=2))
        np.save(destination/'classified-road-mask.npy',road_mask)
    if lidar_report is not None:
        report['building_geometry'] = '2022 DSM sampled heightfield within county footprints; exposed side shell'
        report['lidar_surface_source'] = lidar_report
        report['building_height_metadata_role'] = 'County maximum heights are metadata, not per-cell roof heights.'
        report['limitations'][0] = 'DSM preserves sampled roof levels but includes interpolation and possible vegetation; vertical walls are derived shells, facades and interiors unknown.'
        report['roof_source_cells'] = int(roof_mask.sum())
        np.savez_compressed(destination/'roof-observations.npz', top_y=roof_top, mask=roof_mask)
        (destination/'earthcraft.json').write_text(json.dumps(report,indent=2))
    if point_report is not None:
        report['building_geometry'] = 'Observed 3D point voxels; no heightfield extrusion'
        report['point_geometry_source'] = point_report
        report['building_height_metadata_role'] = 'County heights retained only as metadata; observed 3D points determine occupied cells.'
        report['limitations'][0] = 'Sparse airborne observations; unknown gaps remain empty and one-metre quantization loses fine detail.'
        (destination/'earthcraft.json').write_text(json.dumps(report,indent=2))
        (destination/'point-geometry.json').write_text(json.dumps(point_report,indent=2))
        np.save(destination/'point-voxels.npy',point_cells)
    print(destination,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--surface-source',type=Path)
    parser.add_argument('--point-source',type=Path)
    parser.add_argument('--world-frame',type=Path,help='Frozen shared city frame JSON')
    args=parser.parse_args()
    build(args.source,args.output,args.surface_source,args.point_source,
          json.loads(args.world_frame.read_text()) if args.world_frame else None)
