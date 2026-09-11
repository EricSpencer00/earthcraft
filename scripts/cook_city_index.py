"""Join Chicago build tiles to the actual Cook 2022 survey and remote ZIP members.

Only ZIP directory ranges are fetched. Neither indexed coverage nor an available
member proves that its points or facade photographs have been acquired.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET

from pyproj import CRS,Transformer
import shapefile
from shapely.geometry import shape,box,mapping
from shapely.ops import transform,unary_union
from shapely.strtree import STRtree

from chicago_tiles import digest,boundary_geometry
from http_zip_range import RangeReader
from metric_frame import validate_frame

ROOT=Path(__file__).resolve().parents[1]
METADATA=ROOT/'runs/water-tower-reference/cook_TileIndex_metadata.zip'
SOURCE_PAGE='https://clearinghouse.isgs.illinois.edu/node/1879'
PREFIX='https://clearinghouse.isgs.illinois.edu/distribute/district1/cook/2022/'


def bounded_member(archive,name,limit=4*2**20):
    info=archive.getinfo(name)
    if info.file_size>limit:raise ValueError('Metadata member exceeds budget')
    return archive.read(info)


def survey_index(path):
    if path.stat().st_size>2**20:raise ValueError('Unexpected publisher metadata size')
    raw=path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as outer:
        zipped=bounded_member(outer,'cook_TileIndex_metadata/cook_tile_index.zip')
        xml=bounded_member(outer,'cook_TileIndex_metadata/cook_2022_metadata.xml')
    xml_tree=ET.fromstring(xml)
    interval=[datetime.strptime(xml_tree.findtext('.//'+key,''),'%Y%m%d').date().isoformat()
              for key in ('begdate','enddate')]
    access=xml_tree.findtext('.//accconst','').strip();use=xml_tree.findtext('.//useconst','').strip()
    datum=xml_tree.findtext('.//altdatum','').strip();units=xml_tree.findtext('.//altunits','').strip()
    if interval!=['2022-04-05','2022-06-29'] or access!='No restrictions apply to these data.' or use!='None.':
        raise ValueError('Publisher capture/rights metadata changed; review before reusing this adapter')
    if datum!='North American Vertical Datum of 1988 Geoid2018' or units!='feet':
        raise ValueError('Publisher vertical reference changed')
    with zipfile.ZipFile(io.BytesIO(zipped)) as inner:
        base='tile_index/CookLiDARtile.'
        wkt=bounded_member(inner,base+'prj').decode()
        crs=CRS.from_wkt(wkt)
        if crs.to_epsg()!=6455:raise ValueError('Publisher index CRS changed; review required')
        reader=shapefile.Reader(**{kind:io.BytesIO(bounded_member(inner,base+kind)) for kind in ('shp','shx','dbf')})
        if not 1<=len(reader)<=10000:raise ValueError('Survey index record budget exceeded')
        rows=[];seen=set()
        for record in reader.iterShapeRecords():
            name=record.record.as_dict()['Tile_Name'];geometry=shape(record.shape.__geo_interface__)
            if not re.fullmatch(r'[0-9]{8}',name) or name in seen:raise ValueError('Invalid or duplicate survey tile identifier')
            if geometry.geom_type!='Polygon' or not geometry.is_valid or geometry.is_empty:
                raise ValueError('Invalid survey tile polygon')
            seen.add(name)
            rows.append({'id':name,'native_geometry':geometry})
    return rows,{'metadata_sha256':hashlib.sha256(raw).hexdigest(),'xml_sha256':hashlib.sha256(xml).hexdigest(),
        'index_zip_sha256':hashlib.sha256(zipped).hexdigest(),'index_crs_wkt':wkt,'native_crs':'EPSG:6455',
        'capture_interval':interval,'source_page':SOURCE_PAGE,
        'access_constraints':access,'use_constraints':use,'vertical_datum':datum,
        'vertical_source_unit_label':units,'vertical_unit_interpretation':'US survey feet per paired State Plane source; convert by 1200/3937'}


class RecordingReader(RangeReader):
    def __init__(self,url,output):
        self.output=output;self.assets=[]
        super().__init__(url,budget=4*2**20)

    def read(self,size=-1):
        start=self.tell();data=super().read(size)
        if data:
            name=f'range-{len(self.assets):02}.bin'
            (self.output/name).write_bytes(data)
            self.assets.append({'file':name,'start':start,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        return data


def archive_index(part,root):
    if part not in range(1,6):raise ValueError('Only the five published Cook archives are in scope')
    output=root/f'cook-las{part}';receipt=output/'index.json'
    if output.exists():
        record=json.loads(receipt.read_text())
        for asset in record['ranges']:
            if Path(asset['file']).name!=asset['file']:raise ValueError('Cached metadata path escaped source')
            raw=(output/asset['file']).read_bytes()
            if len(raw)!=asset['bytes'] or hashlib.sha256(raw).hexdigest()!=asset['sha256']:
                raise ValueError('Cached ZIP directory range changed')
        if record['index_sha256']!=digest(record['members']):raise ValueError('Cached member index changed')
        return record
    output.mkdir()
    remote=RecordingReader(PREFIX+f'cook-las{part}.zip',output)
    members=[]
    with zipfile.ZipFile(remote) as archive:
        if len(archive.infolist())>20000:raise ValueError('Archive entry budget exceeded')
        for info in archive.infolist():
            if info.is_dir():continue
            path=PurePosixPath(info.filename)
            if path.suffix.lower()!='.las':continue
            if path.is_absolute() or '..' in path.parts or not re.fullmatch(r'[0-9]{8}',path.stem):
                raise ValueError('Unexpected LAS archive path')
            members.append({'id':path.stem,'member':info.filename,'url':remote.url,'archive_etag':remote.etag,
                'compressed_bytes':info.compress_size,'uncompressed_bytes':info.file_size,
                'crc32':f'{info.CRC:08x}','header_offset':info.header_offset,'compression':info.compress_type,
                'source_locally_acquired':False})
    if not members:raise ValueError('No source LAS files indexed')
    members.sort(key=lambda row:row['member'])
    record={'url':remote.url,'etag':remote.etag,'archive_bytes':remote.length,
        'metadata_bytes_transferred':remote.transferred,'retrieved_utc':datetime.now(timezone.utc).isoformat(),
        'ranges':remote.assets,'members':members,'index_sha256':digest(members),'point_data_downloaded':False}
    receipt.write_text(json.dumps(record,indent=2));return record


def attach_members(survey,archives):
    lookup={}
    for archive in archives:
        for member in archive['members']:
            lookup.setdefault(member['id'],[]).append(member)
    result=[]
    for tile in survey:
        choices=lookup.get(tile['id'],[])
        if not choices:
            result.append(dict(tile,asset=None));continue
        if len({(m['crc32'],m['uncompressed_bytes']) for m in choices})!=1:
            raise ValueError('Conflicting original versions for one survey tile')
        result.append(dict(tile,asset=min(choices,key=lambda m:(m['url'],m['member']))))
    return result


def tile_sources(plan,survey):
    validate_frame(plan['frame'])
    projection=Transformer.from_crs(6455,plan['frame']['crs'],always_xy=True)
    # Densify survey edges before projection; native corners alone are not curves.
    geometries=[transform(projection.transform,t['native_geometry'].segmentize(100)) for t in survey]
    tree=STRtree(geometries);jobs=[];used=set()
    for tile in plan['tiles']:
        core=box(tile['west'],tile['north']-tile['size'],tile['west']+tile['size'],tile['north'])
        halo=box(*tile['source_bounds'])
        indices=sorted(int(i) for i in tree.query(halo,predicate='intersects') if geometries[i].intersection(halo).area>0)
        names=sorted(survey[i]['id'] for i in indices);used.update(indices)
        covered=unary_union([geometries[i] for i in indices])
        available=unary_union([geometries[i] for i in indices if survey[i]['asset'] is not None])
        jobs.append({'tile':tile['id'],'source_tiles':names,
            'missing_archive_members':[survey[i]['id'] for i in indices if survey[i]['asset'] is None],
            'core_indexed_fraction':covered.intersection(core).area/core.area,
            'halo_indexed_fraction':covered.intersection(halo).area/halo.area,
            'core_downloadable_fraction':available.intersection(core).area/core.area,
            'original_points_acquired':False,'facade_appearance_acquired':False})
    assets=[dict(survey[i]['asset'],native_geometry=mapping(survey[i]['native_geometry']))
        for i in sorted(used,key=lambda i:survey[i]['id']) if survey[i]['asset'] is not None]
    return {'world_plan_sha256':digest(plan),'jobs':jobs,'assets':assets,
        'unique_source_tiles':len(assets),'planned_world_tiles':len(jobs),
        'compressed_source_bytes':sum(a['compressed_bytes'] for a in assets),
        'uncompressed_source_bytes':sum(a['uncompressed_bytes'] for a in assets),
        'largest_uncompressed_member_bytes':max((a['uncompressed_bytes'] for a in assets),default=0),
        'core_tiles_below_99_999_percent_indexed':sum(j['core_indexed_fraction']<.99999 for j in jobs),
        'minimum_core_indexed_fraction':min(j['core_indexed_fraction'] for j in jobs),
        'missing_relevant_archive_members':sorted(survey[i]['id'] for i in used if survey[i]['asset'] is None),
        'coverage_role':'Publisher tile polygons only; not point completeness or physical surface visibility',
        'source_data_downloaded':False,'llm_used':False,'playable_city':False}


def municipal_coverage(plan,document,survey):
    if digest(document)!=plan['boundary_geometry_sha256']:raise ValueError('Municipal boundary changed')
    city=transform(Transformer.from_crs(4326,plan['frame']['crs'],always_xy=True).transform,boundary_geometry(document))
    project=Transformer.from_crs(6455,plan['frame']['crs'],always_xy=True)
    indexed=[];available=[]
    for tile in survey:
        geometry=transform(project.transform,tile['native_geometry'].segmentize(100))
        if not geometry.intersects(city):continue
        part=geometry.intersection(city);indexed.append(part)
        if tile['asset'] is not None:available.append(part)
    area=city.area;index_area=unary_union(indexed).area;available_area=unary_union(available).area
    return {'city_area_m2':area,'city_indexed_fraction':index_area/area,
        'city_archive_available_fraction':available_area/area,'city_unindexed_area_m2':max(0,area-index_area),
        'city_indexed_but_missing_archive_area_m2':max(0,index_area-available_area),
        'scope':'Municipal polygon only; availability is not acquired point coverage or accuracy'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--archive-cache',type=Path,help='Reuse checksum-verified ZIP directories without fetching them again')
    parser.add_argument('--boundary',type=Path,default=ROOT/'runs/chicago-adaptation-boundary/boundary.geojson')
    args=parser.parse_args()
    if shutil.disk_usage(args.output.parent).free<21*2**30:raise ValueError('Preserve internal free space')
    args.output.mkdir(exist_ok=True)
    if (args.output/'city-sources.json').exists():raise FileExistsError('Completed index already exists; preserve it')
    survey,provenance=survey_index(METADATA);archives=[];network_bytes=0
    cache=args.archive_cache or args.output
    for part in range(1,6):
        existed=(cache/f'cook-las{part}'/'index.json').is_file()
        indexed=archive_index(part,cache);archives.append(indexed)
        if not existed:network_bytes+=indexed['metadata_bytes_transferred']
        print(f"Cook archive {part}: {len(indexed['members'])} original LAS files indexed; no point payloads downloaded",flush=True)
    plan=json.loads(args.plan.read_text());survey=attach_members(survey,archives)
    report=tile_sources(plan,survey)
    report['municipal_coverage']=municipal_coverage(plan,json.loads(args.boundary.read_text()),survey)
    report['publisher']=provenance
    report['original_index_transfer_bytes']=sum(a['metadata_bytes_transferred'] for a in archives)
    report['network_bytes_this_run']=network_bytes
    (args.output/'city-sources.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('jobs','assets','publisher')},indent=2))


if __name__=='__main__':main()
