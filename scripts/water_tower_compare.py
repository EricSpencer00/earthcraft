"""Orthographic diagnostics from actual saved blocks and measured LAS points.

Not a Minecraft screenshot; unknown surfaces are not filled or drawn.
"""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from inspect_world import chunks
from verify_metric_world import unpack

ROOT = Path(__file__).resolve().parents[1]
GROUND_M = 181.19298958597915  # County Ground_Z, not fitted to reference height.


def read_building_blocks(world):
    meta=json.loads((world/'earthcraft.json').read_text())
    result=[]
    for region in (world/'region').glob('r.*.*.mca'):
        for _, tag, _ in chunks(region):
            for section in tag['sections']:
                state=section['block_states']; palette=state['palette']
                mapping=np.array([str(p['Name']) not in ('minecraft:air','minecraft:water') for p in palette])
                indices=unpack(state['data'],max(4,(len(palette)-1).bit_length()),4096) if len(palette)>1 else np.zeros(4096,int)
                yy,zz,xx=np.nonzero(mapping[indices].reshape(16,16,16))
                x=xx+int(tag['xPos'])*16+meta['source']['west']
                north=meta['source']['north']-(zz+int(tag['zPos'])*16)
                height=yy+int(section['Y'])*16-meta['vertical_offset_m']-GROUND_M
                valid=(x>=-12)&(x<12)&(north>-16)&(north<=8)&(height>=1)&(height<60)
                result.append(np.column_stack((x[valid],north[valid],height[valid])))
    return np.concatenate(result),meta


def compare(world, points_path, output):
    blocks, meta=read_building_blocks(world)
    data=np.load(points_path)
    points=data['xyz'].copy();points[:,2]-=GROUND_M
    mask=(points[:,0]>=-12)&(points[:,0]<12)&(points[:,1]>-16)&(points[:,1]<=8)&(points[:,2]>=1)&(points[:,2]<60)
    mask &= (data['withheld']==0)&~np.isin(data['classification'],[7,18])
    points=points[mask]; labels=data['classification'][mask]
    scale=10; panel=300; top=70; bottom=670
    image=Image.new('RGB',(1200,740),(246,244,238));draw=ImageDraw.Draw(image)
    draw.text((20,12),'WATER TOWER | saved blocks vs original measured points (2022)',fill='black')
    draw.text((20,30),'Orthographic diagnostic, not gameplay. Same metre scale. No missing surfaces filled.',fill='black')
    for i,(values,axis,label,voxels) in enumerate([(blocks,0,'Blocks: south elevation',True),
            (points,0,'LAS: south elevation',False),(blocks,1,'Blocks: east elevation',True),
            (points,1,'LAS: east elevation',False)]):
        left=i*panel+30;shift=12 if axis==0 else 16
        draw.text((left,50),label,fill='black')
        for h in range(0,61,10):
            y=bottom-h*scale
            draw.line((left,y,left+240,y),fill=(215,214,209))
            draw.text((left-23,y-5),str(h),fill=(90,90,90))
        for j,p in enumerate(values):
            x=left+(p[axis]+shift)*scale;y=bottom-p[2]*scale
            if voxels:
                # Block north coordinate is its north edge, X is west edge.
                x0,x1=(x,x+scale-1) if axis==0 else (x-scale,x-1)
                draw.rectangle((x0,y-scale+1,x1,y),fill=(105,106,106),outline=(130,130,127))
            else:
                color=(180,119,47) if labels[j]==6 else (68,117,150)
                draw.point((round(x),round(y)),fill=color)
    draw.text((20,695),'Grey = actual occupied blocks. Ochre = LAS class 6; blue = other non-noise returns.',fill='black')
    draw.text((20,713),'Ground reference fixed at county Ground_Z. Airborne coverage and classifications remain imperfect.',fill='black')
    output.parent.mkdir(parents=True,exist_ok=True);image.save(output)
    report={'world':str(world),'points':str(points_path),'diagnostic':str(output),
        'block_cells_above_ground_in_roi':len(blocks),'measured_non_noise_points_in_roi':len(points),
        'classification_counts':dict(zip(*[a.tolist() for a in np.unique(labels,return_counts=True)])),
        'block_top_boundary_height_m':float(blocks[:,2].max()+1),
        'observed_point_max_height_m':float(points[:,2].max()),
        'reference_mast_height_m':55.626,'reference_cupola_top_height_m':52.8828,
        'reference_role':'HABS sheet 3, endpoint-specific historical evaluation; not fitted or treated as perfect truth',
        'geographic_accuracy_pass':False,'client_visual_verification':False}
    output.with_suffix('.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('world',type=Path);p.add_argument('--points',type=Path,default=ROOT/'runs/water-tower-points-2022/points.npz')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();compare(args.world,args.points,args.output)
