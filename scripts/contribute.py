"""Contribute compute: generate unclaimed tiles and propose them as a dataset pull request.

Runs on anyone's machine with their own Hugging Face token. It picks tiles that
are in the plan, not yet on the dataset, and not in any open dataset pull
request; generates them with the same bounded one-pass generator the AWS host
runs (regional_generate.Worker, same caps and guards); and opens ONE pull
request on EricSpencer00/earthcraft holding only tiles/v2/<tile>/ files plus a
small manifest under contributions/. The owner's validator checks the pull
request and comments; the owner merges.

Inputs, all obtainable by anyone:
  * plan, journal and catalogs: the dataset's archive/mini-worker-20260929/
  * world templates: the dataset's sources/templates/v1/ (player-free)
  * source indexes: the dataset's sources/indexes/
  * Minecraft 1.21.10 client JAR: from your own launcher install (or Mojang's
    official download); used locally for block colours, never uploaded

  python scripts/contribute.py --tiles 20 [--shard i/N] [--client-jar PATH] [--dry-run]
  python scripts/contribute.py --check-tile TILE        (no upload; prints chunk digests)
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import contributions  # noqa: E402
from hf_store import DEFAULT_REPO, GiB, MiB, DatasetReader, parse_size  # noqa: E402
from region_pack import unpack  # noqa: E402

LEGACY_CONTROL = 'archive/mini-worker-20260929/runs/chicagoland-scans-20260929'
COVERAGE = 'sources/indexes/index-coverage.json'


def log(**event):
    print(json.dumps(event, sort_keys=True, default=str), flush=True)


def default_client_jar():
    home = Path.home()
    version = contributions.CLIENT_VERSION
    candidates = [home / 'Library/Application Support/minecraft/versions' / version / f'{version}.jar',
                  home / '.minecraft/versions' / version / f'{version}.jar']
    if os.environ.get('APPDATA'):
        candidates.append(Path(os.environ['APPDATA']) / '.minecraft/versions' / version / f'{version}.jar')
    return next((path for path in candidates if path.is_file()), None)


def client_jar(path):
    path = Path(path) if path else (Path(os.environ['EARTHCRAFT_CLIENT_JAR']) if os.environ.get('EARTHCRAFT_CLIENT_JAR')
                                    else default_client_jar())
    if path is None or not path.is_file():
        raise SystemExit(f'Minecraft {contributions.CLIENT_VERSION} client JAR not found. Start that version once '
                         'in the official launcher, or pass --client-jar PATH (see CONTRIBUTING.md).')
    digest = hashlib.sha1(path.read_bytes()).hexdigest()
    if digest != contributions.CLIENT_SHA1:
        raise SystemExit(f'{path} is not the official {contributions.CLIENT_VERSION} client.jar '
                         f'(sha1 {digest}, expected {contributions.CLIENT_SHA1})')
    return path


def prepare(workdir, reader):
    """Control directory from public dataset files only; returns (control, frame path)."""
    control = workdir / 'control'
    control.mkdir(parents=True, exist_ok=True)
    plan_path = control / 'plan.json'
    if not plan_path.exists():
        reader.download(f'{LEGACY_CONTROL}/plan.json', plan_path)
    frame_path = workdir / 'frame.json'
    if not frame_path.exists():
        frame_path.write_text(json.dumps(json.loads(plan_path.read_text())['frame']))
    import regional_generate
    regional_generate.main(['--control', str(control), '--frame', str(frame_path), '--min-free', '1GiB',
                            '--nice', '0', '--seed-control'])
    import clean_templates
    clean_templates.fetch(control / 'templates', reader)
    coverage = control / 'index-coverage.json'
    if not coverage.exists() and reader.info([COVERAGE]):
        reader.download(COVERAGE, coverage)
    return control, frame_path


def pending_in_journal(control):
    with sqlite3.connect(f'file:{control / "jobs.sqlite"}?mode=ro', uri=True) as db:
        return {row[0] for row in db.execute("SELECT tile FROM jobs WHERE stage=0 AND state='pending'")}


def parse_shard(value):
    """'i/N' -> (i, N); the same 4-tile east-west bands as the AWS hosts' --shard."""
    if not value:
        return None
    index, count = (int(part) for part in str(value).split('/'))
    if not 0 <= index < count:
        raise SystemExit(f'--shard needs 0 <= i < N, not {value}')
    return index, count


def tile_shard(tile, count):
    return (int(str(tile).rsplit('_', 1)[1]) // 4) % count


def pick(plan, count, *, on_hub, in_prs, pending, shard=None, order='reverse', only=None):
    """First `count` plan tiles (from the far end by default, away from the AWS host) still unclaimed."""
    tiles = [tile['id'] for tile in plan['tiles']]
    if order == 'reverse':
        tiles.reverse()
    chosen = []
    for tile in tiles:
        if only is not None and tile not in only:
            continue
        if shard and tile_shard(tile, shard[1]) != shard[0]:
            continue
        if tile in on_hub or tile in in_prs or tile not in pending:
            continue
        chosen.append(tile)
        if len(chosen) >= count:
            break
    return chosen


class PullRequestStore:
    """Worker store: one batch -> one dataset pull request with a contribution manifest."""

    def __init__(self, workdir, repo, user, revision, plan_sha256, cap=GiB, min_free=2 * GiB):
        import hf_store
        self.workdir, self.repo, self.user, self.revision = workdir, repo, user, revision
        self.plan_sha256, self.results = plan_sha256, []
        guard = hf_store.DiskGuard(workdir / 'hf', cap, floor_bytes=min_free)
        guard.ensure_dir()
        hf_store.bound_hf_environment(guard)
        self.ctx = hf_store.Context(repo, guard, hf_store.Hub(), None, GiB, 400, True, None)
        self.ctx.retry = hf_store.Retry(attempts=6, log=self.ctx.log)

    def manifest(self, items):
        tiles = {}
        for item in items:
            kind, tile = contributions.classify(item['path'])
            if kind == 'tile':
                tiles.setdefault(tile, {})[item['path']] = {'sha256': item['sha256'], 'bytes': item['bytes']}
        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        user = re.sub(r'[^A-Za-z0-9-]', '-', self.user)[:40]
        name = f'{contributions.MANIFEST_DIR}/{stamp}-{user}-{sorted(tiles)[0]}.json'
        record = {'schema': contributions.MANIFEST_SCHEMA, 'contributor': self.user, 'created_utc': stamp,
                  'git_sha': self.revision, 'plan_sha256': self.plan_sha256, 'tiles': tiles,
                  'client_jar_sha1': contributions.CLIENT_SHA1, 'tool': 'scripts/contribute.py',
                  'platform': platform.platform(terse=True), 'python': platform.python_version()}
        raw = (json.dumps(record, indent=1, sort_keys=True) + '\n').encode()
        local = self.workdir / 'gen' / ('_' + name.replace('/', '_'))
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(raw)
        return {'path': name, 'local': str(local), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}

    def push(self, items, label):
        import hf_store
        items = [item for item in items if contributions.classify(item['path'])[0] == 'tile']  # never status files
        manifest = self.manifest(items)
        tiles = sorted({contributions.classify(item['path'])[1] for item in items})
        title = f'Contribute {len(tiles)} tile(s): ' + ', '.join(tiles[:6]) + (' ...' if len(tiles) > 6 else '')
        try:
            result = hf_store.push_batch(self.ctx, items + [manifest], title, create_pr=True)
        finally:
            Path(manifest['local']).unlink(missing_ok=True)
        self.results.append(result)
        if result.get('pr'):
            log(event='pull_request_opened', url=result['pr'].get('url'), num=result['pr'].get('num'), tiles=tiles)
        return result


class LocalStore:
    """Worker store for --check-tile: nothing is uploaded; region chunk digests are recorded."""

    def __init__(self):
        self.regions = {}

    def push(self, items, label):
        files = {}
        for item in items:
            if item['path'].endswith(contributions.REGION_SUFFIXES):
                raw = Path(item['local']).read_bytes()
                if item['path'].endswith('.mcp'):
                    raw = unpack(raw)
                self.regions[item['path']] = {'sha256': item['sha256'], 'chunks_sha256': contributions.chunk_digest(raw)}
            files[item['path']] = {'sha256': item['sha256'], 'bytes': item['bytes'], 'commit': 'local-check'}
        return {'files': files, 'commits': []}


def worker_args(control, frame, workdir, tiles, args):
    import regional_generate
    argv = ['--control', str(control), '--frame', str(frame), '--scratch', str(workdir),
            '--worker-id', 'contrib-1', '--tiles=' + ','.join(tiles), '--limit', str(len(tiles)),
            '--batch-tiles', str(len(tiles)), '--batch-seconds', '1e9', '--batch-cap', '4GiB',
            '--cache-cap', str(args.cache_cap), '--footprint-cap', str(args.footprint_cap),
            '--min-free', str(args.min_free), '--tile-cap', str(args.tile_cap), '--hf-cap', '1GiB',
            '--templates', str(control / 'templates'), '--nice', '0']
    if args.cache_dir is not None:
        argv += ['--cache-root', str(args.cache_dir.resolve())]
    if args.check_tile:
        argv.append('--no-adopt')  # regenerate even though the tile may be on the Hub
    if not args.no_footprints:
        argv.append('--footprints')
    parsed = regional_generate.build_parser().parse_args(argv)
    parsed.prefetch = False  # only the picked tiles are generated here; never fetch sources for others
    if args.source_revision:
        parsed.source_revision = args.source_revision
    return parsed


def requeue(control, tile):
    with sqlite3.connect(control / 'jobs.sqlite') as db:
        db.execute("UPDATE jobs SET state='pending',token=NULL,owner=NULL,expires=NULL,not_before=0 "
                   "WHERE tile=? AND stage IN (0,1,2)", (tile,))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tiles', type=int, default=None, help='how many tiles to generate (1-200)')
    parser.add_argument('--tile-ids', type=lambda v: [t for t in v.split(',') if t], default=None,
                        help='only consider these plan tile ids')
    parser.add_argument('--shard', default=None, help='i/N: only tiles of this share (4-tile east-west bands)')
    parser.add_argument('--order', choices=('reverse', 'plan'), default='reverse',
                        help='reverse (default) starts at the far end of the plan, away from the AWS host')
    parser.add_argument('--client-jar', type=Path, default=None)
    parser.add_argument('--workdir', type=Path, default=ROOT / 'runs/scratch/contribute')
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--cache-cap', type=parse_size, default=16 * GiB)
    parser.add_argument('--footprint-cap', type=parse_size, default=30 * GiB)
    parser.add_argument('--min-free', type=parse_size, default=10 * GiB)
    parser.add_argument('--tile-cap', type=parse_size, default=4 * GiB)
    parser.add_argument('--no-footprints', action='store_true', help='skip the Overture footprint association')
    parser.add_argument('--source-revision', default=None, help=argparse.SUPPRESS)
    parser.add_argument('--dry-run', action='store_true', help='pick tiles and stop')
    parser.add_argument('--check-tile', default=None, help='generate one tile without uploading (validator)')
    parser.add_argument('--keep-workdir', action='store_true', help='keep the source cache for the next run')
    parser.add_argument('--cache-dir', type=Path, default=None,
                        help='share an existing source cache (e.g. the AWS host\'s) instead of workdir/cache')
    args = parser.parse_args(argv)

    shard = parse_shard(args.shard)
    workdir = args.workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    os.environ['EARTHCRAFT_CLIENT_JAR'] = str(client_jar(args.client_jar))
    reader = DatasetReader(args.repo)
    control, frame = prepare(workdir, reader)
    plan = json.loads((control / 'plan.json').read_text())
    import regional_generate

    if args.check_tile:
        requeue(control, args.check_tile)
        store = LocalStore()
        code = regional_generate.Worker(worker_args(control, frame, workdir, [args.check_tile], args),
                                        reader=reader, store=store).run()
        log(event='check_tile', tile=args.check_tile, exit=code, regions=store.regions)
        return 0 if store.regions else 1

    if not args.tiles or not 1 <= args.tiles <= contributions.MAX_TILES:
        raise SystemExit(f'Pass --tiles N (1-{contributions.MAX_TILES})')
    from chicago_tiles import digest
    import hf_store
    # Bound the Hugging Face client's caches inside workdir/hf before the client is imported.
    store = PullRequestStore(workdir, args.repo, None, args.source_revision or hf_store.git_revision(ROOT),
                             digest(plan))
    from huggingface_hub import HfApi
    api = HfApi()
    store.user = None if args.dry_run else api.whoami()['name']
    on_hub = contributions.tiles_on_hub(api, args.repo)
    in_prs = contributions.tiles_in_open_pull_requests(api, args.repo)
    chosen = pick(plan, args.tiles, on_hub=on_hub, in_prs=in_prs, pending=pending_in_journal(control), shard=shard,
                  order=args.order, only=set(args.tile_ids) if args.tile_ids else None)
    log(event='tiles_picked', tiles=chosen, on_hub=len(on_hub), in_open_prs=len(in_prs))
    if not chosen or args.dry_run:
        return 0
    code = regional_generate.Worker(worker_args(control, frame, workdir, chosen, args), reader=reader,
                                    store=store).run()
    if not args.keep_workdir:
        shutil.rmtree(workdir / 'gen', ignore_errors=True)
    opened = [r['pr'] for r in store.results if r.get('pr')]
    log(event='contribution_done', exit=code, pull_requests=opened)
    return 0 if opened else 1


if __name__ == '__main__':
    raise SystemExit(main())
