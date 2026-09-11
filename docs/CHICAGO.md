# Chicago case study

Chicago is Earthcraft's larger public case study. It exercises the same
principles as the small Water Tower example with a real regional extract:
source data stays local, generated geometry remains labeled as a draft, and
the no-AI path is the baseline.

This is a map-derived city draft, not a scanned model of every façade. Arnis
supplies procedural defaults when the source has no height, material, roof, or
vegetation observation. Those defaults must not be reported as measurements.

## Inputs and boundaries

- A City of Chicago boundary layer selects the case-study area.
- A pinned Geofabrik Illinois extract supplies the OSM features and attribution
  record.
- Optional public elevation data supplies terrain context; it does not become
  building geometry merely because it is available at the same location.
- A context halo preserves complete source geometries that cross the selected
  boundary.

The source inventory should record each snapshot's retrieval date, checksum,
license, coverage, and missing attributes before generation. Keep source files,
derived XML, caches, and generated worlds outside Git by setting
`EARTHCRAFT_BULK_ROOT` to a suitable workspace.

## Pipeline

1. Verify the regional extract and write a source inventory.
2. Select features spatially from the city boundary plus context halo.
3. Preserve complete referenced geometries and building-part relationships.
4. Write a frozen local input for the pinned Arnis executable.
5. Run a bounded downtown pilot before attempting the larger rectangle.
6. Generate into a new output directory and retain the manifest and logs.
7. Treat the result as unverified until the target Java version loads, saves,
   and reloads it and the known dimensions are checked independently.

The current exporter uses a local metric approximation. It is useful for a
bounded draft, but it is not a surveyed 1:1 guarantee across the whole city.
Do not stitch independently rebased small worlds together or compress tall
buildings to fit a vanilla height range. Projection error, source gaps,
procedural defaults, and game-version compatibility belong in the release
report.

## Reproduce locally

Install the opt-in dependencies and provide a bulk workspace:

```sh
uv pip install --python .venv/bin/python -r requirements-chicago.txt
export EARTHCRAFT_BULK_ROOT=/path/to/earthcraft-data
.venv/bin/python scripts/chicago.py prepare --root "$EARTHCRAFT_BULK_ROOT/chicago"
.venv/bin/python scripts/chicago.py pilot --root "$EARTHCRAFT_BULK_ROOT/chicago"
.venv/bin/python scripts/chicago.py generate --root "$EARTHCRAFT_BULK_ROOT/chicago"
```

These commands are intentionally not part of the offline pull-request check.
They may download public source data and create large local outputs. Each stage
must stop on an existing output rather than silently replacing a previous run.

## Sources

- [City of Chicago boundary service](https://gisapps.cityofchicago.org/arcgis/rest/services/CachedMaps/AerialCache/MapServer/0)
- [Geofabrik Illinois extract](https://download.geofabrik.de/north-america/us/illinois.html)
- [OSM attribution and license](https://www.openstreetmap.org/copyright)
- [Pinned Arnis source](https://github.com/louis-e/arnis/tree/v3.1.0)

Review source terms and attribution again for every public artifact. A public
download or a generated world is not automatically redistributable.
