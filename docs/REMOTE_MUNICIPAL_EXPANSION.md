# Remote municipal expansion

`municipal_generate.py` plans a complete municipal polygon on the existing
world's metre grid. It reads one frozen local OSM PBF, caches public AWS terrain
tiles, and writes receipt-checked 256 m staging tiles. It does not query Overpass
once per tile or create another installed Minecraft save.

The default geometry profile requires explicit mapped heights and widths.
`--presentation` adds a separate display profile for missing measurements:
mapped levels use 3 m per level; other buildings use 6 m, or 3 m for garages and
sheds; roads use class-based widths. Observed values take precedence. Every
derived attribute is recorded separately in `presentation-estimates.json`;
original OSM observations stay unchanged. Unclassified ground is shown as grass.
These are presentation choices, not survey measurements. Multipolygon relations,
complete land cover, detailed facades and independent physical accuracy remain
unverified. This bounded continuation uses the existing metric builder; it does
not complete the planned Arnis-to-atlas adapter.

Set `EARTHCRAFT_CLIENT_JAR` to an owned Minecraft 1.21.10 client JAR when a remote
worker has no Minecraft installation. The JAR supplies block texture colours.
The metric builder's existing world and height templates must also be available.
Keep bulk output on the selected storage volume and control journals on a
filesystem suitable for SQLite.

`region_expansion.py` assembles completed plans into a **closed staging copy**.
It verifies geometry receipts, world manifests, source region hashes, frame
agreement, ownership and complete chunk counts. It copies compressed Anvil
records instead of decompressing and recompressing millions of block volumes.
Every existing chunk wins, even an entirely cleared chunk. Both its compressed
NBT record and timestamp are preserved. A progress file supports restart and
rejects changed completed output. External chunk references are rejected rather
than silently discarded.

Previously visited destinations can contain automatically saved void chunks.
An explicit second pass with `--baseline`, `--ownership`, and each plan's
progress file can fill those chunks. Published ownership and the original
coverage protect previously generated geography, including player-cleared
chunks. Any non-air block, block entity, or legacy entity also prevents
replacement. Each eligible void replacement is recorded separately; the
default expansion remains conservative.

Before publishing a staging copy, retain a verified original backup, audit all
original chunk records and non-region files against it, and test representative
locations in Minecraft. Transfer only completed output and check its hashes.
Acquire the installed world's session lock and verify it still matches the
original snapshot before changing the installed path. Keep the same destination
name and launcher link, with the previous save available for recovery. If the
player has changed the original meanwhile, preserve those changes before
publication. An assembly receipt alone does not certify game loading or
geographic accuracy.
