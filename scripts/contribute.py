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
  * Minecraft 1.21.10 client JAR: from your own launcher install, or fetched
    from Mojang's official download; used locally for block colours, never uploaded

  python scripts/contribute.py                  build until Ctrl-C on every spare core
  python scripts/contribute.py --tiles 20 [--cores N] [--shard i/N] [--client-jar PATH] [--dry-run]
  python scripts/contribute.py --check-tile TILE        (no upload; prints chunk digests)

Each finished tile is uploaded at once as a commit on your one open pull
request (refs/pr/N), so nothing waits for the batch and an interrupted run
loses at most the tiles still being built. The next run extends the same
pull request until it holds MAX_TILES tiles, then opens a new one.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
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


def preflight():
    """Fail once, clearly, instead of failing every tile on a missing package."""
    from importlib.util import find_spec  # look, don't import: hf_store bounds the HF caches first
    missing = [m for m in ('lazrs', 'laspy', 'scipy', 'huggingface_hub', 'nbtlib') if find_spec(m) is None]
    if missing:
        raise SystemExit(f'Missing {", ".join(missing)}: run pip install -r requirements-contribute.txt')


def default_client_jar():
    home = Path.home()
    version = contributions.CLIENT_VERSION
    candidates = [home / 'Library/Application Support/minecraft/versions' / version / f'{version}.jar',
                  home / '.minecraft/versions' / version / f'{version}.jar']
    if os.environ.get('APPDATA'):
        candidates.append(Path(os.environ['APPDATA']) / '.minecraft/versions' / version / f'{version}.jar')
    return next((path for path in candidates if path.is_file()), None)


def download_client_jar(target, url=None, fetch=None):
    """The official client.jar from Mojang's own download server, kept only on this machine.

    The URL is the one the Minecraft launcher uses (piston-data, addressed by the file's SHA-1), so
    nothing is redistributed. The caller checks the SHA-1.
    """
    import urllib.request
    url = url or contributions.CLIENT_URL
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f'{target.name}.{os.getpid()}.part')
    log(event='client_jar_download', url=url)
    try:
        with (fetch or urllib.request.urlopen)(urllib.request.Request(url, headers={'User-Agent': 'earthcraft-contribute/1'}),
                                               timeout=120) as response, partial.open('wb') as out:
            copied = 0
            while chunk := response.read(1 << 20):
                copied += len(chunk)
                if copied > contributions.CLIENT_MAX_BYTES:
                    raise SystemExit('The client JAR download is larger than expected; stopped.')
                out.write(chunk)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def client_jar(path, workdir=None):
    explicit = Path(path) if path else (Path(os.environ['EARTHCRAFT_CLIENT_JAR'])
                                        if os.environ.get('EARTHCRAFT_CLIENT_JAR') else None)
    path = explicit or default_client_jar()
    if path is None and workdir is not None:
        path = workdir / f'minecraft-client-{contributions.CLIENT_VERSION}.jar'
        if not path.is_file():
            try:
                download_client_jar(path)
            except OSError as error:
                raise SystemExit(f'Could not download the Minecraft {contributions.CLIENT_VERSION} client JAR '
                                 f'({error}). Start that version once in the official launcher, or pass '
                                 '--client-jar PATH (see CONTRIBUTING.md).')
    if path is None or not path.is_file():
        raise SystemExit(f'Minecraft {contributions.CLIENT_VERSION} client JAR not found at {path}. '
                         'Pass --client-jar PATH (see CONTRIBUTING.md).')
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
    # The local copy of a WAL journal: a read-only open fails until its -shm file exists.
    with sqlite3.connect(control / 'jobs.sqlite') as db:
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


# The build grows outward from here (the AWS host's first full-detail disk, around Elmhurst).
# Building nearest-first closes the unbuilt ring between that disk and the regional build
# instead of starting yet another island at the plan's far edge.
FRONTIER_CENTER = (-60, 4)


def tile_xz(tile):
    x, z = str(tile).rsplit('_', 1)
    return int(x), int(z)


def pick(plan, count, *, on_hub, in_prs, pending, shard=None, order='frontier', only=None, center=FRONTIER_CENTER):
    """First `count` unclaimed plan tiles: nearest the build's centre by default (fills gaps first)."""
    tiles = [tile['id'] for tile in plan['tiles']]
    if order == 'reverse':
        tiles.reverse()
    elif order == 'frontier':
        tiles.sort(key=lambda t: (tile_xz(t)[0] - center[0]) ** 2 + (tile_xz(t)[1] - center[1]) ** 2)
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
    """Worker store: every pushed tile becomes a commit on one dataset pull request.

    `pr` is an open pull request number (several worker processes share it) or None, in
    which case the first push opens one. The pull request's single contribution manifest
    is rewritten after each push under a file lock, so it always lists exactly the tiles
    committed so far, as the validator requires.
    """

    def __init__(self, workdir, repo, user, revision, plan_sha256, cap=GiB, min_free=2 * GiB, pr=None, hf_dir=None):
        import hf_store
        self.workdir, self.repo, self.user, self.revision = workdir, repo, user, revision
        self.plan_sha256, self.results, self.pr = plan_sha256, [], pr
        guard = hf_store.DiskGuard(hf_dir or workdir / 'hf', cap, floor_bytes=min_free)
        guard.ensure_dir()
        hf_store.bound_hf_environment(guard)
        self.ctx = hf_store.Context(repo, guard, hf_store.Hub(), None, GiB, 400, True, None)
        self.ctx.retry = hf_store.Retry(attempts=6, log=self.ctx.log)

    def manifest(self, tiles, name=None, created=None):
        stamp = created or time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        user = re.sub(r'[^A-Za-z0-9-]', '-', self.user)[:40]
        name = name or f'{contributions.MANIFEST_DIR}/{stamp}-{user}-{sorted(tiles)[0]}.json'
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
        tiles = sorted({contributions.classify(item['path'])[1] for item in items})
        added = {}
        for item in items:
            added.setdefault(contributions.classify(item['path'])[1], {})[item['path']] = {
                'sha256': item['sha256'], 'bytes': item['bytes']}
        with (self.workdir / 'manifest.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # one manifest per pull request, shared by every worker
            state = load_pr_state(self.workdir)
            if self.pr is None and state and state.get('open'):
                self.pr = state['num']
            if self.pr is None:  # standalone use: the first push opens the pull request
                manifest = self.manifest(added)
                title = f'Contribute tiles from {self.user}'
                result = hf_store.push_batch(self.ctx, items + [manifest], title, create_pr=True)
                Path(manifest['local']).unlink(missing_ok=True)
                self.pr = result['pr']['num']
                save_pr_state(self.workdir, {'num': self.pr, 'url': result['pr'].get('url'), 'open': True,
                                             'manifest': manifest['path'], 'created': None, 'tiles': added,
                                             'repo': self.repo, 'user': self.user})
            else:
                state = state or {}
                result = hf_store.push_batch(self.ctx, items, label, revision=f'refs/pr/{self.pr}')
                state['tiles'] = {**state.get('tiles', {}), **added}
                manifest = self.manifest(state['tiles'], name=state.get('manifest'), created=state.get('created'))
                hf_store.push_batch(self.ctx, [manifest], f'Manifest: {len(state["tiles"])} tile(s)',
                                    revision=f'refs/pr/{self.pr}')
                Path(manifest['local']).unlink(missing_ok=True)
                state['manifest'] = manifest['path']
                save_pr_state(self.workdir, state)
        self.results.append({**result, 'pr': {'num': self.pr}})
        log(event='tiles_uploaded', pull_request=self.pr, tiles=tiles,
            total=len(load_pr_state(self.workdir).get('tiles', {})))
        return result


PR_STATE = 'pull-request.json'


def load_pr_state(workdir):
    path = Path(workdir) / PR_STATE
    return json.loads(path.read_text()) if path.is_file() else None


def save_pr_state(workdir, state):
    path = Path(workdir) / PR_STATE
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(path)


def open_pull_request(api, repo, workdir, user, plan_sha256, revision):
    """Reuse this machine's open contribution pull request while it has room, else open one."""
    state = load_pr_state(workdir)
    if state and state.get('open') and state.get('repo') == repo and state.get('user') == user:
        details = api.get_discussion_details(repo_id=repo, discussion_num=state['num'], repo_type='dataset')
        if details.status in ('open', 'draft') and len(state.get('tiles', {})) < contributions.MAX_TILES:
            return state, contributions.MAX_TILES - len(state.get('tiles', {}))
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    pr = api.create_pull_request(repo_id=repo, repo_type='dataset', title=f'Contribute tiles from {user} ({stamp})',
                                 description='Tiles built by `scripts/contribute.py`. Each tile is added as its own '
                                             'commit as soon as it is built; the validator comments PASS or FAIL.')
    safe = re.sub(r'[^A-Za-z0-9-]', '-', user)[:40]
    state = {'num': pr.num, 'url': getattr(pr, 'url', None), 'open': True, 'repo': repo, 'user': user,
             'manifest': f'{contributions.MANIFEST_DIR}/{stamp}-{safe}-pr{pr.num}.json', 'created': stamp,
             'tiles': {}, 'plan_sha256': plan_sha256, 'git_sha': revision}
    save_pr_state(workdir, state)
    log(event='pull_request_opened', num=pr.num, url=state['url'])
    return state, contributions.MAX_TILES


def default_cores(workdir, args):
    """Half the logical CPUs (generation is memory- and IO-heavy), at most 8, bounded by free disk."""
    cpus = os.cpu_count() or 2
    per_worker = args.tile_cap + GiB + GiB  # tile scratch + one-tile batch + hf scratch
    free = shutil.disk_usage(workdir).free - args.min_free - args.cache_cap
    by_disk = max(1, int(free // per_worker))
    return max(1, min(8, cpus // 2 or 1, by_disk))


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


def worker_args(control, frame, workdir, tiles, args, worker=1, workers=1):
    import regional_generate
    footprint = max(args.footprint_cap, args.cache_cap + workers * (args.tile_cap + 2 * GiB) + 2 * GiB)
    argv = ['--control', str(control), '--frame', str(frame), '--scratch', str(workdir),
            '--worker-id', f'contrib-{worker}', '--tiles=' + ','.join(tiles), '--limit', str(len(tiles)),
            '--batch-tiles', '1', '--batch-seconds', '0', '--batch-cap', '1GiB', '--workers', str(workers),
            '--cache-cap', str(args.cache_cap), '--footprint-cap', str(footprint),
            '--min-free', str(args.min_free), '--tile-cap', str(args.tile_cap), '--hf-cap', '1GiB',
            '--templates', str(control / 'templates'), '--nice', '0']
    argv += ['--cache-root', str((args.cache_dir or workdir / 'cache').resolve())]  # one cache for every worker
    if args.check_tile:
        argv.append('--no-adopt')  # regenerate even though the tile may be on the Hub
    if not args.no_footprints:
        argv.append('--footprints')
    # The runners' quality lane (scripts/aws_pipeline_setup.sh): contributed tiles match theirs.
    argv += ['--lane', 'quality', '--trees', '--facades', '--ground']
    parsed = regional_generate.build_parser().parse_args(argv)
    parsed.prefetch = False  # only the picked tiles are generated here; never fetch sources for others
    if args.source_revision:
        parsed.source_revision = args.source_revision
    return parsed


def requeue(control, tile):
    with sqlite3.connect(control / 'jobs.sqlite') as db:
        db.execute("UPDATE jobs SET state='pending',token=NULL,owner=NULL,expires=NULL,not_before=0 "
                   "WHERE tile=? AND stage IN (0,1,2)", (tile,))


def run_child(args, workdir, control, frame, reader):
    """One worker process: build the tiles it leases from the shared journal, push each to the PR."""
    import regional_generate
    state = load_pr_state(workdir)
    store = PullRequestStore(workdir, args.repo, state['user'], state.get('git_sha'), state.get('plan_sha256'),
                             pr=state['num'], hf_dir=workdir / f'hf-{args.worker}')
    tiles = args.tile_ids or []
    return regional_generate.Worker(worker_args(control, frame, workdir, tiles, args, args.worker, args.cores),
                                    reader=reader, store=store).run()


def run_round(args, workdir, chosen, cores):
    """Start `cores` worker processes over `chosen`; they share the journal's leases, so no tile is built twice."""
    me = [sys.executable, str(Path(__file__).resolve())]
    common = ['--workdir', str(workdir), '--repo', args.repo, '--cores', str(cores), '--tile-ids=' + ','.join(chosen),
              '--cache-cap', str(args.cache_cap), '--footprint-cap', str(args.footprint_cap),
              '--min-free', str(args.min_free), '--tile-cap', str(args.tile_cap)]
    if args.client_jar:
        common += ['--client-jar', str(args.client_jar)]
    if args.cache_dir:
        common += ['--cache-dir', str(args.cache_dir)]
    if args.no_footprints:
        common.append('--no-footprints')
    procs = [subprocess.Popen(me + common + ['--worker', str(i + 1)]) for i in range(cores)]
    try:
        return max(proc.wait() for proc in procs)
    except KeyboardInterrupt:  # workers finish (and upload) the tile in hand, then stop
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--tiles', type=int, default=None,
                        help='how many tiles to build, then stop (default: keep going until Ctrl-C)')
    parser.add_argument('--cores', type=int, default=None, help='worker processes (default: half the CPUs, max 8)')
    parser.add_argument('--tile-ids', type=lambda v: [t for t in v.split(',') if t], default=None,
                        help='only consider these plan tile ids')
    parser.add_argument('--shard', default=None, help='i/N: only tiles of this share (4-tile east-west bands)')
    parser.add_argument('--order', choices=('frontier', 'reverse', 'plan'), default='frontier',
                        help='frontier (default) builds outward from the finished centre, closing gaps first')
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
    parser.add_argument('--worker', type=int, default=None, help=argparse.SUPPRESS)  # internal: child process
    args = parser.parse_args(argv)

    preflight()
    shard = parse_shard(args.shard)
    workdir = args.workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    os.environ['EARTHCRAFT_CLIENT_JAR'] = str(client_jar(args.client_jar, workdir))
    reader = DatasetReader(args.repo)
    if args.worker:
        control, frame = workdir / 'control', workdir / 'frame.json'
        return run_child(args, workdir, control, frame, reader)
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

    if args.tiles is not None and args.tiles < 1:
        raise SystemExit('Pass --tiles N (N >= 1), or leave it out to build until Ctrl-C')
    from chicago_tiles import digest
    import hf_store
    from huggingface_hub import HfApi
    api = HfApi()
    cores = args.cores or default_cores(workdir, args)
    revision = args.source_revision or hf_store.git_revision(ROOT)
    user = None if args.dry_run else api.whoami()['name']
    left = args.tiles
    built = 0
    try:
        while left is None or left > 0:
            on_hub = contributions.tiles_on_hub(api, args.repo)
            in_prs = contributions.tiles_in_open_pull_requests(api, args.repo)
            room = contributions.MAX_TILES
            if not args.dry_run:
                state, room = open_pull_request(api, args.repo, workdir, user, digest(plan), revision)
            want = min(room, left if left is not None else room, cores * 4)
            # Built means a tiles/v2 folder on the Hub. The seeded journal is the September Mac mini's:
            # it marks ~24k tiles complete whose output never reached the dataset (the unbuilt ring
            # on the map), so its stage-0 state is not trusted; those tiles are requeued here.
            journal_pending = pending_in_journal(control)
            chosen = pick(plan, want, on_hub=on_hub, in_prs=in_prs, pending={t['id'] for t in plan['tiles']},
                          shard=shard, order=args.order, only=set(args.tile_ids) if args.tile_ids else None)
            stale = [tile for tile in chosen if tile not in journal_pending]
            log(event='tiles_picked', tiles=chosen, cores=cores, on_hub=len(on_hub), in_open_prs=len(in_prs),
                requeued_stale=len(stale))
            if not chosen or args.dry_run:
                break
            for tile in stale:
                requeue(control, tile)
            from chicago_tiles import Journal
            Journal(control / 'jobs.sqlite', plan).close()  # migrate the schema once, before workers race to
            before = len(load_pr_state(workdir).get('tiles', {}))
            run_round(args, workdir, chosen, cores)
            done = len(load_pr_state(workdir).get('tiles', {})) - before
            built += done
            if left is not None:
                left -= done or len(chosen)  # tiles that fail still count, so this ends
    except KeyboardInterrupt:
        log(event='stopped', reason='interrupted')
    if not args.keep_workdir:
        shutil.rmtree(workdir / 'gen', ignore_errors=True)
    state = load_pr_state(workdir) or {}
    log(event='contribution_done', tiles_built=built, pull_request=state.get('num'),
        url=f'https://huggingface.co/datasets/{args.repo}/discussions/{state["num"]}' if state.get('num') else None)
    return 0 if built or args.dry_run else 1


if __name__ == '__main__':
    raise SystemExit(main())
