"""Optional asynchronous LiDAR detail lane for completed Chicago base tiles."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

from chicago_tiles import digest
from cook_city_cache import acquire, save_json, sha
from lidar_spatial_index import SpatialPointIndex, crop_indexed_sources
from local_paths import bulk_path
from metric_world import build
from verify_metric_world import verify


ROOT=Path(__file__).resolve().parents[1]


class RefinementJournal:
    def __init__(self,path,plan,catalog_path):
        self.db=sqlite3.connect(path,timeout=20,isolation_level=None)
        self.db.row_factory=sqlite3.Row;self.db.execute('PRAGMA busy_timeout=20000')
        for attempt in range(40):
            try:
                self.db.execute('PRAGMA journal_mode=WAL');break
            except sqlite3.OperationalError as error:
                if 'locked' not in str(error).lower() or attempt==39:raise
                time.sleep(min(.05*(attempt+1),.5))
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS jobs(tile TEXT PRIMARY KEY,priority REAL NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending',owner TEXT,token TEXT,expires REAL,
            attempts INTEGER NOT NULL DEFAULT 0,evidence TEXT,evidence_sha256 TEXT);
        ''')
        fingerprint=json.dumps({'plan':digest(plan),'catalog_sha256':sha(catalog_path)},sort_keys=True)
        self._begin()
        try:
            old=self.db.execute("SELECT value FROM meta WHERE key='inputs'").fetchone()
            if old and old[0]!=fingerprint:raise ValueError('Refinement inputs changed; start a new journal')
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('inputs',?)",(fingerprint,))
            self.db.executemany('INSERT OR IGNORE INTO jobs(tile,priority) VALUES(?,?)',
                                [(tile['id'],tile['priority']) for tile in plan['tiles']])
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK');self.db.close();raise

    def claim(self,owner,eligible,lease=3600):
        now=time.time();self._begin()
        try:
            self.db.execute("UPDATE jobs SET state='pending',owner=NULL,token=NULL,expires=NULL "
                            "WHERE state='leased' AND expires<?",(now,))
            choice=None
            for row in self.db.execute("SELECT tile FROM jobs WHERE state='pending' ORDER BY priority,tile"):
                if row['tile'] in eligible:choice=row['tile'];break
            if choice is None:self.db.execute('COMMIT');return None
            token=uuid.uuid4().hex
            changed=self.db.execute("UPDATE jobs SET state='leased',owner=?,token=?,expires=?,attempts=attempts+1 "
                                    "WHERE tile=? AND state='pending'",(owner,token,now+lease,choice)).rowcount
            if changed!=1:raise RuntimeError('Refinement lease race')
            self.db.execute('COMMIT');return {'tile':choice,'token':token,'owner':owner}
        except Exception:
            self.db.execute('ROLLBACK');raise

    def finish(self,job,evidence):
        evidence=Path(evidence)
        changed=self.db.execute("UPDATE jobs SET state='complete',evidence=?,evidence_sha256=?,expires=NULL "
            "WHERE tile=? AND state='leased' AND token=? AND owner=?",
            (str(evidence.resolve()),sha(evidence),job['tile'],job['token'],job['owner'])).rowcount
        if changed!=1:raise RuntimeError('Lost refinement lease')

    def fail(self,job,evidence):
        changed=self.db.execute("UPDATE jobs SET state='failed',evidence=?,evidence_sha256=?,expires=NULL "
            "WHERE tile=? AND state='leased' AND token=? AND owner=?",
            (str(Path(evidence).resolve()),sha(evidence),job['tile'],job['token'],job['owner'])).rowcount
        if changed!=1:raise RuntimeError('Lost refinement lease')

    def recover_owner(self,owner):
        return self.db.execute("UPDATE jobs SET state='pending',owner=NULL,token=NULL,expires=NULL "
                               "WHERE state='leased' AND owner=?",(owner,)).rowcount

    def retry_failed(self):return self.db.execute("UPDATE jobs SET state='pending' WHERE state='failed'").rowcount
    def close(self):self.db.close()

    def _begin(self):
        for attempt in range(40):
            try:self.db.execute('BEGIN IMMEDIATE');return
            except sqlite3.OperationalError as error:
                if 'locked' not in str(error).lower() or attempt==39:raise
                time.sleep(min(.05*(attempt+1),.5))


def completed_base_tiles(base_journal):
    with sqlite3.connect(f'file:{Path(base_journal).resolve()}?mode=ro',uri=True) as db:
        return {row[0]:row[1] for row in db.execute(
            "SELECT tile,evidence FROM jobs WHERE stage=1 AND state='complete'")}


def refine(job,base_receipt,catalog,assets,output,raw_root,indexer,plan):
    base_receipt=Path(base_receipt);base_hash=sha(base_receipt)
    base=json.loads(base_receipt.read_text());root=Path(base.get('tile_output',base_receipt.parent))
    # Geometry receipts live directly under the immutable tile root today.
    if root/'geometry-receipt.json' != base_receipt:
        root=base_receipt.parent
    destination=root/'world.refined';receipt=root/'lidar-refinement-receipt.json'
    if destination.exists():
        checks=verify(destination)
    else:
        source=json.loads((root/'sources/sources.json').read_text())
        catalog_job={item['tile']:item for item in catalog['jobs']}[job['tile']]
        if catalog_job['missing_archive_members']:
            raise ValueError('Missing source members for LiDAR refinement')
        indexed=[]
        for identifier in catalog_job['source_tiles']:
            asset=assets[identifier]
            raw,record=acquire(asset,catalog['publisher'],raw_root)
            index=indexer.ensure(raw,record,asset)
            indexed.append((index,raw,record,asset))
        points=root/'points.refinement';point_data=points/'points.npz'
        if points.exists() and not point_data.exists():
            # A prior completed refinement may have released this reproducible
            # working container; without a world it is an incomplete attempt.
            import shutil
            shutil.rmtree(points)
        if not points.exists():
            staging=root/'points.refinement.building'
            if staging.exists():
                import shutil
                shutil.rmtree(staging)
            crop_indexed_sources(indexer,indexed,source,staging);staging.rename(points)
        point_manifest=json.loads((points/'manifest.json').read_text())
        if sha(point_data)!=point_manifest['points_sha256']:
            raise ValueError('Refinement point crop changed')
        staging=root/'world.refined.building'
        if not staging.exists():
            build(root/'sources',staging,point_source=points,world_frame=plan['frame'])
        checks=verify(staging);staging.rename(destination)
        point_data.unlink()
        save_json(points/'retention.json',{'normalized_working_copy_released':True,
            'persistent_spatial_indexes':[str(item[0].resolve()) for item in indexed],
            'original_las_observations_preserved':True,
            'points_sha256':point_manifest['points_sha256'],'inference_used':False})
    record={'tile':job['tile'],'result':'pass','base_geometry_receipt':str(base_receipt.resolve()),
        'base_geometry_receipt_sha256':base_hash,'world':str(destination.resolve()),
        'world_manifest_sha256':sha(destination/'earthcraft.json'),
        'regions':{path.name:sha(path) for path in sorted((destination/'region').glob('r.*.*.mca'))},
        'checks':checks,'geometry_profile':'raw-lidar-voxels',
        'critical_path':False,'asynchronous':True,'llm_used':False,
        'physical_accuracy_verified':False}
    save_json(receipt,record);return receipt


def run(args):
    plan=json.loads((args.plan/'plan.json').read_text());catalog=json.loads(args.catalog.read_text())
    if catalog['world_plan_sha256']!=digest(plan):raise ValueError('Source catalog does not match plan')
    assets={asset['id']:asset for asset in catalog['assets']}
    output=args.output;output.mkdir(parents=True,exist_ok=True)
    lock=(args.plan/f'lidar-refinement-{args.worker_id}.lock').open('a+')
    fcntl.lockf(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);lock.write(str(os.getpid()));lock.flush()
    journal=RefinementJournal(args.plan/'lidar-refinement.sqlite',plan,args.catalog)
    journal.recover_owner(args.worker_id)
    if args.retry_failed:print(f'REQUEUED FAILED REFINEMENTS: {journal.retry_failed()}',flush=True)
    stopping=False
    def stop(signum,frame):
        nonlocal stopping;stopping=True;print('Finishing current refinement before stopping',flush=True)
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    indexer=SpatialPointIndex(args.index_root);processed=0
    try:
        while processed<args.limit and not stopping:
            eligible=completed_base_tiles(args.base_journal)
            if args.tile:
                unknown=set(args.tile)-set({tile['id'] for tile in plan['tiles']})
                if unknown:raise ValueError(f'Unknown requested refinement tiles: {sorted(unknown)}')
                eligible={tile:evidence for tile,evidence in eligible.items() if tile in set(args.tile)}
            job=journal.claim(args.worker_id,set(eligible))
            if job is None:break
            print(f"REFINE {job['tile']}",flush=True)
            try:
                evidence=refine(job,eligible[job['tile']],catalog,assets,output,
                                args.bulk,indexer,plan)
                journal.finish(job,evidence)
            except Exception as error:
                failure=output/f"lidar-refinement-failure-{job['tile']}.json"
                save_json(failure,{'tile':job['tile'],'result':'failed','error_type':type(error).__name__,
                                   'error':str(error),'fallback_used':False})
                journal.fail(job,failure);raise
            processed+=1
    finally:journal.close();lock.close()


def spawn(args):
    children=[]
    for index in range(args.workers):
        budget=args.limit//args.workers+(index<args.limit%args.workers)
        if not budget:continue
        command=[sys.executable,str(Path(__file__).resolve()),'--plan',str(args.plan),
            '--catalog',str(args.catalog),'--output',str(args.output),'--bulk',str(args.bulk),
            '--index-root',str(args.index_root),'--base-journal',str(args.base_journal),
            '--limit',str(budget),'--workers','1','--worker-id',f'parallel-{index+1}']
        for tile in args.tile:command.append(f'--tile={tile}')
        if index==0 and args.retry_failed:command.append('--retry-failed')
        children.append(subprocess.Popen(command))
    failed=0
    try:
        for child in children:
            code=child.wait();failed=failed or code
    finally:
        for child in children:
            if child.poll() is None:child.terminate()
    if failed:raise SystemExit(failed)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,default=ROOT/'runs/chicago-adaptation-city-001')
    parser.add_argument('--catalog',type=Path,default=ROOT/'runs/chicago-source-index-003/city-sources.json')
    parser.add_argument('--output',type=Path,required=True,help='Failure receipts and operational progress')
    parser.add_argument('--bulk',type=Path,default=bulk_path('chicago','lidar-2022'))
    parser.add_argument('--index-root',type=Path,default=bulk_path('chicago','cache','lidar-spatial-v1'))
    parser.add_argument('--base-journal',type=Path,default=ROOT/'runs/chicago-adaptation-city-001/jobs.sqlite')
    parser.add_argument('--limit',type=int,default=1);parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--worker-id',default='local-refinement-worker')
    parser.add_argument('--tile',action='append',default=[],help='Restrict refinement to a named plan tile')
    parser.add_argument('--retry-failed',action='store_true')
    args=parser.parse_args()
    if not 1<=args.limit<=10000:parser.error('Limit must be 1..10000')
    if not 1<=args.workers<=8:parser.error('Workers must be 1..8')
    if args.workers>1:spawn(args)
    else:run(args)


if __name__=='__main__':main()
