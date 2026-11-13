"""Load published tiles in a real Minecraft server and check the game sees the generator's blocks.

The validators parse region files with our own code. This asks the game itself: it downloads a
few published tiles, puts their regions in an empty world with the dataset's player-free
level.dat and height datapack, starts Mojang's 1.21.10 dedicated server, force-loads every chunk
and asks the server, block by block, whether sampled positions hold the block the generator
wrote (`execute if block`). It fails on a wrong block, a chunk the server could not load, or a
chunk error in the server log.

  load_check.py --accept-eula [--count 3 | --tiles 17_54,-56_-5] [--samples 48] [--workdir DIR] [--java java]
  load_check.py --prepare-only            fetch the tiles and sample their blocks; no server

Needs Java 21 or newer. The server JAR comes from Mojang's own download server (SHA-1 checked)
and stays in the work directory, which is deleted at the end unless --keep is given. Running the
server means agreeing to the Minecraft EULA (https://aka.ms/MinecraftEULA): pass --accept-eula or
set MINECRAFT_EULA=true to say you do. Without it nothing is downloaded from Mojang or started.
"""
import argparse
import base64
import hashlib
import json
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import contributions  # noqa: E402
from hf_store import DEFAULT_REPO, DatasetReader, GiB, MiB  # noqa: E402

VERSION = contributions.CLIENT_VERSION
VERSIONS = 'https://piston-meta.mojang.com/mc/game/version_manifest_v2.json'
STATUS = 'status/pipeline.json'
LEVEL_DAT = 'chicago-water-tower-64/Arnis World 1/level.dat'
DATAPACK = 'Earthcraft-Chicago-Photo-Atlas-v2/datapacks/earthcraft_height'
AIR = {'minecraft:air', 'minecraft:cave_air', 'minecraft:void_air'}
# Lines the server prints when it cannot read a chunk as stored.
BAD_LOG = re.compile(r"Chunk file at|Couldn't load chunk|Failed to (?:load|read|parse) chunk|Recoverable errors when loading"
                     r"|Invalid chunk|Exception reading|is in the wrong location|Error loading chunk", re.I)
VERDICT = re.compile(r'Test (passed|failed)|is not loaded|Unknown or incomplete command|Incorrect argument|Expected ')


def get(url, limit=128 * MiB):
    request = urllib.request.Request(url, headers={'User-Agent': 'earthcraft-load-check/1'})
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f'{url} is larger than its read bound')
    return raw


def bitmap_tiles(entry):
    """Tile ids from a status bitmap (scripts/aws_pipeline_setup.sh status)."""
    if not entry:
        return []
    bits = zlib.decompress(base64.b64decode(entry['bitmap']))
    width, x0, z0 = entry['width'], entry['x0'], entry['z0']
    return [f'{x0 + i % width}_{z0 + i // width}' for i in range(width * entry['height'])
            if bits[i >> 3] >> (i & 7) & 1]


def choose(status, count, seed):
    """`count` tiles, the same for a given seed: full-detail ones first, since they carry the most blocks."""
    rng = random.Random(seed)
    detailed = sorted(bitmap_tiles(status.get('detailed_tiles')))
    published = sorted(set(bitmap_tiles(status.get('published_tiles'))) - set(detailed))
    rng.shuffle(detailed)
    rng.shuffle(published)
    return (detailed + published)[:count]


def top_blocks(chunks, samples, rng):
    """[(x, y, z, state)]: the highest non-air block of `samples` random columns of a parsed region."""
    from detail_anvil import decode_states
    found, decoded = [], {}
    keys = sorted(chunks)
    for _ in range(samples * 4):
        if len(found) >= samples or not keys:
            break
        cx, cz = rng.choice(keys)
        lx, lz = rng.randrange(16), rng.randrange(16)
        sections = sorted((s for s in chunks[(cx, cz)]['tag'].get('sections', []) if 'block_states' in s),
                          key=lambda s: -int(s['Y']))
        for section in sections:
            key = (cx, cz, int(section['Y']))
            if key not in decoded:
                decoded[key] = decode_states(section['block_states'])
            palette, values = decoded[key]
            column = values.reshape(16, 16, 16)[:, lz, lx]
            solid = [y for y in range(15, -1, -1) if palette[int(column[y])] not in AIR]
            if solid:
                found.append((cx * 16 + lx, int(section['Y']) * 16 + solid[0], cz * 16 + lz, palette[int(column[solid[0]])]))
                break
    return found


def fetch_tile(reader, tile, regions):
    """Download one tile's delivered region into `regions` as .mca; returns its path."""
    from region_pack import unpack
    from regional_tiles import tile_coords
    manifest = json.loads(reader.read(f'tiles/v2/{tile}/tile.json', limit=4 * MiB))
    rx, rz = tile_coords(tile)
    entry = next(f for f in manifest['files']
                 if re.fullmatch(rf'tiles/v2/{re.escape(tile)}/region/r\.{rx}\.{rz}\.mc[ap]', f['path']))
    raw = reader.read(entry['path'], limit=contributions.MAX_REGION_BYTES)
    if hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError(f'{entry["path"]} differs from tile.json')
    if entry['path'].endswith('.mcp'):
        raw = unpack(raw)
    path = regions / f'r.{rx}.{rz}.mca'
    path.write_bytes(raw)
    return path


def server_jar(workdir):
    path = workdir / f'server-{VERSION}.jar'
    versions = json.loads(get(VERSIONS, 8 * MiB))['versions']
    meta = json.loads(get(next(v['url'] for v in versions if v['id'] == VERSION), 8 * MiB))['downloads']['server']
    if not path.is_file() or hashlib.sha1(path.read_bytes()).hexdigest() != meta['sha1']:
        path.write_bytes(get(meta['url']))
    if hashlib.sha1(path.read_bytes()).hexdigest() != meta['sha1']:
        raise SystemExit('The server JAR does not match Mojang\'s SHA-1')
    return path


class Server:
    """Mojang's dedicated server on a loopback port, driven through its console."""

    def __init__(self, java, jar, directory, memory='2G'):
        self.lines, self.log = queue.Queue(), []
        self.process = subprocess.Popen([java, f'-Xmx{memory}', '-jar', str(jar), 'nogui'], cwd=directory,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, bufsize=1)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            self.log.append(line.rstrip('\n'))
            self.lines.put(line)
        self.lines.put(None)

    def wait_for(self, pattern, timeout):
        """The first console line matching `pattern` (a compiled regex)."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                line = ''
            if line is None:
                raise RuntimeError('The server exited: ' + ' | '.join(self.log[-5:]))
            match = pattern.search(line)
            if match:
                return match
            if time.monotonic() > deadline:
                raise TimeoutError(f'No "{pattern.pattern}" within {timeout} s: ' + ' | '.join(self.log[-5:]))

    def ask(self, command, pattern=VERDICT, timeout=60):
        self.process.stdin.write(command + '\n')
        self.process.stdin.flush()
        return self.wait_for(pattern, timeout)

    def stop(self, timeout=120):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('stop\n')
                self.process.stdin.flush()
                self.process.wait(timeout)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
        return self.process.returncode


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def prepare_world(workdir, reader):
    """An empty world: the dataset's player-free level.dat and the height datapack. Returns the server dir."""
    import clean_templates
    server = workdir / 'server'
    world = server / 'world'
    shutil.rmtree(server, ignore_errors=True)
    (world / 'region').mkdir(parents=True)
    templates = workdir / 'templates'
    if not (templates / LEVEL_DAT).is_file():
        clean_templates.fetch(templates, reader)
    shutil.copy(templates / LEVEL_DAT, world / 'level.dat')
    shutil.copytree(templates / DATAPACK, world / 'datapacks' / Path(DATAPACK).name)
    (server / 'server.properties').write_text('\n'.join([
        'level-name=world', 'online-mode=false', 'server-ip=127.0.0.1', f'server-port={free_port()}',
        'enable-rcon=false', 'enable-query=false', 'view-distance=2', 'simulation-distance=2', 'max-tick-time=-1',
        'spawn-protection=0', 'sync-chunk-writes=false', 'max-players=0', 'enable-status=false', '']))
    return server


def check(tiles, workdir, java, samples, seed, timeout, reader=None, log=print, start=True):
    """Run the whole check; returns a report dict with 'ok'. start=False stops before the server."""
    from detail_anvil import read_region
    reader = reader or DatasetReader(DEFAULT_REPO)
    server_dir = prepare_world(workdir, reader)
    rng = random.Random(seed)
    expected = {}
    for tile in tiles:
        path = fetch_tile(reader, tile, server_dir / 'world/region')
        chunks = read_region(path)
        expected[tile] = {'chunks': sorted(chunks), 'blocks': top_blocks(chunks, samples, rng)}
        log(f'{tile}: {len(chunks)} chunks, {len(expected[tile]["blocks"])} sampled blocks')
    if not start:
        return {'ok': all(len(t['chunks']) == 1024 and t['blocks'] for t in expected.values()), 'prepared_only': True,
                'tiles': {tile: {'chunks': len(t['chunks']), 'blocks_sampled': len(t['blocks'])}
                          for tile, t in expected.items()}}
    (server_dir / 'eula.txt').write_text('eula=true\n')  # the caller agreed: --accept-eula or MINECRAFT_EULA=true
    server = Server(java, server_jar(workdir), server_dir)
    report = {'tiles': {}, 'minecraft': VERSION}
    try:
        server.wait_for(re.compile(r'Done \('), timeout)
        for tile, want in expected.items():
            wrong, unloaded = [], []
            xs, zs = [c[0] for c in want['chunks']], [c[1] for c in want['chunks']]
            # 256 chunks is the most one forceload command takes: 16 x 16 chunk squares.
            for cx in range(min(xs), max(xs) + 1, 16):
                for cz in range(min(zs), max(zs) + 1, 16):
                    server.ask(f'forceload add {cx * 16} {cz * 16} {cx * 16 + 255} {cz * 16 + 255}',
                               re.compile(r'force loaded|No chunks were marked|Too many chunks|Incorrect|Unknown'))
            for cx, cz in want['chunks'][::max(1, len(want['chunks']) // 64)]:  # loading is asynchronous: wait for it
                deadline = time.monotonic() + timeout
                while server.ask(f'execute if loaded {cx * 16} 64 {cz * 16}').group(1) != 'passed':
                    if time.monotonic() > deadline:
                        unloaded.append([cx, cz])
                        break
                    time.sleep(0.5)
            for x, y, z, state in want['blocks']:
                answer = server.ask(f'execute if block {x} {y} {z} {state}')
                if answer.group(1) != 'passed':
                    wrong.append({'at': [x, y, z], 'expected': state, 'server': answer.group(0)})
            server.ask('forceload remove all', re.compile(r'Unmarked|No chunks|force loaded'))
            report['tiles'][tile] = {'chunks': len(want['chunks']), 'blocks_checked': len(want['blocks']),
                                     'wrong_blocks': wrong, 'chunks_not_loaded': unloaded}
            log(f'{tile}: {len(want["blocks"]) - len(wrong)}/{len(want["blocks"])} blocks match, '
                f'{len(unloaded)} chunks not loaded')
    finally:
        report['server_exit'] = server.stop()
    report['log_errors'] = [line for line in server.log if BAD_LOG.search(line)][:20]
    report['ok'] = bool(report['tiles']) and report['server_exit'] == 0 and not report['log_errors'] and all(
        t['chunks'] == 1024 and t['blocks_checked'] and not t['wrong_blocks'] and not t['chunks_not_loaded']
        for t in report['tiles'].values())
    if not report['ok']:
        report['log_tail'] = server.log[-30:]
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tiles', type=lambda v: [t for t in v.split(',') if t], default=None)
    parser.add_argument('--count', type=int, default=3, help='random published tiles to load (default 3)')
    parser.add_argument('--samples', type=int, default=48, help='blocks asked about per tile')
    parser.add_argument('--seed', default=time.strftime('%Y-%m-%d', time.gmtime()), help='default: today (UTC)')
    parser.add_argument('--workdir', type=Path, default=ROOT / 'runs/scratch/load-check')
    parser.add_argument('--java', default='java')
    parser.add_argument('--timeout', type=int, default=300, help='seconds for server start and for chunk loading')
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--keep', action='store_true', help='keep the work directory')
    parser.add_argument('--accept-eula', action='store_true',
                        help='you agree to the Minecraft EULA (https://aka.ms/MinecraftEULA); or MINECRAFT_EULA=true')
    parser.add_argument('--prepare-only', action='store_true', help='fetch and sample the tiles; start no server')
    args = parser.parse_args(argv)
    import os
    agreed = args.accept_eula or os.environ.get('MINECRAFT_EULA', '').lower() == 'true'
    if not agreed and not args.prepare_only:
        raise SystemExit('The server needs your agreement to the Minecraft EULA (https://aka.ms/MinecraftEULA): '
                         'pass --accept-eula or set MINECRAFT_EULA=true. --prepare-only runs without it.')
    workdir = args.workdir.resolve()
    if shutil.disk_usage(workdir.parent if workdir.parent.exists() else ROOT).free < 4 * GiB:
        raise SystemExit('Less than 4 GiB free; not starting')
    workdir.mkdir(parents=True, exist_ok=True)
    reader = DatasetReader(args.repo)
    tiles = args.tiles or choose(json.loads(reader.read(STATUS, limit=4 * MiB)), args.count, args.seed)
    if not tiles:
        raise SystemExit('No published tiles to check')
    try:
        report = check(tiles, workdir, args.java, args.samples, args.seed, args.timeout, reader,
                       start=not args.prepare_only)
    finally:
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)
    print(json.dumps(report, indent=1))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
