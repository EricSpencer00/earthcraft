"""Crop original Will/Kendall Arc/Info grids using bounded ZIP-member ranges.

The publisher's XML proves horizontal and vertical references independently.
Only intersecting native grid files are fetched, with original CRCs and hashes;
GDAL decodes their values without a rendered image or an invented roof model.
"""
import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import fcntl
import functools
import hashlib
import json
import math
from pathlib import Path
import copy
import re
import struct
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib

import numpy as np
from pyproj import CRS, Transformer
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from shapely.geometry import box
from shapely.ops import transform

from http_zip_range import RangeReader
import source_http
from regional_scans import frozen_get, SURVEY_FOOT
from region_expansion import atomic, sha
import regional_cache

BASE='https://clearinghouse.isgs.illinois.edu/distribute/'
SOURCES=(('Will',2021,'district1/will/2021/','will'),
         ('Kendall',2018,'district3/kendall/2018/','kend'))
_VERIFIED={}


class BufferedRangeReader(RangeReader):
    """Coalesce sequential payload reads while keeping strict bounded ranges."""
    def read(self,size=-1):
        if size<0:size=self.length-self.position
        size=min(size,self.length-self.position)
        if size>16*2**20:raise ValueError('Bounded original grid range required')
        if not size:return b''
        remaining=size;pieces=[]
        while remaining:
            start=self.position;cached=getattr(self,'_buffer',b'');origin=getattr(self,'_buffer_start',-1)
            if origin<=start<origin+len(cached):
                take=min(remaining,origin+len(cached)-start)
                pieces.append(cached[start-origin:start-origin+take]);self.position+=take;remaining-=take
            elif size<64*2**10:
                pieces.append(super().read(remaining));remaining=0
            else:
                raw=super().read(min(max(remaining,8*2**20),self.length-start))
                self._buffer_start=start;self._buffer=raw;self.position=start
        return b''.join(pieces)


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
        'cache':str(cache),'spatial_reference':reference,
        'metres_per_vertical_unit':factor,'vertical_reference':datum,
        'native_pixel_size':layout['pixel_size'][0],
        'extent':{'xmin':west,'ymin':south,'xmax':east,'ymax':north,'spatialReference':reference},
        'header_sha256':hashlib.sha256(header).hexdigest(),'bounds_sha256':hashlib.sha256(bounds).hexdigest()}
    atomic(destination,json.dumps(record,indent=2).encode());return record


def _local_data_start(layer,entry):
    """Offset of a member's payload: its local header is 30 bytes + name + extra field."""
    offset=entry['offset']
    head=source_http.get_range(layer['archive_url'],offset,min(offset+4095,layer['archive_bytes']-1),layer['archive_etag'],layer['archive_bytes'])
    if head[:4]!=b'PK\x03\x04':raise ValueError('Original grid ZIP local header missing')
    name_length,extra_length=struct.unpack('<2H',head[26:30])
    if head[30:30+name_length].decode('utf-8','replace')!=entry['name']:raise ValueError('Original grid ZIP directory changed')
    return offset+30+name_length+extra_length


def fetch_member(layer,entry,output,chunk=source_http.MAX_RANGE,connections=None,log=None):
    """Stream one ZIP member with parallel bounded ranges; returns (sha256, bytes, crc32).

    Ranges are fetched by at most ``connections`` threads (each also holds a
    machine-wide host slot), consumed strictly in order, inflated, CRC-checked
    against the frozen ZIP directory and written once.
    """
    connections=connections or source_http.connections()
    start=_local_data_start(layer,entry);total=entry['compressed_bytes']
    if start+total>layer['archive_bytes']:raise ValueError('Original grid member exceeds archive')
    spans=[(start+at,min(start+at+chunk,start+total)-1) for at in range(0,total,chunk)]
    inflate=zlib.decompressobj(-15) if entry['compression']==8 else None
    digest=hashlib.sha256();crc=0;count=0;began=time.monotonic()

    def get(span):
        return source_http.get_range(layer['archive_url'],span[0],span[1],layer['archive_etag'],layer['archive_bytes'])

    def emit(raw):
        nonlocal crc,count
        count+=len(raw)
        if count>entry['bytes']:raise ValueError('Original grid decompression exceeded indexed length')
        crc=zlib.crc32(raw,crc);digest.update(raw);output.write(raw)

    with ThreadPoolExecutor(max_workers=connections,thread_name_prefix='grid-range') as pool:
        pending=deque()
        for span in spans:
            pending.append(pool.submit(get,span))
            if len(pending)<=connections:continue
            raw=pending.popleft().result()
            emit(inflate.decompress(raw) if inflate else raw)
        while pending:
            raw=pending.popleft().result()
            emit(inflate.decompress(raw) if inflate else raw)
    if inflate:emit(inflate.flush())
    if count!=entry['bytes']:raise ValueError('Original grid member truncated')
    if crc!=entry['crc32']:raise ValueError('Original grid member CRC differs from ZIP directory')
    seconds=time.monotonic()-began
    (log or _log)(event='grid_member_fetched',member=entry['name'],compressed_bytes=total,bytes=count,
                  seconds=round(seconds,1),mb_per_s=round(total/2**20/max(seconds,1e-3),2),connections=connections)
    return digest.hexdigest(),count,crc


def _log(**event):
    print(json.dumps(event,sort_keys=True),flush=True)


def original_member(layer,name,reserve_bytes=150*2**30):
    entry=layer['members'][name];root=Path(layer['cache']);root.mkdir(parents=True,exist_ok=True)
    path=root/name;proof=root/(name+'.receipt.json')
    with (root/(name+'.lock')).open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if path.exists() and proof.exists():
            record=json.loads(proof.read_text())
            identity=(path.stat().st_size,path.stat().st_mtime_ns,path.stat().st_ctime_ns)
            if record['member']!=entry or record['archive_etag']!=layer['archive_etag']:raise ValueError('Original grid cache identity differs')
            if _VERIFIED.get(str(path))!=identity:
                if path.stat().st_size!=entry['bytes'] or sha(path)!=record['sha256']:raise ValueError('Original grid cache bytes changed')
                _VERIFIED[str(path)]=identity
            regional_cache.touch(path)
            return path,record
        # A half-evicted unit (member without receipt or receipt without member) is refetched.
        path.unlink(missing_ok=True);proof.unlink(missing_ok=True)
        regional_cache.admit(path,entry['bytes']+2**20,what='grid member '+name,
            legacy_reserve=reserve_bytes+entry['bytes']+2**30,legacy_message='Preserve original grid cache storage reserve')
        temporary=path.with_name(name+'.partial')
        try:
            with temporary.open('wb') as output:
                digest,count,_=fetch_member(layer,entry,output)
        except BaseException:
            temporary.unlink(missing_ok=True);raise
        record={'member':entry,'archive_url':layer['archive_url'],'archive_etag':layer['archive_etag'],
                'sha256':digest,'bytes':count,'zip_crc32_verified':True}
        temporary.replace(path);atomic(proof,json.dumps(record,indent=2).encode());return path,record


@functools.lru_cache(maxsize=8)
def _to_native(crs):
    return Transformer.from_crs(crs,6455,always_xy=True)


def wanted_members(layer,grid):
    """Every original member that sample() reads for this grid, in fetch order."""
    crs=grid['crs'];native=_to_native(crs) if isinstance(crs,(str,int)) else Transformer.from_crs(crs,6455,always_xy=True)
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
    wanted+=['hdr.adf','dblbnd.adf','prj.adf','sta.adf']
    return [name for name in wanted if name in layer['members']]  # publisher-absent files are explicit NoData


def cached(layer,name):
    root=Path(layer['cache']);return (root/name).exists() and (root/(name+'.receipt.json')).exists()


def prefetch(layer,grid,limit=None,stop=None):
    """Fetch a grid's missing members into the cache ahead of time; returns the names fetched."""
    fetched=[]
    for name in wanted_members(layer,grid):
        if (stop is not None and stop.is_set()) or (limit is not None and len(fetched)>=limit):break
        if not cached(layer,name):
            original_member(layer,name);fetched.append(name)
    return fetched


def sample(layer,grid,destination):
    size=grid['size'];proofs={}
    for name in wanted_members(layer,grid):
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


def localize(catalog,cache_root):
    """Point original-grid layers at this machine's source cache.

    Catalogs name the member cache relative to the cache root
    ('arcgrid/<county>/<kind>'). Catalogs written by the retired external-drive
    worker held an absolute path; its last two components are the same
    county/kind pair, so they map onto the cache root as well.
    """
    catalog=copy.deepcopy(catalog);cache_root=Path(cache_root)
    for source in catalog.get('sources',[]):
        for kind in ('dtm','dsm'):
            layer=source.get(kind) or {}
            if layer.get('kind')!='original-arcgrid-zip':continue
            cache=Path(layer['cache'])
            relative=Path('arcgrid',*cache.parts[-2:]) if cache.is_absolute() else cache
            if relative.parts[:1]!=('arcgrid',) or len(relative.parts)!=3 or '..' in relative.parts:
                raise ValueError('Unexpected original grid cache location in scan catalog')
            layer['cache']=str(cache_root/relative)
    return catalog


def prepare(control):
    """Index the original county grid archives; members are fetched lazily into the cache."""
    control=Path(control)
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
                  Path('arcgrid')/county/kind,reference,datum,factor)
        catalog['sources']=[source for source in catalog['sources'] if source['county']!=county]
        catalog['sources'].append({'county':county,'year':year,'boundary':borders[county_ids[county]],
            'publisher_xml_sha256':hashlib.sha256(raw).hexdigest(),**pair})
        print(json.dumps({'county':county,'year':year,'result':'original grid pair indexed'}),flush=True)
    atomic(control/'scans/catalog.json',json.dumps(catalog,indent=2).encode())


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control',type=Path,required=True)
    args=parser.parse_args();prepare(args.control)
