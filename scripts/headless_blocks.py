"""Read sealed generated Anvil blocks, check them, and render cubes without Minecraft.

No provider requests, inference, game assets, game launch, or source writes.
Display colors are explicitly illustrative; they are never measured imagery.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import tarfile
import zlib

import nbtlib
import numpy as np
from PIL import Image, ImageDraw, ImageFont

AIR = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}
COLORS = {'minecraft:grass_block': (107,145,91), 'minecraft:dirt': (135,108,82),
          'minecraft:stone_bricks': (151,157,157), 'minecraft:bricks': (160,109,89),
          'minecraft:gray_concrete': (110,118,121), 'minecraft:water': (76,133,173),
          'minecraft:stone': (143,151,152), 'minecraft:sand': (199,183,138),
          'minecraft:lava': (204,98,46)}
CHUNK_KEYS = {'Status','InhabitedTime','DataVersion','zPos','isLightOn','Heightmaps','structures',
              'yPos','xPos','entities','block_entities','PostProcessing','LastUpdate','sections',
              'block_ticks','fluid_ticks'}
HASH = re.compile(r'[a-f0-9]{64}')
LIMIT = 128*2**20


def require(condition, message):
    if not condition: raise ValueError(message)


def digest(raw): return hashlib.sha256(raw).hexdigest()


def checksum(value): return isinstance(value,str) and HASH.fullmatch(value) is not None


def bounded_expand(raw, kind=2, limit=LIMIT):
    decoder = zlib.decompressobj(31 if kind == 1 else 15)
    data = decoder.decompress(raw, limit+1)
    require(len(data) <= limit and decoder.eof and not decoder.unused_data,
            'Compressed data exceeds bound or has trailing/incomplete contents')
    return data


def unpack(words, bits, count):
    per_word = 64//bits
    require(len(words) == (count+per_word-1)//per_word, 'Packed array length differs')
    values = np.asarray(words, dtype=np.int64).view(np.uint64)
    index = np.arange(count, dtype=np.uint64)
    return ((values[index//per_word] >> ((index%per_word)*bits)) & ((1<<bits)-1)).astype(np.int32)


def region_records(raw):
    require(8192 <= len(raw) <= LIMIT and len(raw)%4096 == 0, 'Region size differs')
    used, result = set(), {}
    for slot in range(1024):
        offset = int.from_bytes(raw[slot*4:slot*4+3], 'big'); count = raw[slot*4+3]
        if not offset:
            require(count == 0, 'Sector count without location'); continue
        sectors = set(range(offset, offset+count))
        require(offset >= 2 and count and (offset+count)*4096 <= len(raw) and not used & sectors,
                'Invalid or overlapping region sectors')
        used |= sectors; begin = offset*4096; length = int.from_bytes(raw[begin:begin+4], 'big')
        require(1 < length <= count*4096-4 and raw[begin+4] in (1,2,3), 'Chunk encoding differs')
        result[slot] = raw[begin+4:begin+4+length]
    return result


def read_chunk(record, slot, tile, minimum, height):
    data = bounded_expand(record[1:], record[0], 16*2**20) if record[0] != 3 else record[1:]
    require(len(data) <= 16*2**20, 'Chunk expanded size exceeds bound')
    stream=io.BytesIO(data);tag = nbtlib.File.parse(stream); tx,tz = map(int,tile.split('_'))
    require(stream.tell()==len(data),'Unexpected trailing NBT data')
    require(not set(tag)-CHUNK_KEYS, 'Unexpected generated chunk fields')
    require((int(tag['xPos']),int(tag['zPos'])) == (tx*32+slot%32,tz*32+slot//32), 'Chunk coordinates differ')
    require(not any(tag.get(k) for k in ('entities','block_entities','block_ticks','fluid_ticks')) and
            not any(tag.get('structures', {}).values()), 'Personal/entity content forbidden')
    sections, seen = [], set()
    for section in tag.get('sections', []):
        sy = int(section['Y'])*16
        require(sy not in seen and minimum <= sy < minimum+height and
                not set(section)-{'Y','block_states','biomes','BlockLight','SkyLight'}, 'Section bounds/schema differ')
        seen.add(sy)
        state = section.get('block_states')
        if state is None: continue  # Absent block states mean air; never fill them.
        palette = state.get('palette', [])
        require(0 < len(palette) <= 4096, 'Block palette size differs')
        names = []
        for entry in palette:
            require(not set(entry)-{'Name','Properties'} and
                    re.fullmatch(r'minecraft:[a-z0-9_]+',str(entry.get('Name',''))), 'Block palette identity differs')
            names.append(str(entry['Name']))
        values = (np.zeros(4096,dtype=np.int32) if len(names)==1 else
                  unpack(state.get('data', []),max(4,(len(names)-1).bit_length()),4096))
        require(values.max() < len(names), 'Block palette index differs')
        sections.append((sy,names,values.reshape(16,16,16)))
    return tag, sections


def analyze_region(raw, entry, frame):
    tx,tz = map(int,entry['tile'].split('_')); minimum,height = frame['dimension_min_y'],frame['dimension_height']
    require(type(minimum) is int and type(height) is int and minimum%16==0 and
            16 <= height <= 1024 and height%16==0, 'Declared dimension differs')
    require(entry['world_offset_xz']==[tx*512,tz*512] and entry['size_m']==512 and
            entry['metres_per_block']==1 and entry['region_file']==f'r.{tx}.{tz}.mca' and
            entry['source_grid']=={'west':frame['west']+tx*512,'north':frame['north']-tz*512,'size':512},
            'Metric grid/frame differs')
    records = region_records(raw)
    require(len(records)==1024 and type(entry['chunks']) is int and entry['chunks']==len(records),
            'Complete 512-m tile must have 1024 actual chunks')
    counts, top = Counter(), np.full((512,512),minimum-1,dtype=np.int32)
    top_names = np.full((512,512),'minecraft:air',dtype=object)
    checked = 0; versions = set()
    for slot, record in records.items():
        tag, sections = read_chunk(record,slot,entry['tile'],minimum,height)
        if 'DataVersion' in tag: versions.add(int(tag['DataVersion']))
        rz,rx = (slot//32)*16,(slot%32)*16
        local_top = np.full((16,16),minimum-1,dtype=np.int32)
        local_names = np.full((16,16),'minecraft:air',dtype=object)
        for sy,names,values in sections:
            weights = np.bincount(values.ravel(),minlength=len(names))
            counts.update({name:int(value) for name,value in zip(names,weights) if value})
            occupied = np.array([name not in AIR for name in names])[values]
            ys = np.where(occupied,np.arange(sy,sy+16)[:,None,None],minimum-1)
            maxima = ys.max(axis=0); mask = maxima>local_top
            last = np.maximum(0,maxima-sy)
            material = np.array(names,dtype=object)[values[last,np.arange(16)[:,None],np.arange(16)[None,:]]]
            local_top[mask]=maxima[mask]; local_names[mask]=material[mask]
        top[rz:rz+16,rx:rx+16]=local_top;top_names[rz:rz+16,rx:rx+16]=local_names
        native = tag.get('Heightmaps', {}).get('WORLD_SURFACE')
        if native is not None:
            declared = unpack(native,height.bit_length(),256).reshape(16,16)+minimum-1
            require(np.array_equal(declared,local_top), 'Native WORLD_SURFACE heightmap differs from actual blocks')
            checked += 256
    report = {'tile':entry['tile'],'quality':entry['quality'],'region_sha256':digest(raw),
              'receipt_sha256':entry['receipt_sha256'],'actual_chunks':len(records),
              'actual_non_air_blocks':sum(value for name,value in counts.items() if name not in AIR),
              'block_counts':dict(sorted(counts.items())), 'occupied_columns':int((top>=minimum).sum()),
              'heightmap_columns_checked':checked,'top_y_range':[int(top.min()),int(top.max())],
              'data_versions':sorted(versions),'geometry_grid_privacy_checks':'verified',
              'measured_roof_cells_declared':entry['roof_source_cells'],
              'facade_photo_colors_declared':entry['real_facade_photo_colors_applied'],
              'game_visual_verified':entry['client_visual_verified']}
    return report, top, top_names, records


def select_crop(top, names, width):
    require(type(width) is int and 16 <= width <= 128 and width%16==0, 'Bounded chunk-aligned crop required')
    buildings = np.isin(names, ['minecraft:stone_bricks','minecraft:bricks'])
    best = None
    for z in range(0,513-width,16):
        for x in range(0,513-width,16):
            score = int(buildings[z:z+width,x:x+width].sum())
            candidate = (score,-z,-x)
            if best is None or candidate > best[0]: best = (candidate,x,z)
    return best[0][0],best[1],best[2]


def crop_volume(records, entry, frame, x, z, width, floor, ceiling):
    chunks=[];materials=set()
    for cz in range(z//16,(z+width)//16):
        for cx in range(x//16,(x+width)//16):
            slot=cz*32+cx
            _,sections=read_chunk(records[slot],slot,entry['tile'],frame['dimension_min_y'],frame['dimension_height'])
            chunks.append((cx,cz,sections));materials.update(name for _,palette,_ in sections for name in palette)
    names=['minecraft:air']+sorted(materials-AIR);indices={name:i for i,name in enumerate(names)}
    volume=np.zeros((ceiling-floor+1,width,width),dtype=np.uint16)
    for cx,cz,sections in chunks:
        for sy,palette,values in sections:
            low,high=max(floor,sy),min(ceiling+1,sy+16)
            if low>=high:continue
            mapping=np.array([0 if name in AIR else indices[name] for name in palette],dtype=np.uint16)
            volume[low-floor:high-floor,cz*16-z:cz*16-z+16,cx*16-x:cx*16-x+16]=mapping[values[low-sy:high-sy]]
    return volume,names


def render(volume,names,target,tile,quality):
    occupied=volume!=0;faces=[];face_count=0
    specs=[(0,1.,[(0,1,0),(1,1,0),(1,1,1),(0,1,1)]),
           (2,.76,[(1,0,0),(1,0,1),(1,1,1),(1,1,0)]),
           (1,.87,[(0,0,1),(1,0,1),(1,1,1),(0,1,1)])]
    for axis,shade,corners in specs:
        neighbor=np.zeros_like(occupied)
        current=[slice(None)]*3;other=[slice(None)]*3
        current[axis]=slice(0,-1);other[axis]=slice(1,None)
        neighbor[tuple(current)]=occupied[tuple(other)]
        visible=occupied & ~neighbor;face_count+=int(visible.sum())
        require(face_count<=250000,'Visible face count exceeds bounded preview')
        for y,z,x in np.argwhere(visible):
            vertices=np.array(corners,dtype=float)+[x,y,z]
            projected=np.column_stack((vertices[:,0]-vertices[:,2],.5*(vertices[:,0]+vertices[:,2])-vertices[:,1]))
            rgb=COLORS.get(names[int(volume[y,z,x])],(150,140,159))
            faces.append((float(vertices.mean(axis=0).sum()),axis,projected,tuple(round(c*shade) for c in rgb)))
    require(faces and len(faces)<=250000,'Visible face count exceeds bounded preview')
    extent=np.concatenate([f[2] for f in faces]);low=extent.min(axis=0);high=extent.max(axis=0)
    scale=min(1120/max(1,high[0]-low[0]),680/max(1,high[1]-low[1]))
    offset=np.array([640,445])-(low+high)*.5*scale
    image=Image.new('RGB',(1280,960),(233,239,240));draw=ImageDraw.Draw(image)
    for _,_,polygon,color in sorted(faces,key=lambda f:(f[0],f[1])):
        points=[tuple(np.round(point*scale+offset).astype(int)) for point in polygon]
        draw.polygon(points,fill=color,outline=tuple(max(0,c-14) for c in color))
    draw.text((40,28),'EARTHCRAFT / ACTUAL BLOCK DATA',fill=(32,54,58),font=ImageFont.load_default(size=24))
    draw.text((40,67),f'Tile {tile} / headless orthographic cube render',fill=(52,71,74),font=ImageFont.load_default(size=18))
    draw.text((40,866),f'{quality} geometry / illustrative display colors / absolute datum unverified',
              fill=(32,54,58),font=ImageFont.load_default(size=18))
    draw.text((40,905),'Clipped data window. No Minecraft, game textures, photo inference, or scene generation.',
              fill=(52,71,74),font=ImageFont.load_default(size=15))
    image.save(target,compress_level=9)
    return {'occupied_voxels_in_render_window':int(occupied.sum()),'visible_faces':len(faces),
            'display_palette':{name:list(COLORS.get(name,(150,140,159))) for name in names if name not in AIR},
            'palette_role':'illustrative block-identity colors; not measured imagery or game textures',
            'block_shape_role':'unit cubes; blockstate-specific meshes and liquid heights are not modeled'}


def analyze(archive, expected, output, revision, observed_utc, crop_size=96):
    require(checksum(expected) and isinstance(revision,str) and re.fullmatch(r'[a-f0-9]{40}',revision), 'Pinned archive/revision required')
    require(datetime.fromisoformat(observed_utc.replace('Z','+00:00')).utcoffset() is not None,'UTC observation timestamp required')
    archive,output=Path(archive),Path(output)
    require(archive.stat().st_size<=64*2**20, 'Archive exceeds 64 MiB bound')
    raw=archive.read_bytes();require(digest(raw)==expected,'Archive checksum differs')
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as tar:
        members=[]
        for member in tar:
            members.append(member);require(len(members)<=33 and member.size<=64*2**20 and
                                           member.offset_data+member.size<=len(raw),
                                           'Bounded tile/member count and size required')
        require(all(m.isfile() for m in members) and len({m.name for m in members})==len(members),
                                        'Unique regular archive members required')
        manifest=json.loads(tar.extractfile('manifest.json').read(1024*1024+1))
        require(manifest['schema']=='earthcraft.generated-tile-shard.v1' and 1<=len(manifest['entries'])<=32,
                'Supported sealed shard required')
        require(len({entry['tile'] for entry in manifest['entries']})==len(manifest['entries']), 'Duplicate tile identity')
        frame=manifest['frame'];expected_members={'manifest.json'};reports=[];best=None
        require(all(type(frame[k]) in (int,float) and math.isfinite(frame[k]) for k in ('west','north','vertical_offset_m')),
                'Finite metric frame required')
        for entry in manifest['entries']:
            require(re.fullmatch(r'-?\d+_-?\d+',entry['tile']) and checksum(entry['region_sha256']) and
                    checksum(entry['receipt_sha256']) and entry['quality'] in ('LQ','scan-roof','scan-points-and-roof') and
                    type(entry['roof_source_cells']) is int and 0<=entry['roof_source_cells']<=512**2 and
                    type(entry['real_facade_photo_colors_applied']) is int and entry['real_facade_photo_colors_applied']==0 and
                    entry['client_visual_verified'] is False, 'Frozen tile identities and supported claims required')
            name=entry['tile']+'/'+entry['region_file']+'.gz';expected_members.add(name)
            compressed=tar.extractfile(name).read(64*2**20+1)
            require(len(compressed)<=64*2**20,'Compressed region exceeds bound')
            region=bounded_expand(compressed,1);require(digest(region)==entry['region_sha256'],'Region checksum differs')
            report,top,names,records=analyze_region(region,entry,frame);reports.append(report)
            score,x,z=select_crop(top,names,crop_size)
            choice=(score,entry['tile'])
            if best is None or choice>best[0]:best=(choice,entry,top,names,records,x,z)
        require({m.name for m in members}==expected_members,'Unexpected archive contents')
    _,entry,top,names,records,x,z=best
    crop_top=top[z:z+crop_size,x:x+crop_size]
    floor=max(frame['dimension_min_y'],int(crop_top.min())-4);ceiling=int(crop_top.max())
    volume,materials=crop_volume(records,entry,frame,x,z,crop_size,floor,ceiling)
    output.mkdir(parents=True,exist_ok=True)
    image=output/'blocks.png';rendered=render(volume,materials,image,entry['tile'],entry['quality'])
    overview=Image.new('RGB',(512,512));pixels=np.zeros((512,512,3),dtype=np.uint8)
    for name in np.unique(names):pixels[names==name]=COLORS.get(name,(150,140,159))
    overview=Image.fromarray(pixels);drawing=ImageDraw.Draw(overview)
    drawing.rectangle((x,z,x+crop_size-1,z+crop_size-1),outline=(200,45,30),width=2)
    overview.save(output/'topdown.png',compress_level=9)
    result={'schema':'earthcraft.headless-block-evidence.v1','observed_utc':observed_utc,
      'source':{'archive_sha256':expected,'revision':revision,'plan_sha256':manifest['plan_sha256'],
                'audited_tiles':len(reports),'actual_chunks':sum(r['actual_chunks'] for r in reports),
                'attribution':manifest['attribution'],'source_observation_is_new_generation':False},
      'tiles':reports,'render':dict(rendered,tile=entry['tile'],crop_local_xz=[x,z],crop_size_m=crop_size,
          world_offset_xz=entry['world_offset_xz'],vertical_crop_y=[floor,ceiling],
          png_sha256=digest(image.read_bytes()),topdown_sha256=digest((output/'topdown.png').read_bytes()),
          renderer_sha256=digest(Path(__file__).read_bytes())),
      'checks':{'archive_and_region_hashes':'verified','actual_block_decoding':'verified',
          'coordinates_and_generator_privacy':'verified',
          'native_heightmaps':'verified' if all(r['heightmap_columns_checked']==512**2 for r in reports) else 'partial',
          'datum':'unresolved_inherited_frame','measured_color':'unresolved_no_source_color_receipt',
          'independent_geometry_accuracy':'unresolved','game_visual_acceptance':'not_performed',
          'completeness':'complete chunk coverage in sampled tiles; global and facade coverage incomplete'},
      'frame':frame,'minecraft_opened':False,'inference_used':False,
      'limits':['Previously archived sample; no new generation or installation claimed.',
          'LQ shapes can be inferred or extruded; block correctness is not survey accuracy.',
          'Image geometry comes only from decoded cells. Cropping exposes cut faces.',
          'Display palette is illustrative; no facade photography or measured material claim.']}
    (output/'report.json').write_text(json.dumps(result,sort_keys=True,indent=2)+'\n')
    validate_bundle(output)
    return result


def validate_bundle(directory):
    directory=Path(directory);path=directory/'report.json'
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<=128*1024,
            'Bounded regular report required')
    report=json.loads(path.read_text())
    require(report['schema']=='earthcraft.headless-block-evidence.v1' and report['minecraft_opened'] is False and
            report['inference_used'] is False,'Headless evidence schema differs')
    require(type(report['source']['audited_tiles']) is int and type(report['source']['actual_chunks']) is int and
            report['source']['audited_tiles']==len(report['tiles']) and
            report['source']['actual_chunks']==sum(tile['actual_chunks'] for tile in report['tiles']), 'Evidence totals differ')
    require(1<=len(report['tiles'])<=32 and len({tile['tile'] for tile in report['tiles']})==len(report['tiles']) and
            checksum(report['source']['archive_sha256']) and checksum(report['source']['plan_sha256']) and
            re.fullmatch(r'[a-f0-9]{40}',report['source']['revision']) and
            report['source']['source_observation_is_new_generation'] is False and
            all(type(tile['actual_chunks']) is int and tile['actual_chunks']==1024 and
                type(tile['actual_non_air_blocks']) is int and tile['actual_non_air_blocks']>0 and
                type(tile['occupied_columns']) is int and 0<tile['occupied_columns']<=512**2 and
                tile['quality'] in ('LQ','scan-roof','scan-points-and-roof') and
                checksum(tile['region_sha256']) and checksum(tile['receipt_sha256']) and
                type(tile['heightmap_columns_checked']) is int and
                0<=tile['heightmap_columns_checked']<=512**2 and tile['heightmap_columns_checked']%256==0 and
                tile['game_visual_verified'] is False and tile['facade_photo_colors_declared']==0 and
                all(re.fullmatch(r'minecraft:[a-z0-9_]+',name) for name in tile['block_counts']) and
                all(type(count) is int and count>=0 for count in tile['block_counts'].values()) and
                sum(count for name,count in tile['block_counts'].items() if name not in AIR)==tile['actual_non_air_blocks']
                for tile in report['tiles']), 'Typed actual block evidence required')
    require(report['render']['renderer_sha256']==digest(Path(__file__).read_bytes()),
            'Evidence must be regenerated after a renderer change')
    require(report['render']['tile'] in {tile['tile'] for tile in report['tiles']} and
            type(report['render']['occupied_voxels_in_render_window']) is int and
            report['render']['occupied_voxels_in_render_window']>0 and
            type(report['render']['visible_faces']) is int and 0<report['render']['visible_faces']<=250000,
            'Bounded render evidence required')
    stamp=datetime.fromisoformat(report['observed_utc'].replace('Z','+00:00'))
    require(stamp.utcoffset() is not None,'Observation timestamp must include its timezone')
    crop=report['render'];width=crop['crop_size_m'];xz=crop['crop_local_xz'];yrange=crop['vertical_crop_y']
    minimum,height=report['frame']['dimension_min_y'],report['frame']['dimension_height']
    require(type(width) is int and 16<=width<=128 and width%16==0 and len(xz)==2 and
            all(type(v) is int and v%16==0 and 0<=v<=512-width for v in xz) and
            len(yrange)==2 and all(type(v) is int for v in yrange) and
            minimum<=yrange[0]<=yrange[1]<minimum+height and
            crop['occupied_voxels_in_render_window']<=width*width*(yrange[1]-yrange[0]+1),
            'Render crop bounds or volume differ')
    for name,key in [('blocks.png','png_sha256'),('topdown.png','topdown_sha256')]:
        path=directory/name;require(path.is_file() and not path.is_symlink() and path.stat().st_size<=2*2**20 and
                                   digest(path.read_bytes())==report['render'][key],'Evidence image hash/size differs')
        with Image.open(path) as image:
            require(image.format=='PNG' and image.size==((1280,960) if name=='blocks.png' else (512,512)),
                    'Evidence PNG dimensions differ')
            image.verify()
    require(report['checks']['datum']=='unresolved_inherited_frame' and
            report['checks']['measured_color']=='unresolved_no_source_color_receipt' and
            report['checks']['independent_geometry_accuracy']=='unresolved' and
            report['checks']['native_heightmaps']==('verified' if all(t['heightmap_columns_checked']==512**2
                                                                     for t in report['tiles']) else 'partial') and
            report['checks']['game_visual_acceptance']=='not_performed','Unsupported accuracy/color/game claim')
    text=json.dumps(report)
    require(not any(value in text for value in ('/Users/','/Volumes/','/home/','/private/','file://')), 'Private machine path in public evidence')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);commands=p.add_subparsers(dest='command',required=True)
    a=commands.add_parser('analyze');a.add_argument('--archive',type=Path,required=True)
    a.add_argument('--sha256',required=True);a.add_argument('--revision',required=True);a.add_argument('--output',type=Path,required=True)
    a.add_argument('--observed-utc',default=datetime.now(timezone.utc).isoformat());a.add_argument('--crop-size',type=int,default=96)
    v=commands.add_parser('validate');v.add_argument('--directory',type=Path,required=True)
    args=p.parse_args()
    result=validate_bundle(args.directory) if args.command=='validate' else analyze(
        args.archive,args.sha256,args.output,args.revision,args.observed_utc,args.crop_size)
    print(json.dumps({'audited_tiles':result['source']['audited_tiles'],'actual_chunks':result['source']['actual_chunks'],
                      'render':result['render']['tile'],'checks':result['checks']}))
