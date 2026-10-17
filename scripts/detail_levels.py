#!/usr/bin/env python3
"""Compute a simple detail level for every 256 m cell (16x16 chunks).

Levels (owner's definitions):
  none  - no chunks with non-air blocks.
  low   - any chunk has a block other than air (from LiDAR base generator output).
  high  - low plus a street-level detail pass from detail/<layer>/v1 that has been
          applied to the world (listed in detail/applied.json, or the layer manifest
          has "applied_to_world": true).
  hand  - hand-built (ArnisMC-style); only cells listed in world/index/hand-tiles.json.

Region bytes are streamed from the Hugging Face dataset with HTTP Range requests and
parsed in memory; nothing but the small JSON output is written to disk. Only block
palettes are read. Standard library only, so it runs unchanged on any host:

  python scripts/detail_levels.py --sample 0.1 --seed 1 --output detail-levels.json
  python scripts/detail_levels.py --output detail-levels.json          # full pass
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import math
import random
import shutil
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

REPO = 'EricSpencer00/earthcraft'
API = f'https://huggingface.co/api/datasets/{REPO}'
SCHEMA = 'earthcraft.detail-levels.v1'
LEVELS = ('none', 'low', 'high', 'hand')
AIR = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}
OUTPUT_CAP = 4 * 1024 * 1024
MIN_FREE = 1 << 30
PROBE_CHUNKS = 8


def http(url: str, start: int | None = None, end: int | None = None, tries: int = 4) -> bytes:
    headers = {'User-Agent': 'earthcraft-detail-levels'}
    if start is not None:
        headers['Range'] = f'bytes={start}-{end}'
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise
            if attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == tries - 1:
                raise
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError('unreachable')


def json_or(url: str, default):
    try:
        return json.loads(http(url))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return default
        raise


# --- minimal NBT walker: collect block palette names only -------------------
def _string(buf, pos):
    n = struct.unpack_from('>H', buf, pos)[0]
    return buf[pos + 2:pos + 2 + n].decode('utf-8', 'replace'), pos + 2 + n


FIXED = {1: 1, 2: 2, 3: 4, 4: 8, 5: 4, 6: 8}


def _payload(buf, pos, tag, path, names):
    if tag in FIXED:
        return pos + FIXED[tag]
    if tag == 7:
        return pos + 4 + struct.unpack_from('>i', buf, pos)[0]
    if tag == 8:
        value, pos = _string(buf, pos)
        if len(path) >= 2 and path[-1] == 'Name' and path[-2] == 'palette' and 'block_states' in path:
            names.add(value)
        return pos
    if tag == 9:
        inner, count = buf[pos], struct.unpack_from('>i', buf, pos + 1)[0]
        pos += 5
        if inner in FIXED and inner != 0:
            return pos + FIXED[inner] * max(count, 0)
        for _ in range(max(count, 0)):
            pos = _payload(buf, pos, inner, path, names)
        return pos
    if tag == 10:
        while True:
            child = buf[pos]
            pos += 1
            if child == 0:
                return pos
            name, pos = _string(buf, pos)
            pos = _payload(buf, pos, child, path + (name,), names)
    if tag == 11:
        return pos + 4 + 4 * struct.unpack_from('>i', buf, pos)[0]
    if tag == 12:
        return pos + 4 + 8 * struct.unpack_from('>i', buf, pos)[0]
    raise ValueError(f'bad NBT tag {tag}')


def palette_names(chunk_nbt: bytes) -> set[str]:
    names: set[str] = set()
    tag = chunk_nbt[0]
    _, pos = _string(chunk_nbt, 1)
    _payload(chunk_nbt, pos, tag, (), names)
    return names


def has_non_air_block(record: bytes) -> bool:
    """Early-exit on first non-air block in palette."""
    nbt = decode_chunk(record)
    if nbt is None:
        return False
    return bool(palette_names(nbt) - AIR)


def decode_chunk(record: bytes) -> bytes | None:
    length, kind = struct.unpack_from('>iB', record, 0)
    data = record[5:4 + length]
    if kind == 2:
        return zlib.decompress(data)
    if kind == 1:
        import gzip
        return gzip.decompress(data)
    if kind == 3:
        return data
    return None  # external (.mcc) or unknown: treat as not decidable here


def chunk_has_blocks(record: bytes) -> bool | None:
    nbt = decode_chunk(record)
    if nbt is None:
        return None
    return bool(palette_names(nbt) - AIR)


def region_cells(url: str, rx: int, rz: int) -> dict[tuple[int, int], str]:
    """Return dict of (cell_x, cell_z) -> 'low'/'none' for each cell in region."""
    header = http(url, 0, 8191)
    # Group chunks by cell: (chunk_index) -> (cell_x, cell_z)
    chunks_by_cell: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    for i in range(1024):
        entry = int.from_bytes(header[4 * i:4 * i + 4], 'big')
        offset, sectors = entry >> 8, entry & 0xFF
        if offset and sectors:
            chunk_x = rx * 32 + (i % 32)
            chunk_z = rz * 32 + (i // 32)
            cell_x = chunk_x // 16
            cell_z = chunk_z // 16
            if (cell_x, cell_z) not in chunks_by_cell:
                chunks_by_cell[(cell_x, cell_z)] = []
            chunks_by_cell[(cell_x, cell_z)].append((i, offset * 4096, sectors * 4096))

    if not chunks_by_cell:
        # No chunks in any cell
        return {(rx * 2, rz * 2): 'none', (rx * 2 + 1, rz * 2): 'none',
                (rx * 2, rz * 2 + 1): 'none', (rx * 2 + 1, rz * 2 + 1): 'none'}

    # Collect all chunk ranges (coalesce adjacent ones)
    all_chunks = []
    for cell_coords, chunks in chunks_by_cell.items():
        all_chunks.extend(chunks)

    # Sort by offset for efficient reading
    all_chunks.sort(key=lambda x: x[1])

    # Probe first few chunks with small reads
    result: dict[tuple[int, int], str] = {}
    probed_chunks = set()

    all_chunks_sorted = sorted(all_chunks, key=lambda item: abs(item[0] % 32 - 15.5) + abs(item[0] // 32 - 15.5))
    for idx, start, size in all_chunks_sorted[:PROBE_CHUNKS]:
        if chunk_has_blocks(http(url, start, start + size - 1)):
            chunk_x = rx * 32 + (idx % 32)
            chunk_z = rz * 32 + (idx // 32)
            cell_x = chunk_x // 16
            cell_z = chunk_z // 16
            result[(cell_x, cell_z)] = 'low'
            probed_chunks.add((cell_x, cell_z))

    # If we didn't probe all chunks, read the whole region for the rest
    if len(all_chunks) > PROBE_CHUNKS:
        body = http(url)
        for idx, start, size in all_chunks[PROBE_CHUNKS:]:
            chunk_x = rx * 32 + (idx % 32)
            chunk_z = rz * 32 + (idx // 32)
            cell_x = chunk_x // 16
            cell_z = chunk_z // 16
            if (cell_x, cell_z) not in result:
                if chunk_has_blocks(body[start:start + size]):
                    result[(cell_x, cell_z)] = 'low'

    # Mark all cells in this region (fill in empty ones)
    for cx in range(rx * 2, (rx + 1) * 2):
        for cz in range(rz * 2, (rz + 1) * 2):
            if (cx, cz) not in result:
                result[(cx, cz)] = 'none'

    return result


def applied_cells(base: str, revision: str) -> dict[tuple[int, int], list[str]]:
    """Map (cell_x, cell_z) -> applied detail layers."""
    applied = json_or(f'{base}/detail/applied.json', {'layers': []})
    names = {(item['layer'], item.get('version', 'v1')) for item in applied.get('layers', [])}
    try:
        tree = json.loads(http(f'{API}/tree/{revision}/detail'))
    except urllib.error.HTTPError:
        tree = []
    for entry in tree:
        if entry.get('type') == 'directory':
            layer = entry['path'].split('/')[-1]
            manifest = json_or(f'{base}/detail/{layer}/v1/manifest.json', {})
            if manifest.get('applied_to_world') is True:
                names.add((layer, 'v1'))
    result: dict[tuple[int, int], list[str]] = {}
    for layer, version in sorted(names):
        manifest = json_or(f'{base}/detail/{layer}/{version}/manifest.json', {})
        for key, cells_data in manifest.get('cells', {}).items():
            for cell_key in cells_data if isinstance(cells_data, list) else []:
                if isinstance(cell_key, str):
                    parts = cell_key.split('_')
                    if len(parts) == 2:
                        try:
                            cell_x, cell_z = int(parts[0]), int(parts[1])
                            result.setdefault((cell_x, cell_z), []).append(layer)
                        except ValueError:
                            pass
    return result


def interval(k: int, n: int, total: int, z: float = 1.96):
    """Wilson 95% interval for a proportion, scaled to the population (with FPC)."""
    if n == 0:
        return [0, total]
    p = k / n
    fpc = math.sqrt(max(total - n, 0) / max(total - 1, 1))
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom * fpc
    low = 0 if k == 0 else max(k, round((centre - half) * total))
    high = total if k == n else min(total - (n - k), round((centre + half) * total))
    return [low, high]


def guarded_write(path: Path, payload: bytes) -> None:
    if len(payload) > OUTPUT_CAP:
        raise SystemExit(f'refusing to write {len(payload)} bytes (cap {OUTPUT_CAP})')
    path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(path.parent).free < MIN_FREE:
        raise SystemExit('refusing to write: less than 1 GB free')
    path.write_bytes(payload)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sample', type=float, default=1.0, help='fraction of regions to read (default 1.0)')
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--revision', default=None, help='dataset commit (default: current main)')
    args = parser.parse_args(argv)
    if shutil.disk_usage(args.output.resolve().parent if args.output.resolve().parent.exists() else Path('.')).free < MIN_FREE:
        raise SystemExit('less than 1 GB free; aborting')

    revision = args.revision or json.loads(http(API))['sha']
    base = f'https://huggingface.co/datasets/{REPO}/resolve/{revision}'
    regions = json.loads(http(f'{base}/world/index/regions.json'))['regions']
    hand_cells = {tuple(map(int, str(t).split('_'))) for t in json_or(f'{base}/world/index/hand-tiles.json', {'tiles': []}).get('tiles', []) if '_' in str(t)}
    applied = applied_cells(base, revision)

    chosen = list(regions)
    if args.sample < 1:
        chosen = random.Random(args.seed).sample(chosen, round(len(regions) * args.sample))
    cells: dict[str, str] = {}
    errors: dict[str, str] = {}
    lock, done = threading.Lock(), [0]

    def work(region):
        rx, rz = region['x'], region['z']
        if not region.get('chunks'):
            # No chunks in this region, all cells are empty
            cell_levels = {(rx * 2 + dx, rz * 2 + dz): 'none' for dx in [0, 1] for dz in [0, 1]}
        else:
            cell_levels = region_cells(f"{base}/{region['path']}", rx, rz)

        result = {}
        for (cx, cz), level in cell_levels.items():
            if (cx, cz) in hand_cells:
                result[f"{cx}_{cz}"] = 'hand'
            elif level == 'low' and (cx, cz) in applied:
                result[f"{cx}_{cz}"] = 'high'
            else:
                result[f"{cx}_{cz}"] = level
        return result

    with futures.ThreadPoolExecutor(max_workers=min(args.workers, 6)) as pool:
        jobs = {pool.submit(work, region): region for region in chosen}
        for job in futures.as_completed(jobs):
            region = jobs[job]
            try:
                cell_levels = job.result()
                cells.update(cell_levels)
            except Exception as error:  # keep going; report failures
                region_key = f"r.{region['x']}.{region['z']}"
                errors[region_key] = f'{type(error).__name__}: {error}'[:200]
            with lock:
                done[0] += 1
                if done[0] % 50 == 0:
                    print(f'{done[0]}/{len(chosen)}', file=sys.stderr, flush=True)

    n = len(cells)
    counts = {level: sum(1 for v in cells.values() if v == level) for level in LEVELS}
    output = {
        'schema': SCHEMA,
        'revision': revision,
        'computed_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'regions_total': len(regions),
        'sampled': None if args.sample >= 1 else args.sample,
        'seed': args.seed if args.sample < 1 else None,
        'cell_size_m': 256,
        'levels': {
            'none': 'No chunks with non-air blocks.',
            'low': 'Land and buildings from LiDAR.',
            'high': 'LiDAR plus street-level detail (trees, street furniture, sidewalks, facades).',
            'hand': 'Hand-built (ArnisMC-style).',
        },
        'counts': counts,
        'cells': dict(sorted(cells.items())),
    }
    if args.sample < 1 and n:
        output['estimated_totals'] = {
            level: {'estimate': round(counts[level] / n * len(regions) * 4), 'ci95': interval(counts[level], n, len(regions) * 4)}
            for level in LEVELS
        }
    if errors:
        output['errors'] = errors
    guarded_write(args.output, (json.dumps(output, separators=(',', ':')) + '\n').encode())
    print(json.dumps({k: output.get(k) for k in ('counts', 'estimated_totals', 'regions_total', 'sampled')}, indent=1))
    print(f'{len(errors)} errors', file=sys.stderr)
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
