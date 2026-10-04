"""Read-only local metro map, with generation and saved-world evidence separate."""
from datetime import datetime, timezone
from collections import Counter
import gzip
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import threading
import time

from pyproj import Transformer
from world_coverage import read_world_coverage, STATUS

STAGES = ('sources', 'geometry', 'appearance', 'game_verify')
STAGE_FIELDS = ('source_state', 'geometry_state', 'appearance_state', 'game_verify_state')
STATES = {'pending', 'running', 'complete', 'failed'}


def read(path):
    return json.loads(Path(path).read_text())


def validate_queue(record):
    jobs, counts = record.get('jobs'), record.get('counts')
    if (record.get('schema_version') != 1 or not isinstance(jobs, dict) or
            len(jobs) > 100_000 or not isinstance(counts, list) or len(counts) != 4 or
            not isinstance(record.get('observed_unix'), (int, float)) or
            any(not isinstance(states, list) or len(states) != 4 or
                any(state not in STATES for state in states) for states in jobs.values())):
        raise ValueError('Invalid metro queue snapshot')
    derived = [dict(Counter(states[i] for states in jobs.values())) for i in range(4)]
    if counts != derived:
        raise ValueError('Metro queue counts disagree with tile records')


def sync_queue(config, destination):
    """Copy only sanitized read-only journal states over the established route."""
    config = read(config)
    route = subprocess.run(['/usr/bin/ssh', '-G', config['host']], capture_output=True,
                           text=True, check=True, timeout=10)
    values = dict(line.split(' ', 1) for line in route.stdout.splitlines())
    if (config['host'] != 'mac-mini' or values.get('hostname') != config['hostname'] or
            values.get('user') != config['user'] or values.get('proxyjump', 'none') != 'none' or
            values.get('identityfile') != '~/.ssh/id_ed25519_mac_mini'):
        raise ValueError('Metro map SSH route differs from the authorized mini route')
    code = '''from pathlib import Path
import json,sqlite3,time,gzip,sys,hashlib
from collections import Counter
c=Path(CONTROL);now=time.time();jobs={};counts=[{} for _ in range(4)];owners=set()
with sqlite3.connect((c/'jobs.sqlite').as_uri()+'?mode=ro',uri=True) as db:
 db.execute('BEGIN')
 for tile,stage,state,expires,owner in db.execute('SELECT tile,stage,state,expires,owner FROM jobs'):
  if stage not in range(4):continue
  if state=='running' and (expires is None or expires<=now):state='pending'
  if state=='running' and owner:owners.add(owner)
  jobs.setdefault(tile,['pending']*4)[stage]=state
  counts[stage][state]=counts[stage].get(state,0)+1
counts=[dict(Counter(states[i] for states in jobs.values())) for i in range(4)]
s=json.loads((c/'supervisor-status.json').read_text())
record={'schema_version':1,'observed_unix':now,'jobs':jobs,'counts':counts,
 'plan_sha256':hashlib.sha256((c/'plan.json').read_bytes()).hexdigest(),
 'active_workers':len(owners),'generation_state':s.get('state','unknown')}
sys.stdout.buffer.write(gzip.compress(json.dumps(record,separators=(',',':')).encode(),compresslevel=1))
'''.replace('CONTROL', json.dumps(config['control']))
    command = ' '.join(shlex.quote(v) for v in [config['python'], '-u', '-'])
    result = subprocess.run(['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'AddressFamily=inet',
                             '-o', 'ConnectTimeout=20', config['host'], command],
                            input=code.encode(), capture_output=True, check=True, timeout=45)
    if len(result.stdout) > 8 * 2**20:
        raise ValueError('Unbounded metro queue response')
    with gzip.GzipFile(fileobj=io.BytesIO(result.stdout)) as compressed:
        raw = compressed.read(32 * 2**20 + 1)
        if len(raw) > 32 * 2**20:
            raise ValueError('Unbounded metro queue snapshot')
    record = json.loads(raw)
    validate_queue(record)
    destination = Path(destination)
    temporary = destination.with_suffix('.partial')
    temporary.write_bytes(raw)
    temporary.replace(destination)


def follow_queue(config, destination):
    while True:
        time.sleep(60)
        try:
            sync_queue(config, destination)
        except (OSError, ValueError, subprocess.SubprocessError):
            # Keep the last observed record, including its original timestamp.
            pass


class MetroMap:
    def __init__(self, root, cache):
        self.root, self.cache = Path(root), Path(cache)
        self.save = self.root / 'runtime/traversal/saves/Earthcraft'
        plan_bytes = (self.cache / 'plan.json').read_bytes()
        self.plan = json.loads(plan_bytes)
        self.plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()
        self.frame = read(self.root / 'runtime/traversal/config/earthcraft-live.json')['coordinate_frame']
        if self.plan['frame'] != self.frame or not 0 < len(self.plan['tiles']) <= 100_000:
            raise ValueError('Metro plan differs from the existing world frame')
        self.boundary = {'type': 'FeatureCollection', 'features': []}
        for name in ('metro.geojson', 'kenosha.geojson'):
            for feature in read(self.cache / name)['features']:
                self.boundary['features'].append({'type': 'Feature', 'properties': {},
                                                  'geometry': feature['geometry']})
        def coordinates(value):
            if isinstance(value[0], (int, float)):
                yield value
            else:
                for child in value:
                    yield from coordinates(child)
        points = [p for f in self.boundary['features'] for p in coordinates(f['geometry']['coordinates'])]
        self.bounds = [min(p[0] for p in points), min(p[1] for p in points),
                       max(p[0] for p in points), max(p[1] for p in points)]
        inverse = Transformer.from_crs(self.frame['crs'], 4326, always_xy=True)
        self.locations = []
        for tile in self.plan['tiles']:
            x, y, size = tile['west'], tile['north'], tile['size']
            corners = [list(inverse.transform(x + dx, y - dz)) for dx, dz in
                       ((0, 0), (size, 0), (size, size), (0, size))]
            lon, lat = inverse.transform(x + size / 2, y - size / 2)
            self.locations.append({'id': 'metro/' + tile['id'], 'region_id': 'metro',
                                   'tile_id': tile['id'], 'size_m': size,
                                   'longitude': lon, 'latitude': lat, 'corners_lonlat': corners})
        self.lock = threading.Lock()
        self.cached = {}

    def snapshot(self, view='metro'):
        if view not in ('metro', 'world'):
            raise ValueError('Unknown metro map view')
        paths = [self.cache / 'queue.json', self.save / 'city-coverage.json',
                 self.save / 'regional-quality.json',
                 self.cache.parent / 'publisher-status.json']
        fingerprint = tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in paths)
        with self.lock:
            previous = self.cached.get(view)
            if previous and previous[0] == fingerprint and time.time() - previous[2] < 10:
                return previous[1]
            queue = read(paths[0]) if paths[0].exists() else {}
            if queue:
                validate_queue(queue)
                if queue.get('plan_sha256') != self.plan_sha256:
                    raise ValueError('Queue does not belong to the frozen metro plan')
            coverage, quality = read(paths[1]), read(paths[2])
            if coverage['frame'] != self.frame or quality['frame'] != self.frame:
                raise ValueError('Save receipts differ from the frozen metro frame')
            publisher = read(paths[3]) if paths[3].exists() else {}
            installed_cells = set()
            for tile in coverage['tiles'].values():
                size, offset = tile.get('size_m'), tile.get('world_offset_xz')
                if (tile.get('status') != STATUS or type(size) is not int or
                        not 0 < size <= 4096 or size % 256 or
                        not isinstance(offset, list) or len(offset) != 2 or
                        any(type(v) is not int or v % 256 for v in offset)):
                    continue
                for dz in range(0, size, 256):
                    for dx in range(0, size, 256):
                        installed_cells.add(((offset[0] + dx) // 256, (offset[1] + dz) // 256))
                if len(installed_cells) > 500_000:
                    raise ValueError('Unbounded installed footprint')
            scans = sum(r.get('quality') == 'scan-points-and-roof' and r.get('upgraded_chunks', 0) > 0
                        for r in quality['tiles'].values())
            counts = {stage: (queue.get('counts', [{}] * 4)[i]) for i, stage in enumerate(STAGES)}
            rows = []
            for tile, position in zip(self.plan['tiles'], self.locations):
                stages = queue.get('jobs', {}).get(tile['id'])
                stages = stages or ['pending'] * 4
                x, z = tile['world_offset_xz']
                saved = all((x // 256 + dx, z // 256 + dz) in installed_cells
                            for dx in range(2) for dz in range(2))
                receipt = quality['tiles'].get('512:' + tile['id'], {})
                scan = receipt.get('quality') == 'scan-points-and-roof' and receipt.get('upgraded_chunks', 0) > 0
                state = ('failed' if 'failed' in stages else 'running' if 'running' in stages else
                         'generated' if stages[1] == 'complete' or saved else
                         'sourced' if stages[0] == 'complete' else 'queued')
                rows.append({**position, 'state': state,
                             **dict(zip(STAGE_FIELDS, stages)),
                             'installed_in_save': saved,
                             'scan_upgrade_recorded_in_parent_tile': scan,
                             'photo_color_applied': False})
            observed = queue.get('observed_unix')
            now = time.time()
            updated = datetime.fromtimestamp(observed or now, timezone.utc).isoformat()
            payload = {'schema_version': 1, 'source': 'local', 'updated_utc': updated,
                       'regions': [{'id': 'metro', 'label': 'Chicagoland metro'}],
                       'cell_grid': {'cell_size_m': 512}, 'cells': rows,
                       'rollup': {'generated_tiles': counts['geometry'].get('complete'),
                                  'planned_tiles': len(rows), 'state': queue.get('generation_state', 'unknown')},
                       'local': {'source_tiles_complete': counts['sources'].get('complete'), 'stages': counts},
                       'metro': {'scope': self.plan['scope'], 'planned_tiles': len(rows),
                                 'area_km2': self.plan['city_area_m2'] / 1e6,
                                 'boundary': self.boundary, 'bounds_lonlat': self.bounds,
                                 'installed_scan_tiles': scans,
                                 'installed_tiles': sum(r['installed_in_save'] for r in rows),
                                 'acknowledged_tiles': publisher.get('delivered_tiles'),
                                 'delivery_state': publisher.get('state', 'unknown'),
                                 'generation_state': queue.get('generation_state', 'unknown'),
                                 'active_workers': queue.get('active_workers', 0),
                                 'queue_available': bool(queue),
                                 'queue_stale': observed is None or now - observed > 180,
                                 'photo_colored_cells': 0}}
            if not queue:
                for row in rows:
                    for field in STAGE_FIELDS:
                        row[field] = 'unknown'
            if view == 'world':
                payload['generated_world'] = read_world_coverage(self.root)
                payload['cells'] = []
            # Reject a mixed read while the publisher is atomically installing metadata.
            after = tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in paths)
            if after != fingerprint:
                raise ValueError('Map records changed during read; retry')
            self.cached[view] = fingerprint, payload, time.time()
            return payload
