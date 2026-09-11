"""Independently read generated Anvil blocks and compare terrain, heightmaps and spawn."""
import argparse
import json
import math
from pathlib import Path
import numpy as np
from PIL import Image
from inspect_world import chunks
from material_router import texture_colors


def unpack(words, bits, count):
    data = np.asarray(words,dtype=np.int64).view(np.uint64)
    indices=np.arange(count)
    return ((data[indices//(64//bits)] >> ((indices%(64//bits))*bits).astype(np.uint64)) & ((1<<bits)-1)).astype(int)


def verify(world):
    report=json.loads((world/'earthcraft.json').read_text())
    size=report['source']['size']; h=report['dimension_height']; bottom=report['dimension_min_y']
    ox,oz=report.get('world_offset_xz',[0,0])
    import rasterio
    with rasterio.open(report['dem_path']) as dem:
        expected=np.floor(dem.read(1)+report['vertical_offset_m']).astype(int)
    tops=np.zeros((size,size),int)
    colors=np.zeros((size,size,3),np.uint8)
    water_mask=np.zeros((size,size),bool)
    seen=set(); ground_checks=0; heightmap_checks=0; spawn_checks=False
    point_cells=np.load(world/'point-voxels.npy') if (world/'point-voxels.npy').exists() else None
    point_checks=0
    road_mask=np.load(world/'classified-road-mask.npy') if (world/'classified-road-mask.npy').exists() else None
    road_checks=0
    paint=json.loads((world/'ground-appearance.json').read_text()) if (world/'ground-appearance.json').exists() else None
    painted=np.load(world/'ground-appearance-blocks.npy') if paint else None
    if painted is not None and painted.shape!=(size,size):raise ValueError('Ground appearance grid mismatch')
    rgb={'air':(10,10,10),'stone':(115,115,115),'grass_block':(85,133,57),
         'sand':(210,198,146),'water':(52,103,164),'snow_block':(240,245,250),
         'gray_concrete':(92,96,99),'stone_bricks':(146,146,142),'bricks':(153,91,75),
         'sandstone':(209,188,142),'dirt':(132,99,65),'clay':(150,155,165),'bedrock':(60,60,60)}
    rgb.update({block:tuple(map(int,color)) for block,color in texture_colors().items()})
    names=list(rgb); ids={name:i for i,name in enumerate(names)}
    color_table=np.array([rgb[name] for name in names],np.uint8)
    for region in (world/'region').glob('r.*.*.mca'):
        for _,tag,_ in chunks(region):
            cx,cz=int(tag['xPos'])-ox//16,int(tag['zPos'])-oz//16
            assert 0<=cx<size//16 and 0<=cz<size//16, 'Chunk outside declared tile'
            assert (cx,cz) not in seen
            seen.add((cx,cz))
            volume=np.zeros((h,16,16),np.uint8)
            for section in tag['sections']:
                sy=int(section['Y'])*16-bottom
                palette=section['block_states']['palette']
                mapping=np.array([ids[str(p['Name']).removeprefix('minecraft:')] for p in palette],np.uint8)
                if len(palette)==1:values=np.full(4096,mapping[0],np.uint8)
                else:values=mapping[unpack(section['block_states']['data'],max(4,(len(palette)-1).bit_length()),4096)]
                volume[sy:sy+16]=values.reshape(16,16,16)
            zs,xs=slice(cz*16,cz*16+16),slice(cx*16,cx*16+16)
            yy=expected[zs,xs]-bottom; zz,xx=np.mgrid[:16,:16]
            assert np.all(volume[yy,zz,xx]!=ids['air']), 'Missing source ground support'
            if road_mask is not None:
                roads=road_mask[zs,xs]
                target=(np.array([ids[b] for b in paint['palette']])[painted[zs,xs]] if paint else np.full((16,16),ids['gray_concrete']))
                assert np.all(volume[yy,zz,xx][roads]==target[roads]), 'Observed pavement material differs'
                road_checks+=int(roads.sum())
            ground_checks+=256
            if point_cells is not None:
                local=point_cells[(point_cells[:,0]//16==cx)&(point_cells[:,2]//16==cz)]
                expected_structure=np.zeros_like(volume,dtype=bool)
                expected_structure[local[:,1]-bottom,local[:,2]%16,local[:,0]%16]=True
                above_ground=np.arange(bottom,bottom+h)[:,None,None]>expected[zs,xs]
                np.testing.assert_array_equal((volume!=ids['air'])&above_ground,expected_structure)
                point_checks+=len(local)
            top=h-np.argmax((volume!=ids['air'])[::-1],axis=0)
            packed=unpack(tag['Heightmaps']['WORLD_SURFACE'],h.bit_length(),256).reshape(16,16)
            np.testing.assert_array_equal(top,packed)
            heightmap_checks+=256
            tops[zs,xs]=top+bottom-1
            colors[zs,xs]=color_table[volume[top-1,zz,xx]]
            water_mask[zs,xs]=volume[top-1,zz,xx]==ids['water']
            sx,sy,sz=map(math.floor,report['spawn'])
            sx-=ox;sz-=oz
            if sx//16==cx and sz//16==cz:
                assert volume[sy-bottom-1,sz%16,sx%16] not in (ids['air'],ids['water'])
                assert np.all(volume[sy-bottom:sy-bottom+2,sz%16,sx%16]==ids['air'])
                spawn_checks=True
    assert len(seen)==(size//16)**2 and spawn_checks
    roof_checks = 0
    if (world/'mapped-building-tops.npz').exists():
        mapped=np.load(world/'mapped-building-tops.npz')
        np.testing.assert_array_equal(tops[mapped['mask']],mapped['top_y'][mapped['mask']])
    if (world/'roof-observations.npz').exists():
        roof = np.load(world/'roof-observations.npz')
        np.testing.assert_array_equal(tops[roof['mask']], roof['top_y'][roof['mask']])
        roof_checks = int(roof['mask'].sum())
    Image.fromarray(colors).save(world/'block-overview.png')
    np.save(world/'top-heights.npy',tops)
    np.savez_compressed(world/'water-observations.npz',mask=water_mask,top_y=tops)
    result={'native_chunks':len(seen),'ground_support_cells':ground_checks,
        'heightmap_cells':heightmap_checks,'safe_spawn':spawn_checks,
        'sampled_roof_cells':roof_checks,
        'observed_3d_voxels':point_checks,
        'classified_road_ground_cells':road_checks,
        'top_y_range':[int(tops.min()),int(tops.max())],
        'game_load_verified':False,'appearance_verified':False}
    (world/'block-verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('world',type=Path)
    verify(parser.parse_args().world)
