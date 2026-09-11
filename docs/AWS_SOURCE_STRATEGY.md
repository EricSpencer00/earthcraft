# Free elevation and building evidence

Decision: use an on-demand regional atlas, not a pre-generated global volume.
Select evidence per layer and location. Keep continuous geographic observations
until final voxelization. The current executable batch is Chicago plus a bounded
high-elevation terrain test; global coverage is not implemented or verified.

## Sources checked, 2026-09-10 UTC

| Source | Useful role | Critical limitation | Current decision |
|---|---|---|---|
| [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) | Broad bare-earth-style elevation basemap | Mixed source resolution, dates and datums; Web Mercator tile route excludes poles | Implemented anonymous, bounded, cached adapter; coarse fallback only |
| [AWS Copernicus DEM](https://registry.opendata.aws/copernicus-dem/) | Global/near-global elevation context | GLO-30/GLO-90 are DSMs including vegetation/infrastructure; 30/90 m are not building geometry | Do not use as building height or silently substitute for bare ground; adapter deferred |
| [AWS USGS LiDAR](https://registry.opendata.aws/usgs-lidar/) | Original 3D observations where public EPT coverage exists | Public EPT coverage differs from raw archive; raw `usgs-lidar` bucket is Requester Pays | Public `usgs-lidar-public` metadata only; paid bucket excluded |
| [Cook 2022 LAS](https://clearinghouse.isgs.illinois.edu/node/1879) | Detailed local 3D returns, including shaft observations lost in DSM | Classification errors, incomplete facades, large non-cloud-optimized archive members | Cache one bounded original tile on bulk drive, then crop locally per batch |

The AWS registry explicitly lists anonymous access for Terrain Tiles and the
public EPT bucket. The adapter never reads credentials, signs requests, starts
AWS compute, or supplies a Requester Pays header. Anonymous transfer does not
make local storage/compute infinite; enforce independent resource budgets.

[Terrarium encoding](https://github.com/tilezen/joerd/blob/master/docs/formats.md)
is decoded numerically, not interpreted as image colour. Transform each metre
cell centre from a local metric chart to the source tile grid. Nearest sampling
is explicit. A roughly 3–4 m tile pixel can itself be oversampled from coarser
upstream data. Keep source capture date unknown when unavailable; HTTP modification
time is not capture time. Preserve attribution and any imagery-source headers.
[Upstream sources](https://github.com/tilezen/joerd/blob/master/docs/data-sources.md)
and [attribution](https://github.com/tilezen/joerd/blob/master/docs/attribution.md)
remain part of the provenance rather than a blanket new license.

The Copernicus AWS snapshot documentation states it is a surface model and that
some 30 m tiles are absent. Missing tiles cannot simply become oceans without
separate land/water evidence. Product/view-service licensing and snapshot access
are distinct; do not assume access to every Copernicus service follows from a
public AWS object. No Copernicus bulk acquisition has been performed here.

## What per-building analysis means

Every intersecting source object stays in the denominator, including tiny clipped
objects, missing point coverage, and absent facades. A report keeps stable IDs,
source hashes, polygon extent/area, alternate footprint overlap, heightfield
statistics, raw class counts, acquisition coverage, and source-strip consistency.
An agreement between two related sources is not independent truth.

The portable audit boundary accepts WGS84 GeoJSON with stable IDs and explicit
height provenance. The first production-source adapter is Cook County; Overture,
other municipal catalogs, and global building discovery are not yet implemented.
The Cook class/spatial candidate policy is not advertised as a universal semantic
classifier. In particular class 1 and class 15 contain important tower returns,
but blindly accepting those classes everywhere can admit clutter.

Satellite imagery can supply large-scale roof/land-cover evidence where spatial
resolution, dates and registration justify it. It cannot establish hidden facade
geometry. No synthetic window bands or LLM-invented buildings are admitted.

## Promotion gates

1. Measured per-layer coverage and units before fusion; unsupported layer stays unknown.
2. Reuse frozen assets and original points; do not collapse 3D into roof maxima.
3. Per-building checks plus independent controls where available; unknown accuracy stays unknown.
4. One block/metre, explicit chart/vertical offset, no height compression.
5. Saved-block read-back, seams and exact-version game load/save/reload.
6. Client visual review remains separate; server checks do not certify appearance.

One globally flat plane cannot preserve every spherical distance. Independent
local charts preserve bounded metric geometry, but seamless chart navigation is
still a separate unimplemented feature. No current artifact is a 1:1 complete Earth.
