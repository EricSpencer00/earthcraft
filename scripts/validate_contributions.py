"""Check open dataset pull requests that contribute tiles, and comment PASS or FAIL. Never merges.

For each open pull request on the dataset whose head commit has no verdict yet:

  1. every changed path is tiles/v2/<tile>/... or one contributions/<name>.json, nothing deleted
  2. every tile is in the frozen plan and not already on main
  3. sizes are sane (region <= 64 MiB, JSON <= 4 MiB, <= 200 tiles, <= 4 GiB)
  4. tile.json: schema, tile id, plan hash, every listed file present with its SHA-256 and size;
     receipts are dataset paths (tiles/v2/<tile>/receipts/...), no local machine paths anywhere
  5. regions are valid Anvil with 1024 chunks of parseable NBT and generator-only content:
     no players, entities, block entities or scheduled ticks (regional_tiles.audit_region)
  6. no player data files (playerdata/, stats/, advancements/, level.dat) can pass rule 1
  7. the manifest lists exactly the tiles and SHA-256s in the pull request and a git SHA
  8. spot check, about 1 pull request in 20 (deterministic): one tile is regenerated here with
     contribute.py --check-tile and its chunks must match the contribution

Then it posts one comment with a report. The owner reviews and merges.

  validate_contributions.py [--repo R] [--pr N] [--spot-check-rate 20] [--dry-run]
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]

import contributions  # noqa: E402
from hf_store import DEFAULT_REPO, DatasetReader, MiB  # noqa: E402

LEGACY_PLAN = 'archive/mini-worker-20260929/runs/chicagoland-scans-20260929/plan.json'


class Report:
    def __init__(self):
        self.checks, self.notes = [], []

    def check(self, name, ok, detail=''):
        self.checks.append((name, bool(ok), detail))
        return bool(ok)

    @property
    def passed(self):
        return bool(self.checks) and all(ok for _, ok, _ in self.checks)

    def markdown(self, pr, head, tiles):
        verdict = 'PASS' if self.passed else 'FAIL'
        lines = [f'## Earthcraft validator: **{verdict}**', '',
                 f'Pull request #{pr} at `{head[:12]}`: {len(tiles)} tile(s) ' +
                 ', '.join(f'`{t}`' for t in tiles[:12]) + (' ...' if len(tiles) > 12 else ''), '',
                 '| Check | Result | Detail |', '| --- | --- | --- |']
        for name, ok, detail in self.checks:
            lines.append(f'| {name} | {"pass" if ok else "**FAIL**"} | {str(detail)[:300].replace("|", "/")} |')
        lines += [''] + [f'- {note}' for note in self.notes]
        lines += ['', 'This bot does not merge. ' + ('The owner can review and merge this pull request.' if self.passed
                                                      else 'Fix the failures and push again, or close this request.'),
                  '', f'<!-- {contributions.VALIDATOR_MARKER} head={head} verdict={verdict} -->']
        return '\n'.join(lines)


def already_judged(details, head):
    marker = f'{contributions.VALIDATOR_MARKER} head={head}'
    return any(marker in (getattr(event, 'content', '') or '') for event in details.events
               if getattr(event, 'type', '') == 'comment')


def spot_check_due(pr, head, rate):
    return rate > 0 and int(hashlib.sha256(f'{pr}:{head}'.encode()).hexdigest(), 16) % rate == 0


def regenerate(tile, args):
    """Chunk digests of the tile regenerated on this machine (contribute.py --check-tile)."""
    command = [sys.executable, str(Path(__file__).with_name('contribute.py')), '--check-tile', tile,
               '--workdir', str(args.spot_workdir), '--keep-workdir', '--cache-cap', args.spot_cache_cap]
    if args.spot_cache_dir:
        command += ['--cache-dir', str(args.spot_cache_dir)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=args.spot_timeout)
    for line in reversed(completed.stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('event') == 'check_tile':
            return event.get('regions') or {}
    raise RuntimeError('regeneration produced no result: ' + (completed.stderr or completed.stdout)[-300:])


def validate(api, reader, repo, pr, head, files, plan, plan_sha, args, gitattributes=((), ())):
    """Report for one pull request; files is {path: 'added'|'modified'|'deleted'}.

    gitattributes: (added, removed) lines of .gitattributes; the Hub itself adds one LFS rule per large
    file, so only added rules for this pull request's own tile files are accepted.
    """
    from region_pack import unpack
    from regional_tiles import REGION_ENCODING, TILE_SCHEMA, assert_portable, audit_region, tile_coords
    report = Report()
    plan_tiles = {tile['id'] for tile in plan['tiles']}
    tiles, manifests, bad = {}, [], []
    files = dict(files)
    if contributions.GITATTRIBUTES in files:
        files.pop(contributions.GITATTRIBUTES)
        added, removed = gitattributes
        rules = [contributions.LFS_RULE.fullmatch(line.strip()) for line in added if line.strip()]
        if removed or not all(rule and contributions.classify(rule.group(1))[0] == 'tile' and rule.group(1) in files
                              for rule in rules):
            bad.append('.gitattributes (only LFS rules for this request\'s tile files may be added)')
    for path, change in sorted(files.items()):
        kind, value = contributions.classify(path)
        if change == 'deleted':
            bad.append(f'{path} (deleted)')
        elif kind == 'tile':
            tiles.setdefault(value, []).append(path)
        elif kind == 'manifest':
            manifests.append(path)
        else:
            bad.append(f'{path} ({value})')
    report.check('Only allowed paths', files and not bad, '; '.join(bad[:5]) or f'{len(files)} files')
    report.check('One contribution manifest', len(manifests) == 1, ', '.join(manifests) or 'none')
    names = sorted(tiles)
    report.check('Tile count', 0 < len(names) <= contributions.MAX_TILES, len(names))
    outside = [t for t in names if t not in plan_tiles]
    report.check('Tiles are in the plan', not outside, ', '.join(outside[:8]) or 'all in plan')
    info = reader.info(list(files), revision=head) if files else {}
    main_info = reader.info([f'tiles/v2/{t}/tile.json' for t in names])
    report.check('Tiles not already on main', not main_info,
                 ', '.join(p.split('/')[2] for p in main_info) or 'all new')
    oversize = [p for p, r in info.items() if r.size > (contributions.MAX_REGION_BYTES
                                                       if p.endswith(contributions.REGION_SUFFIXES)
                                                       else contributions.MAX_JSON_BYTES)]
    total = sum(r.size for r in info.values())
    report.check('Sane sizes', not oversize and total <= contributions.MAX_PR_BYTES,
                 (', '.join(oversize[:4]) + ' ' if oversize else '') + f'{total / MiB:.1f} MiB total')
    if not report.passed:
        return report, names
    # Content: tile.json, receipts, regions
    problems, region_problems, digests, regions = [], [], {}, {}
    for tile in names:
        base = f'tiles/v2/{tile}'
        try:
            manifest = json.loads(reader.read(f'{base}/tile.json', limit=contributions.MAX_JSON_BYTES, revision=head))
            assert_portable(manifest, f'{base}/tile.json', home='/nonexistent-home')
            if manifest.get('schema') != TILE_SCHEMA or manifest.get('tile') != tile:
                raise ValueError('tile.json schema or tile id')
            if manifest.get('plan_sha256') != plan_sha:
                raise ValueError('tile.json plan_sha256 differs from the frozen plan')
            listed = {entry['path']: entry for entry in manifest.get('files', [])}
            expected = set(listed) | {f'{base}/tile.json'}
            if expected != set(tiles[tile]):
                raise ValueError('files in the pull request differ from tile.json files[]')
            for stage, record in manifest.get('stages', {}).items():
                if not str(record.get('receipt', '')).startswith(f'{base}/receipts/'):
                    raise ValueError(f'{stage} receipt is not a dataset path in this tile')
            for path, entry in listed.items():
                remote = info.get(path)
                if remote is None or remote.size != entry['bytes'] or (remote.sha256 and remote.sha256 != entry['sha256']):
                    raise ValueError(f'{path} size or SHA-256 differs from tile.json')
                limit = (contributions.MAX_REGION_BYTES if path.endswith(contributions.REGION_SUFFIXES)
                         else contributions.MAX_JSON_BYTES)
                raw = reader.read(path, limit=limit, revision=head, expected=remote)
                if hashlib.sha256(raw).hexdigest() != entry['sha256']:
                    raise ValueError(f'{path} content differs from tile.json')
                digests[path] = entry['sha256']
                if path.endswith(contributions.REGION_SUFFIXES):
                    try:
                        if path.endswith('.mcp'):
                            decoded = entry.get('decoded') or {}
                            raw = unpack(raw)
                            if (decoded.get('encoding') != REGION_ENCODING or
                                    hashlib.sha256(raw).hexdigest() != decoded.get('sha256')):
                                raise ValueError('packed region does not rebuild its decoded .mca')
                        audit_region(raw, *tile_coords(tile))
                        regions[path] = contributions.chunk_digest(raw)
                    except Exception as error:  # noqa: BLE001 - reported under the region check
                        region_problems.append(f'{path}: {type(error).__name__}: {str(error)[:160]}')
                else:
                    assert_portable(json.loads(raw), path, home='/nonexistent-home')
            digests[f'{base}/tile.json'] = info[f'{base}/tile.json'].sha256
        except Exception as error:  # noqa: BLE001 - reported per tile
            problems.append(f'{tile}: {type(error).__name__}: {str(error)[:160]}')
    report.check('tile.json, hashes and dataset receipt paths', not problems,
                 '; '.join(problems[:4]) or f'{len(names)} tile(s) consistent')
    report.check('Valid Anvil/NBT, generator-only, no player data', regions and not region_problems,
                 '; '.join(region_problems[:4]) or f'{len(regions)} region(s) audited: 1024 chunks each, '
                 'no players, entities, block entities or ticks')
    # Contribution manifest
    try:
        record = json.loads(reader.read(manifests[0], limit=MiB, revision=head))
        listed = {path: entry['sha256'] for tile in record.get('tiles', {}).values() for path, entry in tile.items()}
        wanted = {p: d for p, d in digests.items() if not p.endswith('/tile.json')}
        ok = (record.get('schema') == contributions.MANIFEST_SCHEMA and sorted(record.get('tiles', {})) == names and
              all(listed.get(p) == d for p, d in wanted.items()) and
              len(str(record.get('git_sha', ''))) >= 7 and record.get('plan_sha256') == plan_sha)
        report.check('Contribution manifest matches', ok,
                     f"contributor {record.get('contributor')}, git {str(record.get('git_sha'))[:12]}")
    except Exception as error:  # noqa: BLE001
        report.check('Contribution manifest matches', False, f'{type(error).__name__}: {error}')
    # Spot check
    if report.passed and (args.spot_check_rate == 1 or spot_check_due(pr, head, args.spot_check_rate)):
        tile = names[int(hashlib.sha256(head.encode()).hexdigest(), 16) % len(names)]
        try:
            local = regenerate(tile, args)
            mismatched = [p for p, digest in regions.items() if p.startswith(f'tiles/v2/{tile}/') and
                          local.get(p, {}).get('chunks_sha256') != digest]
            compared = [p for p in regions if p.startswith(f'tiles/v2/{tile}/')]
            report.check(f'Spot check: regenerated {tile}', compared and not mismatched,
                         f'{len(compared)} region(s) chunk-identical' if not mismatched else
                         'differs: ' + ', '.join(mismatched))
        except Exception as error:  # noqa: BLE001
            report.check(f'Spot check: regenerated {tile}', False, f'{type(error).__name__}: {str(error)[:200]}')
    else:
        report.notes.append(f'No regeneration spot check this time (about 1 in {args.spot_check_rate}).')
    return report, names


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--pr', type=int, default=None, help='only this pull request number')
    parser.add_argument('--spot-check-rate', type=int, default=20, help='regenerate in ~1 of N (1 = always, 0 = never)')
    parser.add_argument('--spot-workdir', type=Path, default=ROOT / 'runs/scratch/validate')
    parser.add_argument('--spot-timeout', type=int, default=3600)
    parser.add_argument('--spot-cache-dir', type=Path, default=None, help='share this source cache (AWS host)')
    parser.add_argument('--spot-cache-cap', default='16GiB')
    parser.add_argument('--dry-run', action='store_true', help='print reports, do not comment')
    parser.add_argument('--force', action='store_true', help='judge again even if this head has a verdict')
    args = parser.parse_args(argv)
    import hf_store
    guard = hf_store.DiskGuard(args.spot_workdir / 'hf', hf_store.GiB, floor_bytes=2 * hf_store.GiB)
    guard.ensure_dir()
    hf_store.bound_hf_environment(guard)  # keep the client's caches inside guarded scratch
    from huggingface_hub import HfApi
    api = HfApi()
    reader = DatasetReader(args.repo)
    plan_raw = reader.read(LEGACY_PLAN, limit=256 * MiB)
    plan = json.loads(plan_raw)
    from chicago_tiles import digest
    plan_sha = digest(plan)
    results = []
    for discussion in contributions.open_pull_requests(api, args.repo):
        if args.pr is not None and discussion.num != args.pr:
            continue
        details = api.get_discussion_details(repo_id=args.repo, discussion_num=discussion.num, repo_type='dataset')
        head = api.repo_info(args.repo, repo_type='dataset', revision=details.git_reference).sha
        if already_judged(details, head) and not args.force:
            continue
        try:
            diff = contributions.pull_request_diff(details, args.repo, contributions.hub_token())
            files = contributions.diff_files(diff)
            report, tiles = validate(api, reader, args.repo, discussion.num, head, files, plan, plan_sha, args,
                                     contributions.gitattributes_lines(diff))
        except Exception as error:  # noqa: BLE001 - still answer the contributor
            report, tiles = Report(), []
            report.check('Validator ran', False, f'{type(error).__name__}: {str(error)[:200]}')
            traceback.print_exc()
        text = report.markdown(discussion.num, head, tiles)
        if not args.dry_run:
            api.comment_discussion(repo_id=args.repo, discussion_num=discussion.num, comment=text, repo_type='dataset')
        results.append({'pr': discussion.num, 'head': head, 'verdict': 'PASS' if report.passed else 'FAIL',
                        'tiles': tiles, 'commented': not args.dry_run})
        print(text if args.dry_run else json.dumps(results[-1]), flush=True)
    print(json.dumps({'event': 'validated', 'pull_requests': results}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
