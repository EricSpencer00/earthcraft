# Chicagoland measured scan pipeline

The regional pipeline expands the existing Chicago coordinate frame and save.
It first generates terrain and mapped buildings for broad coverage, then
replaces eligible height extrusions with measured roof samples and classified
building points. Geometry, source acquisition, Minecraft loading and actual
installation have separate receipts.

The frozen regional scope is the Census Chicago–Naperville–Elgin metropolitan
area plus Kenosha County, Wisconsin. The September 2026 boundary produced
95,399 chunk-aligned 512 m tiles over 24,809.6 km². Elmhurst receives the first
scan tiles. This definition does not include every county in the larger
Chicago combined statistical area. A planned tile is not completed coverage.

## Sources and quality

The source inventory uses the [Illinois county collections](https://clearinghouse.isgs.illinois.edu/lidar-county)
and the public [USGS full-density EPT archive](https://registry.opendata.aws/usgs-lidar/).
The initial paired surface/ground catalog includes Cook 2022, DuPage 2022,
McHenry 2022, DeKalb 2018, Grundy 2018, Kane 2017 and Lake 2017.
The original Arc/Info ZIP adapter adds Will 2021 and Kendall 2018. It indexes
native file bounds and fetches intersecting members rather than whole county
archives. ZIP CRCs, ETags, original member lengths and SHA-256 hashes retain
the identities of downloaded inputs. The publisher's XML verifies horizontal
NAD83(2011) and vertical NAVD88 survey feet separately.

The point inventory contains 40 intersecting Illinois, Indiana and Wisconsin
EPT candidates. Acquisition traverses every intersecting octree depth without
an overview or downsampling fallback. It retains original LAZ hashes and
direct class-6 building returns, excludes withheld points, and compares
class-2 ground returns with the chosen ground reference. Original Cook 2022
LAS members use the publisher's survey index and lossless ZIP-member cache.
The inventory contains 5,093 original Cook members; that count describes
available files, not downloaded or installed buildings.

Will 2021 original LAS members are also indexed. Its approximately 640 GB
outer ZIP contains a stored inner ZIP, so a bounded seek window exposes
individual compressed LAS members without fetching the county archive.
The frozen publisher tile index selects 4,101 original members. The adapter
checks both ZIP directories, the outer ETag, member CRCs, recovered LAS bytes,
horizontal references and the separately verified survey-foot Z reference.
Original DEFLATE payloads are preserved losslessly in gzip caches.
The tested Will member contained class-1 unclassified returns rather than
provider class-6 buildings. Cook, Will and EPT adapters can associate class-1 returns
only inside mapped building cells with valid paired DSM/DTM, more than 2 m
above ground and no more than 2 m above the measured surface. Source labels,
association masks and surface hashes remain explicit. These are spatially
associated observations; clutter and survey-date conflicts remain possible.

An optional frozen [Overture building index](https://docs.overturemaps.org/guides/buildings/)
supplements outlines missing from OSM. The 2026-09-23.1 release produced
3,840,217 features in the regional bounding rectangle, which also includes
land outside the frozen metro boundary. These are OSM, contributor and
ML-derived footprints, not measured LiDAR. Original attribution is retained
per feature in a metre SQLite R-tree, published as
`sources/indexes/regional-footprints.sqlite` (`regional_footprints.py` builds
a new one with `requirements-footprints.txt`). Pass `--footprints` to the
generator: it fetches the index into its source cache only for tiles with a
paired survey and verifies its SHA-256 and CRS before use; tile crops have
separate receipts.

Supplemental roof geometry requires valid paired DSM/DTM and more than 2 m
of measured ground clearance. Existing mapped buildings own overlapping
cells. Missing survey pixels cannot create estimated supplemental buildings.
Enabling the index affects fresh scan sources; completed immutable candidates
are not silently rewritten. Full building coverage is still an acquisition
and verification task, not a consequence of having the outline inventory.

Within paired survey coverage, mapped footprints restrict DSM roofs so trees
outside buildings do not become roof geometry. Every usable intersecting
point survey can contribute returns supported by the current DSM; returns
more than 2 m above it are rejected as surface or date conflicts. Roof side
walls are derived shells. Missing scan pixels retain explicit mapped-height
fallbacks. The native grid is sampled into one-metre Minecraft cells; one
metre is the output resolution, not a claim of survey accuracy.

Outside paired coverage, the newest usable classified point survey can
replace the mapped extrusion. Its Z values must agree with the base terrain,
and its receipt explicitly says that the absolute vertical datum is
unverified against a paired survey. An empty building-point crop retains the
base rather than claiming a measured building upgrade.

Available NAIP orthophotography supplies roof colours only on observed roof
cells. Its date and resolution are retained; it can predate the geometry.
No façade photography or complete interior reconstruction is claimed.
Google Street View is not an input to a derived geometry pipeline. The
standard [Google Maps Platform terms](https://cloud.google.com/maps-platform/terms)
restrict scraping, caching and creating content from Maps content. Separately
licensed or user-owned photographs could support photogrammetry or inferred
depth; neither becomes measured LiDAR merely by conversion.
Sources without verified units, changed frozen bytes or unsupported formats
are rejected. This pipeline does not establish that every publicly available
survey has been acquired. The unavailable Kendall original LAS link remains
a source gap; its paired DSM/DTM products and usable EPT candidates provide
the current route. Original-file inventories do not imply that all county
point payloads have been downloaded or installed.

The base uses frozen Illinois, Indiana and Wisconsin OSM way indexes
(`sources/indexes/*-ways.sqlite`) and public terrain tiles. The PBF extracts
the indexes were built from died with the external drive, so a reader trusts
the source SHA-256, size and mtime recorded inside each index instead of
re-hashing the PBF; the index file itself is still checked against its receipt
on open and stat-checked on every query, and receipts say which check was
made. `control/index-coverage.json` (from `regional_generate.py
--index-coverage`) lists the tiles that have at least one way of each index in
their padded window, so a tile opens only the state indexes it needs; the
selected ways are identical. Missing height and road-width measurements remain
explicit presentation estimates. Multipolygon relations, complete land
cover and the full Lake Michigan water relation remain missing from this
regional way-only base. These limitations are recorded in each source
manifest and must not be described as complete geographic fidelity.

## Execution and storage

Generation runs on a host with the repository virtualenv and a Hub write
token (the plan names the MacBook) and writes straight to the public dataset.
Nothing depends on an external drive, and every byte it writes counts against
one guarded footprint:

| Root | Holds | Bound |
| --- | --- | --- |
| `runs/scratch/cache/` | every source cache: terrain tiles, EPT nodes and their spatial indexes, original Cook/Will LAS and ArcGrid members, the way and footprint indexes | LRU, `--cache-cap` (12 GiB) |
| `runs/scratch/gen/<tile>/` | one tile's sources, worlds and package until its commit is verified | `--tile-cap` (4 GiB) per tile |
| `runs/scratch/gen/_hf-<worker>/` | hf_store's client caches and batch ledger | `--hf-cap` (1 GiB) |
| `runs/regional/control/` | plan, `jobs.sqlite`, catalogs, templates, failure receipts, logs | ~100 MB |

Cache, scratch and control together stay under `--footprint-cap` (25 GiB) and
the volume keeps at least `--min-free` (40 GiB) free. Writers into the cache
call `regional_cache.admit()` first: least-recently-used units are evicted,
then the cap and floor are checked, before any byte is written. Units a running
tile has used are pinned. A watcher samples scratch between checks, so a world
build that outgrows its tile cap is caught at the next check. When the guard
refuses, the worker does not crash: at start it exits with status 75 (the
supervisor waits); mid-tile it returns the tile to the queue and waits for
space; a tile that alone needs more source cache than the cap fails with an
explicit capacity receipt (raise `--cache-cap`, within the footprint, and
requeue it). Dense Cook tiles with original LAS and all three way indexes can
need about 18 GiB of cache.

One tile is one unit of work. `Journal.claim_tile` leases every unfinished
stage of a tile under one token. In `runs/scratch/gen/<tile>/` the worker
builds the sources, the LQ geometry and the measured appearance in one pass,
packages the result as `tiles/v2/<tile>/` (see [the dataset](WORLD_DATASET.md)),
and keeps only the package while its batch waits. Several tiles go into one
commit (`--batch-tiles`, default 6, or after `--batch-seconds`), because the
Hub allows about 128 commits an hour. `hf_store.py push-batch` re-hashes every
file, commits in bounded batches with backoff, and verifies each path's
SHA-256 on the Hub. Only then does the journal record each stage's dataset
receipt path, SHA-256 and commit, and the tile scratch is deleted. A lease
keeper renews leases until then. A tile whose package is already on the Hub
(a crash after the commit) is adopted rather than regenerated. Lane `base`
takes tiles never generated; lane `scans` takes tiles whose geometry
completed on the retired worker and regenerates their LQ geometry as the
upgrade baseline (and as the delivered world when no scan improves it: many
were never installed or archived).

Two interpreters are involved: the repository virtualenv runs generation and
reads the public dataset with the standard library; an interpreter with
`huggingface_hub` (for example the `hf` CLI's) runs `hf_store.py` for writes.
Set it with `--hf-python` or `EARTHCRAFT_HF_PYTHON`. Set up a control directory
once:

```sh
.venv/bin/python scripts/regional_generate.py --frame runs/chicago-adaptation-city-001/frame.json \
  --seed-control     # plan, journal and catalogs from archive/mini-worker-20260929/
```

and copy the two world templates (`chicago-water-tower-64/Arnis World 1` and
`Earthcraft-Chicago-Photo-Atlas-v2/datapacks/earthcraft_height`) into
`runs/regional/control/templates/`. They are not published: the template
`level.dat` holds a real player compound. `--prepare` rediscovers the catalogs
from the publishers instead. Then run one tile, or the supervisor:

```sh
/usr/sbin/taskpolicy -c utility .venv/bin/python scripts/regional_generate.py \
  --frame runs/chicago-adaptation-city-001/frame.json --hf-python "$HF_PYTHON" --tiles 17_54 --limit 1
.venv/bin/python scripts/regional_supervisor.py \
  --frame runs/chicago-adaptation-city-001/frame.json --hf-python "$HF_PYTHON" --footprints
.venv/bin/python scripts/regional_generate.py --frame runs/chicago-adaptation-city-001/frame.json --status
```

Workers run at nice +10 under the macOS `utility` QoS clamp
(`taskpolicy -c utility`), which yields CPU and disk to interactive work, so
Minecraft stays playable while generation runs. `--qos background`
(`taskpolicy -b`) yields more but also throttles networking: on the MacBook it
cut Hub downloads from about 20 MB/s to 0.3 MB/s, so a 3.4 GB way index took
hours. `configs/earthcraft-regional-generator.plist` is a launchd template
(not installed) for unattended runs; its comments say how to fill it in, load
it and stop it. Mind host policy: the MacBook's coordinator guard kills local
`python -u .../scripts/...` workloads (`local_compute_blocked`), so generation
there needs its owner's exemption, or another compute host with the two
interpreters and a Hub write token. The supervisor keeps one worker (two at
most on the MacBook), retries failures with backoff, stops a lane after ten,
and keeps a 45-day deadline. `supervisor-status.json`, the journal summary and
`control/logs/` record progress; `--clear-cache` empties the source cache.

The scan path collects observations before building the final geometry.
Full-density EPT node downloads use four bounded concurrent requests;
decoding and indexing remain serial within each worker to limit memory.
Unchanged frozen files are hashed once per worker and file identity instead of
for every neighbouring crop. Original point densities and node identities stay
unchanged.

The private AWS accelerator (`regional_cloud_exchange.py` and its launch and
collect scripts) is retired: it shipped staged sources from the external drive
and returned candidates and original LAZ caches to it. Any machine with the
two interpreters can now run the same one-pass generator against the same
journal semantics; it needs no exchange format.

The September 30 burst produced 24 scan/roof candidates with four workers on
a 16-vCPU instance; median per-tile generation was about 62 seconds (27 to
454 seconds). `benchmark_regional_scans.py` checks exact point arrays and
original node identities before reporting a narrower crop speedup. The full
95,399-tile regional pass remains unfinished.

## Delivery into the existing world

`scripts/hf_install.py` installs on the machine that holds the save; it
replaces the earlier bundle, SSH, rsync and remote-merge round trip. It
refuses while Minecraft holds `session.lock`, downloads the needed `tiles/v2`
packages and LQ baselines into capped scratch, and merges under one
session-lock fence with `regional_install.install_tile`. Every existing player
chunk wins unless all blocks, properties and biomes still match a recorded
extrusion baseline (`tiles/v2/<tile>/lq/` from the same pass, or the tile's
`tiles/v1` shard member). Vanilla lighting, palette order and maintenance
timestamps can change without changing geometry. Entities, block entities,
scheduled ticks and changed or cleared blocks protect the chunk. Measured
roof/point worlds are never extrusion baselines, so a candidate cannot replace
those observations. Coverage and the world border expand only when new chunks
were added; `regional-quality.json` reports chunks added, upgraded and kept.

Pre-update copies of every touched file are written as gzip to capped scratch,
uploaded to `archive/install-backups/<attempt>/`, verified and deleted locally;
`level.dat` is not archived publicly (it holds the player), only its border
fields. The changed regions then go to `world/` with `hf_store.py push-world
--only-changed`. Regions are written with macOS `ditto --hfsCompression`
(`--no-native-compression` turns it off); readable bytes and SHA-256 are
checked before the atomic rename. A representative 4.01 MiB scan region
occupied 676 KiB after compression; Minecraft can decompress a file when it
rewrites it. `--dry-run` selects tiles without touching the lock or the disk.

`regional_alignment.py` checks the inherited metre CRS, exact tile/chunk
translation, height offset, every Anvil region name/slot and complete chunk
coverage. It also checks geographic round trips for the Water Tower origin
and the Elmhurst planning anchor. The current frame places Water Tower at
X=32, Z=33 and the Elmhurst anchor at approximately X=-26,179.22, Z=-270.72.
These tests establish coordinate continuity; they do not independently
establish the geographic accuracy of each source building.

The Elmhurst pilot passed an isolated vanilla 1.21.10 load/save/reload check
and upgraded 253 chunks while preserving three changed chunks. Broader
generation receipts do not imply that every regional tile has passed game
loading or client visual inspection. Run `metric_server_check.py` against
isolated materialized candidates, and retain that proof separately.
The cloud candidate audit checked all 1,024 chunks against three current-save
frame receipts. An isolated cloud tile also passed vanilla 1.21.10 loading,
saving and reloading. These checks do not imply client visual verification
or installation of candidates while the current save remains open.
An actual scan tile rebuilt with the supplemental outlines added 97 valid
measured roof cells, retained its 45,014 observed point voxels, passed all
1,024 chunk placement checks and passed vanilla loading/saving/reloading.

Offline regressions cover vertical-unit admission, original grid selection,
point classification and tampering, mixed roof/point height envelopes, scan
gaps, chunk upgrade eligibility, guard refusals, LRU eviction and pinning, the
one-pass tile lifecycle against a fake Hub, portable receipts, and an
installer that refuses an open world. Source and derived-data licenses remain separate from the code
license; consult the publisher records before redistributing a world.
