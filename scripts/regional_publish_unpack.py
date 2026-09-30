"""Strict bounded unpacking of generated regional transport bundles."""
import json
from pathlib import Path
import tarfile

from region_expansion import atomic


def unpack(bundle,destination):
    total=0
    with tarfile.open(bundle,'r') as archive:
        for entry in archive:
            name=Path(entry.name)
            if not entry.isfile() or name.is_absolute() or '..' in name.parts:
                raise ValueError('Unsafe bundle member')
            total+=entry.size
            if total>512*2**20:raise ValueError('Bundle exceeds batch storage bound')
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            raw=archive.extractfile(entry).read()
            if target.exists() and target.read_bytes()!=raw:raise ValueError('Prior immutable batch changed')
            if not target.exists():atomic(target,raw)
    return json.loads((destination/'index.json').read_text())
