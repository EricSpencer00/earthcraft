"""Restartable Chicagoland base and scan lanes in the existing Chicago frame.

Each immutable tile is verified separately. Queued/generated tiles are never
reported as installed, and existing Chicago observations are not downgraded.
Control journals must use the system disk; bulk artifacts use mounted LaCie.
"""
import argparse
from contextlib import ExitStack
import fcntl
import faulthandler
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import time
import urllib.parse
import urllib.error

import numpy as np
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform

from aws_terrain import prepare as terrain
from chicago_tiles import Journal, boundary_geometry, digest, tile_plan
from metric_way_index import MetricWayIndex, build_index
from metric_world import build
from municipal_generate import presentation_ways
from region_expansion import atomic, sha
from regional_scans import acquire, discover, frozen_get
from verify_metric_world import verify
from regional_store import compact, materialized, region_hashes
from regional_sources import validate as validate_sources
from worker_lease import heartbeat

_SCAN_COVERAGES = {}


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


def indexes(control, bulk, frame, illinois):
    sources = {'illinois': Path(illinois)}
    for state in ('indiana', 'wisconsin'):
        path = bulk/'osm'/(state+'.osm.pbf')
        frozen_get('https://download.geofabrik.de/north-america/us/'+state+'-latest.osm.pbf',
                   path, limit=1024*2**20)
        sources[state] = path
    result = []
    for state, source in sources.items():
        path = control/(state+'-ways.sqlite')
        if not path.exists():
            build_index(source, path, frame['crs'], max_bytes=4*2**30)
        result.append((path, source))
    return result


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


def make_source(tile, frame, root, readers, cache):
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
        osm_inputs=[reader.meta for reader in readers],
        missing_layers=['multipolygon relations', 'complete land cover', 'facade photography'])
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


def scan_tile(tile, frame, root, pair, point_catalog=None, point_cache=None,cook_catalog=None,will_catalog=None):
    with ExitStack() as stack:
        for candidate in root.glob('world*'):
            if candidate.name!='world' and candidate.is_dir() and not any(word in candidate.name for word in ('building','incomplete')):
                stack.enter_context(materialized(candidate))
        return _scan_tile(tile,frame,root,pair,point_catalog,point_cache,cook_catalog,will_catalog)


def _scan_tile(tile, frame, root, pair, point_catalog=None, point_cache=None,cook_catalog=None,will_catalog=None):
    if pair is None:
        if point_catalog is not None:
            result=point_only_tile(tile,frame,root,point_catalog,point_cache)
            if result is not None:return result
        return {'tile': tile['id'], 'stage': 'appearance', 'result': 'pass',
            'scan_status': 'No usable paired county scan; retain LQ', 'quality': 'LQ', 'installed': False}
    source = root/'sources'
    scan_source = root/'scan-sources'
    surfaces = root/'scan-surfaces'
    world = root/'world.scanned'
    if not (scan_source/'sources.json').exists():
        with np.load(source/'rasters.npz') as rasters:
            elevation, record = acquire(pair, json.loads((source/'sources.json').read_text()),
                                         surfaces, rasters['elevation'])
            cover = rasters['cover'].copy()
        shutil.copytree(source, scan_source, ignore=shutil.ignore_patterns('._*'))
        np.savez_compressed(scan_source/'rasters.npz', elevation=elevation, cover=cover)
        update_elevation(scan_source/'elevation.tif', elevation)
        meta = json.loads((scan_source/'sources.json').read_text())
        meta.update(elevation_source=f"{pair['county']} {pair['year']} LiDAR DTM; explicit LQ coverage gaps",
            elevation_vertical_datum=pair['dtm']['vertical_reference'], scan_surface_receipt=record,
            elevation_sha256=sha(scan_source/'elevation.tif'))
        if os.environ.get('EARTHCRAFT_BUILDING_INDEX'):
            from regional_footprints import crop as footprint_crop
            meta['scan_footprint_receipt']=footprint_crop(os.environ['EARTHCRAFT_BUILDING_INDEX'],meta,scan_source/'scan-footprints.geojson')
        atomic(scan_source/'sources.json', json.dumps(meta).encode())
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
            for survey in candidates(tile,frame,point_catalog):
                point_root=root/'point-crops'/survey['project']
                try:
                    if (point_root/'manifest.json').exists():
                        record=json.loads((point_root/'manifest.json').read_text())
                        if sha(point_root/'points.npz')!=record['points_sha256']:
                            raise ValueError('Original full-density crop changed')
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
    # Determine point coverage first, then serialize the final geometry once.
    # The immutable LQ baseline is used only at installation, not read back here.
    if not world.exists():
        staging=world.with_name(world.name+'.building')
        if staging.exists():staging.rename(root/(world.name+'.incomplete-'+str(time.time_ns())))
        build(build_source,staging,surface_source=surfaces,point_source=points,world_frame=frame)
        checks=verify(staging);staging.rename(world)
    else:checks=verify(world)
    meta=json.loads((world/'earthcraft.json').read_text())
    imagery_result = None
    if meta.get('roof_source_cells',0):
        from naip_imagery import acquire as imagery_acquire
        from roof_color import apply as paint_roofs
        imagery=root/'naip-imagery'; painted=root/'world.scan-roof-colour'
        try:
            if not (imagery/'imagery.json').exists():
                if imagery.exists():imagery.rename(root/f'naip.incomplete-{time.time_ns()}')
                imagery_acquire(Path(meta['dem_path']).parent,imagery,preferred_year=pair['year'])
            if not painted.exists():
                imagery_result=paint_roofs(world,imagery,painted)
            else:
                imagery_result=json.loads((painted/'roof-colour.json').read_text());verify(painted)
            world=painted;checks=json.loads((painted/'block-verification.json').read_text())
        except (ValueError, urllib.error.URLError, TimeoutError) as error:
            imagery_result={'result':'unavailable','reason':str(error)}
    return {'tile': tile['id'], 'stage': 'appearance', 'result': 'pass', 'quality': quality,
        'world': str(world.resolve()), 'world_manifest_sha256': sha(world/'earthcraft.json'),
        'regions': {path.name: sha(path) for path in (world/'region').glob('r.*.*.mca')},
        'roof_source_cells': meta['roof_source_cells'], 'scan_source': pair,
        'checks': checks, 'facade_measured': False, 'raw_points_acquired': quality=='scan-points-and-roof',
        'point_survey_attempts': attempts, 'roof_appearance': imagery_result, 'installed': False}


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


def update_elevation(path, array):
    import rasterio
    with rasterio.open(path, 'r+') as raster:
        raster.write(array.astype(np.float32), 1)


def run(args):
    control, bulk = args.control.resolve(), args.bulk.resolve()
    if not Path('/Volumes/LaCie').is_mount() or not bulk.is_relative_to('/Volumes/LaCie/Earthcraft'):
        raise ValueError('Regional bulk writes require the mounted mini LaCie; no internal fallback')
    control.mkdir(parents=True, exist_ok=True); bulk.mkdir(parents=True, exist_ok=True)
    with (control/('worker-'+args.worker_id+'.lock')).open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        faulthandler.register(signal.SIGUSR1,file=sys.stderr,all_threads=True)
        frame = json.loads(args.frame.read_text())
        if os.environ.get('EARTHCRAFT_BUILDING_INDEX'):
            index=Path(os.environ['EARTHCRAFT_BUILDING_INDEX']);record=json.loads(index.with_suffix('.json').read_text())
            if sha(index)!=record['sha256'] or record['crs']!=frame['crs']:
                raise ValueError('Configured regional footprint index changed')
        if (control/'plan.json').exists():
            plan = json.loads((control/'plan.json').read_text())
            if plan['frame'] != frame:
                raise ValueError('Regional frozen frame changed')
        else:
            plan = prepare_scope(control, frame)
        if args.prepare:
            discover(control/'scans')
            from regional_arcgrid import prepare as prepare_grid_archives
            prepare_grid_archives(control,bulk)
            from regional_cook_points import prepare as prepare_original_cook
            prepare_original_cook(control)
            from regional_will_points import prepare as prepare_original_will
            prepare_original_will(control)
            from regional_point_catalog import discover as discover_points
            discover_points(plan,control/'point-surveys')
            entries = indexes(control, bulk, frame, args.illinois)
            atomic(control/'indexes.json', json.dumps([[str(a), str(b)] for a,b in entries]).encode())
            journal = Journal(control/'jobs.sqlite', plan); journal.close()
            print(json.dumps({'tiles': len(plan['tiles']), 'area_km2': plan['city_area_m2']/1e6}), flush=True)
            return
        readers = []
        with ExitStack() as stack:
            for path, source in json.loads((control/'indexes.json').read_text()):
                reader = MetricWayIndex(path, source, frame['crs']); stack.callback(reader.close); readers.append(reader)
            journal = Journal(control/'jobs.sqlite', plan); stack.callback(journal.close)
            journal.requeue_owner_leases(args.worker_id)
            catalog = json.loads((control/'scans/catalog.json').read_text())
            point_catalog = json.loads((control/'point-surveys/catalog.json').read_text()) if args.lane=='scans' else None
            tiles = {tile['id']: tile for tile in plan['tiles']}
            stopping = False
            def stop(signum, _):
                nonlocal stopping
                stopping = True
            signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
            processed = 0
            consecutive_failures = 0
            while not stopping and processed < args.limit:
                if shutil.disk_usage(bulk).free < args.reserve_gib*2**30:
                    raise ValueError('Regional bulk reserve reached; preserve existing artifacts')
                if shutil.disk_usage(control).free < 12*2**30:
                    raise ValueError('Regional control disk reserve reached')
                job = journal.claim('appearance' if args.lane == 'scans' else 'geometry',
                                    args.worker_id, lease_seconds=3600)
                if job is None and args.lane == 'base':
                    job = journal.claim('sources', args.worker_id, lease_seconds=3600)
                if job is None:
                    if args.lane == 'scans' and any(row['state'] != 'complete' and row['stage'] in ('sources', 'geometry')
                                                  for row in journal.summary()):
                        time.sleep(15); continue
                    break
                tile = tiles[job['tile']]; root = bulk/'tiles'/tile['id']; root.mkdir(parents=True, exist_ok=True)
                print(json.dumps({'tile': tile['id'], 'lane': args.lane, 'stage': job['stage']}), flush=True)
                try:
                    with heartbeat(control/'jobs.sqlite', job, ('sources','geometry','appearance','game_verify').index(job['stage'])):
                        if job['stage'] == 'sources':
                            make_source(tile, frame, root, readers, bulk/'terrain-cache')
                            receipt_file = root/'sources-receipt.json'
                        elif job['stage'] == 'geometry':
                            validate_sources(root, tile)
                            world = root/'world'
                            if not world.exists():
                                staging = root/'world.building'
                                if staging.exists(): staging.rename(root/f'world.incomplete-{time.time_ns()}')
                                build(root/'sources', staging, world_frame=frame)
                                checks = verify(staging); staging.rename(world)
                            else:
                                with materialized(world):checks = verify(world)
                            receipt = {'tile': tile['id'], 'stage': 'geometry', 'result': 'pass',
                                'world': str(world.resolve()), 'quality': 'LQ', 'installed': False,
                                'world_manifest_sha256': sha(world/'earthcraft.json'),
                                'regions': region_hashes(world), 'checks': checks}
                            atomic(root/'geometry-receipt.json', json.dumps(receipt).encode())
                            compact(world, expected=receipt['regions'])
                            receipt_file = root/'geometry-receipt.json'
                        else:
                            receipt = scan_tile(tile, frame, root, select_pair(tile, frame, catalog),
                                                point_catalog, bulk/'ept-cache',control/'cook2022/catalog.json',control/'will2021/catalog.json')
                            atomic(root/'scan-receipt.json', json.dumps(receipt).encode())
                            for candidate in root.glob('world*'):
                                if candidate.is_dir() and not any(word in candidate.name for word in ('building','incomplete')):
                                    expected = receipt.get('regions') if str(candidate.resolve()) == receipt.get('world') else None
                                    compact(candidate, expected=expected)
                            receipt_file = root/'scan-receipt.json'
                    journal.finish(job, receipt_file)
                    consecutive_failures = 0
                except Exception as error:
                    failure_file = root/('failure-'+job['stage']+'-'+str(time.time_ns())+'.json')
                    atomic(failure_file, json.dumps({'tile': tile['id'], 'stage': job['stage'],
                        'result': 'failed', 'error_type': type(error).__name__, 'reason': str(error)[:500],
                        'installed': False}).encode())
                    journal.fail(job, failure_file)
                    print(json.dumps({'tile': tile['id'], 'result': 'failed', 'error_type': type(error).__name__}), flush=True)
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        raise RuntimeError('Three consecutive tile failures; inspect retained evidence') from error
                processed += 1
            print(json.dumps({'processed': processed, 'summary': journal.summary(), 'installed': False}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--bulk', type=Path, required=True)
    parser.add_argument('--frame', type=Path, required=True)
    parser.add_argument('--illinois', type=Path, required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--lane', choices=('base', 'scans'), default='base')
    parser.add_argument('--worker-id', default='base-1')
    parser.add_argument('--limit', type=int, default=1_000_000)
    parser.add_argument('--reserve-gib', type=int, default=150)
    run(parser.parse_args())
