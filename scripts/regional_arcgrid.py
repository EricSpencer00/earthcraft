"""Crop original Will/Kendall Arc/Info grids using bounded ZIP-member ranges.

The publisher's XML proves horizontal and vertical references independently.
Only intersecting native grid files are fetched, with original CRCs and hashes;
GDAL decodes their values without a rendered image or an invented roof model.
"""
import argparse
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
from pyproj import CRS, Transformer
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from shapely.geometry import box
from shapely.ops import transform

from http_zip_range import RangeReader
from regional_scans import frozen_get, SURVEY_FOOT
from region_expansion import atomic, sha

BASE='https://clearinghouse.isgs.illinois.edu/distribute/'
SOURCES=(('Will',2021,'district1/will/2021/','will'),
         ('Kendall',2018,'district3/kendall/2018/','kend'))
_VERIFIED={}


def publisher_reference(raw):
    root=ET.fromstring(raw)
    embedded=[ET.fromstring(node.text) for node in root.iter()
              if node.tag=='peXml' and node.text and '<ProjectedCoordinateSystem' in node.text]
    if not embedded:raise ValueError('Publisher coordinate reference unavailable')
    for reference in embedded:
        if reference.findtext('LatestWKID')!='6455' or reference.findtext('LatestVCSWKID')!='6360':
            raise ValueError('Publisher horizontal or vertical reference changed')
        wkt=reference.findtext('WKT','')
        if 'VERTCS[' not in wkt or 'Foot_US' not in wkt or 'NAVD' not in wkt:
            raise ValueError('Explicit NAVD88 survey-foot Z reference required')
    return {'wkid':6455,'vcsWkid':6360},'NAVD88; US survey feet (publisher XML)',SURVEY_FOOT


def grid_layout(header,bounds):
    if len(header)!=308 or not header.startswith(b'GRID1.2') or len(bounds)!=32:
        raise ValueError('Unexpected original Arc/Info grid header')
    cell_x,cell_y=struct.unpack('>2d',header[256:272])
    cols,rows,block_x,_,block_y=struct.unpack('>5i',header[288:308])
    extent=struct.unpack('>4d',bounds)
    if not all(math.isfinite(v) for v in (*extent,cell_x,cell_y)) or not 0<cell_x<=10 or not 0<cell_y<=10:
        raise ValueError('Invalid original grid pixel size or bounds')
    if min(cols,rows,block_x,block_y)<=0 or max(cols*block_x,rows*block_y)>1_000_000:
        raise ValueError('Invalid original grid file layout')
    if extent[0]>=extent[2] or extent[1]>=extent[3]:raise ValueError('Invalid original grid extent')
    return {'bounds':list(extent),'pixel_size':[cell_x,cell_y],
            'file_pixels':[cols*block_x,rows*block_y]}


def tile_members(layout,native_bounds):
    """Native file numbering follows GDAL's AIGAccessTile convention."""
    west,south,east,north=layout['bounds'];dx,dy=layout['pixel_size']
    width,height=layout['file_pixels'];query=box(*native_bounds).intersection(box(west,south,east,north))
    if query.is_empty:return []
    x0,y0,x1,y1=query.bounds
    last_x=math.ceil((east-west)/(dx*width))-1
    last_y=math.ceil((north-south)/(dy*height))-1
    ranges=(range(max(0,math.floor((x0-west)/(dx*width))),min(last_x,math.floor((x1-west)/(dx*width)))+1),
            range(max(0,math.floor((north-y1)/(dy*height))),min(last_y,math.floor((north-y0)/(dy*height)))+1))
    result=[]
    for x in ranges[0]:
        for y in ranges[1]:
            name=f'w{x+1:03}001' if y==0 else f'w{x+1:03}000' if y==1 else f'z{x+1:03}{y-1:03}'
            result.extend((name+'.adf',name+'x.adf'))
    if len(result)>32:raise ValueError('Bounded regional grid crop required')
    return result


def index_archive(url,destination,prefix,cache,reference,datum,factor):
    destination=Path(destination)
    if destination.exists():
        record=json.loads(destination.read_text())
        if record['archive_url']!=url or record['grid_prefix']!=prefix:raise ValueError('Frozen grid archive changed')
        return record
    reader=RangeReader(url,budget=8*2**20)
    with zipfile.ZipFile(reader) as archive:
        infos=[info for info in archive.infolist() if info.filename.startswith(prefix+'/') and not info.is_dir()]
        if not infos or len(infos)>2000:raise ValueError('Original grid members missing or excessive')
        members={}
        for info in infos:
            name=info.filename.split('/')[-1]
            if info.filename!=prefix+'/'+name or not re.fullmatch(r'[a-zA-Z0-9_.]+',name):raise ValueError('Unexpected grid member path')
            if info.flag_bits&1 or info.compress_type not in (0,8) or info.file_size>2*2**30:raise ValueError('Unsupported original grid member')
            members[name]={'name':info.filename,'bytes':info.file_size,'compressed_bytes':info.compress_size,
                           'crc32':info.CRC,'offset':info.header_offset,'compression':info.compress_type}
        header=archive.read(prefix+'/hdr.adf');bounds=archive.read(prefix+'/dblbnd.adf')
        layout=grid_layout(header,bounds)
    west,south,east,north=layout['bounds']
    record={'kind':'original-arcgrid-zip','archive_url':url,'archive_etag':reader.etag,
        'archive_bytes':reader.length,'grid_prefix':prefix,'members':members,'layout':layout,
        'cache':str(Path(cache).resolve()),'spatial_reference':reference,
        'metres_per_vertical_unit':factor,'vertical_reference':datum,
        'native_pixel_size':layout['pixel_size'][0],
        'extent':{'xmin':west,'ymin':south,'xmax':east,'ymax':north,'spatialReference':reference},
        'header_sha256':hashlib.sha256(header).hexdigest(),'bounds_sha256':hashlib.sha256(bounds).hexdigest()}
    atomic(destination,json.dumps(record,indent=2).encode());return record


def original_member(layer,name,reserve_bytes=150*2**30):
    entry=layer['members'][name];root=Path(layer['cache']);root.mkdir(parents=True,exist_ok=True)
    path=root/name;proof=root/(name+'.receipt.json')
    with (root/(name+'.lock')).open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if path.exists() or proof.exists():
            record=json.loads(proof.read_text())
            identity=(path.stat().st_size,path.stat().st_mtime_ns,path.stat().st_ctime_ns)
            if record['member']!=entry or record['archive_etag']!=layer['archive_etag']:raise ValueError('Original grid cache identity differs')
            if _VERIFIED.get(str(path))!=identity:
                if path.stat().st_size!=entry['bytes'] or sha(path)!=record['sha256']:raise ValueError('Original grid cache bytes changed')
                _VERIFIED[str(path)]=identity
            return path,record
        if shutil.disk_usage(root).free<reserve_bytes+entry['bytes']+2**30:raise ValueError('Preserve original grid cache storage reserve')
        reader=RangeReader(layer['archive_url'],budget=entry['compressed_bytes']+8*2**20)
        if reader.etag!=layer['archive_etag'] or reader.length!=layer['archive_bytes']:raise ValueError('Remote grid archive version changed')
        temporary=path.with_name(name+'.partial');digest=hashlib.sha256();count=0
        with zipfile.ZipFile(reader) as archive:
            info=archive.getinfo(entry['name'])
            if (info.file_size,info.compress_size,info.CRC,info.header_offset,info.compress_type)!=(
                entry['bytes'],entry['compressed_bytes'],entry['crc32'],entry['offset'],entry['compression']):
                raise ValueError('Original grid ZIP directory changed')
            with archive.open(info) as source,temporary.open('wb') as output:
                while raw:=source.read(2**20):
                    count+=len(raw)
                    if count>entry['bytes']:raise ValueError('Original grid decompression exceeded indexed length')
                    digest.update(raw);output.write(raw)
        if count!=entry['bytes']:raise ValueError('Original grid member truncated')
        record={'member':entry,'archive_url':layer['archive_url'],'archive_etag':reader.etag,
                'sha256':digest.hexdigest(),'bytes':count,'zip_crc32_verified':True}
        temporary.replace(path);atomic(proof,json.dumps(record,indent=2).encode());return path,record


def sample(layer,grid,destination):
    native=Transformer.from_crs(grid['crs'],6455,always_xy=True)
    size=grid['size'];bounds=transform(native.transform,box(grid['west'],grid['north']-size,
                         grid['west']+size,grid['north']).segmentize(16)).bounds
    margin=2*max(layer['layout']['pixel_size'])
    bounds=(bounds[0]-margin,bounds[1]-margin,bounds[2]+margin,bounds[3]+margin)
    wanted=tile_members(layer['layout'],bounds)
    if not wanted:raise ValueError('Original grid does not cover requested tile')
    # GDAL's AIG identification checks for a first-column raster filename.
    # Retain a genuine original member when a crop uses only other columns;
    # an invented placeholder would silently turn real source pixels to NoData.
    if not any(re.fullmatch(r'[wz]0010\d\d\.adf',name) for name in wanted):
        anchors=[name for name in layer['members'] if re.fullmatch(r'[wz]0010\d\d\.adf',name)]
        if not anchors:raise ValueError('Original grid lacks a GDAL identification member')
        wanted.append(min(anchors,key=lambda name:layer['members'][name]['compressed_bytes']))
    wanted+=['hdr.adf','dblbnd.adf','prj.adf','sta.adf'];proofs={}
    for name in wanted:
        if name not in layer['members']:continue # Publisher absent files are explicit NoData.
        path,record=original_member(layer,name);proofs[name]=record
    expected=rasterio.transform.from_origin(grid['west'],grid['north'],1,1)
    with rasterio.open(Path(layer['cache'])/'hdr.adf') as source:
        if source.driver!='AIG' or not np.allclose(tuple(source.bounds),layer['layout']['bounds'],rtol=0,atol=1e-5):
            raise ValueError('GDAL original grid bounds differ from frozen header')
        # XML is the authority for NAD83(2011); old prj.adf omits its epoch.
        with WarpedVRT(source,src_crs=CRS.from_epsg(6455),crs=grid['crs'],transform=expected,
                       width=size,height=size,resampling=Resampling.nearest,dtype='float32',nodata=np.nan) as projected:
            array=projected.read(1,masked=True).filled(np.nan).astype(np.float32)
    destination=Path(destination)
    with rasterio.open(destination,'w',driver='GTiff',height=size,width=size,count=1,
                       dtype='float32',crs=grid['crs'],transform=expected,nodata=np.nan,compress='deflate') as output:
        output.write(array,1)
    atomic(destination.with_name(destination.name+'.originals.json'),json.dumps(proofs,indent=2).encode())
    return array*layer['metres_per_vertical_unit']


def prepare(control,bulk):
    control=Path(control);bulk=Path(bulk)
    catalog=json.loads((control/'scans/catalog.json').read_text())
    boundaries=json.loads((control/'scans/county-boundaries.geojson').read_text())
    borders={feature['properties']['GEOID']:feature['geometry'] for feature in boundaries['features']}
    county_ids={'Will':'17197','Kendall':'17093'}
    for county,year,directory,short in SOURCES:
        destination=control/'arcgrid'/county;destination.mkdir(parents=True,exist_ok=True)
        raw=frozen_get(BASE+directory+county.lower()+f'_{year}_metadata.xml',destination/'publisher.xml')
        reference,datum,factor=publisher_reference(raw);pair={}
        for kind in ('dtm','dsm'):
            prefix=f'{short}_{kind}_{year}'
            pair[kind]=index_archive(BASE+directory+prefix+'.zip',destination/(kind+'.json'),prefix,
                  bulk/'arcgrid-cache'/county/kind,reference,datum,factor)
        catalog['sources']=[source for source in catalog['sources'] if source['county']!=county]
        catalog['sources'].append({'county':county,'year':year,'boundary':borders[county_ids[county]],
            'publisher_xml_sha256':hashlib.sha256(raw).hexdigest(),**pair})
        print(json.dumps({'county':county,'year':year,'result':'original grid pair indexed'}),flush=True)
    atomic(control/'scans/catalog.json',json.dumps(catalog,indent=2).encode())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control',type=Path,required=True);parser.add_argument('--bulk',type=Path,required=True)
    args=parser.parse_args();prepare(args.control,args.bulk)
