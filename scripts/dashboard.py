"""Loopback-only, read-only Earthcraft progress server. No inference or external assets."""
import argparse
import json
import math
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import psutil
from local_paths import bulk_path
from progress_snapshot import build_snapshot

REPO = Path(__file__).resolve().parents[1]
ROOT = bulk_path('chicago')
STAGES = ('city-draft', 'loop-pilot')
CACHE = {}
REGION_CACHE = {}
LOCK = threading.Lock()

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
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
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
