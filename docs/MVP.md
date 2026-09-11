# Smallest building-first MVP

The MVP is a bounded Water Tower comparison: a 64 × 64 block square at the
project's one-block-per-metre setting. It is useful for testing source handling,
coordinate transforms, world writing, and inspection before a larger run.

It is not a claim of geographic or façade accuracy. The public baseline is
deterministic and does not require AI, a hosted service, a model download, or a
GUI. Optional visual experiments must remain separate from this path.

## Components

- `scripts/mvp.py` runs the pinned Arnis baseline on the fixed area and writes a
  new world, log, and manifest.
- `scripts/tower_overlay.py` creates a named, landmark-specific derived input
  for comparison. It does not rewrite the original observations or generalize
  to other buildings.
- `scripts/inspect_world.py` reads generated NBT and makes a compact playtest
  copy without overwriting an existing destination.

The checked-in tests cover source preservation, unrelated-feature preservation,
relation roles, idempotence, chunk boundaries, and retained compressed data.
The offline suite is the required contribution check; the MVP is an opt-in
local experiment.

## Run the baseline

The run requires macOS, Python 3.11, `uv`, and the pinned Arnis executable:

```sh
mkdir -p vendor
curl -fL https://github.com/louis-e/arnis/releases/download/v3.1.0/arnis-mac-universal.tar.gz \
  -o vendor/arnis-mac-universal.tar.gz
tar -xzf vendor/arnis-mac-universal.tar.gz -C vendor
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-mvp.txt
.venv/bin/python scripts/mvp.py
```

The runner prints a manifest path. Review its input hashes, source attribution,
output location, and warnings. Elevation or land-cover inputs may need network
access; a generated file is not an offline replay until its inputs are frozen.

To make a compact copy, choose a new destination:

```sh
.venv/bin/python scripts/inspect_world.py \
  "worlds/RUN/Arnis World 1" \
  --compact-copy "worlds/NEW-PLAYTEST"
```

Open a copy in a test Java world while the destination is closed. Keep the
original generated artifacts intact. File parsing and a server check do not
replace a real client load/save/reload check.

## Acceptance

Before calling the MVP useful, check the coordinate orientation, a known
dimension, the building footprint, the highest occupied block, terrain and
road behavior, and the result after a game save/reload. Record concrete
failures and unknowns. A pleasing screenshot is supporting evidence, not the
acceptance test.
