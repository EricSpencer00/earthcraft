"""Package current committed code with retained cloud templates and catalogs."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile


def write_seed(base, output, sources):
    with tarfile.open(base) as original, tarfile.open(output, 'w:gz', compresslevel=1) as target:
        members = original.getmembers()
        if len(members) > 10_000 or sum(m.size for m in members) > 1024 ** 3:
            raise ValueError('Unbounded seed assets')
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError('Unsafe seed member')
            if name.parts[0] == 'scripts':
                continue
            target.addfile(member, original.extractfile(member) if member.isfile() else None)
        for name, raw in sources.items():
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or path.parts[0] != 'scripts':
                raise ValueError('Unsafe code member')
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(raw), 0o644
            target.addfile(member, io.BytesIO(raw))


def build(base, output, repo):
    repo, output = Path(repo), Path(output)
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    names = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', revision, 'scripts'], cwd=repo, text=True).splitlines()
    sources = {name: subprocess.check_output(['git', 'show', f'{revision}:{name}'], cwd=repo) for name in names}
    write_seed(base, output, sources)
    proof = {'revision': revision, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
             'scripts': {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}}
    output.with_suffix('.json').write_text(json.dumps(proof, indent=2) + '\n')
    return {'revision': revision, 'code_files': len(names), 'sha256': proof['sha256']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output, args.repo)))
