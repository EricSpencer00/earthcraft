# Broader observed appearance: source and alignment gate

2026-09-10 overnight pass 3. Decision: add a reusable public-domain orthophoto
adapter and offline replay, but do not project unvalidated overhead pixels onto
buildings. The installed Chicago Photo Atlas v2 remains unchanged.

## Sources inspected

- [CookOrtho2022 service](https://gis.cookcountyil.gov/imagery/rest/services/CookOrtho2022/ImageServer):
  four U8 RGB/NIR bands, 0.5 US-survey-foot pixels, NAD83(2011) Illinois East.
  Its own item information and XML metadata leave license/use-limit fields blank.
  The separate [tile-index item](https://gis.cookcountyil.gov/traditional/rest/services/Ortho_Reference_Tiles/MapServer/0/iteminfo)
  restricts redistribution and requires attribution. That restriction is not
  silently transferred to the imagery, but neither is an empty license field
  treated as a broad derivative-use grant. No county imagery was downloaded.
- [USGS NAIP service](https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/info/iteminfo):
  explicitly describes public-domain NAIP imagery, natural colour and four-band
  products. The catalog includes acquisition dates, source resolution, original
  projection and stable raster names. Selected this clearer source for the probe.

## Implemented and measured

`naip_imagery.py` accepts a compatible frozen source grid, queries at most fifty
catalog records and chooses a single full-coverage four-band source. It does not
mosaic different dates. It locks the selected raster for a bounded served TIFF
crop, records source responses and hashes, verifies the returned CRS/grid, and
samples at metre-cell centres locally. NAD83 northern UTM zone follows metadata,
not a Chicago-specific CRS. Other source projections are rejected. Maximum scene
512 m, two million served pixels, 10 MiB image plus three 1 MiB metadata responses.

Chicago result `runs/chicago-naip-appearance-001`:

- source `m_4108703_se_16_060_20190802`, service OBJECTID 56383;
- capture **2019-08-02**, not service publication/update year 2025;
- source pixels 0.6 m; this is resolution, not an accuracy claim;
- 1,066,305 bytes downloaded, all 65,536 metre-grid cells valid;
- no LLM, no world edits, no geographic or material admission.

`--replay` verifies every cached source checksum and repeats normalization with
no network calls. `runs/chicago-naip-replay-001/replay.json` records exact equality
of all six arrays, including validity and diagnostic spectral masks. Archive
timestamps are not treated as image equality. Full unittest discovery: 62 pass.

## What changed the decision

`imagery_alignment_audit.py` reports all 24 intersecting county features. Ten
complete footprints clear the eight-metre image-boundary margin and are scored;
fourteen are explicitly unscored because they are clipped or too close to that
margin. Fixed Canny thresholds 50/150 and an integer ±8 m translation search are
diagnostic only. Generic image edges include shadows, trees and adjacent buildings.
Footprints need not be roof boundaries, and adjacent interleaved samples are not
independent validation. No fitted translation is applied to imagery or geography.

For Water Tower (833197), only 4/55 boundary samples lie within 1 m of any detected
image edge. Mean nearest-edge distance is 9.95 m, p95 17.94 m. The best generic-edge
fit hits the search corner `[8,8]`; it does not establish a valid correction or
prove a 10 m georeferencing error. The overlay shows the tower footprint in a dark,
low-information area. Painting those pixels onto the measured tower would not
recover stone, windows or the roof. Other buildings also have ambiguous fits.

Artifacts: `runs/chicago-naip-alignment-001/alignment.json` and
`footprint-overlay.png`. The overlay is a source diagnostic, not a game screenshot.

The initial brightness/NDVI thresholds flag zero shadow cells and only 128
vegetation candidates despite obvious appearance variation. Digital bands are not
calibrated reflectance and those masks have no independent semantic validation.
In particular, `usable_candidate` is NOT an admission mask. Do not loosen thresholds
until these masks look convenient and then claim calibrated labels.

## Next decision

Retain this adapter for source coverage and future supported ground/roof work.
Reject unconditional overhead-to-facade projection. Do not expand generic-edge
translation ranges to manufacture registration. The next appearance intervention
should test genuinely overlapping permitted facade photographs and a materially
different classical correspondence/registration method, or a supported fine
surface representation. Existing photo/scan failures remain frozen comparators.
More packaging, neutral world copies or repeated photo stickers do not improve
observed facade fidelity.
