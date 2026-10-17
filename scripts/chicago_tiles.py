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


SHARD_BAND_TILES=4  # a shard owns whole 4-tile (2 km) east-west bands, so source strips stay on one box


def parse_shard(value):
    """'i/N' -> (i, N) with 0 <= i < N; None or '' -> None (all tiles)."""
    if value in (None,'','0/1'):return None
    try:
        index,count=(int(part) for part in str(value).split('/'))
    except ValueError:
        raise ValueError(f'Shard must look like i/N, not {value!r}') from None
    if not 0<=index<count<=1024:raise ValueError(f'Shard {value!r} needs 0 <= i < N <= 1024')
    return None if count==1 else (index,count)


def tile_shard(tile,count):
    """Deterministic shard of a plan tile id 'tx_tz': its 4-row band of the plan grid, modulo count."""
    return (int(str(tile).rsplit('_',1)[1])//SHARD_BAND_TILES)%int(count)


def is_local_evidence(evidence):
    """Local receipts are absolute paths; dataset evidence is a repository path (tiles/v2/...)."""
    return Path(str(evidence)).is_absolute()


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


def tile_plan(document, frame, tile_size=256, halo=32, *, max_area_m2=1e9,
              max_candidate_tiles=100_000):
    validate_frame(frame)
    if type(tile_size) is not int or not 16<=tile_size<=512 or tile_size%16:
        raise ValueError('Tile size must be 16..512 m, chunk aligned')
    if type(halo) is not int or not 0<=halo<=128:raise ValueError('Bounded context halo required')
    geography=boundary_geometry(document)
    project=Transformer.from_crs(4326,frame['crs'],always_xy=True)
    city=transform(project.transform,geography)
    if not 0 < max_area_m2 <= 4e10 or not 1 <= max_candidate_tiles <= 1_000_000:
        raise ValueError('Regional planning limits exceed the bounded regional budget')
    if not city.is_valid or not 0<city.area<=max_area_m2:
        raise ValueError('City planning extent exceeds configured area budget')
    left,bottom,right,top=city.bounds
    origin_x,origin_z=frame['west'],frame['north']
    x0=math.floor((left-origin_x)/tile_size);x1=math.ceil((right-origin_x)/tile_size)
    z0=math.floor((origin_z-top)/tile_size);z1=math.ceil((origin_z-bottom)/tile_size)
    if (x1-x0)*(z1-z0)>max_candidate_tiles:raise ValueError('Tile planning budget exceeded')
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
        self.db.create_function('tile_shard',2,tile_shard,deterministic=True)
        # Multiple tile workers can open the journal at the same instant.  WAL
        # mode changes the database header, so SQLite may briefly report a
        # startup lock even though the connection timeout is set.  Retry that
        # one-time mode negotiation explicitly; later lease transactions keep
        # using SQLite's normal busy timeout.
        self.db.execute('PRAGMA busy_timeout=20000')
        for attempt in range(8):
            try:
                self.db.execute('PRAGMA journal_mode=WAL')
                break
            except sqlite3.OperationalError as error:
                if 'locked' not in str(error).lower() or attempt == 7:
                    raise
                time.sleep(0.05 * (attempt + 1))
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
              tile TEXT NOT NULL,stage INTEGER NOT NULL,priority REAL NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending',token TEXT,owner TEXT,expires REAL,
              attempts INTEGER NOT NULL DEFAULT 0,evidence TEXT,evidence_sha256 TEXT,
              source_key TEXT NOT NULL DEFAULT '',route_rank INTEGER,route_label TEXT,
              not_before REAL NOT NULL DEFAULT 0,deferrals INTEGER NOT NULL DEFAULT 0,
              defer_reason TEXT,
              PRIMARY KEY(tile,stage));
        ''')
        # Older journals predate the locality scheduler.  This additive
        # migration changes only scheduling metadata, never the frozen plan or
        # any stage evidence.
        columns={row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}
        if 'source_key' not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN source_key TEXT NOT NULL DEFAULT ''")
        if 'route_rank' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN route_rank INTEGER')
        if 'route_label' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN route_label TEXT')
        if 'not_before' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN not_before REAL NOT NULL DEFAULT 0')
        if 'deferrals' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN deferrals INTEGER NOT NULL DEFAULT 0')
        if 'defer_reason' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN defer_reason TEXT')
        # One-pass regional tiles record dataset evidence (tiles/v2/... path,
        # SHA-256 and the verified Hub commit) instead of a local receipt path.
        if 'evidence_commit' not in columns:
            self.db.execute('ALTER TABLE jobs ADD COLUMN evidence_commit TEXT')
        fingerprint=digest(plan)
        self._begin_immediate()
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
            # Regional queues have hundreds of thousands of stage rows. Match
            # the normal deterministic dispatch order without sorting the
            # entire queue for every claim. Existing city journals migrate
            # additively and keep their receipts and priorities unchanged.
            self.db.execute('''CREATE INDEX IF NOT EXISTS jobs_dispatch_order ON jobs(
                stage, (CASE WHEN route_rank IS NULL THEN 1 ELSE 0 END), route_rank, priority, tile)''')
        except Exception:
            self.db.execute('ROLLBACK');self.db.close();raise

    def close(self):self.db.close()

    def _begin_immediate(self):
        """Absorb short SQLite writer races during parallel worker startup."""
        for attempt in range(40):
            try:
                self.db.execute('BEGIN IMMEDIATE');return
            except sqlite3.OperationalError as error:
                if 'locked' not in str(error).lower() or attempt==39:raise
                time.sleep(min(.05*(attempt+1),.5))

    def claim(self,stage,owner,now=None,lease_seconds=300,source_locality_after=None):
        index=STAGES.index(stage);now=time.time() if now is None else now
        if not owner or not math.isfinite(now) or not 1<=lease_seconds<=3600:raise ValueError('Invalid worker lease')
        if source_locality_after is not None and (not math.isfinite(source_locality_after) or source_locality_after<0):
            raise ValueError('Invalid source locality threshold')
        self._begin_immediate()
        try:
            if stage=='sources' and source_locality_after is not None:
                # Keep the already-near playable frontier first.  Once that
                # bounded radius is complete, process tiles by their lowest
                # shared LAS member so a decoded member is reused for its
                # neighboring tiles.  Every tie-break is explicit, making the
                # faster schedule reproducible without changing tile output.
                ordering='''
                    ORDER BY CASE WHEN j.route_rank IS NULL THEN 1 ELSE 0 END,
                             j.route_rank,
                             CASE WHEN j.priority<=? THEN 0 ELSE 1 END,
                             CASE WHEN j.priority<=? THEN j.priority ELSE j.source_key END,
                             CASE WHEN j.priority<=? THEN j.tile ELSE printf('%020.6f:%s',j.priority,j.tile) END'''
                params=(index,now,now,source_locality_after,source_locality_after,source_locality_after)
            else:
                ordering='ORDER BY CASE WHEN route_rank IS NULL THEN 1 ELSE 0 END, route_rank, priority,tile'; params=(index,now,now)
            row=self.db.execute('''SELECT * FROM jobs j WHERE stage=? AND
              (state='pending' OR (state='running' AND expires<=?)) AND
              COALESCE(not_before,0)<=? AND
              NOT EXISTS (SELECT 1 FROM jobs p WHERE p.tile=j.tile AND p.stage<j.stage AND p.state!='complete')
              '''+ordering+''' LIMIT 1''',params).fetchone()
            result=None
            if row:
                self._check_prior(row['tile'],index)
                token=uuid.uuid4().hex
                self.db.execute("UPDATE jobs SET state='running',token=?,owner=?,expires=?,not_before=0,attempts=attempts+1 WHERE tile=? AND stage=?",
                    (token,owner,now+lease_seconds,row['tile'],index))
                result={'tile':row['tile'],'stage':stage,'token':token}
            self.db.execute('COMMIT');return result
        except Exception:self.db.execute('ROLLBACK');raise

    def claim_exact(self, stage, tile, owner, now=None, lease_seconds=300):
        """Claim one eligible named job without disturbing the global queue.

        Appearance workers use this to advance an auditable corridor only when
        its geometry is already complete and compatible with their evidence
        stage.  It retains the same receipt and lease fences as ``claim``.
        """
        index=STAGES.index(stage);now=time.time() if now is None else now;tile=str(tile)
        if not owner or not math.isfinite(now) or not 1<=lease_seconds<=3600:
            raise ValueError('Invalid worker lease')
        self._begin_immediate()
        try:
            row=self.db.execute('''SELECT * FROM jobs j WHERE tile=? AND stage=? AND
              (state='pending' OR (state='running' AND expires<=?)) AND
              COALESCE(not_before,0)<=? AND
              NOT EXISTS (SELECT 1 FROM jobs p WHERE p.tile=j.tile AND p.stage<j.stage AND p.state!='complete')''',
                (tile,index,now,now)).fetchone()
            result=None
            if row:
                self._check_prior(tile,index)
                token=uuid.uuid4().hex
                self.db.execute('UPDATE jobs SET state=\'running\',token=?,owner=?,expires=?,not_before=0,attempts=attempts+1 WHERE tile=? AND stage=?',
                    (token,owner,now+lease_seconds,tile,index))
                result={'tile':tile,'stage':stage,'token':token}
            self.db.execute('COMMIT');return result
        except Exception:self.db.execute('ROLLBACK');raise

    def _check_prior(self,tile,index,legacy_ok=False):
        """Re-hash earlier local receipts; dataset evidence carries its verified SHA-256.

        Returns {stage: kind} with kind 'local', 'dataset' or 'legacy' (a local
        receipt whose file no longer exists, accepted only when legacy_ok).
        """
        kinds={}
        for prior in self.db.execute('SELECT stage,evidence,evidence_sha256 FROM jobs WHERE tile=? AND stage<?',(tile,index)):
            evidence=prior['evidence'];name=STAGES[prior['stage']]
            if evidence is None or not is_local_evidence(evidence):
                kinds[name]='dataset';continue
            path=Path(evidence)
            if legacy_ok and not path.exists():
                kinds[name]='legacy';continue
            if path.stat().st_size>2**20 or hashlib.sha256(path.read_bytes()).hexdigest()!=prior['evidence_sha256']:
                raise ValueError('Prior stage evidence changed; do not advance tile')
            kinds[name]='local'
        return kinds

    def claim_tile(self,owner,lane='base',now=None,lease_seconds=3600,tiles=None,shard=None):
        """Lease every unfinished generation stage of one tile under one token.

        One tile is one unit of work. Lane 'base' takes a tile whose sources are
        unfinished and leases sources, geometry and appearance together. Lane
        'scans' takes a tile whose sources and geometry are complete and leases
        appearance alone. Failed or complete stages are never re-leased here;
        game_verify is never claimed. Earlier receipts are checked as in claim();
        a lost legacy receipt (written on the retired external drive) is accepted
        because the pass regenerates every input it needs.
        """
        now=time.time() if now is None else now
        if not owner or not math.isfinite(now) or not 1<=lease_seconds<=3600:raise ValueError('Invalid worker lease')
        if lane not in ('base','scans'):raise ValueError('Unsupported tile lane')
        anchor=0 if lane=='base' else 2
        claimable="(state='pending' OR (state='running' AND expires<=?)) AND COALESCE(not_before,0)<=?"
        query=("SELECT * FROM jobs j WHERE j.stage=? AND (j.state='pending' OR (j.state='running' AND j.expires<=?)) "
               "AND COALESCE(j.not_before,0)<=? "
               "AND NOT EXISTS (SELECT 1 FROM jobs p WHERE p.tile=j.tile AND p.stage<j.stage AND p.state!='complete')")
        params=[anchor,now,now]
        if tiles is not None:
            tiles=tuple(dict.fromkeys(str(tile) for tile in tiles))
            if not tiles:return None
            query+=' AND j.tile IN ('+','.join('?' for _ in tiles)+')';params+=list(tiles)
        if shard is not None:  # (index, count): this machine's deterministic share of the plan
            query+=' AND tile_shard(j.tile,?)=?';params+=[int(shard[1]),int(shard[0])]
        query+=' ORDER BY CASE WHEN j.route_rank IS NULL THEN 1 ELSE 0 END, j.route_rank, j.priority, j.tile LIMIT 1'
        self._begin_immediate()
        try:
            row=self.db.execute(query,params).fetchone();result=None
            if row:
                prior=self._check_prior(row['tile'],anchor,legacy_ok=True)
                later=[] if lane=='scans' else [r['stage'] for r in self.db.execute(
                    'SELECT stage FROM jobs WHERE tile=? AND stage IN (1,2) AND '+claimable+' ORDER BY stage',
                    (row['tile'],now,now))]
                stages=[anchor,*later];token=uuid.uuid4().hex
                for index in stages:
                    self.db.execute("UPDATE jobs SET state='running',token=?,owner=?,expires=?,not_before=0,attempts=attempts+1 WHERE tile=? AND stage=?",
                        (token,owner,now+lease_seconds,row['tile'],index))
                result={'tile':row['tile'],'lane':lane,'token':token,'stages':[STAGES[i] for i in stages],'prior':prior}
            self.db.execute('COMMIT');return result
        except Exception:self.db.execute('ROLLBACK');raise

    def finish_remote(self,job,stage,path_in_repo,raw,commit,now=None):
        """Complete one leased stage with a receipt that is verified on the Hub.

        The journal keeps the dataset path, the receipt's SHA-256 and the commit
        that carries it, never a local scratch path.
        """
        now=time.time() if now is None else now
        if is_local_evidence(path_in_repo) or not str(path_in_repo).startswith('tiles/'):
            raise ValueError('Dataset evidence path required')
        if len(raw)>2**20:raise ValueError('Receipt exceeds journal budget')
        evidence=json.loads(raw)
        if evidence.get('tile')!=job['tile'] or evidence.get('stage')!=stage or evidence.get('result')!='pass':
            raise ValueError('Matching successful stage receipt required')
        changed=self.db.execute("""UPDATE jobs SET state='complete',evidence=?,evidence_sha256=?,evidence_commit=?,
            token=NULL,expires=NULL WHERE tile=? AND stage=? AND token=? AND state='running' AND expires>?""",
            (path_in_repo,hashlib.sha256(raw).hexdigest(),commit,job['tile'],STAGES.index(stage),job['token'],now)).rowcount
        if changed!=1:raise ValueError('Stale or expired worker cannot publish completion')

    def release_tile(self,job,delay_seconds,reason,stages=None):
        """Return a tile's leased stages to the queue after a capacity pause (not a failure)."""
        for stage in stages or job['stages']:
            self.defer({'tile':job['tile'],'stage':stage,'token':job['token']},delay_seconds,reason)

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

    def has_claimable_sources(self,now=None):
        """Read whether source work is eligible without creating a lease or attempt."""
        now=time.time() if now is None else now
        if not math.isfinite(now):raise ValueError('Invalid source eligibility time')
        return self.db.execute("""SELECT 1 FROM jobs WHERE stage=0 AND
            (state='pending' OR (state='running' AND expires<=?)) AND
            COALESCE(not_before,0)<=? LIMIT 1""",(now,now)).fetchone() is not None

    def fail(self,job,receipt):
        """Retain a concrete failure and let other tiles advance; no fake success."""
        receipt=Path(receipt);raw=receipt.read_bytes();evidence=json.loads(raw)
        if len(raw)>2**20 or evidence.get('tile')!=job['tile'] or evidence.get('stage')!=job['stage'] or evidence.get('result')!='failed':
            raise ValueError('Matching failure receipt required')
        changed=self.db.execute("""UPDATE jobs SET state='failed',evidence=?,evidence_sha256=?,token=NULL,expires=NULL
            WHERE tile=? AND stage=? AND token=? AND state='running'""",
            (str(receipt.resolve()),hashlib.sha256(raw).hexdigest(),job['tile'],STAGES.index(job['stage']),job['token'])).rowcount
        if changed!=1:raise ValueError('Stale worker cannot fail another attempt')

    def defer(self,job,delay_seconds,reason,now=None):
        """Release a busy dependency without consuming a worker or recording failure."""
        now=time.time() if now is None else now
        if (not math.isfinite(now) or not math.isfinite(delay_seconds) or
                not 0<delay_seconds<=300 or not reason or len(reason)>240):
            raise ValueError('Invalid bounded work deferral')
        changed=self.db.execute("""UPDATE jobs SET state='pending',token=NULL,owner=NULL,expires=NULL,
            not_before=?,deferrals=deferrals+1,defer_reason=?
            WHERE tile=? AND stage=? AND token=? AND state='running'""",
            (now+delay_seconds,reason,job['tile'],STAGES.index(job['stage']),job['token'])).rowcount
        if changed!=1:raise ValueError('Stale worker cannot defer another attempt')

    def requeue_failed(self, stages=('sources','geometry'), tiles=None):
        """Return fenced failures to pending for an explicit retry run.

        Failure receipts remain on disk as audit evidence; a later successful
        receipt replaces the journal pointer only after the normal checksum and
        dependency gates pass.  No running or complete job is touched.
        """
        indexes=tuple(STAGES.index(stage) for stage in stages)
        if not indexes or any(stage not in STAGES for stage in stages):
            raise ValueError('Unknown retry stage')
        marks=','.join('?' for _ in indexes)
        tile_clause=''
        params=list(indexes)
        if tiles is not None:
            tiles=tuple(str(tile) for tile in tiles)
            if not tiles:return 0
            tile_marks=','.join('?' for _ in tiles)
            tile_clause=f' AND tile IN ({tile_marks})'
            params.extend(tiles)
        changed=self.db.execute(
            f"UPDATE jobs SET state='pending',token=NULL,owner=NULL,expires=NULL,not_before=0 "
            f"WHERE state='failed' AND stage IN ({marks}){tile_clause}", params).rowcount
        return changed

    def requeue_owner_leases(self, owner):
        """Recover leases from an earlier process with the same locked owner ID."""
        if not owner:raise ValueError('Worker owner is required')
        return self.db.execute(
            "UPDATE jobs SET state='pending',token=NULL,owner=NULL,expires=NULL,not_before=0 "
            "WHERE state='running' AND owner=?",(owner,)).rowcount

    def prioritize_tiles(self,tiles,label,stages=('sources','geometry'),replace=True):
        """Route pending work through a named, auditable geographic corridor.

        This changes only the local dispatch order.  The immutable plan,
        source membership, completed receipts and running leases are retained.
        A replacement clears an earlier route only for work that has not
        started, so a graceful worker stop can safely pick up the new route.
        """
        tiles=tuple(dict.fromkeys(str(tile) for tile in tiles))
        indexes=tuple(STAGES.index(stage) for stage in stages)
        if not tiles or not label or any(stage not in STAGES for stage in stages):
            raise ValueError('Nonempty known tiles, label and stages required')
        known={row[0] for row in self.db.execute('SELECT DISTINCT tile FROM jobs')}
        unknown=set(tiles)-known
        if unknown:raise ValueError('Unknown route tiles: '+','.join(sorted(unknown)))
        marks=','.join('?' for _ in indexes)
        self._begin_immediate()
        try:
            if replace:
                self.db.execute(f"UPDATE jobs SET route_rank=NULL,route_label=NULL WHERE state='pending' AND stage IN ({marks})",indexes)
            changed=0
            for rank,tile in enumerate(tiles):
                changed+=self.db.execute(
                    f"UPDATE jobs SET route_rank=?,route_label=? WHERE tile=? AND state='pending' AND stage IN ({marks})",
                    (rank,label,tile,*indexes)).rowcount
            self.db.execute('COMMIT')
            return changed
        except Exception:
            self.db.execute('ROLLBACK');raise


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
