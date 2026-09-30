"""Original Will 2021 LAS crops without fetching the 640 GB nested ZIP."""
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

import laspy
import numpy as np
from pyproj import CRS,Transformer
import shapefile
from shapely.geometry import box,shape,mapping
from shapely.ops import transform
from shapely.strtree import STRtree

from cook_city_cache import acquire_validated_deflate
from cook_city_index import RecordingReader
from http_zip_range import RangeReader
from lidar_spatial_index import SpatialPointIndex
from regional_arcgrid import publisher_reference
from regional_scans import frozen_get,SURVEY_FOOT
from region_expansion import atomic,sha
from zip_member_window import ZipMemberWindow

BASE='https://clearinghouse.isgs.illinois.edu/distribute/district1/will/2021/'
URL=BASE+'will-las2021.zip'
_TREES={}


def validate_asset(asset):
    if asset['url']!=URL or not re.fullmatch(r'\d{4}',asset['id']):raise ValueError('Not an indexed Will survey member')
    if asset['member']!='las/'+asset['id']+'.las':raise ValueError('Will member/source mismatch')
    if asset['compression']!=8 or not 0<asset['compressed_bytes']<asset['uncompressed_bytes']<=3*2**30:
        raise ValueError('Unsupported original Will codec or size')
    if not re.fullmatch(r'[0-9a-f]{8}',asset['crc32']):raise ValueError('Invalid original Will CRC')


def nested_reader(source,budget):
    parent=RangeReader(URL,budget=budget)
    if parent.etag!=source['etag'] or parent.length!=source['bytes']:
        raise ValueError('Original Will parent archive changed')
    return ZipMemberWindow(parent,'will-las.zip',source['member'])


def prepare(control):
    destination=Path(control)/'will2021';destination.mkdir(parents=True,exist_ok=True)
    catalog=destination/'catalog.json'
    if catalog.exists():
        record=json.loads(catalog.read_text())
        for name,digest in record['publisher_files'].items():
            if sha(destination/name)!=digest:raise ValueError('Original Will publisher metadata changed')
        for item in record['ranges']:
            raw=(destination/'ranges'/item['file']).read_bytes()
            if len(raw)!=item['bytes'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:
                raise ValueError('Original Will archive index bytes changed')
        return record
    xml=frozen_get(BASE+'will_2021_metadata.xml',destination/'publisher.xml')
    reference,datum,factor=publisher_reference(xml)
    raw_index=frozen_get(BASE+'will_tile_index.zip',destination/'publisher-index.zip',limit=8*2**20)
    with zipfile.ZipFile(io.BytesIO(raw_index)) as archive:
        parts={}
        for kind in ('shp','shx','dbf','prj'):
            names=[name for name in archive.namelist() if name.endswith('.'+kind)]
            if len(names)!=1 or archive.getinfo(names[0]).file_size>8*2**20:raise ValueError('Ambiguous or oversized Will index')
            parts[kind]=archive.read(names[0])
        if CRS.from_wkt(parts['prj'].decode()).to_epsg()!=6455:raise ValueError('Will survey index reference changed')
        reader=shapefile.Reader(**{kind:io.BytesIO(parts[kind]) for kind in ('shp','shx','dbf')})
        geometries={}
        for item in reader.iterShapeRecords():
            name=item.record.as_dict()['Name']
            if not re.fullmatch(r'\d{1,4}',name):raise ValueError('Unexpected Will tile identifier')
            identifier=name.zfill(4);geometry=shape(item.shape.__geo_interface__)
            if identifier in geometries or geometry.is_empty or not geometry.is_valid:
                raise ValueError('Invalid Will survey index polygon')
            geometries[identifier]=mapping(geometry)
        if not 1<=len(geometries)<=10000:raise ValueError('Will survey index record budget exceeded')
    ranges=destination/'ranges';ranges.mkdir(exist_ok=True)
    parent=RecordingReader(URL,ranges)
    with zipfile.ZipFile(parent) as outer:
        for name,raw in [('will_2021_metadata.xml',xml),('will_tile_index.zip',raw_index)]:
            if outer.getinfo(name).file_size>8*2**20 or outer.read(name)!=raw:
                raise ValueError('LAS archive publisher metadata differs from frozen public source')
    window=ZipMemberWindow(parent,'will-las.zip')
    source={'url':URL,'etag':parent.etag,'bytes':parent.length,'member':window.member}
    assets=[];unindexed=[]
    with zipfile.ZipFile(window) as inner:
        if len(inner.infolist())>12000:raise ValueError('Will original member count exceeds budget')
        for info in inner.infolist():
            if info.is_dir() or not info.filename.lower().endswith('.las'):continue
            if not re.fullmatch(r'las/\d{4}\.las',info.filename):raise ValueError('Unexpected original Will member path')
            identifier=Path(info.filename).stem
            asset={'id':identifier,'member':info.filename,'url':URL,'archive_etag':parent.etag,
                'compressed_bytes':info.compress_size,'uncompressed_bytes':info.file_size,
                'crc32':f'{info.CRC:08x}','header_offset':info.header_offset,'compression':info.compress_type}
            validate_asset(asset)
            if identifier not in geometries:
                # Two archive members lack publisher tile-index polygons. Use
                # their actual original LAS header bounds for source selection.
                with inner.open(info) as member:prefix=member.read(64*2**10)
                with laspy.open(io.BytesIO(prefix),read_evlrs=False) as las:
                    reference=las.header.parse_crs()
                    if reference is None or reference.to_2d().to_epsg()!=6455:
                        raise ValueError('Unindexed Will LAS header reference differs')
                    geometry=mapping(box(*las.header.mins[:2],*las.header.maxs[:2]))
                atomic(destination/(identifier+'.header-prefix'),prefix)
                asset=dict(asset,native_geometry=geometry,selection_bounds_source='Original LAS header')
                unindexed.append(asset);assets.append(asset)
            else:assets.append(dict(asset,native_geometry=geometries.pop(identifier),selection_bounds_source='Publisher tile index'))
    if geometries:raise ValueError('Published Will survey tiles have no original LAS member')
    publisher={'source_page':'https://clearinghouse.isgs.illinois.edu/node/1804',
        'native_crs':'EPSG:6455','vertical_reference':datum,'metres_per_vertical_unit':factor,
        'capture_interval':['2021'],'exact_capture_interval_verified':False,
        'publisher_xml_sha256':hashlib.sha256(xml).hexdigest(),'nested_archive':source,
        'license':'Consult original publisher metadata and Illinois clearinghouse terms; no redistribution assessment'}
    record={'schema':'earthcraft-original-will-2021-las-inventory-v1','publisher':publisher,
        'nested_archive':source,'assets':assets,'unindexed_original_members':unindexed,
        'publisher_files':{name:sha(destination/name) for name in
            ['publisher.xml','publisher-index.zip']+[a['id']+'.header-prefix' for a in unindexed]},
        'ranges':parent.assets,'original_points_acquired':False,'metadata_bytes_transferred':parent.transferred}
    atomic(catalog,json.dumps(record,indent=2).encode());return record


def admit_points(xyz,labels,grid,surface_context=None):
    """Admit class 6 directly; associate class 1 only within measured buildings."""
    west,north,size=grid['west'],grid['north'],grid['size']
    inside=(xyz[:,0]>=west)&(xyz[:,0]<west+size)&(xyz[:,1]>north-size)&(xyz[:,1]<=north)
    classified=inside&(labels==6);associated=np.zeros(len(xyz),bool)
    if surface_context is not None:
        for key in ('dtm','dsm','valid','roof_mask'):
            if np.shape(surface_context[key])!=(size,size):raise ValueError('Will association uses another surface grid')
        indexes=np.flatnonzero(inside&(labels==1))
        rows=np.floor(north-xyz[indexes,1]).astype(int);cols=np.floor(xyz[indexes,0]-west).astype(int)
        supported=(surface_context['valid'][rows,cols]&surface_context['roof_mask'][rows,cols]&
            (xyz[indexes,2]>surface_context['dtm'][rows,cols]+2)&
            (xyz[indexes,2]<=surface_context['dsm'][rows,cols]+2))
        associated[indexes[supported]]=True
    return classified|associated,classified,associated


def crop(catalog_path,grid,destination,cache,surface_context=None):
    catalog_path=Path(catalog_path);destination=Path(destination);cache=Path(cache)
    record=json.loads(catalog_path.read_text());publisher=record['publisher'];source=record['nested_archive']
    if publisher['native_crs']!='EPSG:6455' or publisher['metres_per_vertical_unit']!=SURVEY_FOOT:
        raise ValueError('Will original reference changed')
    if str(catalog_path) not in _TREES:
        assets=record['assets'];_TREES[str(catalog_path)]=(STRtree([shape(a['native_geometry']) for a in assets]),assets)
    tree,assets=_TREES[str(catalog_path)]
    native=Transformer.from_crs(grid['crs'],6455,always_xy=True);metric=Transformer.from_crs(6455,grid['crs'],always_xy=True)
    west,north,size=grid['west'],grid['north'],grid['size']
    bounds=transform(native.transform,box(west,north-size,west+size,north).segmentize(16))
    selected=sorted(int(i) for i in tree.query(bounds,predicate='intersects'))
    if not selected:raise ValueError('No indexed original Will member covers the tile')
    indexer=SpatialPointIndex(cache/'indexes');pieces=[];classifications=[];sources=[];associated_count=classified_count=0
    for index in selected:
        asset=assets[index];validate_asset(asset)
        factory=lambda url,budget:nested_reader(source,budget)
        path,receipt=acquire_validated_deflate(asset,publisher,cache/'originals',150*2**30,reader_factory=factory)
        if receipt['publisher']['nested_archive']!=source:raise ValueError('Cached Will parent identity differs')
        with gzip.open(path,'rb') as stream,laspy.open(stream,closefd=False,read_evlrs=False) as las:
            reference=las.header.parse_crs()
            if reference is None or reference.to_2d().to_epsg()!=6455:raise ValueError('Original Will LAS horizontal CRS differs')
            # The publisher's clipped tile polygon can differ from actual point
            # extrema. Index exact LAS header bounds while retaining that polygon.
            indexed_asset=dict(asset,native_geometry=mapping(box(*las.header.mins[:2],*las.header.maxs[:2])))
        indexed=indexer.ensure(path,receipt,indexed_asset);points=indexer.query(indexed,bounds.bounds)
        xyz=points['xyz'];labels=points['classification']
        x,y=metric.transform(xyz[:,0],xyz[:,1]) if len(xyz) else (np.empty(0),np.empty(0))
        metric_xyz=np.column_stack((x,y,xyz[:,2]*SURVEY_FOOT))
        keep,classified,associated=admit_points(metric_xyz,labels,grid,surface_context)
        pieces.append(metric_xyz[keep]);classifications.append(labels[keep])
        associated_count+=int(associated.sum());classified_count+=int(classified.sum())
        sources.append({'survey_id':asset['id'],'original_source':receipt,'index':str(indexed),
                        'original_building_points_in_crop':int(keep.sum())})
    xyz=np.concatenate(pieces) if pieces else np.empty((0,3));destination.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(destination/'points.npz',xyz=xyz,classification=np.concatenate(classifications))
    receipt={'schema':'earthcraft-associated-las-building-crop-v1' if surface_context is not None else 'earthcraft-classified-las-building-crop-v1',
        'project':'Will original 2021 LAS',
        'output_horizontal_crs':grid['crs'],'grid':{k:grid[k] for k in ('crs','west','north','size')},
        'points_sha256':sha(destination/'points.npz'),'building_points':len(xyz),'sources':sources,
        'publisher':publisher,'retained_classes':[1,6] if surface_context is not None else [6],
        'provider_class6_points':classified_count,'associated_class1_points':associated_count,
        'withheld_retained':False,'downsampling':False,
        'vertical_reference':publisher['vertical_reference'],
        'all_intersecting_indexed_original_members_acquired':True,
        'unindexed_original_members':len(record['unindexed_original_members']),
        'independent_accuracy_verified':False,'facade_colour_measured':False}
    if surface_context is not None:
        receipt['class1_association']={'method':'Mapped building footprint with valid paired DSM/DTM; >2 m above ground and <=DSM+2 m',
            'source_classes_preserved':True,'provider_building_classification':False,
            'arrays_sha256':{key:hashlib.sha256(np.asarray(surface_context[key]).tobytes()).hexdigest()
                             for key in ('dtm','dsm','valid','roof_mask')},
            'limitations':['Spatially associated unclassified returns can include clutter; this is not a facade truth mask.']}
    atomic(destination/'manifest.json',json.dumps(receipt,indent=2).encode());return receipt
