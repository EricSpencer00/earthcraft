"""Diagnose footprint/orthophoto edge disagreement, never auto-warp or colour walls."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from rasterio.features import rasterize
from rasterio.transform import from_origin
from scipy.ndimage import binary_erosion, distance_transform_edt
from shapely.geometry import box

from building_audit import county_objects
from world_replay import file_hash


def edge_alignment(boundary, edges, radius=8):
    if boundary.shape != edges.shape or boundary.ndim != 2:
        raise ValueError('Alignment masks must have identical two-dimensional grids')
    points = np.argwhere(boundary)
    if len(points) < 16 or not edges.any():
        raise ValueError('Insufficient boundary or imagery edge evidence')
    if not 0 <= radius <= 16: raise ValueError('Bounded translation search required')
    distance = distance_transform_edt(~edges)
    height, width = boundary.shape
    def residual(dx, dy):
        yy, xx = points[:,0]+dy, points[:,1]+dx
        valid=(yy>=0)&(yy<height)&(xx>=0)&(xx<width)
        values=np.full(len(points), float(np.hypot(height,width)))
        values[valid]=distance[yy[valid],xx[valid]]
        return values
    # Interleaved samples are a tuning diagnostic, not an independent holdout.
    choices=[]
    for dy in range(-radius,radius+1):
        for dx in range(-radius,radius+1):
            values=residual(dx,dy)
            choices.append((float(values[::2].mean()),dx*dx+dy*dy,dx,dy))
    _,_,dx,dy=min(choices)
    initial=residual(0,0);fitted=residual(dx,dy)
    return {'boundary_samples':len(points),'best_shift_xz_m':[dx,dy],
            'zero_shift_mean_edge_distance_m':float(initial.mean()),
            'zero_shift_p95_edge_distance_m':float(np.percentile(initial,95)),
            'zero_shift_fraction_within_1m':float((initial<=1).mean()),
            'fitted_mean_edge_distance_m':float(fitted.mean()),
            'interleaved_check_mean_edge_distance_m':float(fitted[1::2].mean()),
            'independent_alignment_verified':False,
            'interpretation':'Nearest generic image edge, not a confirmed roof correspondence; never an automatic correction'}


def audit(source, imagery, output):
    source,imagery,output=map(Path,(source,imagery,output))
    if output.exists():raise FileExistsError(output)
    meta=json.loads((source/'sources.json').read_text())
    report=json.loads((imagery/'imagery.json').read_text())
    grid={k:meta[k] for k in ('crs','west','north','size')}
    if report['source_grid']!=grid:raise ValueError('Imagery and footprint charts differ')
    data=np.load(imagery/'metric-imagery.npz',allow_pickle=False)
    rgb=data['bands'][:3].transpose(1,2,0)
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    edges=cv2.Canny(gray,50,150)>0
    size=meta['size'];aoi=box(meta['west'],meta['north']-size,meta['west']+size,meta['north'])
    display=rgb.copy();records=[]
    for obj in county_objects(source,meta):
        geometry=obj['geometry']
        if geometry.intersection(aoi).area==0:continue
        record={'building_id':obj['id'],'provider_height_m':obj['height_m']}
        mask=rasterize([(geometry,1)],out_shape=(size,size),
                       transform=from_origin(meta['west'],meta['north'],1,1)).astype(bool)
        boundary=mask&~binary_erosion(mask)
        display[boundary]=[255,70,40]
        if not aoi.buffer(-8).covers(geometry):
            record.update(status='not_scored',reason='Footprint clipped or within 8m search margin of scene boundary')
        elif boundary.sum()<16:
            record.update(status='not_scored',reason='Fewer than 16 boundary samples')
        else:
            record.update(status='diagnostic_only',**edge_alignment(boundary,edges))
        records.append(record)
    output.mkdir(parents=True)
    canvas=Image.new('RGB',(768,832),'#f2eee7')
    canvas.paste(Image.fromarray(display).resize((768,768),Image.Resampling.NEAREST),(0,48))
    draw=ImageDraw.Draw(canvas)
    draw.text((12,10),'2019 NAIP + 2022 county footprint boundaries (red) | ALIGNMENT DIAGNOSTIC',fill='#222222')
    draw.text((12,29),'Not a Minecraft screenshot. Roof displacement, shadows and date changes are unresolved.',fill='#222222')
    canvas.save(output/'footprint-overlay.png')
    summary={'status':'diagnostic_only_no_world_admission','capture_date':report['capture_date_utc'],
             'imagery_sha256':file_hash(imagery/'metric-imagery.npz'),
             'footprints_sha256':file_hash(source/'cook-buildings-2022.json'),
             'building_reports':records,'scored_buildings':sum(r['status']=='diagnostic_only' for r in records),
             'image_edge_thresholds':[50,150],'translation_search_radius_m':8,
             'independent_accuracy_verified':False,'world_modified':False,'llm_used':False,
             'spectral_warning':'Spectral thresholds remain uncalibrated. Do not treat usable_candidate as an admission mask.',
             'limitations':['Mapped footprint boundaries are not necessarily roof edges.',
                'Generic edges include shadows, trees, roads and different buildings.',
                'A translation can match unrelated edges; fitted shifts are NOT georeferencing corrections.',
                '2019 imagery and 2022 footprints/points have a three-year epoch difference.',
                'Adjacent interleaved pixels are correlated, not independent validation.']}
    (output/'alignment.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps({'scored_buildings':summary['scored_buildings'],'reports':records},indent=2))
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--imagery',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();audit(a.source,a.imagery,a.output)
