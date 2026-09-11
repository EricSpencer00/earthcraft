# Google Earth geometry → Minecraft

## Location-driven world build (2026-09-10)

For an exported **single location placemark**, save `location.kml` or
`location.kmz` in the repository root, then double-click `Build Earthcraft.command`.
No Minecraft commands are needed. Do not keep both files there at once. This
location workflow is separate from the polygon/height datapack importer
documented below.

`earth_location.py` reads bounded local KML/KMZ, selects an available cached chart
containing that point, and invokes the full replay/server-check/installation
pipeline. It prioritizes photo-enabled scans and preserves each cached chart's
actual extent rather than silently recentering it. Missing bulk storage is
reported; the locally cached 64 m Water Tower scan/photo profile still works.
Uncached locations acquire bounded AWS terrain and OpenStreetMap ways through
the public Overpass API. Only explicit metre heights create mapped building
shells; unknown heights remain absent. Roof/wall material tags route through the
local palette. Raw responses, source attribution, omissions and hashes travel
with the world. This is partial mapped coverage, not scanned facade detail or
complete worldwide building coverage.

Public acquisition can resume after an interrupted map request using the same
name/location/extent and `--public-data --resume-acquisition`. Saved terrain and
complete raw responses are reused, changed inputs fail, and requests have a
30-second retry cooldown. This recovery is before world generation; it never
overwrites an existing world. The independent frozen-world replay remains part
of every normal location build.

The KML supplies location, not Google's imagery, terrain mesh or building models.
Source provenance remains attached to the actual open-data observations used.
Polygon/area selection and multiple placemarks are currently rejected by this
location entry point. Tested equivalent coordinate invocation generated and
installed `Earthcraft-Location-Water-Tower-v2`, including 193 photo panels and two
successful Java 1.21.10 server cycles. KML/KMZ parsing has synthetic unit tests;
no user-supplied Google export was available for a live import test.

## Legacy explicit-building geometry importer

This is the active, bounded math-only route. It consumes a KML or KMZ exported
from Google Earth containing building polygon placemarks and explicit heights,
then emits a Minecraft Java datapack. It does not download, scrape, interpret,
or reconstruct from Google Maps/Earth imagery. No LLM or other model runs.

The distinction matters: a Google Earth viewport is not a supported raw-data
feed. This importer is for geometry that the operator is authorized to export
and reuse, including manually drawn building footprints with measured height
metadata. The resulting model is only as accurate as those supplied shapes and
heights.

## KML contract

Each building is one `Placemark` containing one outer `Polygon`. Give it a
positive height in one of these deterministic forms, in priority order:

1. `ExtendedData` → `Data name="height_m"` (also accepts `height`,
   `building:height`, or `building_height`);
2. a placemark name containing `height=12.5`;
3. an `extrude=1`, `altitudeMode=relativeToGround` polygon whose coordinates
   have a positive altitude.

No height means no export. This prevents guessed building heights. Holes,
terrain, appearance, interiors, and imagery-visible detail are currently
unknown rather than fabricated.

## Run

From the repository root:

```sh
python3 scripts/google_earth_to_minecraft.py path/to/location.kmz \
  --output outputs/location-kml.zip --block stone_bricks --base-y 80
```

The output path must not already exist. The importer stops above 2,000,000
solid blocks unless `--max-blocks` is explicitly raised. It consolidates each
vertical column into an inspectable Minecraft `fill` command. Place the resulting ZIP in
the target Java world's `datapacks` directory, open the world, run `/reload`,
then run `/function earthcraft:build`. It uses only `fill` commands, so
the result is inspectable and does not rely on display entities or a resource
pack. Make a new test world first; the function edits its current world.

To create that fresh creative test world from local Java metadata (without
copying any old chunks), use:

```sh
.venv/bin/python scripts/install_datapack_world.py \
  --pack outputs/location-kml.zip \
  --template-level "$HOME/Library/Application Support/minecraft/saves/Earthcraft-Photo-Surface-v2/level.dat" \
  --saves "$HOME/Library/Application Support/minecraft/saves" \
  --world-name Earthcraft-KML-Preview
```

Open `Earthcraft-KML-Preview` in Java Edition, then run
`/function earthcraft:build` once. The install step enables the datapack but
does not execute building commands on load.

Coordinates use a local east/north tangent plane centred on the input shapes:
one X/Z block is one metre. Heights are rounded up to whole blocks and become a
solid vertical extrusion from Y=0. The ZIP includes `earthcraft-manifest.json`
with the input checksum, origin, source of each height, and exact block count.

## Acceptance check

Before importing a real location, export a tiny asymmetric L-shaped fixture.
Confirm its long arm points east/west, its short arm north/south, and a known
3 m height occupies exactly Y=0, 1, and 2. The automated counterpart is
`tests/test_google_earth_to_minecraft.py`. A real location passes this slice
only after the exact datapack is run in Minecraft and compared to the KML
source; that game-load check cannot be claimed from file generation alone.

## Large-area open-data route

For broad lawful coverage, use frozen OpenStreetMap building geometry and
heights, then keep the generated KML as an inspectable interop artifact:

```sh
python3 scripts/osm_json_to_kml.py frozen-overpass.json --output outputs/location.kml
python3 scripts/google_earth_to_minecraft.py outputs/location.kml \
  --output outputs/location.zip --base-y 80
```

This is not Google scraping. It makes the same KML import available in Google
Earth for visual review while retaining OSM attribution and the source record.
