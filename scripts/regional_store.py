"""Read task-generated regional Anvil files, raw or in the legacy gzip form.

The one-pass generator keeps raw r.X.Z.mca; worlds compacted by the retired
external-drive worker (r.X.Z.mca.gz, byte-exact gzip) remain readable.
"""
from contextlib import contextmanager
import gzip
import hashlib
from pathlib import Path

from region_expansion import atomic


def region_bytes(path):
    path=Path(path)
    return path.read_bytes() if path.exists() else gzip.decompress(path.with_suffix('.mca.gz').read_bytes())


def region_hashes(world):
    region=Path(world)/'region'
    names={p.name.removesuffix('.gz') for p in region.glob('r.*.*.mca*') if p.name.endswith(('.mca','.mca.gz'))}
    return {name:hashlib.sha256(region_bytes(region/name)).hexdigest() for name in sorted(names)}


@contextmanager
def materialized(world):
    """Temporarily expose original MCA bytes to existing offline verifiers."""
    world=Path(world);created=[]
    try:
        for source in (world/'region').glob('r.*.*.mca.gz'):
            target=source.with_suffix('')
            if not target.exists():atomic(target,gzip.decompress(source.read_bytes()));created.append(target)
        yield world
    finally:
        for path in created:
            if path.exists() and path.read_bytes()==gzip.decompress(path.with_suffix('.mca.gz').read_bytes()):path.unlink()
