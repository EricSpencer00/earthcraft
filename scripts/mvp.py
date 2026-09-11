"""Generate the fixed 64m Chicago Water Tower baseline with pinned Arnis 3.1.0."""
import hashlib, json, shutil, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BBOX = '41.8968825,-87.624823,41.8974575,-87.624051'

def main():
    binary = ROOT/'vendor/arnis-mac-universal'
    if not binary.exists():
        raise SystemExit('Install the official Arnis 3.1.0 Mac binary; see docs/MVP.md.')
    version = subprocess.check_output([str(binary),'--version'],text=True)
    if '3.1.0' not in version:
        raise SystemExit('This fixture requires Arnis 3.1.0.')
    if shutil.disk_usage(ROOT).free < 21*1024**3:
        raise SystemExit('Need 1 GiB working allowance above the 20 GiB reserve.')
    owned_bytes = sum(f.stat().st_size for folder in ('vendor','worlds','runs','.venv','runtime')
                      for f in (ROOT/folder).rglob('*') if f.is_file())
    if owned_bytes > 1024**3:
        raise SystemExit('Minimal MVP project data exceeds 1 GiB; inspect before another run.')
    run = ROOT/'runs'/time.strftime('mvp-%Y%m%d-%H%M%S')
    run.mkdir(parents=True,exist_ok=False)
    output = ROOT/'worlds'/run.name
    args = [str(binary),f'--bbox={BBOX}',f'--output-dir={output}',
            '--scale=1','--mode=geo-terrain','--overture=false','--canopy-height=false',
            '--no-3d','--interior=false','--max-tree-size=small','--signage=none','--map-preview']
    frozen = ROOT/'runs/mvp/osm.json'
    if frozen.exists():
        args.append(f'--file={frozen}')
    else:
        frozen = run/'osm.json'
        args.append(f'--save-json-file={frozen}')
    started = time.monotonic()
    with (run/'generation.log').open('w') as log:
        result = subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,timeout=600)
    manifest = {'status':'generated' if result.returncode==0 else 'failed','argv':args,
                'elapsed_seconds':round(time.monotonic()-started,3),
                'arnis_version':'3.1.0','binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                'osm_sha256':hashlib.sha256(frozen.read_bytes()).hexdigest() if frozen.exists() else None,
                'sources':['OpenStreetMap contributors (ODbL)','USGS 3DEP','ESA WorldCover 2021'],
                'attribution_url':'https://www.openstreetmap.org/copyright',
                'limitations':['Arnis terrain repairs and procedural details are unvalidated',
                               'Elevation/land-cover caches are not yet frozen for offline replay',
                               'Size preflight is not a hard network-byte or memory limiter'],
                'ai_used':False,'game_load_verified':False}
    (run/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(run/'manifest.json')
    raise SystemExit(result.returncode)

if __name__=='__main__': main()
