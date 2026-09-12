# Global projection atlas

Earthcraft should not flatten Earth into one Minecraft coordinate plane. No
single flat projection can preserve metre scale worldwide, and Minecraft's
coordinate limit is much smaller than Earth's circumference. The durable
world model is therefore a sparse atlas of bounded local worlds.

This system is deterministic plumbing. It does not ask a language model to
select a projection, choose a page, resolve a seam, or decide what to build.

## Coordinate contract

WGS84 longitude, latitude, and measured elevation are the stable identity of a
place. A versioned formula maps every WGS84 coordinate to one atlas page:

1. Divide the pole-to-pole WGS84 meridian into equal-distance bands no taller
   than 16,384 metres.
2. Divide each band into enough longitude columns that a page is no wider than
   roughly 16,384 metres at its center latitude.
3. Give the page a stable address, `ec1/b####/c####`.
4. Project that page into a local metre CRS centered on the page. Interior
   pages use transverse Mercator. The two polar-cap bands use azimuthal
   equidistant charts.
5. Cover the projected geographic ownership polygon with 256-metre generation
   tiles aligned to 16-metre Minecraft chunks. Fetch a 512-metre source halo,
   but publish blocks only for the page's ownership polygon.

The formula produces about two million possible pages, but pages and tiles are
materialized only when requested. The address space is global; storage remains
sparse.

The executable reference is `scripts/global_projection.py`. Its manifest
records the geographic ownership bounds, frozen CRS WKT, sampled scale error,
generation envelope, tile size, halo, and axis convention. Page selection is
formulaic and explicitly reports `inference_used: false`.

## Seam and navigation contract

Every source feature keeps its WGS84 geometry and provenance. A page worker
clips output to the page's geographic ownership polygon. Neighboring workers
may download and process the same halo, but only the owning page may publish a
block. This makes seams repeatable and prevents duplicate ownership.

When a player crosses a page boundary, the runtime:

1. converts page-local X/Z to WGS84;
2. selects the destination page with the atlas formula;
3. projects WGS84 into the destination page's X/Z frame;
4. transfers the player while retaining measured elevation, heading, velocity,
   time, and gameplay state.

The transition is a coordinate transform, not generated content. It can be
replayed and tested offline with no network and no inference.

## Chicago compatibility

The current Chicago build stays in its frozen Water Tower-centered transverse
Mercator frame until its journal is complete. Reprojecting completed chunks
would throw away work and subtly move blocks.

Chicago becomes a deterministic high-detail regional overlay in the atlas
registry. The overlay manifest contains its WGS84 ownership polygon, existing
CRS WKT, tile journal, and a fixed priority above baseline atlas pages. Entry
and exit use the same WGS84 rebase operation as ordinary page transitions.
Nothing in the existing Chicago output needs to be regenerated merely to join
the atlas.

## Build plan

### 1. Freeze and audit the atlas formula

- Treat `earthcraft-atlas-v1` page IDs as immutable.
- Add golden addresses for Chicago, the antimeridian, equator, and both poles.
- Audit scale and angular distortion over every band before accepting the
  schema. Changing a bound after publication requires an `ec2` schema.
- Record the PROJ version and CRS WKT in every materialized page manifest.

Exit condition: the full address space has no gaps or ambiguous owners, local
round trips meet numeric tolerances, and the maximum measured distortion is
within the fidelity budget.

### 2. Generalize the current tile journal

- Replace region-specific tile names with `(atlas schema, page ID, local tile
  X, local tile Z, stage)` keys.
- Keep source, geometry, appearance, and assembly as independent idempotent
  stages with leases and content hashes.
- Put manifests and receipts in the control plane; put rasters, point subsets,
  and Anvil output in the configured bulk store.
- Derive all destinations from the page manifest so disk checks always inspect
  the volume that will receive the bytes.

Exit condition: any worker can claim any page tile, crash, and resume without
duplicating or corrupting completed output.

### 3. Register Chicago without moving it

- Generate a regional-overlay manifest from the frozen Chicago frame and plan.
- Publish its completed-tile bitmap and quality states through the same global
  cell API.
- Route locations inside the overlay polygon to Chicago; fall back to baseline
  pages for cells Chicago has not materialized.
- Add page-transition fixtures around all four sides of the Chicago overlay.

Exit condition: a WGS84 location opens the existing Chicago block data when it
exists and a baseline atlas page otherwise.

### 4. Make one non-Chicago page end to end

- Choose a test page by its formula address, not by a hand-authored projection.
- Discover elevation, surface, roads, buildings, water, and imagery through
  typed source adapters with licenses and content hashes.
- Run the existing four build stages and assemble a page-local world.
- Verify ownership clipping, halo continuity, provenance, deterministic rerun,
  and resource budgets.

Exit condition: two clean builds from the same receipts have identical output
hashes and can be entered by WGS84 coordinate.

### 5. Add runtime page rebasing

- Store current page ID with the player's geographic state.
- Prefetch adjacent pages before boundary approach.
- Rebase position and motion through WGS84 in one transaction.
- If a page is absent, enqueue its tiles and show a deterministic unavailable
  boundary; never invent terrain.

Exit condition: repeated crossings preserve location within one block, retain
player state, and never expose unowned seam blocks.

### 6. Scale the sparse control plane

- Shard journals by atlas page prefix and generation stage.
- Keep global catalog indexes immutable and content addressed.
- Schedule by source locality and page-neighbor affinity.
- Publish aggregate progress from receipts rather than scanning Anvil files.
- Cache hot pages near players; archive cold page artifacts without deleting
  their manifests.

Exit condition: adding workers increases throughput without changing output,
and an arbitrary WGS84 request is routable without enumerating the world.

## Non-goals

- No monolithic pre-generation of Earth.
- No global X/Z coordinate pretending to be metre-accurate.
- No guessed buildings, terrain, materials, or seam fixes.
- No LLM calls in discovery, addressing, projection, scheduling, generation,
  assembly, verification, or navigation.

