"""Install generated tiles from Hugging Face into the closed Earthcraft save.

Replaces the old bundle -> ssh -> rsync -> remote merge -> rsync round trip
with one local pass on the machine that holds the save:

  1. Refuse at once while Minecraft holds session.lock (the same POSIX record
     lock the game takes); nothing is downloaded or written.
  2. Choose tiles: --tiles, or every tile whose journal records dataset evidence
     under tiles/v2/ and whose delivered receipt is not yet in
     regional-quality.json.
  3. Download each tile's tiles/v2 package (region, world manifest, receipts)
     and, for scan upgrades, its immutable LQ baselines (tiles/v2/<tile>/lq/ and
     the tile's tiles/v1 shard member, when it has one) into capped scratch,
     verified against the Hub's SHA-256 and the receipts.
  4. Under one session-lock fence, merge with regional_install.install_tile:
     new chunks are added, a chunk is upgraded only while it still equals an LQ
     baseline (or has the same block volume), everything else -- player edits --
     is kept. The original bytes of every touched file go to a gzip backup in
     capped scratch first.
  5. Upload the backups to archive/install-backups/<attempt>/ (verified) and
     delete them locally. level.dat is not uploaded (it holds the player); its
     border fields are recorded in the attempt's installation.json instead.
  6. Sync the changed regions to world/ with `hf_store.py push-world
     --only-changed` (it takes the lock again and refuses if the game opened
     the world in between; rerun it later). The sync is skipped, not turned
     into a full upload, when world/manifest.json is not on the Hub yet.

  hf_install.py --save runtime/traversal/saves/Earthcraft [--control runs/regional/control]
                [--tiles ID,...] [--limit 32] [--dry-run] [--no-sync] [--hf-python PATH]
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hf_store  # noqa: E402
from hf_store import DatasetReader, DiskGuard, DiskGuardError, GiB, MiB, StoreClient, parse_size  # noqa: E402
from regional_tiles import TILE_SCHEMA, region_name, sha_bytes, tile_coords, tile_prefix  # noqa: E402
from world_snapshot import save_lock  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXIT_WORLD_OPEN = 3
BACKUP_PREFIX = 'archive/install-backups'
V1_INDEX = 'tiles/v1/index.json'
TILE_SIZE = 512


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class WorldOpen(RuntimeError):
    pass


def probe_closed(save):
    """Fail fast (before any download) when Minecraft has the world open."""
    try:
        with save_lock(save):
            pass
    except RuntimeError as error:
        raise WorldOpen(str(error)) from error


def installed_receipts(save):
    path = Path(save) / 'regional-quality.json'
    if not path.exists():
        return {}
    return {entry['tile']: entry.get('source_receipt_sha256')
            for entry in json.loads(path.read_text()).get('tiles', {}).values() if 'tile' in entry}


def journal_candidates(control):
    """Tiles whose geometry/appearance completed with tiles/v2 evidence: {tile: {stage: sha256}}."""
    journal = Path(control) / 'jobs.sqlite'
    if not journal.exists():
        return {}
    result = {}
    with closing(sqlite3.connect(journal, timeout=20)) as db:  # WAL journal: a plain reader
        for tile, stage, sha in db.execute("SELECT tile,stage,evidence_sha256 FROM jobs WHERE state='complete' "
                                           "AND stage IN (1,2) AND evidence LIKE 'tiles/v2/%' ORDER BY priority,tile"):
            result.setdefault(tile, {})[stage] = sha
    return result


class Installer:
    def __init__(self, args, *, reader=None, store=None):
        self.args = args
        self.save = Path(args.save).resolve()
        self.scratch = Path(args.scratch).resolve()
        self.guard = DiskGuard(self.scratch, args.cap, floor_bytes=args.min_free)
        self.reader = reader or DatasetReader(args.repo)
        self.store = store
        self.attempt = args.attempt or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + f'-{os.getpid()}'
        self.v1 = None
        self.events = []

    def log(self, **event):
        event = {'utc': utc_now(), **event}
        self.events.append(event)
        print(json.dumps(event, default=str, sort_keys=True), flush=True)

    # -- selection
    def select(self):
        installed = installed_receipts(self.save)
        if self.args.tiles:
            tiles = list(dict.fromkeys(self.args.tiles))
            for tile in tiles:
                tile_coords(tile)
            return tiles, installed
        tiles = []
        for tile, shas in journal_candidates(self.args.control).items():
            if installed.get(tile) in shas.values():
                continue
            tiles.append(tile)
        return tiles[:self.args.limit], installed

    # -- downloads
    def admit(self, size):
        self.guard.check(size + MiB, 'download')

    def fetch(self, path, target, expected=None):
        return self.reader.download(path, target, expected=expected, admit=self.admit)

    def v1_entry(self, tile):
        if self.v1 is None:
            try:
                self.v1 = json.loads(self.reader.read(V1_INDEX, limit=64 * MiB))
            except FileNotFoundError:
                self.v1 = {'tiles': {}, 'shards': {}}
        return self.v1['tiles'].get(tile)

    def v1_baseline(self, tile, directory):
        """The tile's LQ region from its tiles/v1 shard, verified against the v1 index."""
        entry = self.v1_entry(tile)
        if not entry or entry.get('quality') != 'LQ':
            return None
        shard = self.v1['shards'][entry['shard_sha256']]
        path = 'tiles/v1/shards/' + shard['sha256'][:2] + '/' + shard['sha256'] + '.tar'
        local = directory / 'v1-shard.tar'
        digest = self.fetch(path, local)
        if digest.sha256 != shard['sha256']:
            raise ValueError(f'{tile}: v1 shard differs from the v1 index')
        member = f'{tile}/{entry["region_file"]}.gz'
        with tarfile.open(local, 'r:') as archive:
            raw = gzip.decompress(archive.extractfile(member).read(64 * MiB))
        self.guard.remove(local)
        if sha_bytes(raw) != entry['region_sha256'] or entry['region_file'] != region_name(tile):
            raise ValueError(f'{tile}: v1 baseline region differs from the v1 index')
        if entry.get('world_offset_xz') != [tile_coords(tile)[0] * TILE_SIZE, tile_coords(tile)[1] * TILE_SIZE]:
            raise ValueError(f'{tile}: v1 baseline is at another position')
        return {'kind': 'tiles/v1', 'shard': path, 'region_sha256': entry['region_sha256'], 'raw': raw}

    def prepare_tile(self, tile):
        """Download and verify one tile's package; returns the install request or None."""
        base = tile_prefix(tile)
        manifest_raw = self.reader.read(f'{base}/tile.json', limit=4 * MiB)
        manifest = json.loads(manifest_raw)
        if manifest.get('schema') != TILE_SCHEMA or manifest.get('tile') != tile:
            raise ValueError(f'{tile}: unexpected tile manifest')
        if not manifest.get('installable'):
            return {'tile': tile, 'skip': 'nothing installable (the installed LQ stands)'}
        files = {item['path']: item for item in manifest['files']}
        info = self.reader.info(list(files))
        for path, item in files.items():
            remote = info.get(path)
            if remote is None or remote.size != item['bytes'] or (remote.sha256 and remote.sha256 != item['sha256']):
                raise ValueError(f'{tile}: {path} differs from tile.json')
        directory = self.guard.ensure_dir('dl', tile)
        name = region_name(tile)
        local = {}
        for path, item in files.items():
            if item['role'] == 'tile':
                continue
            target = directory / path[len(base) + 1:]
            digest = self.fetch(path, target, expected=info[path])
            if digest.sha256 != item['sha256']:
                raise ValueError(f'{tile}: {path} differs from tile.json')
            local[item['role'], path] = target
        stage = manifest['delivered_receipt']
        receipt_path = directory / 'receipts' / f'{stage}.json'
        receipt_raw = receipt_path.read_bytes()
        receipt = json.loads(receipt_raw)
        if sha_bytes(receipt_raw) != manifest['stages'][stage]['sha256']:
            raise ValueError(f'{tile}: delivered receipt differs from tile.json')
        if receipt.get('result') != 'pass' or not receipt.get('checks') or receipt.get('tile') != tile:
            raise ValueError(f'{tile}: a verified passing receipt is required')
        world_raw = (directory / 'earthcraft.json').read_bytes()
        if sha_bytes(world_raw) != receipt['world_manifest_sha256']:
            raise ValueError(f'{tile}: world manifest differs from its receipt')
        world = json.loads(world_raw)
        region = (directory / 'region' / name).read_bytes()
        if receipt['regions'] != {name: sha_bytes(region)}:
            raise ValueError(f'{tile}: region differs from its receipt')
        rx, rz = tile_coords(tile)
        if world.get('world_offset_xz') != [rx * TILE_SIZE, rz * TILE_SIZE] or world['source']['size'] != TILE_SIZE:
            raise ValueError(f'{tile}: world manifest places the tile elsewhere')
        baselines, kinds = [], []
        if manifest['quality'] != 'LQ':
            from regional_install import baseline_is_extrusion
            lq_dir = directory / 'lq'
            if (lq_dir / 'region' / name).exists():
                lq_world = json.loads((lq_dir / 'earthcraft.json').read_text())
                geometry = json.loads((directory / 'receipts' / 'geometry.json').read_text())
                lq_raw = (lq_dir / 'region' / name).read_bytes()
                if geometry['regions'] != {name: sha_bytes(lq_raw)} or lq_world['world_frame'] != world['world_frame']:
                    raise ValueError(f'{tile}: LQ baseline differs from its geometry receipt')
                if not baseline_is_extrusion(lq_world):
                    raise ValueError(f'{tile}: only mapped height extrusions may be upgraded')
                baselines.append({name: lq_raw})
                kinds.append({'kind': 'tiles/v2-lq', 'region_sha256': sha_bytes(lq_raw)})
            legacy = self.v1_baseline(tile, directory)
            if legacy:
                baselines.append({name: legacy.pop('raw')})
                kinds.append(legacy)
        return {'tile': tile, 'quality': manifest['quality'], 'frame': world['world_frame'],
                'offset_xz': world['world_offset_xz'], 'size': world['source']['size'], 'incoming': {name: region},
                'receipt_sha256': sha_bytes(receipt_raw), 'baselines': baselines, 'baseline_kinds': kinds,
                'directory': directory}

    # -- install
    def backup_writer(self, tile, level_fields):
        directory = self.guard.path('backups', self.attempt, tile)

        def write(name, raw):
            if name == 'level.dat':  # holds Data.Player: keep only what the border update changes
                level_fields.setdefault(tile, border_fields(raw))
                return None
            data = gzip.compress(raw, compresslevel=1, mtime=0)
            target = self.guard.write_bytes(directory / (name + '.gz'), data)
            if gzip.decompress(target.read_bytes()) != raw:
                raise ValueError('Backup differs from the original')
            return target
        return write

    def region_writer(self):
        native = self.args.native_compression and sys.platform == 'darwin' and Path('/usr/bin/ditto').exists()

        def write(target, raw):
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.partial')
            if native:
                # Ordinary Anvil bytes with APFS transparent compression (as the old publisher did).
                staging = self.guard.write_bytes(self.guard.path('native', target.name), raw)
                subprocess.run(['/usr/bin/ditto', '--hfsCompression', str(staging), str(temporary)], check=True)
                self.guard.remove(staging)
            else:
                temporary.write_bytes(raw)
            with temporary.open('rb') as stream:
                if sha_bytes(stream.read()) != sha_bytes(raw):
                    raise ValueError('Region staging changed its bytes')
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        return write

    def install(self, requests):
        from regional_install import install_tile
        reports, level_fields = [], {}
        writer = self.region_writer()
        try:
            with save_lock(self.save):
                for request in requests:
                    report = install_tile(self.save, tile=request['tile'], quality=request['quality'],
                                          frame=request['frame'], offset_xz=request['offset_xz'],
                                          size=request['size'], incoming=request['incoming'],
                                          receipt_sha256=request['receipt_sha256'], baselines=request['baselines'],
                                          backup=self.backup_writer(request['tile'], level_fields),
                                          region_writer=writer)
                    report['baselines'] = request['baseline_kinds']
                    reports.append(report)
                    self.log(event='tile_installed', tile=request['tile'], quality=request['quality'],
                             added=report['added_chunks'], upgraded=report['upgraded_chunks'],
                             preserved=report['existing_or_edited_chunks_preserved'])
        except RuntimeError as error:
            if 'world open' in str(error).lower() or 'Save and Quit' in str(error):
                raise WorldOpen(str(error)) from error
            raise
        return reports, level_fields

    # -- backups and sync
    def upload_backups(self):
        root = self.guard.path('backups')
        if not root.exists():
            return []
        items = []
        for path in sorted(p for p in root.rglob('*') if p.is_file()):
            relative = path.relative_to(root).as_posix()
            digest = hf_store.hash_file(path)
            items.append({'path': f'{BACKUP_PREFIX}/{relative}', 'local': str(path), 'sha256': digest.sha256,
                          'bytes': digest.size})
        if not items:
            return []
        result = self.store.push(items, f'Install backups {self.attempt}')
        for item in items:
            if result['files'][item['path']]['sha256'] != item['sha256']:
                raise hf_store.VerificationError(f'Backup not verified: {item["path"]}')
        self.guard.clear('backups')
        self.log(event='backups_uploaded', files=len(items), bytes=sum(i['bytes'] for i in items),
                 commits=result['commits'], prefix=f'{BACKUP_PREFIX}/{self.attempt}/')
        return result['commits']

    def sync_world(self):
        if self.args.no_sync:
            return {'state': 'skipped (--no-sync)'}
        if not self.reader.info(['world/manifest.json']):
            return {'state': 'skipped: world/manifest.json is not on the Hub yet; run the initial world sync first'}
        python = self.args.hf_python or os.environ.get('EARTHCRAFT_HF_PYTHON')
        command = [str(python), str(Path(__file__).with_name('hf_store.py')), '--repo', self.args.repo,
                   '--scratch', str(self.guard.path('hf-sync')), '--cap', str(int(self.args.sync_cap)),
                   '--min-free', str(int(self.args.min_free)), 'push-world', '--save', str(self.save),
                   '--only-changed', '--frame', str(self.args.frame),
                   '--source-revision', hf_store.git_revision(ROOT) or 'unknown']
        completed = subprocess.run(command, capture_output=True, text=True)
        self.guard.clear('hf-sync') if self.guard.path('hf-sync').exists() else None
        if completed.returncode:
            tail = (completed.stderr or completed.stdout).strip().splitlines()[-2:]
            return {'state': 'pending: push-world failed; rerun hf_store.py push-world --only-changed',
                    'reason': ' | '.join(tail)[-500:]}
        result = next((json.loads(line) for line in reversed(completed.stdout.splitlines())
                       if line.startswith('{') and '"result"' in line), {})
        return {'state': 'synced', 'commits': result.get('commits'), 'uploaded_files': result.get('committed_files')}

    # -- run
    def run(self):
        tiles, installed = self.select()
        if self.args.dry_run:  # never touches session.lock, the network beyond reads, or the disk
            self.log(event='dry_run', tiles=tiles, already_installed=len(installed))
            return {'state': 'dry-run', 'tiles': tiles}
        probe_closed(self.save)
        self.guard.check(0, 'start')
        if self.guard.path('backups').exists():  # an earlier attempt installed but did not finish uploading
            self.upload_backups()
        requests, skipped = [], []
        for tile in tiles:
            request = self.prepare_tile(tile)
            if request.get('skip'):
                skipped.append(request)
            else:
                requests.append(request)
        if not requests:
            self.guard.clear('dl') if self.guard.path('dl').exists() else None
            return {'state': 'nothing to install', 'skipped': skipped}
        reports, level_fields = self.install(requests)
        self.guard.clear('dl')
        summary = {'attempt': self.attempt, 'installed_utc': utc_now(), 'save': self.save.name, 'reports': reports,
                   'level_dat_border_before': level_fields,
                   'note': 'level.dat is not archived publicly (player state); its border fields are recorded here.'}
        self.guard.write_json(self.guard.path('backups', self.attempt, 'installation.json'), summary)
        commits = self.upload_backups()
        sync = self.sync_world()
        return {'state': 'installed', 'tiles': [r['tile'] for r in reports], 'skipped': skipped,
                'added_chunks': sum(r['added_chunks'] for r in reports),
                'upgraded_chunks': sum(r['upgraded_chunks'] for r in reports),
                'preserved_chunks': sum(r['existing_or_edited_chunks_preserved'] for r in reports),
                'backup_commits': commits, 'world_sync': sync}


def border_fields(raw):
    from hf_store import nbt_get, nbt_unwrap
    data, _ = nbt_unwrap(raw)
    return {key: nbt_get(data, ('Data', key)) for key in ('BorderCenterX', 'BorderCenterZ', 'BorderSize',
                                                          'BorderSizeLerpTarget', 'BorderSizeLerpTime')}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--save', type=Path, default=ROOT / 'runtime/traversal/saves/Earthcraft')
    parser.add_argument('--control', type=Path, default=ROOT / 'runs/regional/control')
    parser.add_argument('--frame', type=Path, default=ROOT / 'runs/chicago-adaptation-city-001/frame.json',
                        help='frame.json passed to push-world')
    parser.add_argument('--tiles', type=lambda v: [t for t in v.split(',') if t], default=None)
    parser.add_argument('--limit', type=int, default=32, help='tiles per run (32)')
    parser.add_argument('--scratch', type=Path, default=ROOT / 'runs/scratch/install')
    parser.add_argument('--cap', type=parse_size, default=4 * GiB, help='scratch cap (4GiB)')
    parser.add_argument('--sync-cap', type=parse_size, default=3 * GiB, help='push-world scratch cap (3GiB)')
    parser.add_argument('--min-free', type=parse_size, default=40 * GiB)
    parser.add_argument('--repo', default=hf_store.DEFAULT_REPO)
    parser.add_argument('--hf-python', type=Path, default=None,
                        help='interpreter with huggingface_hub (or EARTHCRAFT_HF_PYTHON)')
    parser.add_argument('--attempt', default=None)
    parser.add_argument('--dry-run', action='store_true', help='select only; no lock, downloads or writes')
    parser.add_argument('--no-sync', action='store_true', help='skip push-world --only-changed')
    parser.add_argument('--native-compression', action=argparse.BooleanOptionalAction, default=True,
                        help='write regions with APFS transparent compression (macOS)')
    return parser


def main(argv=None, *, reader=None, store=None):
    args = build_parser().parse_args(argv)
    installer = Installer(args, reader=reader, store=store)
    if store is None and not args.dry_run:
        python = args.hf_python or os.environ.get('EARTHCRAFT_HF_PYTHON')
        if not python:
            raise SystemExit('No interpreter with huggingface_hub: pass --hf-python or set EARTHCRAFT_HF_PYTHON')
        installer.store = StoreClient(python=python, scratch=installer.guard.path('hf'), repo=args.repo,
                                      cap=GiB, min_free=args.min_free, log=installer.log)
    try:
        result = installer.run()
    except WorldOpen as error:
        installer.log(event='result', state='world_open', reason=str(error))
        return EXIT_WORLD_OPEN
    except DiskGuardError as error:
        installer.log(event='result', state='waiting_for_storage', reason=str(error))
        return 75
    finally:
        for name in ('dl', 'native', 'hf'):
            if installer.guard.path(name).exists():
                installer.guard.clear(name)
    installer.log(event='result', **result, disk=installer.guard.report())
    if installer.scratch.exists() and not any(installer.scratch.rglob('*')):
        installer.scratch.rmdir()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
