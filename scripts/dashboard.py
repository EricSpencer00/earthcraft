"""Loopback-only, read-only Earthcraft progress server. No inference or external assets."""
import argparse
from datetime import datetime, timezone
import json
import math
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import psutil
import nbtlib as n
from local_paths import bulk_path
from progress_snapshot import build_snapshot
from world_border import bounds_for_plan

REPO = Path(__file__).resolve().parents[1]
ROOT = bulk_path('chicago')
STAGES = ('city-draft', 'loop-pilot')
CACHE = {}
REGION_CACHE = {}
CHUNK_CACHE = {}
PLAYABLE_CACHE = {'exchange': None, 'receipts': set(), 'chunks': set()}
LOCK = threading.Lock()
PLAN_PATH = REPO / 'runs/chicago-adaptation-city-001/plan.json'
INSTALLED_COVERAGE_PATH = REPO / 'runtime/traversal/saves/Earthcraft/city-coverage.json'
LIVE_EXCHANGE = REPO / 'runs/chicago-live-001'

def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}

def tail(path, limit=32000):
    try:
        with path.open('rb') as f:
            f.seek(max(0, path.stat().st_size-limit))
            return f.read(limit).decode('utf-8', errors='replace')
    except OSError:
        return ''


def _journal_states():
    """Read the generation lease journal without participating in its writes."""
    path = REPO / 'runs/chicago-adaptation-city-001/jobs.sqlite'
    empty = ({}, {'leased_tiles': 0, 'worker_owners': 0, 'superseded_leases': 0,
                  'deferred_tiles': 0, 'deferrals': 0})
    if not path.exists():
        return empty
    try:
        connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        rows = connection.execute(
            'SELECT tile,stage,state,expires,owner,COALESCE(not_before,0),COALESCE(deferrals,0) FROM jobs').fetchall()
        connection.close()
    except sqlite3.Error:
        return empty
    states = {}; leased_tiles = 0; owners = set(); deferred_tiles = 0; deferrals = 0
    now = time.time()
    for tile, stage, state, expires, owner, not_before, tile_deferrals in rows:
        deferrals += int(tile_deferrals)
        if state == 'running' and expires is not None and float(expires) <= now:
            state = 'pending'
        elif state == 'running':
            leased_tiles += 1
            if owner: owners.add(owner)
        elif state == 'pending' and float(not_before) > now:
            deferred_tiles += 1
        states.setdefault(tile, {})[int(stage)] = state
    return states, {'leased_tiles': leased_tiles, 'worker_owners': len(owners),
                    'superseded_leases': max(0, leased_tiles-len(owners)),
                    'deferred_tiles': deferred_tiles, 'deferrals': deferrals}


def _coverage_rows(path):
    report = read_json(path)
    tiles = report.get('tiles', {})
    if isinstance(tiles, dict):
        return list(tiles.items())
    return [(tile.get('id', str(index)), tile) for index, tile in enumerate(tiles)]


def _tile_report(tile_id, tile):
    candidates = []
    source_world = tile.get('source_world')
    if source_world:
        candidates.append(Path(source_world) / 'earthcraft.json')
    candidates.extend([
        ROOT / 'city-tiles-001' / tile_id / 'world/earthcraft.json',
        REPO / 'runs/chicago-city-tiles-001' / tile_id / 'world/earthcraft.json',
        REPO / 'runs/chicago-adaptation-city-001' / 'tiles' / tile_id / 'world/earthcraft.json',
    ])
    for path in candidates:
        report = read_json(path)
        if report:
            return report
    return {}


def _border_snapshot():
    """Compare both installed records with the frozen Chicago plan."""
    try:
        expected = bounds_for_plan(read_json(PLAN_PATH))
        level = n.load(REPO / 'runtime/traversal/saves/Earthcraft/level.dat')['Data']
        runtime = n.load(REPO / 'runtime/traversal/saves/Earthcraft/data/world_border.dat')['data']
        runtime_values = ((runtime.get('center_x'), expected['center_x']),
                          (runtime.get('center_z'), expected['center_z']),
                          (runtime.get('size'), expected['size']))
        legacy_values = ((level.get('BorderCenterX'), expected['center_x']),
                         (level.get('BorderCenterZ'), expected['center_z']),
                         (level.get('BorderSize'), expected['size']))
        # Minecraft 1.21.10 persists the overworld border in data/world_border.dat
        # and removes the legacy level.dat fields on load. If legacy fields are
        # present, still require all three to agree rather than hiding a split.
        runtime_synced = all(value is not None and abs(float(value)-target)<1e-6
                             for value,target in runtime_values)
        legacy_present = [value is not None for value,_ in legacy_values]
        legacy_synced = not any(legacy_present) or (all(legacy_present) and all(
            abs(float(value)-target)<1e-6 for value,target in legacy_values))
        synced = runtime_synced and legacy_synced
        return {'synced': synced, 'basis': 'full planned Chicago envelope', 'expected': {
            'center_x': expected['center_x'], 'center_z': expected['center_z'], 'size': expected['size']}}
    except Exception:
        return None


def _extent(rows):
    rectangles = []
    for _, tile in rows:
        offset = tile.get('world_offset_xz')
        size = tile.get('size_m', tile.get('size'))
        if offset and size is not None:
            rectangles.append((int(offset[0]), int(offset[1]), int(size)))
    if not rectangles:
        return None
    left = min(x for x, _, _ in rectangles)
    top = min(z for _, z, _ in rectangles)
    right = max(x + size for x, _, size in rectangles)
    bottom = max(z + size for _, z, size in rectangles)
    return {'left_block': left, 'top_block': top, 'right_block': right,
            'bottom_block': bottom, 'width_blocks': right-left, 'height_blocks': bottom-top}


def _playable_chunks(exchange=LIVE_EXCHANGE):
    """Index exact protected and successfully applied live Minecraft chunks."""
    exchange = Path(exchange)
    binding = read_json(exchange / 'binding.json')
    receipt_dir = exchange / 'receipts'
    names = {path.name for path in receipt_dir.glob('*.json')} if receipt_dir.exists() else set()
    with LOCK:
        if PLAYABLE_CACHE['exchange'] != str(exchange.resolve()):
            PLAYABLE_CACHE.update(exchange=str(exchange.resolve()), receipts=set(), chunks=set())
        chunks = PLAYABLE_CACHE['chunks']
        if not PLAYABLE_CACHE['receipts']:
            for value in binding.get('protected_chunks', []):
                if re.fullmatch(r'-?\d+,-?\d+', value):
                    chunks.add(tuple(map(int, value.split(','))))
        pending = names - PLAYABLE_CACHE['receipts']
    admitted = []
    for name in pending:
        receipt = read_json(receipt_dir / name)
        value = receipt.get('chunk', '')
        if (receipt.get('mode') == 'new_chunk' and receipt.get('result') == 'applied_in_memory'
                and re.fullmatch(r'-?\d+,-?\d+', value)):
            admitted.append(tuple(map(int, value.split(','))))
    with LOCK:
        PLAYABLE_CACHE['chunks'].update(admitted)
        PLAYABLE_CACHE['receipts'].update(pending)
        return set(PLAYABLE_CACHE['chunks']), len(PLAYABLE_CACHE['receipts'])


def _live_import_snapshot(exchange=LIVE_EXCHANGE):
    """Report the bounded handoff health separately from build progress."""
    exchange = Path(exchange)
    status_path = exchange / 'status.json'
    status = read_json(status_path)
    try:
        age = max(0, round(time.time() - status_path.stat().st_mtime))
    except OSError:
        age = None
    try:
        pending = sum(1 for path in (exchange / 'inbox').glob('*.json.gz') if path.is_file())
    except OSError:
        pending = 0
    state = status.get('state', 'disconnected')
    return {
        'state': state,
        'status_age_seconds': age,
        'pending_chunks': pending,
        'capacity_chunks': 128,
        'needs_minecraft_relaunch': state == 'error',
    }


def chunk_snapshot(area='queue'):
    """Return a dense, privacy-safe state atlas at an area-appropriate scale."""
    with LOCK:
        cached = CHUNK_CACHE.get(area)
        if cached and time.time() - cached[0] < 5:
            return cached[1]

    if area == 'live':
        path = None
        area_label = 'Playable in Minecraft now'
        cell_size = 16
        cell_label = 'Minecraft chunk'
    elif area == 'installed':
        path = INSTALLED_COVERAGE_PATH
        area_label = 'Original installed footprint'
        cell_size = 16
        cell_label = 'Minecraft chunk'
    elif area == 'queue':
        path = PLAN_PATH
        area_label = 'Full Chicago plan'
        cell_size = 256
        cell_label = 'generation tile'
    else:
        raise ValueError('Unknown chunk atlas area')

    journal, activity = _journal_states()
    codes = {'white': 0, 'gray': 1, 'green': 2}
    labels = ['queued', 'LiDAR + surface data in', 'finished building layer or no buildings']
    rectangles = []
    failed = 0
    running = 0
    receipt_count = 0
    if area == 'live':
        playable, receipt_count = _playable_chunks()
        labels = ['not playable', 'waiting for Minecraft', 'playable in Minecraft']
        rectangles = [(cx * 16, cz * 16, 16, 'green') for cx, cz in playable]
    else:
        report = read_json(path)
        if not report:
            raise FileNotFoundError(path)
        rows = _coverage_rows(path) if area == 'installed' else [(tile['id'], tile) for tile in report.get('tiles', [])]
        if not rows:
            raise ValueError('Chunk atlas has no tiles')
        for tile_id, tile in rows:
            offset = tile.get('world_offset_xz')
            size = tile.get('size_m', tile.get('size'))
            if not offset or size is None:
                continue
            x, z, size = int(offset[0]), int(offset[1]), int(size)
            if x % cell_size or z % cell_size or size <= 0 or size % cell_size:
                continue
            stages = journal.get(tile_id, {})
            if stages.get(0) == 'failed':
                failed += 1
            if any(state == 'running' for state in stages.values()):
                running += 1
            source_ready = stages.get(0) == 'complete'
            geometry_ready = stages.get(1) == 'complete'
            appearance_ready = stages.get(2) == 'complete'
            state = 'white'
            if source_ready:
                state = 'gray'
                if appearance_ready:
                    state = 'green'
                elif geometry_ready:
                    tile_report = _tile_report(tile_id, tile)
                    if tile_report and len(tile_report.get('buildings', [])) == 0:
                        state = 'green'
            rectangles.append((x, z, size, state))

    if not rectangles:
        raise ValueError('Chunk atlas has no aligned tiles')
    left = min(x for x, _, _, _ in rectangles) // cell_size
    top = min(z for _, z, _, _ in rectangles) // cell_size
    right = max(x + size for x, _, size, _ in rectangles) // cell_size
    bottom = max(z + size for _, z, size, _ in rectangles) // cell_size
    width = right - left
    height = bottom - top
    # Outside the municipal plan is distinct from a planned cell awaiting data.
    cells = [-1] * (width * height)
    for x, z, size, state in rectangles:
        code = codes[state]
        for row in range(z // cell_size - top, z // cell_size - top + size // cell_size):
            start = row * width + x // cell_size - left
            for column in range(size // cell_size):
                index = start + column
                # Green is the strongest evidence and wins any overlap.
                cells[index] = max(cells[index], code)
    counts = {name: cells.count(code) for name, code in codes.items()}
    plan_report = read_json(PLAN_PATH)
    planned_rows = [(tile['id'], tile) for tile in plan_report.get('tiles', [])]
    installed_rows = _coverage_rows(INSTALLED_COVERAGE_PATH)
    border = _border_snapshot()
    payload = {
        'schema_version': 2,
        'area': {
            'id': area,
            'label': area_label,
            'left_block': left * cell_size,
            'top_block': top * cell_size,
            'width_cells': width,
            'height_cells': height,
            'width_chunks': width * cell_size // 16,
            'height_chunks': height * cell_size // 16,
            'cell_size_blocks': cell_size,
            'cell_label': cell_label,
            'tiles': len(rectangles),
            'receipt_count': receipt_count,
        },
        'palette': labels,
        'cells': cells,
        'counts': counts,
        'running_tiles': running,
        'global_running_tiles': activity['leased_tiles'],
        'active_worker_owners': activity['worker_owners'],
        'superseded_leases': activity['superseded_leases'],
        'deferred_tiles': activity.get('deferred_tiles', 0),
        'work_steals': activity.get('deferrals', 0),
        'failed_tiles': failed,
        'border': border,
        'minecraft_import': _live_import_snapshot() if area == 'live' else None,
        'overlays': {
            'planned': _extent(planned_rows),
            'installed': {**(_extent(installed_rows) or {}),
                'tiles': [{'x': int(tile['world_offset_xz'][0]),
                           'z': int(tile['world_offset_xz'][1]),
                           'size': int(tile.get('size_m', tile.get('size')))}
                          for _, tile in installed_rows if tile.get('world_offset_xz')]},
            'anchor': {'x': 0, 'z': 0, 'label': 'Water Tower'},
        },
        'updated_utc': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    with LOCK:
        CHUNK_CACHE[area] = (time.time(), payload)
    return payload

def region_header(path):
    """Read only allocated chunk entries whose sectors exist; writer may still be active."""
    match = re.fullmatch(r'r\.(-?\d+)\.(-?\d+)\.mca', path.name)
    if not match:
        return None
    stat = path.stat()
    with path.open('rb') as stream:
        header = stream.read(4096)
    if len(header) < 4096:
        return None
    chunks = []
    for i in range(1024):
        entry = header[4*i:4*i+4]
        offset, sectors = int.from_bytes(entry[:3], 'big'), entry[3]
        if offset >= 2 and sectors and (offset+sectors)*4096 <= stat.st_size:
            chunks.append(i)
    return {'x':int(match[1]), 'z':int(match[2]), 'chunks':len(chunks),
            'bytes':stat.st_size, 'modified':stat.st_mtime}

def snapshot(stage):
    with LOCK:
        old = CACHE.get(stage)
        if old and time.time()-old[0] < 5:
            return old[1]
        state_dir = REPO/'runs/chicago'/stage
        status = read_json(state_dir/'status.json')
        inventory = read_json(REPO/'runs/chicago/inventory.json')
        log = tail(state_dir/'generation.log')
        regions = []
        if ROOT.exists():
            for path in (ROOT/'worlds'/stage).glob('*/region/r.*.*.mca'):
                try:
                    stat = path.stat()
                    fingerprint = (stat.st_mtime_ns, stat.st_size)
                    previous = REGION_CACHE.get(path)
                    if previous and previous[0] == fingerprint:
                        row = previous[1]
                    else:
                        row = region_header(path)
                        REGION_CACHE[path] = (fingerprint, row)
                    if row:
                        regions.append(row)
                except OSError:
                    pass
        dims = inventory.get('rectangle_dimensions_blocks', [34663,42288]) if stage=='city-draft' else [1078,1112]
        bbox = inventory.get('bbox_lat_lon') if stage=='city-draft' else [41.875,-87.642,41.885,-87.629]
        batches = re.findall(r'downloading tile batch (\d+)/(\d+)',log)
        recovered = re.findall(r'recovered (\d+)/(\d+) failed tiles',log)
        active = False
        try:
            process = psutil.Process(status['pid'])
            active = 'arnis' in process.name().lower() and abs(process.create_time()-status['started_unix']) < 30
        except (KeyError, psutil.Error):
            pass
        age = None
        try:
            age = round(time.time()-(state_dir/'status.json').stat().st_mtime)
        except OSError:
            pass
        phase = 'Waiting for first output'
        if regions:
            phase = 'World files appearing'
        elif 'Filtered ' in log or recovered:
            phase = 'Preparing terrain'
        elif 'elevation' in log.lower():
            phase = 'Acquiring elevation'
        steps = re.findall(r'\[\d/7\] ([^\r\n]+)', log)
        if steps:
            phase = steps[-1].rstrip('.')
        if status.get('status') != 'running':
            phase = {'generated_unverified':'Generated · awaiting game verification'}.get(status.get('status'), status.get('status','Not started').replace('_',' '))
        elif not active:
            phase = 'Process not running; status needs review'
        payload = {'stage':stage,'status':status,'active':active,'status_age':age,'phase':phase,
            'regions':regions,'dimensions':dims,'bbox':bbox,'batch':batches[-1] if batches else None,
            'recovered':recovered[-1] if recovered else None,
            'retry_events_in_log_tail':log.count('Elevation request retry'),
            'log':log.splitlines()[-45:], 'updated':time.time(),
            'external_available':ROOT.exists(), 'chunks_written':sum(r['chunks'] for r in regions),
            'region_bytes':sum(r['bytes'] for r in regions),
            'region_slots':math.ceil(dims[0]/512)*math.ceil(dims[1]/512)}
        CACHE[stage] = (time.time(),payload)
        return payload

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/status':
            stage = parse_qs(parsed.query).get('stage',['city-draft'])[0]
            if stage not in STAGES:
                self.send_error(400); return
            content = json.dumps(snapshot(stage)).encode(); mime='application/json'
        elif parsed.path == '/api/earth':
            content = json.dumps(build_snapshot(REPO)).encode(); mime='application/json'
        elif parsed.path == '/api/chunks':
            area = parse_qs(parsed.query).get('area', ['installed'])[0]
            try:
                content = json.dumps(chunk_snapshot(area), separators=(',', ':')).encode()
            except (FileNotFoundError, ValueError):
                self.send_error(404); return
            mime = 'application/json'
        elif parsed.path == '/progress/earth.json':
            content = (REPO/'progress/earth.json').read_bytes(); mime='application/json'
        elif parsed.path == '/api/boundary':
            data = read_json(ROOT/'sources/chicago-boundary.geojson')
            content = json.dumps(data).encode(); mime='application/json'
        elif parsed.path in ('/','/app.js','/style.css'):
            path = REPO/'dashboard'/({'/':'index.html'}.get(parsed.path,parsed.path[1:]))
            content = path.read_bytes()
            mime = {'html':'text/html','js':'text/javascript','css':'text/css'}[path.suffix[1:]]
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header('Content-Type',mime+'; charset=utf-8')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self' https://cdn.jsdelivr.net; connect-src 'self'; img-src 'self' data: https://cdn.jsdelivr.net; frame-ancestors 'none'")
        self.end_headers(); self.wfile.write(content)
    def log_message(self,*args):
        pass

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Earthcraft dashboard: http://127.0.0.1:{args.port}',flush=True)
    server.serve_forever()
