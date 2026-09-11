# Google Earth geometry → Minecraft

Earthcraft can use an authorized KML or KMZ as a geometry interchange format.
The importer reads supplied coordinates and explicit heights; it does not
scrape Google imagery, download a Google terrain mesh, or infer hidden
buildings. The result is only as accurate as the supplied shapes and metadata.

The public baseline remains no-AI. No model is needed for this route.

## Location workflow

For one location placemark, put `location.kml` or `location.kmz` in the
repository root and run `Build Earthcraft.command`. Do not keep both files
there at once. The helper selects a compatible cached chart when a local
catalog is available or performs a bounded open-data acquisition when it is
not. Use `--public-data` to bypass cached charts deliberately.

The KML supplies the location, not Google's imagery or building models. Raw
open-data responses, attribution, omissions, and hashes remain part of the
generated manifest. Polygon selection and multiple placemarks are rejected by
the location entry point until their semantics are defined.

## Explicit-building contract

Each building is one `Placemark` containing one outer `Polygon` and a positive
height in one of these deterministic forms, in priority order:

1. `ExtendedData` with `height_m`, `height`, `building:height`, or
   `building_height`;
2. a placemark name containing `height=12.5`;
3. `extrude=1` with `altitudeMode=relativeToGround` and positive coordinate
   altitudes.

If no height is supplied, the building is omitted rather than guessed. Holes,
terrain, appearance, interiors, and details hidden from the input remain
unknown.

## Run

From the repository root:

```sh
python3 scripts/google_earth_to_minecraft.py path/to/location.kmz \
  --output outputs/location-kml.zip --block stone_bricks --base-y 80
```

The output path must not already exist. The importer limits solid blocks unless
`--max-blocks` is raised explicitly, consolidates columns into inspectable
Minecraft `fill` commands, and writes an `earthcraft-manifest.json` containing
the input checksum, local origin, height provenance, and block count.

Install the resulting ZIP in a fresh Java test world, run `/reload`, then run
`/function earthcraft:build`. The function edits the current world, so do not
use a save containing player work.

Coordinates use a local east/north tangent plane centered on the input shapes:
one X/Z block is one metre. Heights are rounded up to whole blocks and become
solid vertical extrusions from the configured base.

## Acceptance check

Before importing a real location, export an asymmetric L-shaped fixture. Check
that its long arm points east/west, its short arm points north/south, and a
known 3 m height occupies exactly three blocks. The automated counterpart is
`tests/test_google_earth_to_minecraft.py`. A real location passes only after
the exact datapack is loaded and compared with the KML source.

## Open-data interop

For a broad lawful route, convert frozen OpenStreetMap geometry to KML and keep
the source record with it:

```sh
python3 scripts/osm_json_to_kml.py frozen-overpass.json --output outputs/location.kml
python3 scripts/google_earth_to_minecraft.py outputs/location.kml \
  --output outputs/location.zip --base-y 80
```

This is an inspectable interop step, not Google scraping. Preserve OSM
attribution and review the terms of every source before distributing a world.
