"""Audit dataset contributions by looking at them: render each tile, have Claude judge it locally.

One command, run whenever you like:

  python scripts/audit.py                 every open contribution pull request
  python scripts/audit.py --pr 12         one pull request
  python scripts/audit.py --latest        the pipeline's newest tile on main
  python scripts/audit.py --tiles X_Z,...  these tiles on main

For each pull request it
  1. runs the structural validator (scripts/validate_contributions.py --dry-run: hashes,
     regions, no player data, plan membership, manifest),
  2. renders a sample of its tiles at refs/pr/N (scripts/audit_render.mjs: iso, top, street),
  3. asks the local Claude Code CLI (`claude -p`, your login, no API key) to look at every
     image and score it against the rubric in .claude/commands/qa-chunks.md,
and writes runs/scratch/audit/<run>/report.json and report.md. Nothing is posted unless you
pass --comment, which adds the verdict to each pull request on Hugging Face.
"""
import argparse
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import contributions  # noqa: E402

DEFAULT_REPO = 'EricSpencer00/earthcraft'
STATUS = f'https://huggingface.co/datasets/{DEFAULT_REPO}/resolve/main/status/pipeline.json'
RUBRIC = """You are auditing Minecraft renders of real places (Chicago area, 1 block = 1 m) built from
open data. Each tile has three views: iso (oblique), top (map view), street (eye level).
Open EVERY image path listed below with the Read tool and look at it before judging.

Score each tile 1 (broken) to 5 (convincingly real) on:
terrain (continuous ground, no holes, voids, floating blocks, fake terraces),
buildings (closed walls, believable roofs not spiky noise, real massing, facades not one flat block),
streets (continuous roads, curbs, sidewalks, intersections line up),
vegetation (trees and lawns where a real place has them),
color (materials read as the real place, not saturated defaults),
seams (no chunk/tile seams, stripes, missing chunks).
A tile PASSES when no score is below 3 and nothing looks corrupt (empty, garbage blocks, wrong place).
Missing views (render failed) count as FAIL with defect "render failed".

Answer with ONLY one JSON object, no prose:
{"tiles":[{"tile":"X_Z","scores":{"terrain":n,"buildings":n,"streets":n,"vegetation":n,"color":n,"seams":n},
"verdict":"PASS"|"FAIL","defects":["concrete defect, which view"]}],
"verdict":"PASS"|"FAIL","summary":"one sentence"}

Tiles and images:
"""


def log(msg):
    print(msg, flush=True)


def hf_api():
    from huggingface_hub import HfApi
    return HfApi()


def pr_tiles(api, repo, num):
    details = api.get_discussion_details(repo_id=repo, discussion_num=num, repo_type='dataset')
    files = contributions.diff_files(contributions.pull_request_diff(details, repo, contributions.hub_token()))
    return sorted({tile for kind, tile in map(contributions.classify, files) if kind == 'tile'})


def latest_tile():
    import urllib.request
    with urllib.request.urlopen(STATUS, timeout=30) as response:
        last = json.load(response).get('last_tile')
    return [last] if last else []


def validate(num, repo):
    """Structural checks from the existing validator; returns (ok, report text)."""
    cmd = [sys.executable, str(ROOT / 'scripts/validate_contributions.py'), '--repo', repo, '--pr', str(num),
           '--dry-run', '--force', '--spot-check-rate', '0']
    run = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, timeout=1800)
    text = (run.stdout + run.stderr).strip()
    return ('FAIL' not in text and run.returncode == 0), text[-4000:]


def render(tiles, revision, out):
    node = shutil.which('node') or 'node'
    run = subprocess.run([node, str(ROOT / 'scripts/audit_render.mjs'), '--tiles', ','.join(tiles),
                          '--revision', revision, '--out', str(out)], capture_output=True, text=True, cwd=ROOT,
                         timeout=1800)
    if run.returncode != 0:
        log(run.stderr[-2000:])
    path = out / 'renders.json'
    return json.loads(path.read_text())['tiles'] if path.is_file() else [{'tile': t, 'images': {}} for t in tiles]


def judge(renders, model, out):
    """Ask the local Claude Code CLI to look at the renders. Uses your CLI login, never an API key."""
    claude = shutil.which('claude')
    if not claude:
        raise SystemExit('Claude Code CLI not found: install it and log in (claude), then run again.')
    listing = '\n'.join(f"- {r['tile']}: " + (', '.join(f'{v}={p}' for v, p in r['images'].items()) or 'NO IMAGES')
                        for r in renders)
    env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}
    cmd = [claude, '-p', RUBRIC + listing, '--output-format', 'json', '--allowedTools', 'Read',
           '--add-dir', str(out)]
    if model:
        cmd += ['--model', model]
    run = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=out, timeout=1800)
    if run.returncode != 0:
        return {'verdict': 'ERROR', 'summary': run.stderr[-500:], 'tiles': []}
    text = json.loads(run.stdout).get('result', '')
    start, end = text.find('{'), text.rfind('}')
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return {'verdict': 'ERROR', 'summary': 'judge did not return JSON: ' + text[:300], 'tiles': []}


def comment(api, repo, num, entry):
    lines = [f"**Audit {entry['verdict']}** (rendered and reviewed by scripts/audit.py)", '',
             f"Structural checks: {'PASS' if entry['structural_ok'] else 'FAIL'}",
             f"Visual review: {entry['judge'].get('verdict')}: {entry['judge'].get('summary', '')}", '']
    for tile in entry['judge'].get('tiles', []):
        if tile.get('defects'):
            lines.append(f"- {tile['tile']}: " + '; '.join(tile['defects']))
    api.comment_discussion(repo_id=repo, discussion_num=num, comment='\n'.join(lines), repo_type='dataset')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--repo', default=DEFAULT_REPO)
    parser.add_argument('--pr', type=int, action='append', help='only these pull requests')
    parser.add_argument('--latest', action='store_true', help="audit the pipeline's newest tile on main instead")
    parser.add_argument('--tiles', default=None, help='audit these tile ids on main instead (X_Z,...)')
    parser.add_argument('--sample', type=int, default=4, help='tiles rendered per pull request (4)')
    parser.add_argument('--model', default=None, help='model for claude -p (default: your CLI default)')
    parser.add_argument('--comment', action='store_true', help='post the verdict on each pull request')
    parser.add_argument('--keep', action='store_true', help='keep the renders')
    args = parser.parse_args(argv)

    run_dir = ROOT / 'runs/scratch/audit' / time.strftime('%Y%m%dT%H%M%S')
    run_dir.mkdir(parents=True, exist_ok=True)
    targets = []
    if args.tiles or args.latest:
        tiles = args.tiles.split(',') if args.tiles else latest_tile()
        targets.append({'pr': None, 'revision': 'main', 'tiles': tiles})
    else:
        api = hf_api()
        nums = args.pr or [d.num for d in contributions.open_pull_requests(api, args.repo)]
        for num in nums:
            targets.append({'pr': num, 'revision': f'refs/pr/{num}', 'tiles': pr_tiles(api, args.repo, num)})
    if not targets:
        log('No open contribution pull requests. Try --latest or --tiles X_Z.')
        return 0

    report = []
    for target in targets:
        name = f"pr{target['pr']}" if target['pr'] else 'main'
        sample = random.Random(name).sample(target['tiles'], min(args.sample, len(target['tiles'])))
        log(f"{name}: {len(target['tiles'])} tile(s), rendering {len(sample)}")
        structural_ok, structural = validate(target['pr'], args.repo) if target['pr'] else (True, 'not a PR')
        renders = render(sample, target['revision'], run_dir / name)
        verdict = judge(renders, args.model, run_dir / name)
        entry = {'target': name, 'pr': target['pr'], 'tiles': len(target['tiles']), 'sampled': sample,
                 'structural_ok': structural_ok, 'structural': structural, 'judge': verdict,
                 'verdict': 'PASS' if structural_ok and verdict.get('verdict') == 'PASS' else 'FAIL'}
        report.append(entry)
        log(f"{name}: {entry['verdict']} (structure {'ok' if structural_ok else 'FAIL'}, "
            f"visual {verdict.get('verdict')}: {verdict.get('summary', '')})")
        if args.comment and target['pr']:
            comment(hf_api(), args.repo, target['pr'], entry)

    (run_dir / 'report.json').write_text(json.dumps(report, indent=1))
    md = ['# Earthcraft audit', ''] + [
        f"- **{e['target']}** {e['verdict']}: {e['tiles']} tiles, sampled {', '.join(e['sampled'])}. "
        f"{e['judge'].get('summary', '')}" for e in report]
    (run_dir / 'report.md').write_text('\n'.join(md) + '\n')
    if not args.keep:
        for path in run_dir.glob('*/*.png'):
            path.unlink()
    log(f'Report: {run_dir / "report.md"}')
    return 0 if all(e['verdict'] == 'PASS' for e in report) else 1


if __name__ == '__main__':
    raise SystemExit(main())
