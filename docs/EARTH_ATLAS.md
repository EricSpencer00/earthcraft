# Toward an Earth atlas

## Implemented bounded foundation

The user has now authorized generalization beyond Water Tower. The active record
is [AUTOMATIC_WORLD_GOAL.md](AUTOMATIC_WORLD_GOAL.md); historical restrictions below
are prior design gates, not a prohibition on the new explicitly requested work.

`building_audit.py` provides deterministic per-feature analysis for a bounded
catalog, with a portable GeoJSON input boundary. Cook is the first actual provider.
`aws_terrain.py` fetches small anonymous terrain tiles and creates local metric
sources, proven on Chicago and a 512 m Mount Rainier sample. Original Cook 2022
LAS is cached once on LaCie and cropped for reuse, with explicit partial coverage.
See [AWS_SOURCE_STRATEGY.md](AWS_SOURCE_STRATEGY.md) for source decisions.

`configs/atlas-regions.json` selects frozen source profiles. The existing
double-click builder now defaults to the 256 m Chicago 3D pilot; the Water Tower
regression and Rainier terrain-only proof remain separate profiles. Every build
creates a new save and runs the existing block/seam/server/install checks. A
profile is not a globally autonomous acquisition scheduler.

Not implemented: global building discovery, a durable global tile scheduler,
seamless travel between metric charts, universal facade classification, global
satellite-image fusion, or independently verified complete Earth reconstruction.

## Historical architecture proposal

Chicago is the active draft. Global generation has not started. The first global product should be an on-demand catalog of geographic regions, with a bounded local working set on the Mac and LaCie. The current one-city Arnis invocation is not a global scheduler.

## Required changes before geographic expansion

1. Finish Chicago and measure actual elapsed time, compressed output size, source coverage, failure modes, game-load performance and coordinate error. Separate source downloading, terrain writing, building writing and validation milestones.
2. Introduce a durable SQLite catalog for areas, source versions, license records, geographic footprints, projection zones, tile states, dependency hashes, attempts and artifact locations. Treat source download, terrain, structures, export and verification as distinct resumable stages. Recover leases after local process crashes.
3. Use metric regional coordinate systems and one datum/origin per export region. Validate cross-tile buildings and seams. An Earth-sized planar Minecraft world cannot be uniformly distance-preserving; connecting regional worlds requires a geographic navigation layer and explicit discontinuities.
4. Store scene tiles separately from Minecraft output so unchanged observations can be reused. Deduplicate shared source data. Bound cache and queue growth; eviction must preserve source provenance and user edits. Require explicit disk budgets per run.
5. Schedule one bounded geographic work item at a time initially. Measure before increasing concurrency. Respect provider rate limits, persist retry state and errors, and never turn missing data into a silently successful tile.
6. Add lower-detail global previews backed by the catalog, with full-detail world export only for requested areas. Count verified coverage separately from downloaded and generated coverage. Run any imagery interpretation locally, only where observations justify it.

## Acceptance evidence

A second area must run without landmark-specific code. Interrupted work must resume without rewriting completed verified tiles. Neighboring tiles must agree on shared geometry. Exported worlds must load in the supported Minecraft version. The dashboard must show per-stage evidence, storage forecasts based on measurements, and failures without implying global coverage.

The current dashboard is the observation layer for Chicago; the catalog and scheduler described here are not implemented yet.
