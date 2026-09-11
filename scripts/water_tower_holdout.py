"""Acquisition-strip diagnostic, explicitly not an independent accuracy score."""
import json
from pathlib import Path
import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.ops import transform
from shapely import contains_xy
from point_geometry import voxelize
from water_tower_compare import read_building_blocks, GROUND_M

ROOT=Path(__file__).resolve().parents[1]


def main():
    source=ROOT/'runs/water-tower-focus-64'
    meta=json.loads((source/'sources.json').read_text())
    data=np.load(ROOT/'runs/water-tower-points-2022/points.npz');p=data['xyz']
    feature=next(f for f in json.loads((source/'cook-buildings-2022.json').read_text())['features']
                 if f['attributes']['OBJECTID']==833197)
    if len(feature['geometry']['rings'])!=1:raise ValueError('Review changed evaluation footprint')
    envelope=transform(Transformer.from_crs(4326,meta['crs'],always_xy=True).transform,
                       Polygon(feature['geometry']['rings'][0])).buffer(1)
    selected=contains_xy(envelope,p[:,0],p[:,1])&(data['withheld']==0)
    selected &= np.isin(data['classification'],[1,6,15,19])&(p[:,2]>GROUND_M+1)
    train=selected&np.isin(data['point_source_id'],[130,131,132,133,134,135])
    hold=selected&np.isin(data['point_source_id'],[385,386,387,388,389,390])
    if not train.any() or not hold.any() or np.any(selected&~(train|hold)):
        raise ValueError('Frozen strip partition changed')
    baseline=ROOT/'worlds/Earthcraft-Water-Tower-Focus-v6-baseline'
    old,world_meta=read_building_blocks(baseline)
    offset=world_meta['vertical_offset_m']
    training=set(map(tuple,voxelize(p[train],meta,offset)))
    testing=set(map(tuple,voxelize(p[hold],meta,offset)))
    old_cells=np.column_stack((old[:,0]-meta['west'],np.rint(old[:,2]+GROUND_M+offset),
                               meta['north']-old[:,1])).astype(int)
    original=set(map(tuple,old_cells))
    report={'training_point_source_ids':np.unique(data['point_source_id'][train]).tolist(),
        'heldout_point_source_ids':np.unique(data['point_source_id'][hold]).tolist(),
        'training_points':int(train.sum()),'heldout_points':int(hold.sum()),
        'heldout_unique_voxels':len(testing),
        'heldout_voxel_coverage_from_training_3d':len(testing&training)/len(testing),
        'heldout_voxel_coverage_from_dsm_baseline':len(testing&original)/len(testing),
        'caveat':'Acquisition-strip holdout diagnostic, not independent geographic truth. DSM baseline may include both strips.',
        'selection':'County footprint + 1 m; non-withheld classes 1,6,15,19; above county ground + 1 m.',
        'scope':'Evidence that retaining 3D observations avoids heightfield information loss. Not completeness, colour, or facade accuracy.',
        'delivered_world':'v7 uses all strips after this diagnostic; it is not itself held-out.'}
    (ROOT/'runs/water-tower-reference/strip-holdout.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
