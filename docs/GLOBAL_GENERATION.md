# Faster generation, broader Earth coverage

This is the current engineering decision for turning Earthcraft from a
bounded Chicago experiment into a whole-Earth, on-demand atlas. It records
what the repository actually does and what a faster path must preserve.

## Verdict

Yes, independent tile workers are the right next move. A tile has a frozen
coordinate frame, immutable source receipts, and a private output directory.
The existing SQLite journal already fences leases and stage dependencies. The
missing piece was allowing more than one process to consume that queue safely.
`chicago_worker.py --workers N` now starts independent processes over the same
lease journal. Each process owns one tile at a time, and per-source cache locks
prevent two workers from downloading the same indexed LAS member.

Parallel work stops at the immutable tile boundary. Workers must not write the
same Anvil region or assemble the continuous world concurrently. Assembly and
game delivery remain single-writer stages after tile receipts pass validation.

## What the scrape process actually needs

Earthcraft is not primarily a matrix-math problem. The expensive path is a
pipeline with different bottlenecks:

1. Acquire a permitted source or reuse a verified cache entry.
2. Validate the source identity, checksum, coordinate reference system,
   vertical units, coverage, and capture metadata.
3. Project a bounded local extract into the fixed metre grid.
4. Rasterize the surface and admitted feature masks.
5. Decode large point members only when the tile has an admitted building
   observation, then crop and voxelize them.
6. Serialize Minecraft sections, hash the result, and independently read it
   back.
7. Publish the immutable tile receipt, then optionally assemble or stream it.

The numeric kernels are real, but so are network range requests, DEFLATE/LAS
decoding, source indexing, rasterization, NBT compression, checksums, storage
pressure, and the Minecraft server thread. Adding GPU work to a queue that is
waiting on source bytes or serialized Anvil writes will not improve the
acceptance measurement.

## Terrain and the repeated interior

The surface is the high-value boundary. For every admitted cell, Earthcraft
keeps the measured terrain elevation and surface material decision. Unknown
surface observations stay unknown; they are not smoothed into a claim of
accuracy.

Below that surface, the default representation is a repeated substrate block,
with a thin dirt transition and bedrock at the dimension floor. This is a
gameplay representation, not a geological observation. Above the highest
non-air block in a chunk, empty Minecraft sections are now omitted. The writer
retains full-height metadata and heightmaps, so omitted sections load as air
without inventing volume.

Buildings, roofs, water, bridges, and landmarks are exceptions to the simple
substrate rule. They are admitted only from their own source or deterministic
recipe and remain separate from the terrain surface gate.

## Why whole-Earth generation should be sparse

Precomputing every square metre of Earth would spend the budget on places the
player never visits and would create a misleading global completion number.
The whole-Earth target is instead:

- one deterministic global address and local metric chart per tile;
- immutable source indexes and bounded extracts;
- a fidelity class for landmark, urban, rural, natural, low-information, and
  ocean areas;
- content-addressed tile artifacts and halo hashes for seams;
- a bounded local cache that generates or fetches nearby tiles on demand;
- a public progress ledger that reports evidence by stage, not invented area.

Open ocean can use an analytic sea surface and compact bathymetry parameters.
Coasts, rivers, islands, ports, bridges, and lakefronts need source-backed
handling because small positional errors change navigation and are visible.

Public interactive map services are not a planetary batch backend. The durable
route is to acquire a permitted planet or regional archive once, build a
spatial index, and hand workers bounded extracts. Every source adapter keeps
its own rights and uncertainty record.

## Measurement plan

Before increasing worker count or claiming a global forecast, compare the same
frozen tile set at one, two, four, and eight workers. Record wall time, source
cache hits, network bytes, peak resident memory, CPU utilization, output bytes,
SQLite wait time, and identical region hashes. Promote a higher count only if
it improves wall time without exceeding storage, memory, or source-rate limits.

The next useful benchmark set is one dense urban tile, one residential tile,
one rural tile, one mountain tile, one coast tile, one open-ocean tile, and one
landmark overlay. A faster run is not a successful run if its seams,
provenance, or replay hashes change.

The first local sparse-writer proof used a frozen 64 × 64 terrain-only fixture
with the shared 1,024-block vertical envelope. The pre-change writer took
0.1775 s and emitted 1,024 explicit sections. The sparse writer took 0.0272 s,
emitted 176 sections, omitted 848 air sections, and expanded to the same block
payload. That is a 6.53× synthetic writer speedup, not a city or planetary
throughput claim.

The first frozen worker sweep used 64 independent 64 m terrain-only tiles,
the real `chicago_worker.py` entry point, the same source parent, and no
network or LAS work. Wall time was 3.980 s at one worker, 3.025 s at two,
2.215 s at four, and 2.042 s at eight: 1.32×, 1.80×, and 1.95× speedups.
The 16 m micro-tile sweep went the other way because process startup dominated
the work. This supports a bounded default of four to eight workers for useful
tile batches, while tiny or interactive jobs should remain single-worker.
These are synthetic throughput measurements; a source-heavy urban benchmark
still has to record cache hits, network bytes, memory, SQLite wait time, and
replay hashes before changing production defaults.

## Progress surface

`progress/earth.json` is the privacy-safe public contract. The GitHub Pages
workflow assembles `dashboard/` plus that snapshot into a static site at the
repository Pages URL, currently served at `ericspencer.us/earthcraft/`. On a
clean GitHub runner, the workflow preserves the last committed aggregate when
there is no local `runs/` journal, so a deployment does not erase measured
regional progress. The localhost backend exposes the same contract at
`/api/earth` and overlays counts from local journals without exporting machine
paths or raw source metadata.

The dashboard intentionally leaves global coverage as “not computed” until a
global catalog and denominator exist. Chicago is shown as a known working
region, not as a proxy for the planet. The public snapshot now also carries a
global cell address model: one 256 m cell contains 256 Minecraft chunks. The
Earth-scale address space is estimated for planning, while `cells` contains
only cells that are queued or backed by evidence. Missing cells are omitted,
not painted as complete. Each materialized cell reports source, geometry,
appearance, and game-verification state plus its chunk counts.

## Promotion gates

The global path is ready to broaden only when frozen inputs replay to identical
hashes, neighboring tiles agree on halos, preempted jobs resume safely, source
rights are explicit, player edits survive upgrades, and a client can traverse
generated and procedural regions without a coordinate discontinuity.
