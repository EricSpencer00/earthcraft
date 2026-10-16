"""Street-detail layer format: per-region block edits with per-edit provenance.

A layer lives in one directory::

    manifest.json                      schema, frame, sources, region index
    regions/r.<rx>.<rz>.jsonl.gz       one gzip JSON-lines file per region

Region file, line 1 is a header::

    {"schema": "earthcraft.detail-region.v1", "region": [rx, rz],
     "palette": ["minecraft:oak_log[axis=y]", ...]}

Every further line holds the edits of one chunk as parallel flat arrays::

    {"c": [cx, cz], "x": [...], "y": [...], "z": [...],
     "p": [...], "q": [...], "s": [...]}

``x``/``z`` are chunk-local (0..15), ``y`` is absolute. ``p`` indexes the
palette (new state). ``q`` is the precondition: ``-1`` means the cell must
currently be air; otherwise it is a palette index whose *block name* must
match the current block (properties ignored), so a sidewalk recolour only
replaces the ground block it was computed from. ``s`` indexes
``manifest.sources``: one row per source feature (a tree, a lamp, a
building facade), carrying dataset, licence, evidence class
(``observed``/``estimated``/``derived``) and source IDs such as image IDs.

Edits are unique per cell inside a layer; the builder resolves conflicts by
priority when edits are added. Gzip JSON-lines keeps the files small,
streamable and readable from a browser with ``DecompressionStream``.
"""
import gzip
import hashlib
import json
from pathlib import Path

from detail_anvil import canonical

SCHEMA = 'earthcraft.detail-layer.v1'
REGION_SCHEMA = 'earthcraft.detail-region.v1'
MUST_BE_AIR = -1
EVIDENCE = ('observed', 'estimated', 'derived')


class LayerBuilder:
    def __init__(self, name, frame, description=''):
        self.name, self.frame, self.description = name, frame, description
        self.sources = []
        self.palette = {}
        self.edits = {}  # (x, y, z) -> (priority, state, expect, source)
        self.rejected = 0

    def source(self, kind, dataset, license, evidence, **refs):
        if evidence not in EVIDENCE:
            raise ValueError(f'Evidence must be one of {EVIDENCE}')
        self.sources.append({'id': len(self.sources), 'kind': kind, 'dataset': dataset,
                             'license': license, 'evidence': evidence, **refs})
        return len(self.sources) - 1

    def _state(self, key):
        key = canonical(key)
        return self.palette.setdefault(key, len(self.palette))

    def add(self, x, y, z, state, source, expect=None, priority=0):
        """Queue one edit. ``expect=None`` requires air; otherwise a block name/state."""
        if not 0 <= source < len(self.sources):
            raise ValueError('Unknown source index')
        cell = (int(x), int(y), int(z))
        current = self.edits.get(cell)
        if current is not None and current[0] >= priority:
            self.rejected += 1
            return False
        self.edits[cell] = (priority, self._state(state),
                            MUST_BE_AIR if expect is None else self._state(expect), source)
        return True

    def get(self, x, y, z):
        """Return the queued state key at a cell, or None."""
        found = self.edits.get((int(x), int(y), int(z)))
        if found is None:
            return None
        return self.keys()[found[1]]

    def keys(self):
        ordered = [None] * len(self.palette)
        for key, index in self.palette.items():
            ordered[index] = key
        return ordered

    def counts_by_kind(self):
        counts = {}
        for _, _, _, source in self.edits.values():
            kind = self.sources[source]['kind']
            counts[kind] = counts.get(kind, 0) + 1
        return dict(sorted(counts.items()))

    def write(self, directory, guard, extra=None):
        directory = Path(directory)
        keys = self.keys()
        regions = {}
        for (x, y, z), (_, state, expect, source) in sorted(self.edits.items()):
            region = (x >> 9, z >> 9)
            chunk = (x >> 4, z >> 4)
            regions.setdefault(region, {}).setdefault(chunk, []).append((x & 15, y, z & 15, state, expect, source))
        index = {}
        for (rx, rz), chunks in sorted(regions.items()):
            used = sorted({e[3] for edits in chunks.values() for e in edits} |
                          {e[4] for edits in chunks.values() for e in edits if e[4] >= 0})
            local = {g: i for i, g in enumerate(used)}
            lines = [json.dumps({'schema': REGION_SCHEMA, 'layer': self.name, 'region': [rx, rz],
                                 'palette': [keys[g] for g in used]}, separators=(',', ':'))]
            count = 0
            for (cx, cz), edits in sorted(chunks.items(), key=lambda kv: (kv[0][1], kv[0][0])):
                lines.append(json.dumps({'c': [cx, cz], 'x': [e[0] for e in edits], 'y': [e[1] for e in edits],
                                         'z': [e[2] for e in edits], 'p': [local[e[3]] for e in edits],
                                         'q': [local[e[4]] if e[4] >= 0 else MUST_BE_AIR for e in edits],
                                         's': [e[5] for e in edits]}, separators=(',', ':')))
                count += len(edits)
            raw = gzip.compress(('\n'.join(lines) + '\n').encode(), compresslevel=9, mtime=0)
            name = f'regions/r.{rx}.{rz}.jsonl.gz'
            guard.write_bytes(directory / name, raw)
            index[f'r.{rx}.{rz}'] = {'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                                     'edits': count, 'chunks': len(chunks)}
        manifest = {'schema': SCHEMA, 'layer': self.name, 'description': self.description,
                    'frame': self.frame, 'edits': len(self.edits), 'edits_by_kind': self.counts_by_kind(),
                    'conflicts_resolved': self.rejected, 'regions': index, 'sources': self.sources}
        manifest.update(extra or {})
        guard.write_text(directory / 'manifest.json', json.dumps(manifest, indent=1))
        return manifest


def read_region_edits(path):
    """Yield (palette, (cx, cz), edits) where edits are tuples (lx, y, lz, state, expect|None, source)."""
    with gzip.open(path, 'rt') as stream:
        header = json.loads(stream.readline())
        if header.get('schema') != REGION_SCHEMA:
            raise ValueError(f'{path} is not a {REGION_SCHEMA} file')
        palette = [canonical(k) for k in header['palette']]
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            lengths = {len(row[k]) for k in 'xyzpqs'}
            if len(lengths) != 1:
                raise ValueError('Ragged edit arrays')
            edits = []
            for lx, y, lz, p, q, s in zip(row['x'], row['y'], row['z'], row['p'], row['q'], row['s']):
                if not (0 <= lx < 16 and 0 <= lz < 16 and 0 <= p < len(palette) and -1 <= q < len(palette)):
                    raise ValueError('Edit outside chunk or palette')
                edits.append((lx, y, lz, palette[p], None if q == MUST_BE_AIR else palette[q], s))
            yield header, tuple(row['c']), edits


def load_manifest(directory):
    manifest = json.loads((Path(directory) / 'manifest.json').read_text())
    if manifest.get('schema') != SCHEMA:
        raise ValueError('Not a detail layer manifest')
    for name, entry in manifest['regions'].items():
        raw = (Path(directory) / entry['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError(f'Layer region file changed: {entry["path"]}')
    return manifest
