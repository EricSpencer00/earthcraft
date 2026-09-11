# Toward an Earth atlas

Earthcraft's public foundation is a bounded, deterministic geographic
exporter. It can analyze selected source features, fetch small permitted
terrain inputs, preserve provenance, and write local metric outputs. It is not
a global building scan and does not claim complete Earth coverage.

## Implemented foundation

`building_audit.py` provides deterministic per-feature analysis for a portable
GeoJSON boundary. Cook is the first production source profile. `aws_terrain.py`
fetches bounded anonymous terrain tiles and creates local metric sources. The
Chicago case study and a small terrain-only profile exercise these adapters;
they remain opt-in and are not part of the offline pull-request check.

Local catalogs can name frozen source profiles, but they are intentionally
ignored because they contain machine-local run paths. A build creates a new
save and runs block, seam, server, and installation checks where the local
profile supports them. Without a catalog, the location entry point can use a
bounded public-data path. A profile is a reproducible starting point, not an
autonomous scheduler or a promise of worldwide coverage.

## What is not solved

- global building discovery and a durable geographic tile scheduler;
- seamless travel between local metric charts;
- universal façade coverage or classification;
- global imagery fusion with verified rights and capture dates;
- independently verified complete Earth reconstruction.

## Direction

The next useful generalization is an on-demand catalog of bounded regions with
explicit source, license, projection, and artifact records. Keep continuous
observations until final voxelization. Assign each feature one stable owner
across tile boundaries and retain a context halo. Reuse unchanged source data,
but never reuse a derived artifact after its inputs or compiler have changed.

Before expanding a region, measure source coverage, projection error, runtime,
storage, game loading, and failure recovery. A second area must run without
landmark-specific code. Interrupted work must resume without reacquiring
verified inputs, and neighboring tiles must agree on shared geometry.

See [AWS source strategy](AWS_SOURCE_STRATEGY.md),
[resource limits](RESOURCE_LIMITS.md), and the [implementation plan](PLAN.md)
for the current durable decisions. The performance and whole-Earth extension
are recorded in [GLOBAL_GENERATION.md](GLOBAL_GENERATION.md); its progress
surface deliberately leaves a global denominator uncomputed until a global
catalog exists.
