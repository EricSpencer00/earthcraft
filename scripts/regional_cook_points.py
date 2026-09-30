"""Full-density Cook 2022 building crops from original indexed LAS members."""
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely.geometry import box,shape,mapping
from shapely.ops import transform
from shapely.strtree import STRtree

from cook_city_cache import acquire
from lidar_spatial_index import SpatialPointIndex
from region_expansion import atomic,sha

_TREES={}


def prepare(control):
    """Index every original Cook member, without fetching county point payloads."""
    from cook_city_index import survey_index,archive_index,attach_members
    from regional_scans import frozen_get
    destination=Path(control)/'cook2022';destination.mkdir(parents=True,exist_ok=True)
    catalog=destination/'catalog.json'
    if catalog.exists():return json.loads(catalog.read_text())
    metadata=destination/'publisher.zip'
    frozen_get('https://clearinghouse.isgs.illinois.edu/distribute/district1/cook/2022/cook_TileIndex_metadata.zip',metadata,limit=2**20)
    survey,publisher=survey_index(metadata)
    directories=[archive_index(part,destination/'archives') for part in range(1,6)]
    survey=attach_members(survey,directories)
    assets=[dict(tile['asset'],native_geometry=mapping(tile['native_geometry'])) for tile in survey if tile['asset'] is not None]
    record={'schema':'earthcraft-original-cook-2022-las-inventory-v1','publisher':publisher,
            'assets':assets,'original_points_acquired':False,
            'missing_original_members':[tile['id'] for tile in survey if tile['asset'] is None]}
    atomic(catalog,json.dumps(record,indent=2).encode());return record


def crop(catalog_path,grid,destination,cache,surface_context=None):
    catalog_path=Path(catalog_path);destination=Path(destination);cache=Path(cache)
    record=json.loads(catalog_path.read_text());publisher=record['publisher']
    if publisher['vertical_datum']!='North American Vertical Datum of 1988 Geoid2018' or publisher['native_crs']!='EPSG:6455':
        raise ValueError('Cook publisher coordinate reference changed')
    if str(catalog_path) not in _TREES:
        geometries=[shape(asset['native_geometry']) for asset in record['assets']]
        _TREES[str(catalog_path)]=(STRtree(geometries),record['assets'])
    tree,assets=_TREES[str(catalog_path)]
    native=Transformer.from_crs(grid['crs'],6455,always_xy=True)
    metric=Transformer.from_crs(6455,grid['crs'],always_xy=True)
    west,north,size=grid['west'],grid['north'],grid['size']
    bounds=transform(native.transform,box(west,north-size,west+size,north).segmentize(16))
    selected=sorted(int(i) for i in tree.query(bounds,predicate='intersects'))
    if not selected:raise ValueError('No original Cook survey member covers the requested tile')
    indexer=SpatialPointIndex(cache/'indexes');pieces=[];sources=[];associated_count=classified_count=0
    for index in selected:
        asset=assets[index]
        path,source=acquire(asset,publisher,cache/'originals',reserve_bytes=150*2**30)
        indexed=indexer.ensure(path,source,asset)
        points=indexer.query(indexed,bounds.bounds)
        xyz=points['xyz'];labels=points['classification']
        x,y=metric.transform(xyz[:,0],xyz[:,1]) if len(xyz) else (np.empty(0),np.empty(0))
        from scan_envelope import admit
        metric_xyz=np.column_stack((x,y,xyz[:,2]*(1200/3937)))
        keep,classified,associated=admit(metric_xyz,labels,grid,surface_context)
        associated_count+=int(associated.sum());classified_count+=int(classified.sum())
        pieces.append(metric_xyz[keep])
        sources.append({'survey_id':asset['id'],'original_source':source,'index':str(indexed),
            'original_building_points_in_crop':int(keep.sum())})
    xyz=np.concatenate(pieces) if pieces else np.empty((0,3))
    destination.mkdir(parents=True,exist_ok=True);np.savez_compressed(destination/'points.npz',xyz=xyz)
    receipt={'schema':'earthcraft-classified-las-building-crop-v1','project':'Cook original 2022 LAS',
        'output_horizontal_crs':grid['crs'],'grid':{k:grid[k] for k in ('crs','west','north','size')},
        'points_sha256':sha(destination/'points.npz'),'building_points':len(xyz),'sources':sources,
        'publisher':publisher,'retained_classes':[1,6] if surface_context is not None else [6],'downsampling':False,
        'classified_class6_points':classified_count,'associated_class1_points':associated_count,
        'vertical_reference':'Publisher verified NAVD88 Geoid2018, US survey feet converted by 1200/3937',
        'all_intersecting_original_survey_members_acquired':True,
        'independent_accuracy_verified':False,'facade_colour_measured':False}
    if surface_context is not None:
        from scan_envelope import identity
        receipt['association_context_sha256']=identity(surface_context)
        receipt['point_admission']='Provider class 6; class 1 associated inside mapped building and paired DSM/DTM envelope, >2 m ground clearance; possible clutter'
    atomic(destination/'manifest.json',json.dumps(receipt,indent=2).encode());return receipt
