"""Execute real source and geometry jobs from the frozen Chicago city plan.

One worker, bounded tile memory, immutable per-tile outputs and successful
read-back receipts. Appearance/game stages are deliberately not certified here.
"""
import argparse
import json
from pathlib import Path
import time
import shutil
import fcntl
import os
import signal
import sys
import urllib.error
from datetime import datetime,timezone
from city_continuous import initialize,append_tile

from chicago_tiles import Journal, digest
from cook_city_cache import acquire, sha, save_json
from city_point_crop import PointCache, crop_sources
from metric_source_crop import crop
from metric_sources import prepare
from metric_world import build
from verify_metric_world import verify
from local_paths import bulk_path,bulk_root
from osm_json_to_kml import building_tag

ROOT=Path(__file__).resolve().parents[1]
TRANSIENT_HTTP={408,425,429,500,502,503,504}
NON_FATAL_SOURCE_ERRORS=('Missing acquired source coverage; no geometry fallback',)


def point_crop_required(source):
    """Require LiDAR only when this tile has mapped building geometry.

    DEM/land-cover/OSM roads are sufficient for a terrain-only tile.  Skipping
    an otherwise unused LAS crop is explicit in the receipt.  The current
    Chicago geometry profile admits Cook County footprints; an OSM footprint
    is eligible only when a future source explicitly declares the
    ``osm-explicit`` profile with measured height tags.
    """
    source = Path(source)
    county = json.loads((source/'cook-buildings-2022.json').read_text())
    if county.get('features'):
        return True, 'Cook County building footprint present'
    meta = json.loads((source/'sources.json').read_text())
    if meta.get('building_source_kind') == 'osm-explicit':
        ways = json.loads((source/'osm-ways.json').read_text())
        if any(building_tag(way.get('tags', {})) and
               way.get('tags', {}).get('height') for way in ways):
            return True, 'OSM explicit-height building profile present'
    return False, 'No Cook County or OSM building geometry in tile'


def retry_transient(operation, label, attempts=3):
    """Retry only explicitly transient HTTP failures with fixed backoff.

    The schedule is deterministic (1 s, 2 s) and permanent source or
    provenance errors still fail immediately; no data is substituted.
    """
    if type(attempts) is not int or attempts < 1:
        raise ValueError('Retry attempts must be positive')
    for attempt in range(attempts):
        try:
            return operation()
        except urllib.error.HTTPError as error:
            if error.code not in TRANSIENT_HTTP or attempt + 1 >= attempts:
                raise
            delay=2**attempt
            print(f'TRANSIENT {label}: HTTP {error.code}; retrying in {delay}s',flush=True)
            time.sleep(delay)


def source_for_tile(tile,frame,destination,parent=None,way_index=None):
    expected={'crs':frame['crs'],**{k:tile[k] for k in ('west','north','size')}}
    manifest=destination/'sources.json'
    if manifest.is_file():
        existing=json.loads(manifest.read_text())
        if any(existing[k]!=v for k,v in expected.items()):raise ValueError('Cached metric grid changed')
        return expected
    if parent:
        original=json.loads((parent/'sources.json').read_text())
        col=tile['west']-original['west'];row=original['north']-tile['north']
        if original['crs']==frame['crs'] and min(col,row)>=0 and max(col+tile['size'],row+tile['size'])<=original['size']:
            crop(parent,destination,col,row,tile['size']);return expected
    prepare(destination,tile['size'],grid=expected,way_index=way_index)
    return expected


def run(plan_dir,catalog_path,output,limit,source_parent=None,bulk=None,release_points=False,assembly=None,way_index=None,
        point_cache_bytes=16*2**30,source_locality_after=200.0,retry_failed=False):
    plan=json.loads((plan_dir/'plan.json').read_text());catalog=json.loads(catalog_path.read_text())
    if catalog['world_plan_sha256']!=digest(plan):raise ValueError('Source catalog does not match city plan')
    tiles={t['id']:t for t in plan['tiles']};jobs={j['tile']:j for j in catalog['jobs']}
    assets={a['id']:a for a in catalog['assets']};cached={}
    output.mkdir(parents=True,exist_ok=True)
    run_lock=(plan_dir/'worker.lock').open('a+')
    fcntl.lockf(run_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    run_lock.seek(0);run_lock.truncate();run_lock.write(str(os.getpid()));run_lock.flush()
    stopping=False
    def stop_after_tile(signum,frame):
        nonlocal stopping
        stopping=True
        print('Finishing current tile before stopping safely',flush=True)
    signal.signal(signal.SIGTERM,stop_after_tile);signal.signal(signal.SIGINT,stop_after_tile)
    source_order={tile: min((str(value) for value in jobs[tile]['source_tiles']), default='~')
                  for tile in jobs}
    journal=Journal(plan_dir/'jobs.sqlite',plan,source_order=source_order)
    if retry_failed:
        retried=journal.requeue_failed(('sources','geometry'))
        print(f'REQUEUED FAILED JOBS: {retried}',flush=True)
    processed=0;failures=0;geometry_failures=0
    point_cache=PointCache(point_cache_bytes)
    if assembly:
        initialize(assembly,ROOT/'worlds/Earthcraft-Chicago-City-Staging-001',plan)
        # Include successful jobs from earlier runs, even on the other volume.
        rows=journal.db.execute('SELECT tile,evidence FROM jobs WHERE stage=1 AND state=\'complete\' ORDER BY priority,tile').fetchall()
        for row in rows:
            world=Path(row['evidence']).parent/'world'
            append_tile(assembly,world,tiles[row['tile']],plan)
    def progress():
        save_json(output/'progress.json',{'world_plan_sha256':digest(plan),'stages':journal.summary(),
            'installed_world_changed':False,'full_chicago_complete':False})
    def geometry_job(job):
        # The source receipt identifies its output volume, so a resumed worker
        # does not lose pending work when later tiles move to bulk storage.
        row=journal.db.execute('SELECT evidence FROM jobs WHERE tile=? AND stage=0',(job['tile'],)).fetchone()
        receipt=json.loads(Path(row['evidence']).read_text())
        root=Path(receipt.get('tile_output',str(output/job['tile'])))
        world=root/'world';started=time.monotonic()
        print(f"GEOMETRY {job['tile']}",flush=True)
        if not world.exists():
            staging=root/'world.building'
            if not staging.exists():
                point_manifest = root/'points/manifest.json'
                point_source = point_manifest.parent if point_manifest.exists() else None
                build(root/'sources',staging,point_source=point_source,world_frame=plan['frame'])
            # A complete staging directory can survive an interrupted read-back.
            # It is promoted only after the same full checks; nothing overwritten.
            checked=verify(staging);staging.rename(world)
        else:
            checked=verify(world)
        path=root/'geometry-receipt.json'
        save_json(path,dict(job,result='pass',checks=checked,seconds=time.monotonic()-started,
            regions={p.name:sha(p) for p in sorted((world/'region').glob('r.*.*.mca'))},
            world_manifest_sha256=sha(world/'earthcraft.json'),
            physical_accuracy_verified=False,appearance_complete=False,installed=False))
        journal.finish(job,path)
        if assembly:append_tile(assembly,world,tiles[job['tile']],plan)
        if release_points and receipt['point_sha256'] is not None:
            temporary=root/'points/points.npz'
            if sha(temporary)!=receipt['point_sha256']:raise ValueError('Derived point file changed; do not release it')
            temporary.unlink()
            save_json(root/'points/retention.json',{'normalized_working_copy_released':True,
                'original_las_observations_preserved':True,'replay':'Recreate this tile from manifest sources and grid',
                'points_sha256':receipt['point_sha256']})
    try:
        while processed<limit and not stopping:
            if shutil.disk_usage(ROOT).free<20*2**30:raise ValueError('Internal free-space reserve reached')
            if release_points and (not bulk_root().exists() or shutil.disk_usage(output).free<101*2**30):
                raise ValueError('Bulk drive unavailable or free-space reserve reached')
            job=journal.claim('geometry','local-chicago-worker',lease_seconds=3600)
            if job:
                try:
                    geometry_job(job)
                    geometry_failures=0
                except Exception as error:
                    # Keep one bad source tile from taking down the whole
                    # resumable city run.  The failed stage is fenced in the
                    # journal with its concrete error; no geometry fallback
                    # or substituted datum is published.
                    prior=journal.db.execute(
                        'SELECT evidence FROM jobs WHERE tile=? AND stage=0',
                        (job['tile'],)).fetchone()
                    if not prior:
                        raise
                    source_receipt=json.loads(Path(prior['evidence']).read_text())
                    tile_root=Path(source_receipt.get('tile_output',str(output/job['tile'])))
                    receipt=tile_root/'geometry-failure.json'
                    save_json(receipt,dict(job,result='failed',
                        error_type=type(error).__name__,error=str(error),
                        fallback_used=False,physical_accuracy_verified=False))
                    journal.fail(job,receipt)
                    geometry_failures+=1
                    print(f"FAILED GEOMETRY {job['tile']}: {error}",flush=True)
                    progress()
                    if geometry_failures>=3:
                        raise RuntimeError('Three consecutive geometry failures; inspect evidence before continuing') from error
                progress();continue
            job=journal.claim('sources','local-chicago-worker',lease_seconds=3600,
                              source_locality_after=source_locality_after)
            if not job:break
            tile=tiles[job['tile']];tile_out=output/tile['id'];tile_out.mkdir(exist_ok=True)
            print(f"SOURCE {tile['id']}",flush=True);started=time.monotonic()
            try:
                grid=retry_transient(lambda: source_for_tile(tile,plan['frame'],tile_out/'sources',source_parent,way_index),
                                     f'source-grid {tile["id"]}')
                point_dir=tile_out/'points'
                if (point_dir/'manifest.json').is_file():
                    points=json.loads((point_dir/'manifest.json').read_text())
                    if points['grid']!=grid or sha(point_dir/'points.npz')!=points['points_sha256']:
                        raise ValueError('Existing city point crop changed')
                    point_skip_reason=None
                else:
                    needs_points, point_skip_reason = point_crop_required(tile_out/'sources')
                    if needs_points:
                        members=jobs[tile['id']]['source_tiles']
                        if jobs[tile['id']]['missing_archive_members']:raise ValueError('Missing source members in this tile')
                        items=[]
                        for identifier in members:
                            if identifier not in cached:
                                cached[identifier]=retry_transient(
                                    lambda identifier=identifier: acquire(assets[identifier],catalog['publisher'],bulk),
                                    f'LiDAR {identifier}')
                            path,record=cached[identifier];items.append((path,record,assets[identifier]))
                        points=crop_sources(items,grid,point_dir,compress_working=not release_points,
                                             point_cache=point_cache)
                    else:
                        points={'grid':grid,'crop_point_count':0,
                                'coverage_role':'Not acquired: terrain-only tile; '+point_skip_reason,
                                'points_sha256':None,'point_cache':point_cache.snapshot(),
                                'point_acquisition_skipped':True}
                receipt=tile_out/'sources-receipt.json'
                save_json(receipt,dict(job,result='pass',tile_output=str(tile_out.resolve()),
                    source_manifest_sha256=sha(tile_out/'sources/sources.json'),
                    point_manifest_sha256=sha(point_dir/'manifest.json') if (point_dir/'manifest.json').exists() else None,
                    point_sha256=points['points_sha256'],source_ids=jobs[tile['id']]['source_tiles'],
                    point_acquisition_skipped=bool(points.get('point_acquisition_skipped',False)),
                    point_skip_reason=point_skip_reason,seconds=time.monotonic()-started,
                    coverage_role=points['coverage_role'],point_count=points['crop_point_count'],
                    point_cache=points.get('point_cache')))
                journal.finish(job,receipt);failures=0
            except Exception as error:
                receipt=tile_out/'sources-failure.json'
                save_json(receipt,dict(job,result='failed',error_type=type(error).__name__,error=str(error)))
                journal.fail(job,receipt);failures+=1
                print(f"FAILED {tile['id']}: {error}",flush=True)
                # Coverage holes are tile-local facts, not a reason to stop a
                # city run.  Keep their failed receipt and continue; the
                # consecutive-failure fence still protects against a broader
                # outage or repeated transient download errors.
                if any(message in str(error) for message in NON_FATAL_SOURCE_ERRORS):
                    failures=0
                if failures>=3:
                    progress();raise RuntimeError('Three consecutive source failures; inspect evidence before continuing downloads') from error
            # Build each tile before acquiring the next, keeping memory/disk bounded.
            geometry=journal.claim('geometry','local-chicago-worker',lease_seconds=3600)
            if geometry:
                try:
                    geometry_job(geometry)
                    geometry_failures=0
                except Exception as error:
                    prior=journal.db.execute(
                        'SELECT evidence FROM jobs WHERE tile=? AND stage=0',
                        (geometry['tile'],)).fetchone()
                    if not prior:
                        raise
                    source_receipt=json.loads(Path(prior['evidence']).read_text())
                    tile_root=Path(source_receipt.get('tile_output',str(output/geometry['tile'])))
                    receipt=tile_root/'geometry-failure.json'
                    save_json(receipt,dict(geometry,result='failed',
                        error_type=type(error).__name__,error=str(error),
                        fallback_used=False,physical_accuracy_verified=False))
                    journal.fail(geometry,receipt)
                    geometry_failures+=1
                    print(f"FAILED GEOMETRY {geometry['tile']}: {error}",flush=True)
                    if geometry_failures>=3:
                        raise RuntimeError('Three consecutive geometry failures; inspect evidence before continuing') from error
            processed+=1
            progress()
    finally:journal.close();run_lock.close()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan',type=Path,default=ROOT/'runs/chicago-adaptation-city-001')
    p.add_argument('--catalog',type=Path,default=ROOT/'runs/chicago-source-index-003/city-sources.json')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-parent',type=Path)
    p.add_argument('--bulk',type=Path,default=bulk_path('chicago','lidar-2022'))
    p.add_argument('--limit',type=int,default=1)
    p.add_argument('--release-derived-points',action='store_true',help='Retain originals and provenance; release reproducible per-tile point working files after verified export')
    p.add_argument('--assembly',type=Path,help='Closed continuous city world on the bulk volume; never the installed save')
    p.add_argument('--way-index',type=Path,help='Verified read-only OSM spatial index for this exact source and city frame')
    p.add_argument('--point-cache-gib',type=float,default=16.0,
                   help='Bounded decoded LAS-member cache in GiB (0 disables; default 16)')
    p.add_argument('--source-locality-after',type=float,default=200.0,
                   help='Keep near tiles priority-ordered, then batch by shared LAS member (default 200)')
    p.add_argument('--retry-failed',action='store_true',
                   help='Explicitly return failed source/geometry jobs to pending; failure receipts remain audited')
    a=p.parse_args()
    if not 1<=a.limit<=10000:p.error('Limit must be between one and the city job count')
    class LogOutput:
        def __init__(self,original,log):self.original,self.log=original,log
        def write(self,value):self.original.write(value);self.log.write(value);self.log.flush()
        def flush(self):self.original.flush();self.log.flush()
    logdir=a.plan/'worker-logs';logdir.mkdir(exist_ok=True)
    with (logdir/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.log')).open('x') as log:
        sys.stdout=LogOutput(sys.stdout,log);sys.stderr=LogOutput(sys.stderr,log)
        index=None
        try:
            if a.way_index:
                from metric_way_index import MetricWayIndex
                frame=json.loads((a.plan/'frame.json').read_text())
                index=MetricWayIndex(a.way_index,bulk_path('chicago','sources','chicago.osm.pbf'),frame['crs'])
            if not 0 <= a.point_cache_gib <= 64:
                raise ValueError('--point-cache-gib must be between 0 and 64')
            if not 0 <= a.source_locality_after <= 1e9:
                raise ValueError('--source-locality-after must be between 0 and 1e9')
            run(a.plan,a.catalog,a.output,a.limit,a.source_parent,a.bulk,a.release_derived_points,
                a.assembly,index,int(a.point_cache_gib*2**30),a.source_locality_after,a.retry_failed)
        finally:
            if index:index.close()
            sys.stdout=sys.stdout.original;sys.stderr=sys.stderr.original
