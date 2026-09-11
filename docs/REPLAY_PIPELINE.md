# Frozen geographic replay

Installation now verifies every copied file, including photo resources and
datapacks, before publishing the save. A unique staging directory is created
outside the Minecraft saves directory. Failed copies remain there for diagnosis,
not in the world list. After the intended level-name/speed metadata changes,
macOS exclusive atomic rename publishes the complete save without replacing an
existing destination. This publication step currently requires macOS. It does
not provide interrupted-generation resume or verify actual client rendering.

Update: the default catalog now selects `chicago-photo-pilot`. The optional
photo layer is also fingerprinted and replay-compared, including texture/model
assets, mapped anchors, generated commands and initial view. See
`PHOTO_LAYER_PIPELINE.md`. Explicit `--region chicago-pilot` still produces the
neutral accepted geometry used in the first result below.

The existing builder now accepts a regional catalog and can prove that two fresh
builds have identical decoded geographic blocks before installing either result.
It does not fetch missing sources or run any LLM.

```sh
.venv/bin/python scripts/earthcraft.py --region chicago-pilot --replay-check --name Earthcraft-Chicago-Overnight-v1
```

Use a new name for every run. This example already exists and is intentionally
refused if repeated with that same name. `--catalog path/to/catalog.json` selects
another catalog with the same structure as `configs/atlas-regions.json`; source
directories must already contain compatible frozen observations. This is not yet
an arbitrary-coordinate discovery interface.

The primary world contains `build-receipt.json`: source and dependency hashes,
runtime versions, per-chunk/per-section block identity, replay comparison and
verification stage. The source snapshot is checked again before installation.
Failure retains separate diagnostic outputs and stops installation. The secondary
`-replay` world is not installed. An unfinished run is not automatically resumable.

Canonical comparison decodes every block palette, including block-state
properties, and normalizes palette order and unused entries. It retains chart
origin, CRS, size and constant vertical mapping. It excludes timestamps,
compression, lighting, entities and biomes, so the result is geographic-block
replay, not complete save equivalence or independent geographic accuracy.

The current writer still depends on cached Arnis metadata templates and the local
Minecraft texture jar. Their hashes are now explicit; they have not been removed
or turned into portable bundled assets. Real source license/date/CRS records stay
in the frozen source manifests. Hashing data does not validate its accuracy.

Measured first result: both 256 m Chicago builds and the installed copy matched
all 256 chunks and the earlier accepted atlas. Minecraft 1.21.10 server loaded,
saved and reloaded the primary world successfully. Photo details remain in the
separate Water Tower experiment, not in this new Chicago copy. Client appearance
and player-control usability remain unverified.
