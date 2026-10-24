"""One-pass Chicagoland tile generator that writes straight to Hugging Face.

One tile is one unit of work. Inside runs/scratch/gen/<tile>/ (capped per
tile) it builds the tile's sources, its LQ geometry and, where a measured scan
covers it, the appearance upgrade, in one pass. The result is packaged as
tiles/v2/<tile>/ (see regional_tiles), several tiles share one verified Hugging
Face commit made by hf_store, the journal records each stage's dataset receipt
path, SHA-256 and commit, and the scratch directory is deleted. Every source
cache is one capped LRU root and every byte written counts against one
footprint with a free-space floor (see regional_cache); when the guard says no
the worker pauses or refuses to start, it does not crash. No external drive.

Lanes: 'base' takes tiles never generated and runs every stage in one pass;
'scans' takes tiles whose geometry is complete and adds the measured
appearance, regenerating the LQ geometry in the same pass as the upgrade
baseline (and as the delivered world when no measured scan improves it; many
such tiles were generated on the retired drive but never installed).
Generated tiles are never reported as installed; hf_install.py installs them
into the save.

  regional_generate.py --control runs/regional/control --frame FRAME [--lane base|scans|quality]
      [--tiles ID,...] [--limit N] [--footprints] [--hf-python PATH]
  regional_generate.py --control ... --frame ... --status
  regional_generate.py --control ... --frame ... --seed-control     (archived plan, journal, catalogs)
  regional_generate.py --control ... --frame ... --prepare          (fresh catalog discovery, small)
  regional_generate.py --control ... --frame ... --index-coverage [--index-dir DIR]
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import faulthandler
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.error

import numpy as np
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform

from aws_terrain import prepare as terrain
from chicago_tiles import Journal, STAGES, digest, parse_shard, tile_plan
import hf_store
from hf_store import DatasetReader, DiskGuardError, GiB, MiB, StoreClient, human, parse_size
from metric_way_index import MetricWayIndex
from metric_world import build
from municipal_generate import presentation_ways
from region_expansion import atomic, sha, sha_memo
import regional_cache
from regional_cache import CacheCapacityError, Footprint, SourceCache
from regional_scans import acquire, discover, frozen_get
from regional_sources import validate as validate_sources
from regional_store import region_hashes
from regional_tiles import TILE_ID, TILE_SCHEMA, package_tile, receipt_bytes, region_name, tile_prefix
from verify_metric_world import verify
from worker_lease import LeaseKeeper
from world_templates import chunk_template, height_template

ROOT = Path(__file__).resolve().parents[1]
EX_TEMPFAIL = 75  # the guard refused; the supervisor waits and retries
STATUS_PATH = 'status/pipeline.json'  # optional host status, committed with a tile batch
STATUS_INTERVAL = 900  # the status file carries the published-tile bitmap the map reads
CONTROL_ALLOWANCE = 512 * MiB
LAKE_INDEX = 'lake/v1/index.json'
EMPTY_PAD = 16  # metres: a building just over the tile edge still sends the tile to the scans
SPARSE_BUILDINGS = 25  # mapped footprints at or below which a tile skips the scan (LQ shells + trees)
SPARSE_ROOF_CELLS = 4000  # ...and at most this many 1 m cells of LiDAR building returns (~25 houses)
WAY_INDEXES = ('illinois-ways', 'indiana-ways', 'wisconsin-ways')  # first wins where state extracts overlap
FOOTPRINT_INDEX = 'regional-footprints'
INDEX_PREFIX = 'sources/indexes'
# Control state of the retired external-drive worker, archived byte-identical on the Hub. It seeds a
# new control directory (plan, journal, catalogs) and holds the index receipts until sources/indexes/
# carries them itself.
LEGACY_CONTROL = 'archive/mini-worker-20260929/runs/chicagoland-scans-20260929'
SEED_FILES = ('plan.json', 'jobs.sqlite', 'scans/catalog.json', 'point-surveys/catalog.json', 'cook2022/catalog.json',
              'will2021/catalog.json', 'metro.geojson', 'metro.geojson.receipt.json', 'kenosha.geojson',
              'kenosha.geojson.receipt.json')
COVERAGE_SCHEMA = 'earthcraft.way-index-coverage.v1'
ROUTE_VERSION = 3  # bump when the dispatch route changes; workers re-rank pending tiles once
# 3: also clears the locality ranks that tiles reopened by reopen_stage carried back from before v2
_SCAN_COVERAGES = {}


class TileCapacityError(DiskGuardError):
    """One tile needs more scratch or source cache than its cap; fail it, keep going."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def supported_points(pieces,grid,surfaces):
    """An empty or conflicting crop must not claim a point-geometry upgrade."""
    xyz=np.unique(np.concatenate(pieces),axis=0) if pieces else np.empty((0,3))
    if not len(xyz):return xyz,0
    rows=np.floor(grid['north']-xyz[:,1]).astype(int);cols=np.floor(xyz[:,0]-grid['west']).astype(int)
    supported=surfaces['valid'][rows,cols]&(xyz[:,2]<=surfaces['dsm'][rows,cols]+2)
    return xyz[supported],int((~supported).sum())


def prepare_scope(control, frame):
    queries = [('metro', 'CBSA/MapServer/3', "GEOID='16980'"),
               ('kenosha', 'State_County/MapServer/1', "GEOID='55059'")]
    features = []
    for name, service, where in queries:
        url = 'https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/'+service+'/query?'
        url += urllib.parse.urlencode({'where': where, 'outFields': '*', 'outSR': 4326,
            'returnGeometry': 'true', 'f': 'geojson'})
        doc = json.loads(frozen_get(url, control/(name+'.geojson'), limit=16*2**20))
        if len(doc.get('features', [])) != 1 or doc.get('exceededTransferLimit'):
            raise ValueError('Expected one complete official regional boundary')
        features += doc['features']
    document = {'type': 'FeatureCollection', 'features': features}
    plan = tile_plan(document, frame, tile_size=512, max_area_m2=4e10, max_candidate_tiles=1_000_000)
    plan.update(scope='Current Census Chicago-Naperville-Elgin metro plus Kenosha County, Wisconsin',
        quality_policy='LQ base, then newest usable measured scans; preserve prior higher quality and edits',
        installed=False)
    # Elmhurst is the first scan target after the user identified its fallback.
    project = Transformer.from_crs(4326, frame['crs'], always_xy=True)
    ex, ey = project.transform(-87.9403, 41.8995)
    for tile in plan['tiles']:
        tile['priority'] = (tile['west']+256-ex)**2+(tile['north']-256-ey)**2
    plan['tiles'].sort(key=lambda tile: (tile['priority'], tile['id']))
    existing = control/'plan.json'
    if existing.exists() and json.loads(existing.read_text()) != plan:
        raise ValueError('Regional scope changed; keep the frozen prior run')
    atomic(existing, json.dumps(plan).encode())
    return plan


def select_ways(readers, tile, frame):
    unique = {}
    for reader in readers:
        for way in reader.select(reader.source, frame['crs'], tile['west']-32,
                                 tile['north']+32, tile['size']+64):
            if way['id'] in unique and unique[way['id']] != way:
                # State extracts overlap and can have different snapshot dates.
                # The frozen Illinois/Indiana/Wisconsin order resolves these
                # deterministically; every input identity is in sources.json.
                continue
            unique.setdefault(way['id'], way)
    return [unique[key] for key in sorted(unique)]


def make_source(tile, frame, root, readers, cache, footprint_index=None):
    source = root/'sources'
    if (root/'sources-receipt.json').exists():
        return validate_sources(root, tile)
    if source.exists():
        source.rename(root/f'sources.incomplete-{time.time_ns()}')
    inverse = Transformer.from_crs(frame['crs'], 4326, always_xy=True)
    lon, lat = inverse.transform(tile['west']+256, tile['north']-256)
    grid = {'size': tile['size'], 'west': tile['west'], 'north': tile['north'],
        'crs': frame['crs'], 'projection_origin': [lat, lon], 'shared_frame': True,
        'axes': 'east +X, south +Z, elevation +Y', 'metres_per_block': 1,
        'cell_ground_distances_m': []}
    terrain(lon, lat, tile['size'], source, cache=cache, chart_meta=grid)
    ways, estimates = presentation_ways(select_ways(readers, tile, frame))
    atomic(source/'osm-ways.json', json.dumps(ways).encode())
    atomic(source/'presentation-estimates.json', json.dumps(estimates).encode())
    meta = json.loads((source/'sources.json').read_text())
    meta.update(buildings_available=True, building_source_kind='osm-presentation',
        osm_subset_sha256=sha(source/'osm-ways.json'),
        presentation_estimates_sha256=sha(source/'presentation-estimates.json'),
        estimated_attribute_ways=len(estimates), unclassified_ground_block='grass_block',
        unclassified_ground_policy='Grass display fallback; cover not measured',
        building_height_policy='Measured tags first; missing heights explicitly estimated',
        purpose='Regional LQ coverage; separate measured scan upgrade receipts',
        osm_license='ODbL 1.0; OpenStreetMap contributors',
        osm_inputs=[reader.identity for reader in readers],
        missing_layers=['multipolygon relations', 'complete land cover', 'facade photography'])
    if footprint_index is not None:  # Overture fills buildings OSM has not mapped (most of the suburbs)
        from regional_footprints import crop as footprint_crop
        meta['lq_footprint_receipt'] = footprint_crop(footprint_index, meta, source/'lq-footprints.geojson')
    atomic(source/'sources.json', json.dumps(meta).encode())
    receipt = {'tile': tile['id'], 'stage': 'sources', 'result': 'pass',
        'sources_sha256': sha(source/'sources.json'), 'installed': False}
    atomic(root/'sources-receipt.json', json.dumps(receipt).encode())
    return source


def select_pair(tile, frame, catalog):
    extent = box(tile['west'], tile['north']-tile['size'], tile['west']+tile['size'], tile['north'])
    candidates = []
    key=(id(catalog),frame['crs'])
    if key not in _SCAN_COVERAGES:
        _SCAN_COVERAGES[key]=[]
        for pair in catalog['sources']:
            layer = pair['dtm']; reference = layer['spatial_reference']
            crs = reference.get('wkt') or reference.get('latestWkid', reference.get('wkid'))
            projection = Transformer.from_crs(crs, frame['crs'], always_xy=True)
            native = layer['extent']
            coverage = transform(projection.transform, box(native['xmin'], native['ymin'],
                native['xmax'], native['ymax']).segmentize(1000))
            if pair.get('boundary'):
                ownership=Transformer.from_crs(4326,frame['crs'],always_xy=True)
                coverage=coverage.intersection(transform(ownership.transform,shape(pair['boundary'])))
            _SCAN_COVERAGES[key].append((pair,coverage))
    for pair,coverage in _SCAN_COVERAGES[key]:
        layer = pair['dtm']; reference = layer['spatial_reference']
        area = extent.intersection(coverage).area
        if area > 0:
            candidates.append((area, pair['year'], -layer['native_pixel_size'], pair['county'], pair))
    return max(candidates, key=lambda entry: entry[:4])[-1] if candidates else None


def tile_grid(tile, frame):
    return {'crs': frame['crs'], 'west': tile['west'], 'north': tile['north'], 'size': tile['size']}


def grid_need(tile, frame, catalog):
    """{cache path: bytes admitted} of every original ArcGrid member the tile reads (empty when none)."""
    pair, members = grid_members(tile, frame, catalog)
    if not members:
        return {}
    return {Path(pair[kind]['cache']) / name: pair[kind]['members'][name]['bytes'] + 2**20
            for kind in members for name in members[kind]}


def grid_members(tile, frame, catalog):
    """(pair, {'dtm': [...], 'dsm': [...]}) when the tile's scan pair is an original ArcGrid archive, else (pair, None)."""
    pair = select_pair(tile, frame, catalog)
    if pair is None:
        return None, None
    from regional_arcgrid import wanted_members
    members = {}
    for kind in ('dtm', 'dsm'):
        layer = pair.get(kind) or {}
        if layer.get('kind') != 'original-arcgrid-zip':
            return pair, None
        members[kind] = wanted_members(layer, tile_grid(tile, frame))
    return pair, members


def locality_route(plan, frame, catalog, tile_ids):
    """Dispatch order that keeps tiles sharing source files together.

    An original county ArcGrid member is a full-width strip (Will: 31 km x
    0.9 km, ~1.2 GB inflated), so every tile reading the same members runs
    back to back while those members sit in the cache; groups follow each
    other north to south so consecutive groups share a strip. ArcGrid tiles
    go first because they gain from neighbours working the same strip; every
    other tile follows in row-major order (north to south, west to east).
    """
    position = {tile['id']: index for index, tile in
                enumerate(sorted(plan['tiles'], key=lambda tile: (-tile['north'], tile['west'])))}
    tiles = {tile['id']: tile for tile in plan['tiles']}
    groups, rest = {}, []
    for tile_id in tile_ids:
        pair, members = grid_members(tiles[tile_id], frame, catalog)
        if members:
            groups.setdefault((pair['county'], tuple(members['dtm']), tuple(members['dsm'])), []).append(tile_id)
        else:
            rest.append(tile_id)
    route = []
    for key in sorted(groups, key=lambda key: (key[0], min(position[tile] for tile in groups[key]))):
        route += sorted(groups[key], key=position.__getitem__)
    return route + sorted(rest, key=position.__getitem__), len(groups)


def apply_locality_route(control, plan, frame, catalog, log=print, scan_surfaces='ept'):
    """Rank pending tiles once per plan and scheduler version (idempotent).

    With EPT scan surfaces (the default) no tile shares a county strip, so the
    route is the plan's own priority, nearest the origin first: every route rank
    is cleared. County surfaces keep locality_route() so strip readers stay together.
    """
    control = Path(control)
    marker = control / 'route-locality.json'
    plan_sha = digest(plan)
    if marker.exists():
        record = json.loads(marker.read_text())
        if record.get('plan_sha256') == plan_sha and record.get('version') == ROUTE_VERSION:
            return record
    journal = Journal(control / 'jobs.sqlite', plan)
    try:
        started = time.monotonic()
        if scan_surfaces == 'ept':
            route, groups = [], 0
            changed = journal.db.execute("UPDATE jobs SET route_rank=NULL,route_label=NULL "
                                         "WHERE state='pending' AND route_rank IS NOT NULL").rowcount
            journal.db.commit()
        else:
            pending = [row[0] for row in journal.db.execute(
                "SELECT tile FROM jobs WHERE stage=0 AND state='pending' ORDER BY tile")]
            route, groups = locality_route(plan, frame, catalog, pending)
            changed = journal.prioritize_tiles(route, 'locality-v%d' % ROUTE_VERSION,
                                               stages=('sources', 'geometry', 'appearance')) if route else 0
    finally:
        journal.close()
    record = {'plan_sha256': plan_sha, 'version': ROUTE_VERSION, 'route': 'plan priority' if scan_surfaces == 'ept'
              else 'arcgrid locality', 'tiles': len(route), 'arcgrid_groups': groups,
              'rows_ranked': changed, 'seconds': round(time.monotonic() - started, 1), 'created_utc': utc_now()}
    atomic(marker, json.dumps(record).encode())
    log(json.dumps({'event': 'locality_route', **record}))
    return record


class Prefetcher:
    """Fetch the ArcGrid members of the next queued tiles while this tile computes.

    Runs in one background thread per worker. Members land in the shared LRU
    cache under their per-member lock, so a worker that needs a member being
    prefetched waits for it instead of downloading it again.
    """

    def __init__(self, worker, ctx, depth=6, limit=2):
        self.worker, self.ctx, self.depth, self.limit = worker, ctx, depth, limit
        self.thread, self.stop = None, threading.Event()

    def kick(self, journal, current):
        if self.thread is not None and self.thread.is_alive():
            return
        now = time.time()
        upcoming = [row[0] for row in journal.db.execute(
            "SELECT tile FROM jobs WHERE stage=0 AND state='pending' AND COALESCE(not_before,0)<=? AND tile!=? "
            "ORDER BY CASE WHEN route_rank IS NULL THEN 1 ELSE 0 END, route_rank, priority, tile LIMIT ?",
            (now, current, self.depth))]
        if upcoming:
            self.thread = threading.Thread(target=self.run, args=(upcoming,), name='grid-prefetch', daemon=True)
            self.thread.start()

    def run(self, upcoming):
        from regional_arcgrid import prefetch
        fetched = 0
        for tile_id in upcoming:
            tile = self.worker.tiles.get(tile_id)
            if tile is None or self.stop.is_set() or fetched >= self.limit:
                break
            try:
                pair, members = grid_members(tile, self.ctx['frame'], self.ctx['catalog'])
                if not members:
                    continue
                cache = self.worker.cache
                largest = max(pair[kind]['members'][name]['bytes'] for kind in members for name in members[kind])
                if cache.used(fresh=True) + largest > 0.8 * cache.cap:
                    break  # never crowd running tiles out of the cache for a guess about the next one
                for kind in ('dtm', 'dsm'):
                    names = prefetch(pair[kind], tile_grid(tile, self.ctx['frame']), limit=self.limit - fetched,
                                     stop=self.stop)
                    fetched += len(names)
                    if names:
                        self.worker.log(event='grid_prefetched', tile=tile_id, kind=kind, members=names)
            except Exception as error:  # noqa: BLE001 - prefetch is an optimisation; the tile fetches what it needs
                self.worker.log(event='grid_prefetch_failed', tile=tile_id, reason=str(error)[:200])
                break

    def close(self):
        self.stop.set()


def scan_tile(tile, frame, root, pair, point_catalog=None, point_cache=None, cook_catalog=None, will_catalog=None,
              footprint_index=None, phase=None, county_pair=None):
    """Measured appearance for one tile whose sources and LQ geometry are in root.

    pair: a county raster pair, or an 'ept' pair (ept_surfaces) whose DTM/DSM and
    building points all come from one USGS 3DEP survey; county_pair is the
    fallback when that survey's ground disagrees with the LQ terrain.
    footprint_index: the Overture footprint sqlite used to associate
    unclassified returns with mapped buildings (None leaves it out).
    """
    phase = phase or Phases()
    if pair is None:
        if point_catalog is not None:
            result=point_only_tile(tile,frame,root,point_catalog,point_cache)
            phase.lap('point_only')
            if result is not None:return result
        return {'tile': tile['id'], 'stage': 'appearance', 'result': 'pass',
            'scan_status': 'No usable paired county scan; retain LQ', 'quality': 'LQ', 'installed': False}
    source = root/'sources'
    imagery = root/'naip-imagery'
    naip = threading.Thread(target=prefetch_naip, args=(source, imagery, pair['year']), name='naip-prefetch', daemon=True)
    naip.start()  # four slow service requests; they overlap surfaces, points and the build
    try:
        return measured_scan(tile, frame, root, pair, point_catalog, point_cache, cook_catalog, will_catalog,
                             footprint_index, phase, county_pair, naip)
    finally:
        naip.join()  # never leave it writing into a tile scratch that is about to be removed


def measured_scan(tile, frame, root, pair, point_catalog, point_cache, cook_catalog, will_catalog,
                  footprint_index, phase, county_pair, naip):
    """scan_tile's measured pass (surfaces, points, build, roof colour) once a pair is chosen."""
    source = root/'sources'
    scan_source = root/'scan-sources'
    surfaces = root/'scan-surfaces'
    world = root/'world.scanned'
    imagery = root/'naip-imagery'
    if not (scan_source/'sources.json').exists():
        with np.load(source/'rasters.npz') as rasters:
            grid = json.loads((source/'sources.json').read_text())
            if pair.get('kind') == 'ept':
                import ept_surfaces
                try:
                    elevation, record = ept_surfaces.acquire(pair, grid, surfaces, rasters['elevation'], point_cache, frame)
                except ValueError:
                    if county_pair is None:
                        raise
                    shutil.rmtree(surfaces)
                    pair = county_pair
            if pair.get('kind') != 'ept':
                elevation, record = acquire(pair, grid, surfaces, rasters['elevation'])
            cover = rasters['cover'].copy()
        shutil.copytree(source, scan_source, ignore=shutil.ignore_patterns('._*'))
        np.savez_compressed(scan_source/'rasters.npz', elevation=elevation, cover=cover)
        update_elevation(scan_source/'elevation.tif', elevation)
        meta = json.loads((scan_source/'sources.json').read_text())
        meta.update(elevation_source=f"{pair['county']} {pair['year']} LiDAR DTM; explicit LQ coverage gaps",
            elevation_vertical_datum=pair['dtm']['vertical_reference'], scan_surface_receipt=record,
            elevation_sha256=sha(scan_source/'elevation.tif'))
        if footprint_index is not None:
            from regional_footprints import crop as footprint_crop
            meta['scan_footprint_receipt']=footprint_crop(footprint_index,meta,scan_source/'scan-footprints.geojson')
        atomic(scan_source/'sources.json', json.dumps(meta).encode())
    phase.lap('scan_surfaces')
    quality = 'scan-roof'
    attempts = []
    grid=json.loads((scan_source/'sources.json').read_text())
    points=None;build_source=scan_source
    # A full-density upgrade uses every intersecting survey, retaining original
    # crop receipts. Partial roof crops stay in the raster lane until complete.
    if point_catalog is not None:
        from regional_point_catalog import candidates
        from ept_buildings import crop
        with np.load(surfaces/'metric-surfaces.npz') as arrays:
            from scan_envelope import context as association_context
            context=association_context(scan_source,grid,arrays)
            pieces=[]; accepted=[]
            originals=[]
            if pair['county']=='Cook' and cook_catalog and Path(cook_catalog).exists():
                from regional_cook_points import crop as cook_crop
                def associated_cook(catalog,grid,destination,cache):
                    return cook_crop(catalog,grid,destination,cache,surface_context=context)
                originals.append(('Cook-original-2022',cook_catalog,associated_cook,'cook2022-cache'))
            if pair['county']=='Will' and will_catalog and Path(will_catalog).exists():
                from regional_will_points import crop as will_crop
                def associated_will(catalog,grid,destination,cache):
                    return will_crop(catalog,grid,destination,cache,surface_context=context)
                originals.append(('Will-original-2021',will_catalog,associated_will,'will2021-cache'))
            for project,original_catalog,original_crop,original_cache in originals:
                point_root=root/'point-crops'/project
                try:
                    if (point_root/'manifest.json').exists():
                        record=json.loads((point_root/'manifest.json').read_text())
                        if sha(point_root/'points.npz')!=record['points_sha256']:raise ValueError('Frozen original county crop changed')
                    else:record=original_crop(original_catalog,grid,point_root,point_cache.parent/original_cache)
                    with np.load(point_root/'points.npz') as observed_points:pieces.append(observed_points['xyz'].copy())
                    accepted.append(record);attempts.append({'project':record['project'],'result':'acquired','building_points':record['building_points']})
                except (ValueError,urllib.error.URLError,TimeoutError) as error:
                    attempts.append({'project':project,'result':'unavailable','reason':str(error)})
            surveys=candidates(tile,frame,point_catalog)
            if pair.get('kind')=='ept':  # same survey as the surfaces: one epoch, nodes already cached
                surveys=[survey for survey in surveys if survey['project']==pair['project']]
            for survey in surveys:
                point_root=root/'point-crops'/survey['project']
                try:
                    if (point_root/'manifest.json').exists():
                        record=json.loads((point_root/'manifest.json').read_text())
                        if sha(point_root/'points.npz')!=record['points_sha256']:
                            raise ValueError('Original full-density crop changed')
                    elif pair.get('kind')=='ept' and survey['project']==pair['project'] and (surfaces/'ept-points').exists():
                        import ept_surfaces  # the surfaces' own crop: no second download or decode of every node
                        record=ept_surfaces.building_points(surfaces/'ept-points',grid,point_root,arrays['dtm'],arrays['valid'],
                                                            surface_context=context)
                    else:
                        record=crop(survey['project'],grid,point_root,point_cache,arrays['dtm'],arrays['valid'],surface_context=context)
                    with np.load(point_root/'points.npz') as observed_points: pieces.append(observed_points['xyz'].copy())
                    accepted.append(record)
                    attempts.append({'project':survey['project'],'result':'acquired','building_points':record['building_points']})
                except ValueError as error:
                    attempts.append({'project':survey['project'],'result':'unusable','reason':str(error)})
            xyz,rejected_temporal_or_surface_conflicts=supported_points(pieces,grid,arrays)
            if len(xyz):
                points=root/'points.combined';points.mkdir(exist_ok=True)
                np.savez_compressed(points/'points.npz',xyz=xyz)
                record=dict(accepted[0],project='explicit union of all usable intersecting surveys',
                    points_sha256=sha(points/'points.npz'),building_points=len(xyz),source_crops=accepted,
                    retained_classes=sorted({value for crop in accepted for value in crop.get('retained_classes',[6])}),
                    point_admission='Class-6 returns supported by paired DSM within 2 m; class-1 returns additionally require a mapped measured-roof footprint and >2 m ground clearance; association may include clutter',
                    rejected_temporal_or_surface_conflicts=rejected_temporal_or_surface_conflicts)
                atomic(points/'manifest.json',json.dumps(record).encode())
                world=root/'world.scanned-points';quality='scan-points-and-roof'
                build_source=root/'combined-scan-sources'
                if not build_source.exists():shutil.copytree(scan_source,build_source,ignore=shutil.ignore_patterns('._*'))
                combined_meta=json.loads((build_source/'sources.json').read_text())
                combined_meta['classified_point_receipt']=record
                atomic(build_source/'sources.json',json.dumps(combined_meta).encode())
    phase.lap('point_crops')
    # Determine point coverage first, then serialize the final geometry once.
    # The immutable LQ baseline is used only at installation, not read back here.
    if not world.exists():
        staging=world.with_name(world.name+'.building')
        if staging.exists():staging.rename(root/(world.name+'.incomplete-'+str(time.time_ns())))
        build(build_source,staging,surface_source=surfaces,point_source=points,world_frame=frame)
        checks=verify(staging);staging.rename(world)
    else:checks=verify(world)
    phase.lap('scan_build')
    meta=json.loads((world/'earthcraft.json').read_text())
    imagery_result = None
    if meta.get('roof_source_cells',0):
        from naip_imagery import acquire as imagery_acquire
        from roof_color import apply as paint_roofs
        painted=root/'world.scan-roof-colour'
        naip.join()
        try:
            if not (imagery/'imagery.json').exists():
                if imagery.exists():imagery.rename(root/f'naip.incomplete-{time.time_ns()}')
                imagery_acquire(Path(meta['dem_path']).parent,imagery,preferred_year=pair['year'])
            if not painted.exists():
                imagery_result=paint_roofs(world,imagery,painted,verified=True)  # verified just above
            else:
                imagery_result=json.loads((painted/'roof-colour.json').read_text());verify(painted)
            world=painted;checks=json.loads((painted/'block-verification.json').read_text())
        except (ValueError, urllib.error.URLError, TimeoutError) as error:
            imagery_result={'result':'unavailable','reason':str(error)}
        phase.lap('roof_colour')
    return {'tile': tile['id'], 'stage': 'appearance', 'result': 'pass', 'quality': quality,
        'world': str(world.resolve()), 'world_manifest_sha256': sha(world/'earthcraft.json'),
        'regions': {path.name: sha(path) for path in (world/'region').glob('r.*.*.mca')},
        'roof_source_cells': meta['roof_source_cells'], 'scan_source': pair,
        'checks': checks, 'facade_measured': False, 'raw_points_acquired': quality=='scan-points-and-roof',
        'point_survey_attempts': attempts, 'roof_appearance': imagery_result, 'installed': False}


def prefetch_naip(source, imagery, year):
    """Fetch the tile's NAIP roof imagery ahead of the roof-colour step; on failure that step retries."""
    from naip_imagery import acquire
    try:
        if not (imagery/'imagery.json').exists():
            acquire(source, imagery, preferred_year=year)
    except Exception:  # noqa: BLE001 - the roof-colour step fetches again and records the reason
        pass


def point_only_tile(tile,frame,root,catalog,cache):
    """Use classified 3D scans outside the paired Illinois raster coverage.

Keep the base terrain and explicitly mark its lower-quality vertical check.
Choose the newest usable survey to avoid inventing a cross-date scene where
there is no current measured DSM to reject conflicting older structures.
"""
    from regional_point_catalog import candidates
    from ept_buildings import crop
    source=root/'sources';meta=json.loads((source/'sources.json').read_text());attempts=[]
    with np.load(source/'rasters.npz') as rasters:
        for survey in candidates(tile,frame,catalog):
            points=root/'point-only-crops'/survey['project']
            try:
                if (points/'manifest.json').exists():
                    record=json.loads((points/'manifest.json').read_text())
                    if sha(points/'points.npz')!=record['points_sha256']:raise ValueError('Frozen point crop changed')
                else:
                        record=crop(survey['project'],meta,points,cache,rasters['elevation'],np.isfinite(rasters['elevation']),
                            ground_reference='LQ AWS terrain; absolute scan datum unverified')
            except ValueError as error:
                attempts.append({'project':survey['project'],'result':'unusable','reason':str(error)});continue
            if not record['building_points']:
                attempts.append({'project':survey['project'],'result':'no classified building returns'});continue
            candidate=root/'world.points-only';scan_source=root/'point-only-sources'
            if not candidate.exists():
                if not scan_source.exists():shutil.copytree(source,scan_source,ignore=shutil.ignore_patterns('._*'))
                updated=dict(meta,classified_point_receipt=record,
                    vertical_check_reference='LQ terrain; paired county DTM unavailable; absolute scan datum is unverified')
                atomic(scan_source/'sources.json',json.dumps(updated).encode())
                staging=root/'world.points-only.building'
                if staging.exists():staging.rename(root/f'world.points-only.incomplete-{time.time_ns()}')
                build(scan_source,staging,point_source=points,world_frame=frame)
                checks=verify(staging);staging.rename(candidate)
            else:checks=verify(candidate)
            return {'tile':tile['id'],'stage':'appearance','result':'pass','quality':'classified-scan-points',
                'world':str(candidate.resolve()),'world_manifest_sha256':sha(candidate/'earthcraft.json'),
                'regions':{path.name:sha(path) for path in (candidate/'region').glob('r.*.*.mca')},
                'checks':checks,'raw_points_acquired':True,'facade_measured':False,'installed':False,
                'selection':'Newest usable classified survey; no silent cross-date union without a paired DSM',
                'vertical_check_reference':'LQ terrain; absolute vertical datum unverified',
                'prior_survey_attempts':attempts}
    return None


class Phases:
    """Seconds per phase of one tile: lap(name) charges the time since the previous lap to name."""

    def __init__(self):
        self.seconds, self.last = {}, time.monotonic()

    def lap(self, name):
        now = time.monotonic()
        self.seconds[name] = round(self.seconds.get(name, 0) + now - self.last, 1)
        self.last = now


def empty_tiles(tiles, footprint_index, pad=EMPTY_PAD):
    """Ids of the tiles (plan entries) that no footprint in the R-tree comes within pad metres of."""
    import sqlite3
    db = sqlite3.connect(f'file:{footprint_index}?mode=ro', uri=True)
    try:
        query = 'SELECT 1 FROM bounds WHERE maxx>=? AND minx<=? AND maxy>=? AND miny<=? LIMIT 1'
        return [tile['id'] for tile in tiles
                if db.execute(query, (tile['west'] - pad, tile['west'] + tile['size'] + pad,
                                      tile['north'] - tile['size'] - pad, tile['north'] + pad)).fetchone() is None]
    finally:
        db.close()


def update_elevation(path, array):
    import rasterio
    with rasterio.open(path, 'r+') as raster:
        raster.write(array.astype(np.float32), 1)


# --------------------------------------------------------------------------
# Source indexes from sources/indexes/ on the Hub, held in the LRU cache


class IndexUnavailable(RuntimeError):
    pass


class IndexSet:
    """Fetch, verify and open the large source indexes a tile needs, inside the cache budget.

    The way indexes are large (Illinois alone is 3.4 GB), so a tile opens only
    the state indexes that hold at least one way in its query window, as listed
    in control/index-coverage.json (written by --index-coverage). Without that
    file every index is opened, which is always correct, only larger. Skipping an
    index with no way in the window leaves select_ways() output unchanged.
    """

    def __init__(self, control, cache, reader, frame, *, names=WAY_INDEXES, log=None, index_dir=None):
        self.control, self.cache, self.reader, self.frame = Path(control), cache, reader, frame
        self.names, self.log = tuple(names), log or (lambda **_: None)
        self.index_dir = Path(index_dir) if index_dir else None
        self.open, self.coverage = {}, None
        path = self.control / 'index-coverage.json'
        if path.exists():
            record = json.loads(path.read_text())
            if record.get('schema') != COVERAGE_SCHEMA:
                raise ValueError('Unknown index coverage schema')
            self.coverage = {name: (entry['index_sha256'], frozenset(entry['tiles']))
                             for name, entry in record['indexes'].items()}

    def local(self, name):
        if self.index_dir is not None:
            return self.index_dir / (name + '.sqlite')
        return self.cache.root / 'indexes' / (name + '.sqlite')

    def needed(self, tile):
        if self.coverage is None:
            return list(self.names)
        return [name for name in self.names if name not in self.coverage or tile['id'] in self.coverage[name][1]]

    def ensure(self, name):
        """Local verified copy of sources/indexes/<name>.sqlite and its receipt; fetched into the cache."""
        local = self.local(name)
        receipt = local.with_suffix('.json')
        if local.exists() and receipt.exists():
            regional_cache.touch(local)
            return local
        if self.index_dir is None:
            # Workers share the cache: one downloads an index, the others wait and reuse it.
            local.parent.mkdir(parents=True, exist_ok=True)
            with (local.parent / f'.{name}.fetch.lock').open('a+') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if local.exists() and receipt.exists():
                    regional_cache.touch(local)
                    return local
                return self._fetch(name, local, receipt)
        return self._fetch(name, local, receipt)

    def _fetch(self, name, local, receipt):
        if self.index_dir is not None:
            raise IndexUnavailable(f'{local} is missing')
        data, receipts = f'{INDEX_PREFIX}/{name}.sqlite', [f'{INDEX_PREFIX}/{name}.json', f'{LEGACY_CONTROL}/{name}.json']
        info = self.reader.info([data, *receipts])
        receipt_path = next((path for path in receipts if path in info), None)
        if data not in info or receipt_path is None:
            raise IndexUnavailable(f'{data} or its receipt is not published in the dataset yet')
        raw = self.reader.read(receipt_path, limit=MiB, expected=info[receipt_path])
        record = json.loads(raw)
        recorded = record.get('index_sha256') or record.get('sha256')
        if info[data].sha256 and recorded != info[data].sha256:
            raise ValueError(f'{data} on the Hub differs from its receipt {receipt_path}')
        self.cache.admit(local, info[data].size + len(raw) + MiB, what=f'index {name}')
        started = time.monotonic()
        receipt.parent.mkdir(parents=True, exist_ok=True)
        atomic(receipt, raw)
        self.reader.download(data, local, expected=info[data])
        self.log(event='index_fetched', index=name, bytes=info[data].size, receipt=receipt_path,
                 seconds=round(time.monotonic() - started, 1))
        return local

    def readers_for(self, tile):
        readers = []
        for name in self.needed(tile):
            local = self.ensure(name)
            reader = self.open.get(name)
            if reader is not None and not reader.path.exists():  # evicted between tiles
                reader.close()
                reader = None
            if reader is None:
                reader = MetricWayIndex(local, None, self.frame['crs'], dataset_path=f'{INDEX_PREFIX}/{name}.sqlite')
                if self.coverage and name in self.coverage and self.coverage[name][0] != reader.index_sha256:
                    raise ValueError(f'index-coverage.json describes another {name} build')
                self.open[name] = reader
            regional_cache.touch(local)
            readers.append(reader)
        return readers

    def footprints(self):
        local = self.ensure(FOOTPRINT_INDEX)
        if FOOTPRINT_INDEX not in self.open:
            record = json.loads(local.with_suffix('.json').read_text())
            if sha_memo(local) != record['sha256'] or record['crs'] != self.frame['crs']:
                raise ValueError('Regional footprint index differs from its receipt or frame')
            self.open[FOOTPRINT_INDEX] = local
        regional_cache.touch(local)
        return local

    def close(self):
        for reader in self.open.values():
            if hasattr(reader, 'close'):
                reader.close()
        self.open = {}


def index_coverage(control, plan, frame, indexes):
    """Record which plan tiles have at least one way of each index in their 32 m-padded window."""
    record = {'schema': COVERAGE_SCHEMA, 'plan_sha256': digest(plan), 'window': 'tile padded by 32 m',
              'created_utc': utc_now(), 'indexes': {}}
    for name in indexes.names:
        local = indexes.ensure(name)
        reader = MetricWayIndex(local, None, frame['crs'], dataset_path=f'{INDEX_PREFIX}/{name}.sqlite')
        try:
            tiles = sorted(tile['id'] for tile in plan['tiles']
                           if reader.covers(tile['west'] - 32, tile['north'] + 32, tile['size'] + 64))
            record['indexes'][name] = {'index_sha256': reader.index_sha256, 'tiles': tiles}
        finally:
            reader.close()
        print(json.dumps({'event': 'index_coverage', 'index': name, 'tiles': len(tiles)}), flush=True)
    atomic(Path(control) / 'index-coverage.json', json.dumps(record).encode())
    return {name: len(entry['tiles']) for name, entry in record['indexes'].items()}


# --------------------------------------------------------------------------
# One pass, one tile


def generate_tile(tile, job, root, ctx, phase=None):
    """Run every stage of one tile in root; returns (receipts, failures, delivered world, LQ world)."""
    frame = ctx['frame']
    phase = phase or Phases()
    receipts, failures = {}, {}
    readers = ctx['indexes'].readers_for(tile)
    phase.lap('indexes')
    make_source(tile, frame, root, readers, ctx['cache_root'] / 'terrain',
                footprint_index=ctx['indexes'].footprints() if ctx['footprints'] else None)
    validate_sources(root, tile)
    receipts['sources'] = json.loads((root / 'sources-receipt.json').read_text())
    phase.lap('sources')
    world, staging = root / 'world', root / 'world.building'
    footprints = (json.loads((root / 'sources' / 'sources.json').read_text()).get('lq_footprint_receipt') or {}).get('count')
    survey = lidar_roofs = None
    if 'appearance' in job['stages'] and (ctx.get('scan_surfaces', 'ept') == 'ept' or ctx.get('trees')):
        import ept_surfaces
        (root / 'scan-surfaces').mkdir(exist_ok=True)
        survey, crop = ept_surfaces.select(tile, frame, ctx.get('point_catalog'), root / 'scan-surfaces',
                                           ctx['cache_root'] / 'ept')
        if crop is not None:
            lidar_roofs = ept_surfaces.building_cells(root / 'scan-surfaces' / 'ept-points',
                                                      json.loads((root / 'sources' / 'sources.json').read_text()))
        phase.lap('survey')
    # Sparse: few mapped footprints, and the LiDAR agrees (a new subdivision can be missing from the maps).
    sparse = (footprints is not None and footprints <= ctx.get('sparse_buildings', SPARSE_BUILDINGS) and
              (lidar_roofs is None or lidar_roofs <= SPARSE_ROOF_CELLS))
    # A scan of a published tile keeps its published LQ as the upgrade baseline: it is what saves have
    # installed, and rebuilding it with newer code would neither match them nor be needed by the scan.
    published = (published_lq(ctx['reader'], tile, world)
                 if job['stages'] == ['appearance'] and not sparse and ctx.get('reader') is not None else None)
    if published is not None:
        receipts['geometry'] = published
        phase.lap('lq_reused')
    else:
        build(root / 'sources', staging, world_frame=frame)
        checks = verify(staging)
        staging.rename(world)
        phase.lap('lq_build')
        receipts['geometry'] = {'tile': tile['id'], 'stage': 'geometry', 'result': 'pass', 'world': str(world.resolve()),
                                'quality': 'LQ', 'installed': False, 'world_manifest_sha256': sha(world / 'earthcraft.json'),
                                'regions': region_hashes(world), 'checks': checks}
    # The LQ world is delivered unless a measured world supersedes it. In the
    # scans lane that matters too: many tiles completed geometry on the retired
    # drive but were never installed or archived, and installing an LQ tile
    # only ever adds chunks that are absent.
    delivered, lq = world, None
    if 'appearance' in job['stages'] and sparse:
        # A few farm buildings, or none: measured roofs add little; the LQ shells plus LiDAR trees are the tile.
        receipts['appearance'] = {'tile': tile['id'], 'stage': 'appearance', 'result': 'pass', 'quality': 'LQ',
                                  'scan_status': f'Sparse tile ({footprints} mapped footprints, {lidar_roofs} LiDAR '
                                                 'building cells): LQ shells and LiDAR trees; scan skipped',
                                  'footprints_in_tile': footprints, 'lidar_building_cells': lidar_roofs, 'installed': False}
        phase.lap('scan_select')
    elif 'appearance' in job['stages']:
        try:
            county = select_pair(tile, frame, ctx['catalog'])
            pair = county
            if ctx.get('scan_surfaces', 'ept') == 'ept':
                pair = survey or county
            footprint = ctx['indexes'].footprints() if ctx['footprints'] and pair is not None else None
            phase.lap('scan_select')
            receipt = scan_tile(tile, frame, root, pair, ctx['point_catalog'], ctx['cache_root'] / 'ept',
                                ctx['cook_catalog'], ctx['will_catalog'], footprint_index=footprint, phase=phase,
                                county_pair=county if pair is not county else None)
            receipts['appearance'] = receipt
            if receipt.get('world'):
                delivered, lq = Path(receipt['world']), world
        except (DiskGuardError, IndexUnavailable):
            raise
        except Exception as error:  # noqa: BLE001 - a base tile still delivers its verified LQ
            if job['lane'] == 'scans' or job['stages'] == ['appearance']:  # its LQ is already delivered
                raise
            failures['appearance'] = error
    if (ctx.get('trees') or ctx.get('facades') or ctx.get('ground')) and delivered is not None:
        decorate(tile, frame, delivered, root, ctx, receipts, failures, phase)
    return receipts, failures, delivered, lq


def decorate(tile, frame, delivered, root, ctx, receipts, failures, phase):
    """Trees and facades into the delivered world from one block window and one region rewrite.

    Trees only fill air and facades only change exterior walls, so both layers apply together; the
    region was read, re-encoded and verified once per pass before (about nine full parses a tile)."""
    import regional_trees
    from detail_apply import apply
    from detail_guard import WriteGuard
    grid = regional_trees.tile_grid(tile, frame)
    surface = regional_trees.tile_surface(delivered, grid, volume=True)
    reports = {}
    if ctx.get('trees'):
        reports['trees'] = plant_trees(tile, frame, delivered, root, ctx, failures, surface)
        phase.lap('trees')
    if ctx.get('facades'):
        reports['facades'] = paint_facades(tile, frame, delivered, root, failures, ctx.get('indexes'), surface)
        phase.lap('facades')
    if ctx.get('ground'):
        reports['ground'] = cover_ground(tile, frame, delivered, root, failures, surface)
        phase.lap('ground')
    del surface
    reports = {name: report for name, report in reports.items() if report is not None}
    layers = [report.pop('_layer') for report in reports.values() if report.get('_layer')]
    if layers:
        manifest = json.loads((delivered / 'earthcraft.json').read_text())
        work = root / 'decorate'
        receipt = apply(delivered / 'region', layers, work / 'region', WriteGuard(work, 600 * MiB, 4 * GiB),
                        height=manifest['dimension_height'], grow=True)
        name = region_name(tile['id'])[:-len('.mca')]
        applied = receipt['regions'].get(name, {})
        if not applied.get('verified'):
            failures['decorate'] = ValueError(f'{name}: decorated region did not verify')
            return
        os.replace(work / 'region' / f'{name}.mca', delivered / 'region' / f'{name}.mca')
        kinds = applied.get('by_kind', {})
        for key, report in reports.items():
            prefix = {'trees': 'tree_', 'facades': 'facade_', 'ground': 'ground_'}[key]
            report.update(blocks_applied=sum(v for k, v in kinds.items() if k.startswith(prefix)),
                          region_before_sha256=applied.get('input_sha256'), region_after_sha256=applied.get('output_sha256'))
        report_all = {'precondition_failed': applied.get('precondition_failed', 0), 'layers': len(layers)}
        for report in reports.values():
            report['apply'] = report_all
        phase.lap('decorate_apply')
    import regional_facades
    import regional_ground
    if 'trees' in reports:
        regional_trees.record(delivered, reports['trees'])
    if 'facades' in reports:
        regional_facades.record(delivered, reports['facades'])
    if 'ground' in reports:
        regional_ground.record(delivered, reports['ground'])
    if reports:
        restamp(receipts, delivered, **reports)


def cover_ground(tile, frame, delivered, root, failures, surface):
    """Keyless ground-cover layer (paving, soil, fields, prairie, wetland); None on failure."""
    import regional_ground
    imagery = root / 'naip-imagery'
    try:
        if not (imagery / 'imagery.json').exists():  # sparse and LQ tiles: the scan did not fetch it
            if imagery.exists():
                shutil.rmtree(imagery)
            from naip_imagery import acquire as imagery_acquire
            imagery_acquire(root / 'sources', imagery)
        return regional_ground.cover(tile, frame, delivered, root / 'sources', imagery, root / 'ground', surface)
    except DiskGuardError:
        raise
    except Exception as error:  # noqa: BLE001 - the tile still delivers with grass ground
        failures['ground'] = error
        return None


def paint_facades(tile, frame, delivered, root, failures, indexes=None, surface=None):
    """Keyless facade layer (floors, windows, door, wall material) for the delivered world; None on failure."""
    import regional_facades
    try:
        assessor = None
        if indexes is not None:
            try:  # Cook County only; the index is published under sources/indexes/
                assessor = indexes.ensure(regional_facades.ASSESSOR_INDEX)
            except IndexUnavailable:
                pass
        return regional_facades.paint(tile, frame, delivered, root / 'sources', root / 'facades', assessor=assessor,
                                      surface=surface, defer=True)
    except DiskGuardError:
        raise
    except Exception as error:  # noqa: BLE001 - the tile still delivers without facade detail
        failures['facades'] = error
        return None


def published_lq(reader, tile, world):
    """The tile's published LQ world (region and manifest) written to world, with its geometry receipt; None
    when the tile is not published as LQ or anything fails to verify (the caller then builds it)."""
    from region_pack import unpack
    base = tile_prefix(tile['id'])
    try:
        info = reader.info([f'{base}/tile.json'])
        if not info:
            return None
        manifest = json.loads(reader.read(f'{base}/tile.json', limit=4 * MiB, expected=info[f'{base}/tile.json']))
        files = {item['role']: item for item in manifest.get('files', [])}
        stage = manifest.get('stages', {}).get('geometry')
        if manifest.get('quality') != 'LQ' or stage is None or 'region' not in files or 'world-manifest' not in files:
            return None
        region = files['region']
        raw = reader.read(region['path'], limit=64 * MiB)
        if region.get('decoded'):
            raw = unpack(raw)
            if hashlib.sha256(raw).hexdigest() != region['decoded']['sha256']:
                return None
        world_manifest = reader.read(files['world-manifest']['path'], limit=4 * MiB)
        receipt_raw = reader.read(stage['receipt'], limit=MiB)
        if hf_store.hash_bytes(receipt_raw).sha256 != stage['sha256']:
            return None
    except Exception:  # noqa: BLE001 - building the LQ instead is always correct, only slower
        return None
    (world / 'region').mkdir(parents=True, exist_ok=True)
    atomic(world / 'region' / region_name(tile['id']), raw)
    atomic(world / 'earthcraft.json', world_manifest)
    receipt = json.loads(receipt_raw)
    receipt.update(world=str(world.resolve()), world_manifest_sha256=sha(world / 'earthcraft.json'),
                   reused_from=f'{base}/tile.json')
    return receipt


def plant_trees(tile, frame, delivered, root, ctx, failures, surface=None):
    """LiDAR tree layer for the delivered world; None on failure."""
    import regional_trees
    try:
        return regional_trees.plant(tile, frame, delivered, root / 'trees', ctx.get('point_catalog'),
                                    cache=ctx['cache_root'] / 'ept', points=root / 'scan-surfaces' / 'ept-points',
                                    surface=surface, defer=True)
    except DiskGuardError:
        raise
    except Exception as error:  # noqa: BLE001 - the tile still delivers without trees
        failures['trees'] = error
        return None


def restamp(receipts, delivered, **extra):
    """After a pass edits the delivered world, its stage receipt names the region and manifest it now has."""
    stage = next(s for s in ('appearance', 'geometry')
                 if receipts.get(s, {}).get('world') and Path(receipts[s]['world']).resolve() == delivered.resolve())
    receipts[stage].update(regions=region_hashes(delivered), world_manifest_sha256=sha(delivered / 'earthcraft.json'),
                           **extra)


class Worker:
    def __init__(self, args, *, reader=None, store=None):
        self.args, self.stopping = args, False
        self._reader, self._store = reader, store  # injected in tests (fake Hub)
        self.worker_id = args.worker_id
        self.control = args.control.resolve()
        self.scratch = args.scratch.resolve()
        self.cache_root = args.cache_root.resolve() if getattr(args, 'cache_root', None) else self.scratch / 'cache'
        self.gen = self.scratch / 'gen'
        self.hf_scratch = self.gen / f'_hf-{self.worker_id}'
        self.exit_code = 0
        self.events = []
        self.tile_peaks = {}
        self.prefetcher = None

    # -- logging
    def log(self, **event):
        event = {'utc': utc_now(), 'worker': self.worker_id, **event}
        print(json.dumps(event, default=str, sort_keys=True), flush=True)

    # -- setup
    def budget(self):
        a = self.args
        need = a.cache_cap + a.workers * (a.tile_cap + a.batch_cap + a.hf_cap) + CONTROL_ALLOWANCE
        if need > a.footprint_cap:
            raise SystemExit(f'Caps exceed the footprint: cache {human(a.cache_cap)} + {a.workers} x (tile '
                             f'{human(a.tile_cap)} + batch {human(a.batch_cap)} + hf {human(a.hf_cap)}) + control '
                             f'{human(CONTROL_ALLOWANCE)} = {human(need)} > {human(a.footprint_cap)}')

    def setup(self):
        a = self.args
        self.budget()
        self.control.mkdir(parents=True, exist_ok=True)
        self.gen.mkdir(parents=True, exist_ok=True)
        self.cache = SourceCache(self.cache_root, a.cache_cap, None, log=self.log)
        self.footprint = Footprint({'cache': self.cache.used, 'gen': self.gen, 'control': self.control},
                                   a.footprint_cap, a.min_free, volume=self.scratch)
        self.cache.footprint = self.footprint
        regional_cache.activate(self.cache)
        self.footprint.check(0, 'start')  # refuse to start (EX_TEMPFAIL) rather than fill the disk
        self.reader = self._reader or DatasetReader(a.repo)
        hf_python = a.hf_python or os.environ.get('EARTHCRAFT_HF_PYTHON')
        if self._store is not None:
            self.store = self._store
        elif hf_python:
            self.store = StoreClient(python=hf_python, scratch=self.hf_scratch, repo=a.repo, cap=a.hf_cap,
                                     min_free=a.min_free, log=self.log)
        else:
            self.store = None
        templates = a.templates or self.control / 'templates'
        if templates.exists():
            os.environ['EARTHCRAFT_WORLD_TEMPLATES'] = str(templates.resolve())
        chunk_template(ROOT), height_template(ROOT)  # fail fast: every world build needs both
        from material_router import texture_colors
        texture_colors()  # and the Minecraft 1.21.10 client JAR (EARTHCRAFT_CLIENT_JAR) for block colours
        self.frame = json.loads(a.frame.read_text())
        plan_path = self.control / 'plan.json'
        if plan_path.exists():
            self.plan = json.loads(plan_path.read_text())
            if self.plan['frame'] != self.frame:
                raise ValueError('Regional frozen frame changed')
        else:
            self.plan = prepare_scope(self.control, self.frame)
        self.plan_sha = digest(self.plan)
        self.tiles = {tile['id']: tile for tile in self.plan['tiles']}
        self.revision = a.source_revision or os.environ.get('EARTHCRAFT_SOURCE_REVISION') or hf_store.git_revision(ROOT)

    def catalogs(self):
        from regional_arcgrid import localize
        scans = self.control / 'scans' / 'catalog.json'
        points = self.control / 'point-surveys' / 'catalog.json'
        return {'catalog': localize(json.loads(scans.read_text()), self.cache_root) if scans.exists() else {'sources': []},
                'point_catalog': json.loads(points.read_text()) if points.exists() else None,
                'cook_catalog': self.control / 'cook2022' / 'catalog.json',
                'will_catalog': self.control / 'will2021' / 'catalog.json'}

    def retire_lake(self, journal):
        """Open-water plan tiles are published as lake/v1 (built from bathymetry): never lease them."""
        try:
            commit = self.reader.head_revision()
            expected = self.reader.info([LAKE_INDEX], commit).get(LAKE_INDEX)
            if expected is None:
                return
            raw = self.reader.read(LAKE_INDEX, limit=32 * MiB, revision=commit, expected=expected)
            tiles = [tile for tile in json.loads(raw)['tiles'] if tile in self.tiles]
        except Exception as error:  # noqa: BLE001 - the queue still works without it, just slower
            self.log(event='lake_index_unavailable', reason=str(error)[:300])
            return
        retired = journal.retire_tiles(tiles, LAKE_INDEX, hashlib.sha256(raw).hexdigest(), commit)
        self.log(event='lake_tiles_retired', tiles=retired, lake_plan_tiles=len(tiles), commit=commit)

    def reopen_empty_scans(self, journal):
        """Appearance stages retired as 'no buildings' (before trees) become pending for the sparse path."""
        reopened = journal.reopen_stage(2, evidence_suffix='#no-buildings')
        if reopened:
            self.log(event='empty_scans_reopened', tiles=reopened)

    def retire_empty_scans(self, journal, indexes):
        """A tile with no mapped building has nothing for the scan upgrade to add: its LQ is final."""
        open_scans = [row[0] for row in journal.db.execute(
            "SELECT tile FROM jobs WHERE stage=2 AND state IN ('pending','failed')")]
        if not open_scans:
            return
        try:
            local = indexes.footprints()
            record = json.loads(local.with_suffix('.json').read_text())
        except Exception as error:  # noqa: BLE001 - every tile is then scanned, which is only slower
            self.log(event='footprints_unavailable', reason=str(error)[:300])
            return
        empty = empty_tiles([self.tiles[tile] for tile in open_scans if tile in self.tiles], local)
        retired = journal.retire_tiles(empty, f'{INDEX_PREFIX}/{FOOTPRINT_INDEX}.sqlite#no-buildings',
                                       record['sha256'], None, stages=(2,))
        self.log(event='empty_scans_retired', tiles=retired, open_scans=len(open_scans))

    def sweep(self, journal):
        """Remove scratch left by an earlier, interrupted attempt (never a live lease's)."""
        now = time.time()
        for path in sorted(self.gen.iterdir()):
            if path == self.hf_scratch:
                shutil.rmtree(path, ignore_errors=True)
            elif TILE_ID.fullmatch(path.name):
                live = journal.db.execute("SELECT 1 FROM jobs WHERE tile=? AND state='running' AND expires>? "
                                          "AND owner!=? LIMIT 1", (path.name, now, self.worker_id)).fetchone()
                if not live:
                    shutil.rmtree(path, ignore_errors=True)
                    self.log(event='scratch_swept', tile=path.name)

    # -- capacity
    def wait_for_capacity(self, batch):
        waited = False
        while not self.stopping:
            try:
                if self.footprint.breach:
                    self.footprint.clear_breach()
                self.footprint.check(self.args.tile_cap, 'next tile')
                if waited:
                    self.log(event='capacity_available')
                return True
            except DiskGuardError as error:
                if batch.pending:
                    batch.flush()
                    continue
                if not waited:
                    self.log(event='waiting_for_capacity', reason=str(error))
                waited = True
                if self.args.no_wait:
                    return False
                for _ in range(60):
                    if self.stopping:
                        break
                    time.sleep(1)
        return False

    # -- per-tile
    def failure_receipt(self, tile, stage, error):
        directory = self.control / 'failures'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'{tile}-{stage}-{time.time_ns()}.json'
        atomic(path, json.dumps({'tile': tile, 'stage': stage, 'result': 'failed', 'error_type': type(error).__name__,
                                 'reason': str(error)[:500], 'installed': False}).encode())
        return path

    def fail(self, journal, job, stage, error):
        journal.fail({'tile': job['tile'], 'stage': stage, 'token': job['token']},
                     self.failure_receipt(job['tile'], stage, error))

    def fail_tile(self, journal, keeper, job, root, error):
        anchor = job['stages'][0]
        self.fail(journal, job, anchor, error)
        others = [stage for stage in job['stages'] if stage != anchor]
        if others:
            journal.release_tile(job, 1, f'{anchor} failed', stages=others)
        keeper.remove(job)
        shutil.rmtree(root, ignore_errors=True)
        self.log(event='tile_failed', tile=job['tile'], stage=anchor, error_type=type(error).__name__,
                 reason=str(error)[:300])

    def release(self, journal, keeper, job, root, error, event):
        """Return the tile's stages to the queue (capacity or transient error), not a failure."""
        journal.release_tile(job, 300, (type(error).__name__ + ': ' + str(error))[:200])
        keeper.remove(job)
        shutil.rmtree(root, ignore_errors=True)
        self.log(event=event, tile=job['tile'], reason=str(error)[:300])

    def adopt(self, journal, keeper, job):
        """Finish a tile whose package is already verified on the Hub (e.g. after a crash)."""
        base = tile_prefix(job['tile'])
        info = self.reader.info([f'{base}/tile.json'])
        if not info:
            return False
        manifest = json.loads(self.reader.read(f'{base}/tile.json', limit=4 * MiB, expected=info[f'{base}/tile.json']))
        stages = manifest.get('stages', {})
        if (manifest.get('schema') != TILE_SCHEMA or manifest.get('plan_sha256') != self.plan_sha or
                any(stage not in stages for stage in job['stages'])):
            return False
        raws = {stage: self.reader.read(stages[stage]['receipt'], limit=MiB) for stage in job['stages']}
        if any(hf_store.hash_bytes(raw).sha256 != stages[stage]['sha256'] for stage, raw in raws.items()):
            return False
        commit = 'adopted@' + self.reader.head_revision()
        for stage, raw in raws.items():
            journal.finish_remote(job, stage, stages[stage]['receipt'], raw, commit)
        keeper.remove(job)
        self.log(event='tile_adopted', tile=job['tile'], stages=job['stages'], commit=commit)
        return True

    def reserve_grid(self, job, need, poll=5.0):
        """Wait (holding no pins) until the tile's whole ArcGrid member set is reserved; False when stopping."""
        units = self.cache.enqueue(need)
        logged = False
        while not self.cache.try_grant(what=f"tile {job['tile']}"):
            if self.stopping:
                return False
            if not logged:
                self.log(event='tile_waiting_cache', tile=job['tile'], units=len(units), bytes=sum(units.values()))
                logged = True
            time.sleep(poll)
        return True

    def process(self, journal, keeper, batch, job, ctx):
        tile = self.tiles[job['tile']]
        root = self.gen / tile['id']
        started = time.monotonic()
        try:
            try:
                if not self.args.no_adopt and self.adopt(journal, keeper, job):
                    return 'adopted'
            except Exception as error:  # noqa: BLE001 - adoption is an optimisation; generate instead
                self.log(event='adopt_check_failed', tile=tile['id'], reason=str(error)[:200])
            try:  # only the scan upgrade (appearance) reads ArcGrid; an LQ pass reserves nothing
                need = (grid_need(tile, ctx['frame'], ctx.get('catalog') or {'sources': []})
                        if 'appearance' in job['stages'] else {})
            except Exception as error:  # noqa: BLE001 - the tile itself reports a bad grid; just do not reserve
                need = {}
                self.log(event='grid_need_failed', tile=tile['id'], reason=str(error)[:200])
            if need and not self.reserve_grid(job, need):
                self.release(journal, keeper, job, root, CacheCapacityError('stopping while waiting for cache'),
                             'tile_paused_cache')
                return 'paused'
            if root.exists():
                shutil.rmtree(root)
            root.mkdir(parents=True)
            self.cache.begin_tile()
            self.footprint.peak_detail.pop('tile', None)
            self.log(event='tile_start', tile=tile['id'], lane=job['lane'], stages=job['stages'])
            if self.prefetcher is not None and 'appearance' in job['stages']:
                self.prefetcher.kick(journal, tile['id'])
            phase = Phases()
            with self.footprint.watch(interval=self.args.watch_seconds, extra={'tile': (root, self.args.tile_cap)}):
                receipts, failures, delivered, lq = generate_tile(tile, job, root, ctx, phase)
                self.footprint._record(0, self.footprint.free(), {'tile': regional_cache.entry_bytes(root)})
                if self.footprint.breach:
                    raise (TileCapacityError if self.footprint.breach_kind == 'tile' else DiskGuardError)(
                        self.footprint.breach)
                generator = {'revision': self.revision, 'lane': job['lane'], 'worker': self.worker_id,
                             'generated_utc': utc_now(), 'journal_stages': job['stages'],
                             'prior_evidence': job.get('prior', {}), 'seconds': round(time.monotonic() - started, 1),
                             'phase_seconds': phase.seconds}
                notes = [f'appearance failed: {type(e).__name__}: {str(e)[:200]}' for e in failures.values()]
                package = package_tile(tile, root, receipts=receipts, delivered=delivered, lq=lq,
                                       plan_sha256=self.plan_sha, frame_sha256=digest(self.frame), generator=generator,
                                       prefixes={self.cache_root: 'cache:', self.control: 'control:', ROOT: 'repo:'},
                                       notes=notes)
                phase.lap('package')
            for path in sorted(root.iterdir()):  # keep only the package while the batch waits
                if path.name != 'package':
                    shutil.rmtree(path) if path.is_dir() else path.unlink()
            self.tile_peaks[tile['id']] = self.footprint.peak_detail.get('tile', 0)
            batch.add(job, package, failures)
            self.log(event='tile_packaged', tile=tile['id'], quality=package.manifest['quality'],
                     files=len(package.items), bytes=package.bytes, seconds=round(time.monotonic() - started, 1),
                     tile_scratch_peak_bytes=self.tile_peaks[tile['id']], phase_seconds=phase.seconds)
            return 'packaged'
        except CacheCapacityError as error:
            if not self.cache.others_active():  # this tile alone needs more than the cap: fail it, move on
                self.fail_tile(journal, keeper, job, root, error)
                self.footprint.clear_breach()
                return 'capacity'
            # Other running tiles pin the cache (large ArcGrid strips): wait for them, do not fail this tile.
            self.release(journal, keeper, job, root, error, 'tile_paused_cache')
            self.footprint.clear_breach()
            for _ in range(60):
                if self.stopping:
                    break
                time.sleep(1)
            return 'paused'
        except TileCapacityError as error:
            self.fail_tile(journal, keeper, job, root, error)
            self.footprint.clear_breach()
            return 'capacity'
        except DiskGuardError as error:
            if self.footprint.breach_kind == 'tile':  # this tile alone outgrew its scratch cap
                self.fail_tile(journal, keeper, job, root, TileCapacityError(str(error)))
                self.footprint.clear_breach()
                return 'capacity'
            self.release(journal, keeper, job, root, error, 'tile_paused')
            self.footprint.clear_breach()
            return 'paused'
        except IndexUnavailable as error:
            self.release(journal, keeper, job, root, error, 'waiting_for_indexes')
            self.stopping, self.exit_code = True, EX_TEMPFAIL
            return 'paused'
        except Exception as error:  # noqa: BLE001 - recorded as a fenced failure receipt
            if hf_store.is_retryable(error):  # timeouts, resets, 429/5xx: try this tile again later
                self.release(journal, keeper, job, root, error, 'tile_deferred_network')
                return 'deferred'
            if self.args.verbose:
                traceback.print_exc()
            self.fail_tile(journal, keeper, job, root, error)
            return 'failed'
        finally:
            self.cache.end_tile()

    # -- main loop
    def run(self):
        a = self.args
        try:
            self.setup()
        except DiskGuardError as error:
            self.log(event='waiting_for_storage', reason=str(error))
            return EX_TEMPFAIL
        if self.store is None:
            raise SystemExit('No interpreter with huggingface_hub: pass --hf-python or set EARTHCRAFT_HF_PYTHON')
        with (self.control / f'worker-{self.worker_id}.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)

            def stop(signum, _):
                self.stopping = True
            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            journal = Journal(self.control / 'jobs.sqlite', self.plan)
            keeper = LeaseKeeper(self.control / 'jobs.sqlite')
            indexes = IndexSet(self.control, self.cache, self.reader, self.frame, log=self.log)
            batch = Batch(self, journal, keeper)
            try:
                journal.requeue_owner_leases(self.worker_id)
                self.retire_lake(journal)
                if a.footprints and not a.trees:
                    self.retire_empty_scans(journal, indexes)
                elif a.trees:  # an empty tile still has trees: it takes the sparse path (LQ + trees)
                    self.reopen_empty_scans(journal)
                self.sweep(journal)
                ctx = {'frame': self.frame, 'cache_root': self.cache_root, 'indexes': indexes,
                       'footprints': a.footprints, 'trees': a.trees, 'facades': a.facades, 'ground': a.ground,
                       'scan_surfaces': a.scan_surfaces,
                       'sparse_buildings': a.sparse_buildings, 'reader': self.reader,
                       **self.catalogs()}
                if a.prefetch and a.scan_surfaces == 'county':  # ArcGrid strips: nothing to prefetch for EPT
                    self.prefetcher = Prefetcher(self, ctx)
                processed = consecutive = deferred_run = 0
                outcomes = {}
                while not self.stopping and processed < a.limit:
                    if not self.wait_for_capacity(batch):
                        break
                    job = claim_next(journal, self.worker_id, a.lane, tiles=a.tiles, shard=a.shard)
                    if job is None:
                        break
                    keeper.add(job)
                    outcome = self.process(journal, keeper, batch, job, ctx)
                    outcomes[outcome] = outcomes.get(outcome, 0) + 1
                    if outcome not in ('paused', 'deferred'):
                        processed += 1
                    consecutive = consecutive + 1 if outcome == 'failed' else 0
                    deferred_run = deferred_run + 1 if outcome == 'deferred' else 0
                    if consecutive >= 3:
                        batch.flush()
                        raise RuntimeError('Three consecutive tile failures; inspect control/failures/')
                    if deferred_run >= 3:  # the network is down: back off instead of cycling the queue
                        self.log(event='network_backoff', seconds=600)
                        for _ in range(600):
                            if self.stopping:
                                break
                            time.sleep(1)
                        deferred_run = 0
                    if outcome == 'paused' and a.no_wait:
                        break
                    if batch.due():
                        batch.flush()
                batch.flush()
            finally:
                if batch.pending:
                    try:
                        batch.flush()
                    except Exception as error:  # noqa: BLE001 - leases expire; the next run adopts or redoes
                        self.log(event='final_flush_failed', reason=str(error)[:300])
                if self.prefetcher is not None:
                    self.prefetcher.close()
                keeper.close()
                indexes.close()
                journal_summary = journal.summary()
                journal.close()
                self.cache.close()
                shutil.rmtree(self.hf_scratch, ignore_errors=True)
            report = {'event': 'result', 'processed': processed, 'outcomes': outcomes,
                      'commits': batch.commits, 'published_tiles': batch.published,
                      'footprint': self.footprint.report(), 'cache': self.cache.status(),
                      'tile_scratch_peak_bytes': self.tile_peaks, 'summary': journal_summary, 'installed': False}
            self.log(**report)
            return self.exit_code


def claim_next(journal, owner, lane, *, tiles=None, shard=None):
    """Lease the next tile. Lane 'auto' is base-first: LQ (sources + geometry)
    for every tile of the plan, then the scan upgrades (appearance) once no LQ
    work is left. A measured LQ tile takes seconds; a scan upgrade takes
    15-60 minutes and pins gigabytes of ArcGrid cache (docs/PERFORMANCE.md)."""
    lanes = ('lq', 'scans') if lane == 'auto' else (lane,)
    for name in lanes:
        job = journal.claim_tile(owner, name, tiles=tiles, lease_seconds=3600, shard=shard)
        if job is not None:
            return job
    return None


class Batch:
    """Tiles packaged and waiting for one verified commit; journal and scratch follow the commit."""

    def __init__(self, worker, journal, keeper):
        self.worker, self.journal, self.keeper = worker, journal, keeper
        self.pending, self.first, self.commits, self.published = [], None, [], []

    @property
    def bytes(self):
        return sum(entry[1].bytes for entry in self.pending)

    def add(self, job, package, failures):
        self.pending.append((job, package, failures))
        self.first = self.first or time.monotonic()

    def due(self):
        a = self.worker.args
        return bool(self.pending) and (len(self.pending) >= a.batch_tiles or self.bytes >= a.batch_cap or
                                       time.monotonic() - self.first >= a.batch_seconds)

    def flush(self):
        if not self.pending:
            return None
        items = [{key: value for key, value in item.items() if key != 'role'}
                 for _, package, _ in self.pending for item in package.items]
        status = self.status_item()
        if status:
            items.append(status)
        tiles = [job['tile'] for job, _, _ in self.pending]
        label = f'Generate tiles/v2: {len(tiles)} tile(s) ' + ', '.join(tiles[:6]) + (' ...' if len(tiles) > 6 else '')
        for attempt in range(3):
            try:
                result = self.worker.store.push(items, label)
                break
            except Exception as error:  # noqa: BLE001 - hf_store already retried each request
                self.worker.log(event='batch_push_failed', attempt=attempt + 1, reason=str(error)[:400])
                if attempt == 2:
                    raise
                time.sleep(60 * (attempt + 1))
        self.commits += result['commits']
        for job, package, failures in self.pending:
            try:
                self.keeper.check(job)
                for stage in job['stages']:
                    if stage in failures:
                        self.worker.fail(self.journal, job, stage, failures[stage])
                        continue
                    record = package.receipts[stage]
                    commit = result['files'][record['receipt']]['commit']
                    self.journal.finish_remote(job, stage, record['receipt'], receipt_bytes(package, stage), commit)
                self.published.append(job['tile'])
                self.worker.log(event='tile_published', tile=job['tile'], commits=result['commits'],
                                path=tile_prefix(job['tile']))
            except Exception as error:  # noqa: BLE001 - the tile is on the Hub; a later claim adopts it
                self.worker.log(event='journal_finish_failed', tile=job['tile'], reason=str(error)[:300])
            finally:
                self.keeper.remove(job)
                shutil.rmtree(self.worker.gen / job['tile'], ignore_errors=True)
        if status:
            atomic(self.worker.control / 'status-pushed.json', json.dumps(time.time()).encode())
            Path(status['local']).unlink(missing_ok=True)
        self.pending, self.first = [], None
        return result

    def status_item(self):
        """At most hourly, ride a host status file (EARTHCRAFT_STATUS_FILE) along in this tile commit.

        The file is snapshotted into tile scratch so the hash matches what is
        committed; it never adds a commit of its own.
        """
        source = os.environ.get('EARTHCRAFT_STATUS_FILE')
        if not source or not Path(source).is_file():
            return None
        marker = self.worker.control / 'status-pushed.json'
        try:
            if time.time() - float(json.loads(marker.read_text())) < STATUS_INTERVAL:
                return None
        except (OSError, ValueError):
            pass
        data = Path(source).read_bytes()
        if len(data) > 64 * 1024:
            return None
        self.worker.footprint.check(len(data), 'status snapshot')
        local = self.worker.gen / '_status-pipeline.json'
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        shard = self.worker.args.shard
        path = STATUS_PATH if shard is None else f'status/pipeline-{shard[0]}-of-{shard[1]}.json'
        return {'path': path, 'local': str(local), 'sha256': hashlib.sha256(data).hexdigest(),
                'bytes': len(data)}


# --------------------------------------------------------------------------
# CLI


def status(args):
    control = args.control.resolve()
    scratch = args.scratch.resolve()
    footprint = Footprint({'cache': scratch / 'cache', 'gen': scratch / 'gen', 'control': control},
                          args.footprint_cap, args.min_free, volume=scratch)
    result = {'footprint': {'used_bytes': footprint.used(), 'free_bytes': footprint.free(),
                            'cap_bytes': footprint.cap, 'floor_bytes': footprint.floor}}
    try:
        footprint.check(args.tile_cap, 'next tile')
        result['would_start'] = True
    except DiskGuardError as error:
        result['would_start'], result['reason'] = False, str(error)
    if (control / 'jobs.sqlite').exists():
        import sqlite3
        with closing(sqlite3.connect(control / 'jobs.sqlite', timeout=20)) as db:  # WAL journal: a plain reader
            result['journal'] = [{'stage': STAGES[s], 'state': state, 'count': n} for s, state, n in db.execute(
                'SELECT stage,state,count(*) FROM jobs GROUP BY stage,state ORDER BY stage,state')]
    print(json.dumps(result, indent=1))
    return 0


def seed_control(args):
    """Start a control directory from the archived worker state (plan, journal, catalogs; ~75 MB).

    Existing files are never overwritten. The archived journal's completed
    stages point at receipts that died with the external drive; claim_tile()
    accepts them as legacy and the one-pass worker regenerates every input.
    """
    control = args.control.resolve()
    reader = DatasetReader(args.repo)
    paths = [f'{LEGACY_CONTROL}/{name}' for name in SEED_FILES]
    info = reader.info(paths)
    footprint = Footprint({'control': control}, CONTROL_ALLOWANCE, args.min_free, volume=control.parent)
    copied = []
    for name, path in zip(SEED_FILES, paths):
        target = control / name
        if target.exists() or path not in info:
            continue
        footprint.check(info[path].size, f'seed {name}')
        reader.download(path, target, expected=info[path])
        copied.append(name)
    frame = json.loads(args.frame.read_text())
    if (control / 'plan.json').exists() and json.loads((control / 'plan.json').read_text())['frame'] != frame:
        raise ValueError('The archived plan uses another frame')
    print(json.dumps({'event': 'control_seeded', 'copied': copied, 'source': LEGACY_CONTROL}), flush=True)
    return 0


def prepare(args):
    """Discover the scan, point-survey and original-LAS catalogs into control (small files only)."""
    control = args.control.resolve()
    control.mkdir(parents=True, exist_ok=True)
    frame = json.loads(args.frame.read_text())
    plan = json.loads((control / 'plan.json').read_text()) if (control / 'plan.json').exists() else prepare_scope(control, frame)
    discover(control / 'scans')
    from regional_arcgrid import prepare as prepare_grid_archives
    prepare_grid_archives(control)
    from regional_cook_points import prepare as prepare_original_cook
    prepare_original_cook(control)
    from regional_will_points import prepare as prepare_original_will
    prepare_original_will(control)
    from regional_point_catalog import discover as discover_points
    discover_points(plan, control / 'point-surveys')
    Journal(control / 'jobs.sqlite', plan).close()
    print(json.dumps({'tiles': len(plan['tiles']), 'area_km2': plan['city_area_m2'] / 1e6}), flush=True)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--control', type=Path, default=ROOT / 'runs/regional/control')
    parser.add_argument('--frame', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, default=ROOT / 'runs/scratch',
                        help='parent of cache/ and gen/ (default runs/scratch)')
    parser.add_argument('--repo', default=hf_store.DEFAULT_REPO)
    parser.add_argument('--cache-root', type=Path, default=None,
                        help='source cache directory (default SCRATCH/cache); several runs may share one')
    parser.add_argument('--hf-python', type=Path, default=None,
                        help='interpreter with huggingface_hub for hf_store (or EARTHCRAFT_HF_PYTHON)')
    parser.add_argument('--source-revision', default=None, help='generator revision recorded in tile.json')
    parser.add_argument('--templates', type=Path, default=None,
                        help='world templates directory (default control/templates, else the repository worlds/)')
    parser.add_argument('--cache-cap', type=parse_size, default=12 * GiB)
    parser.add_argument('--footprint-cap', type=parse_size, default=25 * GiB,
                        help='cache + gen scratch + control (default 25GiB)')
    parser.add_argument('--min-free', type=parse_size, default=40 * GiB, help='free-space floor (default 40GiB)')
    parser.add_argument('--tile-cap', type=parse_size, default=4 * GiB, help='scratch cap of one tile (4GiB)')
    parser.add_argument('--batch-cap', type=parse_size, default=GiB, help='packaged bytes waiting to commit (1GiB)')
    parser.add_argument('--hf-cap', type=parse_size, default=GiB, help='hf_store scratch cap per worker (1GiB)')
    parser.add_argument('--workers', type=int, default=1, help='concurrent workers sharing the footprint (1-2)')
    parser.add_argument('--batch-tiles', type=int, default=6, help='tiles per commit (6)')
    parser.add_argument('--batch-seconds', type=float, default=1200, help='commit a partial batch after this (1200)')
    parser.add_argument('--watch-seconds', type=float, default=5)
    parser.add_argument('--lane', choices=('auto', 'lq', 'base', 'scans', 'quality'), default='base',
                        help="auto: LQ for the whole plan first, then scan upgrades; lq: sources+geometry only; "
                             "base: sources+geometry+appearance per tile; scans: appearance only")
    parser.add_argument('--worker-id', default='base-1')
    parser.add_argument('--tiles', type=lambda v: [t for t in v.split(',') if t], default=None)
    parser.add_argument('--limit', type=int, default=1_000_000)
    parser.add_argument('--shard', type=parse_shard, default=parse_shard(os.environ.get('EARTHCRAFT_SHARD')),
                        help='i/N: claim only this machine\'s deterministic share of the plan (4-tile '
                             'east-west bands, band mod N); default EARTHCRAFT_SHARD or all tiles')
    parser.add_argument('--footprints', action='store_true', help='associate scans with Overture footprints')
    parser.add_argument('--trees', action='store_true', help='plant LiDAR trees into every delivered tile')
    parser.add_argument('--facades', action='store_true', help='keyless facade detail in every delivered tile')
    parser.add_argument('--ground', action='store_true', help='keyless ground cover in every delivered tile')
    parser.add_argument('--sparse-buildings', type=int, default=SPARSE_BUILDINGS,
                        help='tiles with at most this many mapped footprints skip the scan (default %(default)s)')
    parser.add_argument('--scan-surfaces', choices=('ept', 'county'), default='ept',
                        help='scan DTM/DSM from the newest USGS EPT survey (default) or county rasters')
    parser.add_argument('--nice', type=int, default=10, help='raise niceness by this much (default 10)')
    parser.add_argument('--no-wait', action='store_true', help='exit instead of pausing when the guard refuses')
    parser.add_argument('--no-adopt', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--no-prefetch', dest='prefetch', action='store_false',
                        help='do not fetch the next tiles\' ArcGrid members in the background')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--route-locality', action='store_true',
                      help='rank pending tiles so tiles sharing source files run together (idempotent)')
    mode.add_argument('--status', action='store_true')
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--index-coverage', action='store_true')
    mode.add_argument('--clear-cache', action='store_true')
    mode.add_argument('--seed-control', action='store_true', help='copy the archived plan/journal/catalogs once')
    mode.add_argument('--requeue-failed', action='store_true',
                      help='return failed sources/geometry/appearance stages (of --tiles, or all) to the queue')
    parser.add_argument('--index-dir', type=Path, default=None,
                        help='--index-coverage: read local index copies instead of the cache')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not 1 <= args.workers <= 8:
        raise SystemExit('Use between one and eight workers')
    if args.nice:
        os.nice(args.nice)
    if args.status:
        return status(args)
    if args.route_locality:
        from regional_arcgrid import localize
        control = args.control.resolve()
        plan = json.loads((control / 'plan.json').read_text())
        scans = control / 'scans' / 'catalog.json'
        catalog = localize(json.loads(scans.read_text()), args.scratch.resolve() / 'cache') if scans.exists() else {'sources': []}
        apply_locality_route(control, plan, plan['frame'], catalog, scan_surfaces=args.scan_surfaces)
        return 0
    if args.prepare:
        return prepare(args)
    if args.seed_control:
        return seed_control(args)
    if args.requeue_failed:
        control = args.control.resolve()
        journal = Journal(control / 'jobs.sqlite', json.loads((control / 'plan.json').read_text()))
        try:
            changed = journal.requeue_failed(stages=('sources', 'geometry', 'appearance'), tiles=args.tiles)
        finally:
            journal.close()
        print(json.dumps({'event': 'requeued', 'stages': changed}))
        return 0
    if args.clear_cache:
        cache = SourceCache(args.scratch.resolve() / 'cache', args.cache_cap)
        remaining = cache.clear()
        cache.close()
        print(json.dumps({'cache_bytes': remaining}))
        return 0
    if args.index_coverage:
        control = args.control.resolve()
        frame = json.loads(args.frame.read_text())
        plan = json.loads((control / 'plan.json').read_text())
        if plan['frame'] != frame:
            raise ValueError('Regional frozen frame changed')
        cache = None
        if args.index_dir is None:
            footprint = Footprint({'cache': args.scratch.resolve() / 'cache'}, args.footprint_cap, args.min_free)
            cache = regional_cache.activate(SourceCache(args.scratch.resolve() / 'cache', args.cache_cap, footprint))
        indexes = IndexSet(control, cache, DatasetReader(args.repo), frame, index_dir=args.index_dir)
        print(json.dumps(index_coverage(control, plan, frame, indexes)))
        return 0
    return Worker(args).run()


if __name__ == '__main__':
    raise SystemExit(main())
