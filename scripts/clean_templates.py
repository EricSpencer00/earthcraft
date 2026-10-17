"""Public, player-free world templates for generation (dataset sources/templates/v1/).

Every metric world is built from two small templates (see world_templates):
a vanilla chunk/level template and the tall-world height datapack. The
private originals hold a real player in level.dat, so they are never
published. The generator only needs three things from them:

* one sample chunk tag from region/r.0.0.mca (its keys and version fields; every
  block-state section is replaced),
* level.dat's world settings (the generator rewrites name, game type, flat
  generator layers, spawn, border and data packs; it never reads the player),
* the datapack's pack.mcmeta and dimension_type/overworld.json files.

`build` writes a clean copy: level.dat without Data.Player, a region holding
only the sample chunk (byte-identical record, so generated regions do not
change), and the datapack as is, plus manifest.json with SHA-256s. `fetch`
downloads and verifies the published copy for contributors.

  clean_templates.py build --src runs/regional/control/templates --out runs/scratch/templates-v1
  clean_templates.py fetch --out runs/scratch/contribute/control/templates
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil

PREFIX = 'sources/templates/v1'
SCHEMA = 'earthcraft.world-templates.v1'
CHUNK = Path('chicago-water-tower-64/Arnis World 1')
HEIGHT = Path('Earthcraft-Chicago-Photo-Atlas-v2/datapacks/earthcraft_height')
PLAYER_FILES = ('playerdata', 'stats', 'advancements')
MAX_FILE = 8 * 2**20


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sample_record(region):
    """(slot, raw sector run) of the first chunk, exactly as metric_world reads it."""
    from inspect_world import chunks
    slot, _, raw = next(chunks(Path(region)))
    return slot, raw


def one_chunk_region(slot, raw):
    header = bytearray(8192)
    sectors = len(raw) // 4096
    header[slot * 4:slot * 4 + 3] = (2).to_bytes(3, 'big')
    header[slot * 4 + 3] = sectors
    return bytes(header) + raw


def strip_player(level_dat):
    import nbtlib
    level = nbtlib.load(level_dat)
    level['Data'].pop('Player', None)
    buffer = io.BytesIO()
    level.write(buffer)
    return gzip.compress(buffer.getvalue(), mtime=0)


def check(root):
    """Refuse anything that could carry player data; returns the file list."""
    import nbtlib
    root = Path(root)
    files = sorted(p for p in root.rglob('*') if p.is_file() and p.name != 'manifest.json')
    for path in files:
        relative = path.relative_to(root)
        if any(part in PLAYER_FILES for part in relative.parts) or path.stat().st_size > MAX_FILE:
            raise ValueError(f'Refusing template file {relative}')
    level = nbtlib.load(root / CHUNK / 'level.dat')
    if 'Player' in level['Data']:
        raise ValueError('Template level.dat still holds a player')
    from inspect_world import chunks
    if len(list(chunks(root / CHUNK / 'region/r.0.0.mca'))) != 1:
        raise ValueError('Template region must hold exactly the sample chunk')
    return files


def build(src, out):
    src, out = Path(src), Path(out)
    if out.exists():
        raise FileExistsError(out)
    (out / CHUNK / 'region').mkdir(parents=True)
    (out / CHUNK / 'level.dat').write_bytes(strip_player(src / CHUNK / 'level.dat'))
    slot, raw = sample_record(src / CHUNK / 'region/r.0.0.mca')
    (out / CHUNK / 'region/r.0.0.mca').write_bytes(one_chunk_region(slot, raw))
    for path in sorted((src / HEIGHT).rglob('*')):
        if path.is_file() and (path.name == 'pack.mcmeta' or path.name == 'overworld.json'):
            target = out / HEIGHT / path.relative_to(src / HEIGHT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    files = check(out)
    manifest = {'schema': SCHEMA, 'prefix': PREFIX,
                'files': [{'path': str(p.relative_to(out)), 'bytes': p.stat().st_size, 'sha256': sha(p)} for p in files],
                'sample_chunk': {'slot': slot, 'sha256': hashlib.sha256(raw).hexdigest()},
                'derivation': 'Private templates with Data.Player removed from level.dat, the region reduced to the '
                              'one sample chunk the generator reads (byte-identical record), datapack as is.',
                'player_data': False,
                'use': 'Set EARTHCRAFT_WORLD_TEMPLATES to the directory holding these files.'}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1, sort_keys=True) + '\n')
    return manifest


def fetch(out, reader=None):
    """Download sources/templates/v1 into out, verified against manifest.json; returns out."""
    from hf_store import DatasetReader, MiB
    reader = reader or DatasetReader()
    out = Path(out)
    manifest_raw = reader.read(f'{PREFIX}/manifest.json', limit=MiB)
    manifest = json.loads(manifest_raw)
    if manifest.get('schema') != SCHEMA:
        raise ValueError('Unknown template manifest schema')
    for entry in manifest['files']:
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or entry['bytes'] > MAX_FILE:
            raise ValueError(f'Refusing template path {entry["path"]}')
        target = out / relative
        if target.exists() and sha(target) == entry['sha256']:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = reader.read(f'{PREFIX}/{entry["path"]}', limit=MAX_FILE)
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError(f'Template {entry["path"]} differs from its manifest')
        target.write_bytes(raw)
    (out / 'manifest.json').write_bytes(manifest_raw)
    check(out)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    make = sub.add_parser('build')
    make.add_argument('--src', type=Path, required=True)
    make.add_argument('--out', type=Path, required=True)
    get = sub.add_parser('fetch')
    get.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'build':
        print(json.dumps(build(args.src, args.out), indent=1))
    else:
        print(fetch(args.out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
