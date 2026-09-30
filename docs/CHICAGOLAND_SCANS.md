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
Sources without verified units, changed frozen bytes or unsupported formats
are rejected. This pipeline does not establish that every publicly available
survey has been acquired. The unavailable Kendall original LAS link remains
a source gap; its paired DSM/DTM products and usable EPT candidates provide
the current route. Original-file inventories do not imply that all county
point payloads have been downloaded or installed.

The base uses frozen Illinois, Indiana and Wisconsin OSM way indexes and
public terrain tiles. Missing height and road-width measurements remain
explicit presentation estimates. Multipolygon relations, complete land
cover and the full Lake Michigan water relation remain missing from this
regional way-only base. These limitations are recorded in each source
manifest and must not be described as complete geographic fidelity.

## Execution and storage

Run generation on the authorized personal compute host with LaCie actually
mounted there. Keep SQLite journals, merge replicas and transport exports on
its internal filesystem. The exFAT bulk volume holds source data and verified
tile artifacts; many small merge writes on exFAT can stall for minutes.
Task-generated MCA files can be retained as byte-exact gzip originals.

Install the pinned test/scan dependencies, provide the original world metadata
templates, and set `EARTHCRAFT_CLIENT_JAR` to the owned Minecraft 1.21.10 JAR.
With task-specific paths supplied, prepare and start the regional run:

```sh
PYTHONPATH=scripts .venv/bin/python scripts/regional_generate.py \
  --control "$TASK_CONTROL" --bulk "$TASK_BULK" \
  --frame "$TASK_FRAME" --illinois "$TASK_ILLINOIS_PBF" --prepare

PYTHONPATH=scripts .venv/bin/python scripts/regional_supervisor.py \
  --control "$TASK_CONTROL" --bulk "$TASK_BULK" \
  --frame "$TASK_FRAME" --illinois "$TASK_ILLINOIS_PBF"
```

The supervisor owns two base workers and one scan worker. SQLite leases resume
unfinished jobs after restarts. Ten failures stop the affected worker; it
never reports unfinished jobs as complete. The default generation deadline
is fourteen days and persists across restarts. LaCie retains a 150 GiB
reserve and the control disk a 12 GiB reserve. The receiver retains a 50 GiB
reserve. A full materialized metro save can exceed the coordinator's storage;
these boundaries stop writes with explicit status rather than guaranteeing
that the entire plan fits. Restart or storage changes require checking the
remaining jobs and recorded boundary first.

`supervisor-status.json`, the SQLite stage summary and the worker logs record
generation progress. `publisher-state.json` tracks acknowledged source
receipts; `publisher-status.json` records delivery, retry and storage states.
The world's `regional-quality.json` reports chunks actually added or
upgraded and chunks preserved. A received tile with all chunks protected is
acknowledged but contributes no newly installed geometry.

## Delivery into the existing world

`regional_publish.mjs` performs lightweight coordination on the save's host.
Compile `regional_lock.c` for that host and record its SHA-256 in the private
publisher configuration. The configuration supplies the authorized SSH
alias, expected hostname/user, remote Python/repository/control/bulk paths,
legacy baseline directories, existing save path, reserve and lock-helper
identity. Each SSH operation resolves and checks that route again. Data
transfers use SSH; no public file service is created.

On an APFS save volume, the optional `native_region_compression` setting uses
macOS `ditto --hfsCompression` on incoming region files. Their readable bytes
and SHA-256 must remain identical before installation. Minecraft sees ordinary
Anvil files; native filesystem compression reduces their sector-padding disk
cost. A representative 4.01 MiB scan region occupied 676 KiB after compression.
Ratios vary, Minecraft can decompress files when rewriting them, and the disk
reserve still applies. Test this in private staging on the actual filesystem
before enabling it; a failed readable-byte check refuses installation.

The sender exports at most eight verified tile candidates and their immutable
extrusion alternatives. Under Minecraft's POSIX session lock, the coordinator
copies only the affected current region files and relevant metadata to a
private merge replica. Block comparisons run on the compute host. Every
existing player chunk wins unless all blocks, properties and biomes still
match a recorded extrusion baseline. Vanilla lighting, palette order and
maintenance timestamps can change without changing geometry. Entities,
block entities, scheduled ticks and changed or cleared blocks protect the
chunk. Existing measured roof/point worlds are excluded as extrusion
alternatives, so a base or scan candidate cannot replace those observations.

The coordinator verifies the original snapshot again, checks returned hashes,
retains verified compressed pre-update backups, and replaces touched files
atomically. Player metadata and the existing coordinate frame remain intact;
coverage and the world border expand only when new chunks were added. The
acknowledgement is durable before disposable transport files are removed.
An open world pauses delivery until its lock becomes available.

The Elmhurst pilot passed an isolated vanilla 1.21.10 load/save/reload check
and upgraded 253 chunks while preserving three changed chunks. Broader
generation receipts do not imply that every regional tile has passed game
loading or client visual inspection. Run `metric_server_check.py` against
isolated materialized candidates, and retain that proof separately.

Offline regressions cover vertical-unit admission, original grid selection,
point classification and tampering, mixed roof/point height envelopes, scan
gaps, chunk upgrade eligibility, byte-exact compression and bounded transport
unpacking. Source and derived-data licenses remain separate from the code
license; consult the publisher records before redistributing a world.
