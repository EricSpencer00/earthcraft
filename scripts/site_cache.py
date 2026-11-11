"""Small, cacheable map data for the site, rebuilt by the Pages workflow once a day.

The map used to download progress/earth.json (16 MB: every cell id as a JSON object) and ask
Hugging Face for the pipeline status on every visit. This writes:

  <out>/world.json   the snapshot without its id lists; cells, tiles/v2, lake and planned
                     tiles as bitmaps (base64(zlib(bits)), the format of status/pipeline.json)
  <out>/status.json  status/pipeline.json from the dataset, as of this build
  <out>/detail.json  world/index/detail-levels.json from the dataset, as of this build

so a visit reads about 100 KB from GitHub Pages and nothing from Hugging Face. Hugging Face is
read only for block data, when someone opens a tile.

  python scripts/site_cache.py --snapshot progress/earth.json --out site/progress
"""
import argparse
import base64
import json
from pathlib import Path
import urllib.request
import zlib

DATASET = 'https://huggingface.co/datasets/EricSpencer00/earthcraft/resolve/main/'
PLAN = 'archive/mini-worker-20260929/runs/chicagoland-scans-20260929/plan.json'
REMOTE = {'status.json': 'status/pipeline.json', 'detail.json': 'world/index/detail-levels.json'}
ENCODING = 'base64(zlib(bitmap)); bit i = (z - z0) * width + (x - x0), least significant bit first'


def bitmap(ids):
    points = [tuple(int(v) for v in str(i).rsplit('_', 1)) for i in ids]
    if not points:
        return None
    x0, z0 = min(p[0] for p in points), min(p[1] for p in points)
    width, height = max(p[0] for p in points) - x0 + 1, max(p[1] for p in points) - z0 + 1
    bits = bytearray((width * height + 7) // 8)
    for x, z in points:
        i = (z - z0) * width + (x - x0)
        bits[i >> 3] |= 1 << (i & 7)
    return {'bitmap': base64.b64encode(zlib.compress(bytes(bits), 9)).decode(), 'count': len(set(points)),
            'encoding': ENCODING, 'width': width, 'height': height, 'x0': x0, 'z0': z0}


def world_json(snapshot):
    out = {k: v for k, v in snapshot.items() if k != 'cells'}  # the legacy per-cell records stay in earth.json
    world = snapshot.get('generated_world')
    if isinstance(world, dict) and isinstance(world.get('cells'), list):
        world = dict(world)
        defaults = world.get('cell_defaults') or {}
        extra = [c for c in world['cells'] if set(c) - {'tile_id'}]
        if extra:  # the bitmap carries ids only; refuse to drop per-cell fields silently
            raise SystemExit(f'{len(extra)} cells carry fields beyond tile_id; extend site_cache.py first')
        world['cells_bitmap'] = bitmap(c['tile_id'] for c in world.pop('cells'))
        world['v2_bitmap'] = bitmap(world.pop('v2_tiles', None) or [])
        world['lake_bitmap'] = bitmap(world.pop('lake_tiles', None) or [])
        world['cell_defaults'] = defaults
        out['generated_world'] = world
    return out


def fetch(path, timeout=60, limit=8 * 2**20):
    request = urllib.request.Request(DATASET + path, headers={'User-Agent': 'earthcraft-site-cache'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f'{path} is larger than {limit} bytes')
    return json.loads(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--snapshot', type=Path, default=Path('progress/earth.json'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--offline', action='store_true', help='skip the Hugging Face files')
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    world = world_json(json.loads(args.snapshot.read_text()))
    if not args.offline and isinstance(world.get('generated_world'), dict):
        try:  # every planned 512 m tile, so the map can show what is queued, not a hole
            world['generated_world']['plan_bitmap'] = bitmap(t['id'] for t in fetch(PLAN, limit=64 * 2**20)['tiles'])
        except Exception as error:  # noqa: BLE001
            print(f'plan: not included ({type(error).__name__}: {error})')
    (args.out / 'world.json').write_text(json.dumps(world, separators=(',', ':')))
    print('world.json', (args.out / 'world.json').stat().st_size, 'bytes')
    for name, path in ({} if args.offline else REMOTE).items():
        try:
            data = fetch(path)
        except Exception as error:  # noqa: BLE001 - the map still works from world.json alone
            print(f'{name}: not refreshed ({type(error).__name__}: {error})')
            continue
        (args.out / name).write_text(json.dumps(data, separators=(',', ':')))
        print(name, (args.out / name).stat().st_size, 'bytes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
