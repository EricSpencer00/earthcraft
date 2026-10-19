"""tiles/v2/index.json: one small file listing every published 512 m tile.

The website reads it to show new tiles within minutes of upload. Shape:

  {"schema": "earthcraft.tiles-index.v1", "updated_utc": "...",
   "qualities": ["LQ", ...],
   "tiles": {"<rx>_<rz>": [quality_code, commit_unix_ts], ...}}

quality_code indexes `qualities` (-1: published but not installable). The
generator rewrites it inside each tile-batch commit (no extra commits):
read the current file, add the batch, write. `--rebuild` lists the dataset
tree once and writes the whole index from scratch.

  python scripts/tiles_index.py --rebuild --out DIR   # then hf_store.py push-files --src DIR --dest tiles/v2
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hf_store import DEFAULT_REPO, MiB, DatasetReader  # noqa: E402
from regional_tiles import PREFIX, QUALITIES, TILE_ID  # noqa: E402

PATH = f'{PREFIX}/index.json'
SCHEMA = 'earthcraft.tiles-index.v1'
LIMIT = 16 * MiB


def quality_code(quality):
    return QUALITIES.index(quality) if quality in QUALITIES else -1


def empty():
    return {'schema': SCHEMA, 'updated_utc': None, 'qualities': list(QUALITIES), 'tiles': {}}


def parse(raw):
    """A parsed index, or an empty one when raw is missing or not an index."""
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        value = None
    if not isinstance(value, dict) or value.get('schema') != SCHEMA or not isinstance(value.get('tiles'), dict):
        return empty()
    tiles = {k: [int(v[0]), int(v[1])] for k, v in value['tiles'].items()
             if TILE_ID.fullmatch(k) and isinstance(v, list) and len(v) == 2}
    return {**empty(), 'updated_utc': value.get('updated_utc'), 'tiles': tiles}


def merge(index, additions, now):
    """Add {tile: [code, ts]}; a tile keeps its newest entry. Returns the new index."""
    tiles = dict(index['tiles'])
    for tile, entry in additions.items():
        if not TILE_ID.fullmatch(tile):
            raise ValueError(f'Invalid tile id: {tile!r}')
        if tile not in tiles or entry[1] >= tiles[tile][1]:
            tiles[tile] = [int(entry[0]), int(entry[1])]
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return {**empty(), 'updated_utc': stamp, 'tiles': dict(sorted(tiles.items()))}


def dumps(index):
    return json.dumps(index, separators=(',', ':'), sort_keys=False).encode() + b'\n'


def read_remote(reader):
    try:
        return parse(reader.read(PATH, limit=LIMIT))
    except FileNotFoundError:
        return empty()


# --------------------------------------------------------------------------
# Rebuild from the dataset tree


def next_link(header):
    match = re.search(r'<([^>]+)>\s*;\s*rel="next"', header or '')
    return match[1] if match else None


def list_tiles(reader):
    """{tile: commit_unix_ts} for every tiles/v2/<tile>/ directory, following the tree's Link pages.

    The tree API pages its listing (1,000 entries plain, 50 with expand=true for lastCommit).
    """
    url = f'{reader.endpoint}/api/datasets/{reader.repo}/tree/{reader.revision}/{PREFIX}?expand=true'
    found = {}
    while url:
        def fetch(url=url):
            with reader._open(url) as response:
                return json.loads(response.read(LIMIT)), response.headers.get('Link')
        page, link = reader.retry.call(fetch, what='tree')
        for entry in page:
            name = entry.get('path', '').rsplit('/', 1)[-1]
            if entry.get('type') == 'directory' and TILE_ID.fullmatch(name):
                date = (entry.get('lastCommit') or {}).get('date')
                found[name] = int(datetime.fromisoformat(date.replace('Z', '+00:00')).timestamp()) if date else 0
        url = next_link(link)
        if url and not url.startswith(reader.endpoint + '/'):
            url = urllib.parse.urljoin(reader.endpoint + '/', url)
    return found


def rebuild(reader, now, workers=8):
    stamps = list_tiles(reader)

    def quality(tile):
        try:
            manifest = json.loads(reader.read(f'{PREFIX}/{tile}/tile.json', limit=4 * MiB))
            return tile, quality_code(manifest.get('quality') if manifest.get('installable') else None)
        except FileNotFoundError:
            return tile, None
    with ThreadPoolExecutor(workers) as pool:
        codes = dict(pool.map(quality, sorted(stamps)))
    return merge(empty(), {t: [codes[t], stamps[t]] for t in stamps if codes[t] is not None}, now)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--rebuild', action='store_true', required=True)
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--out', type=Path, required=True, help='directory; writes <out>/index.json')
    args = parser.parse_args(argv)
    import time
    index = rebuild(DatasetReader(args.repo), time.time())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'index.json').write_bytes(dumps(index))
    print(json.dumps({'tiles': len(index['tiles']), 'bytes': len(dumps(index))}))


if __name__ == '__main__':
    main()
