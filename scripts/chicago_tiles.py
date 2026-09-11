"""Bounded Chicago boundary acquisition and restart-safe staged tile journal.

Plans the municipal polygon, not a rectangular claim of completed city coverage.
No world generation or photo registration is implied by a queued work item.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import time
import urllib.parse
import urllib.request
import uuid

from pyproj import Transformer, Proj
from shapely.geometry import shape, box
from shapely.ops import transform, unary_union
from metric_frame import validate_frame

ROOT=Path(__file__).resolve().parents[1]
BOUNDARY='https://gisapps.cityofchicago.org/arcgis/rest/services/CachedMaps/AerialCache/MapServer/0'
STAGES=('sources','geometry','appearance','game_verify')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def fetch_boundary(output):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    if shutil.disk_usage(output.parent).free<21*2**30:raise ValueError('Preserve internal free-space reserve')
    query={'where':'1=1','outFields':'OBJECTID,NAME','outSR':4326,'f':'geojson',
           'returnGeometry':'true','resultRecordCount':10}
    url=BOUNDARY+'/query?'+urllib.parse.urlencode(query)
    request=urllib.request.Request(url,headers={'User-Agent':'Earthcraft/0.1 local geographic reconstruction'})
    with urllib.request.urlopen(request,timeout=30) as response:raw=response.read(4*2**20+1)
    if len(raw)>4*2**20:raise ValueError('Boundary exceeds 4 MiB acquisition cap')
    document=json.loads(raw)
    if document.get('type')!='FeatureCollection' or not 1<=len(document['features'])<=10:
        raise ValueError('Expected bounded municipal GeoJSON')
    if document.get('exceededTransferLimit'):raise ValueError('Truncated boundary')
    boundary_geometry(document)
    output.mkdir()
    (output/'boundary.geojson').write_bytes(raw)
    (output/'source.json').write_text(json.dumps({'url':url,'provider':'City of Chicago',
        'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
        'retrieved_utc':datetime.now(timezone.utc).isoformat(),'capture_date':None,
        'rights':'City terms of use; local planning only, no redistribution',
        'rights_url':'https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html',
        'rights_review':'Official boundary for private local planning; distribution terms not cleared'},indent=2))


def boundary_geometry(document):
    objects=[shape(f['geometry']) for f in document['features']]
    if not objects or any(g.geom_type not in ('Polygon','MultiPolygon') or not g.is_valid or g.is_empty for g in objects):
        raise ValueError('Valid nonempty polygonal boundary required')
    return unary_union(objects)


def tile_plan(document, frame, tile_size=256, halo=32):
    validate_frame(frame)
    if type(tile_size) is not int or not 16<=tile_size<=512 or tile_size%16:
        raise ValueError('Tile size must be 16..512 m, chunk aligned')
    if type(halo) is not int or not 0<=halo<=128:raise ValueError('Bounded context halo required')
    geography=boundary_geometry(document)
    project=Transformer.from_crs(4326,frame['crs'],always_xy=True)
    city=transform(project.transform,geography)
    if not city.is_valid or not 0<city.area<=1e9:raise ValueError('City planning extent exceeds 1000 square km')
    left,bottom,right,top=city.bounds
    origin_x,origin_z=frame['west'],frame['north']
    x0=math.floor((left-origin_x)/tile_size);x1=math.ceil((right-origin_x)/tile_size)
    z0=math.floor((origin_z-top)/tile_size);z1=math.ceil((origin_z-bottom)/tile_size)
    if (x1-x0)*(z1-z0)>100_000:raise ValueError('Tile planning budget exceeded')
    rows=[]
    for tz in range(z0,z1):
        for tx in range(x0,x1):
            west=origin_x+tx*tile_size;north=origin_z-tz*tile_size
            extent=box(west,north-tile_size,west+tile_size,north)
            area=city.intersection(extent).area
            if area<=0:continue
            rows.append({'id':f'{tx}_{tz}','tx':tx,'tz':tz,'west':west,'north':north,
                'size':tile_size,'city_area_m2':area,
                'source_bounds':[west-halo,north-tile_size-halo,west+tile_size+halo,north+halo],
                'world_offset_xz':[tx*tile_size,tz*tile_size],
                'priority':(tx+.5)**2+(tz+.5)**2})
    rows.sort(key=lambda r:(r['priority'],r['id']))
    # Sample point scale at city-boundary coordinates; not a full survey guarantee.
    coordinates=[]
    for polygon in geography.geoms if geography.geom_type=='MultiPolygon' else [geography]:
        coordinates.extend(list(polygon.exterior.coords)[::max(1,len(polygon.exterior.coords)//64)])
    projection=Proj(frame['crs'])
    scales=[projection.get_factors(lon,lat).meridional_scale for lon,lat in coordinates]
    return {'schema_version':1,'scope':'Chicago municipal polygon; full intersecting tiles retain boundary context',
        'frame':frame,'tile_size_m':tile_size,'source_halo_m':halo,'tiles':rows,
        'city_area_m2':city.area,'tiled_area_m2':len(rows)*tile_size**2,
        'scale_samples':len(scales),'max_sampled_local_scale_error_ppm':max(abs(s-1)*1e6 for s in scales),
        'boundary_geometry_sha256':digest(document),'llm_used':False,
        'geometry_tiles_generated':0,'appearance_tiles_verified':0,'playable_city':False}


class Journal:
    """Local SQLite state; leases fence stale workers after crash/restart."""
    def __init__(self,path,plan,source_order=None):
        self.db=sqlite3.connect(path,timeout=20,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
              tile TEXT NOT NULL,stage INTEGER NOT NULL,priority REAL NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending',token TEXT,owner TEXT,expires REAL,
              attempts INTEGER NOT NULL DEFAULT 0,evidence TEXT,evidence_sha256 TEXT,
              source_key TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(tile,stage));
        ''')
        # Older journals predate the locality scheduler.  This additive
        # migration changes only scheduling metadata, never the frozen plan or
        # any stage evidence.
        columns={row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}
        if 'source_key' not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN source_key TEXT NOT NULL DEFAULT ''")
        fingerprint=digest(plan)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            existing=self.db.execute("SELECT value FROM meta WHERE key='plan'").fetchone()
            if existing and existing[0]!=fingerprint:raise ValueError('Plan changed; retain this journal and start a new revision')
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('plan',?)",(fingerprint,))
            self.db.executemany('INSERT OR IGNORE INTO jobs(tile,stage,priority) VALUES (?,?,?)',
                [(tile['id'],stage,tile['priority']) for tile in plan['tiles'] for stage in range(len(STAGES))])
            if source_order:
                self.db.executemany('UPDATE jobs SET source_key=? WHERE tile=? AND stage=0',
                    [(str(source_order[tile['id']]),tile['id']) for tile in plan['tiles']
                     if tile['id'] in source_order])
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK');self.db.close();raise

    def close(self):self.db.close()

    def claim(self,stage,owner,now=None,lease_seconds=300,source_locality_after=None):
        index=STAGES.index(stage);now=time.time() if now is None else now
        if not owner or not math.isfinite(now) or not 1<=lease_seconds<=3600:raise ValueError('Invalid worker lease')
        if source_locality_after is not None and (not math.isfinite(source_locality_after) or source_locality_after<0):
            raise ValueError('Invalid source locality threshold')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if stage=='sources' and source_locality_after is not None:
                # Keep the already-near playable frontier first.  Once that
                # bounded radius is complete, process tiles by their lowest
                # shared LAS member so a decoded member is reused for its
                # neighboring tiles.  Every tie-break is explicit, making the
                # faster schedule reproducible without changing tile output.
                ordering='''
                    ORDER BY CASE WHEN j.priority<=? THEN 0 ELSE 1 END,
                             CASE WHEN j.priority<=? THEN j.priority ELSE j.source_key END,
                             CASE WHEN j.priority<=? THEN j.tile ELSE printf('%020.6f:%s',j.priority,j.tile) END'''
                params=(index,now,source_locality_after,source_locality_after,source_locality_after)
            else:
                ordering='ORDER BY priority,tile'; params=(index,now)
            row=self.db.execute('''SELECT * FROM jobs j WHERE stage=? AND
              (state='pending' OR (state='running' AND expires<=?)) AND
              NOT EXISTS (SELECT 1 FROM jobs p WHERE p.tile=j.tile AND p.stage<j.stage AND p.state!='complete')
              '''+ordering+''' LIMIT 1''',params).fetchone()
            result=None
            if row:
                for prior in self.db.execute("SELECT evidence,evidence_sha256 FROM jobs WHERE tile=? AND stage<?",(row['tile'],index)):
                    path=Path(prior['evidence'])
                    if path.stat().st_size>2**20 or hashlib.sha256(path.read_bytes()).hexdigest()!=prior['evidence_sha256']:
                        raise ValueError('Prior stage evidence changed; do not advance tile')
                token=uuid.uuid4().hex
                self.db.execute("UPDATE jobs SET state='running',token=?,owner=?,expires=?,attempts=attempts+1 WHERE tile=? AND stage=?",
                    (token,owner,now+lease_seconds,row['tile'],index))
                result={'tile':row['tile'],'stage':stage,'token':token}
            self.db.execute('COMMIT');return result
        except Exception:self.db.execute('ROLLBACK');raise

    def finish(self,job,receipt,now=None):
        now=time.time() if now is None else now;receipt=Path(receipt)
        if receipt.stat().st_size>2**20:raise ValueError('Receipt exceeds journal budget')
        raw=receipt.read_bytes();evidence=json.loads(raw)
        if evidence.get('tile')!=job['tile'] or evidence.get('stage')!=job['stage'] or evidence.get('result')!='pass':
            raise ValueError('Matching successful stage receipt required')
        changed=self.db.execute("""UPDATE jobs SET state='complete',evidence=?,evidence_sha256=?,token=NULL,expires=NULL
            WHERE tile=? AND stage=? AND token=? AND state='running' AND expires>?""",
            (str(receipt.resolve()),hashlib.sha256(raw).hexdigest(),job['tile'],STAGES.index(job['stage']),job['token'],now)).rowcount
        if changed!=1:raise ValueError('Stale or expired worker cannot publish completion')

    def summary(self):
        return [{**dict(row),'stage':STAGES[row['stage']]} for row in self.db.execute(
            'SELECT stage,state,count(*) AS count FROM jobs GROUP BY stage,state ORDER BY stage,state')]

    def fail(self,job,receipt):
        """Retain a concrete failure and let other tiles advance; no fake success."""
        receipt=Path(receipt);raw=receipt.read_bytes();evidence=json.loads(raw)
        if len(raw)>2**20 or evidence.get('tile')!=job['tile'] or evidence.get('stage')!=job['stage'] or evidence.get('result')!='failed':
            raise ValueError('Matching failure receipt required')
        changed=self.db.execute("""UPDATE jobs SET state='failed',evidence=?,evidence_sha256=?,token=NULL,expires=NULL
            WHERE tile=? AND stage=? AND token=? AND state='running'""",
            (str(receipt.resolve()),hashlib.sha256(raw).hexdigest(),job['tile'],STAGES.index(job['stage']),job['token'])).rowcount
        if changed!=1:raise ValueError('Stale worker cannot fail another attempt')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boundary-source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--fetch-boundary',action='store_true')
    args=parser.parse_args()
    if args.fetch_boundary:fetch_boundary(args.boundary_source)
    raw=(args.boundary_source/'boundary.geojson').read_bytes()
    provenance=json.loads((args.boundary_source/'source.json').read_text())
    if hashlib.sha256(raw).hexdigest()!=provenance['sha256']:raise ValueError('Changed frozen city boundary')
    baseline=json.loads((ROOT/'worlds/Earthcraft-Water-Tower-Explorer-v4/earthcraft.json').read_text())
    frame={'crs':baseline['source']['crs'],'west':baseline['source']['west'],
           'north':baseline['source']['north'],'vertical_offset_m':baseline['vertical_offset_m'],
           'dimension_min_y':-64,'dimension_height':1024,
           'vertical_reference':'Inherited Water Tower metric frame; datum agreement must be verified for every new source'}
    plan=tile_plan(json.loads(raw),frame);plan['boundary_source']=provenance
    if args.output.exists():
        if json.loads((args.output/'plan.json').read_text())!=plan:raise ValueError('Existing plan differs; preserve it')
    else:
        args.output.mkdir(parents=True)
        (args.output/'plan.json').write_text(json.dumps(plan,indent=2))
        (args.output/'frame.json').write_text(json.dumps(frame,indent=2))
    journal=Journal(args.output/'jobs.sqlite',plan)
    print(json.dumps({'tiles':len(plan['tiles']),'city_area_km2':plan['city_area_m2']/1e6,
        'tiled_area_km2':plan['tiled_area_m2']/1e6,'stage_counts':journal.summary(),
        'maximum_sampled_scale_error_ppm':plan['max_sampled_local_scale_error_ppm'],
        'build_running':False,'note':'Persistent plan only; no source/painting worker dispatched'},indent=2))
    journal.close()


if __name__=='__main__':main()
