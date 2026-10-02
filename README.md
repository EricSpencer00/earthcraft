# Earthcraft

Real places, block by block.

Earthcraft builds real geography in Minecraft Java at one block per metre.
Maps set roads, water, and building footprints. Elevation data sets the ground.
LiDAR adds building height and shape where scans are available. Chicago and
Elmhurst are the current working areas.

[Explore the world map](https://ericspencer.us/earthcraft/) ·
[Build plan](docs/PLAN.md) · [World snapshot](docs/WORLD_DATASET.md)

![Early Earthcraft build around the Chicago Water Tower](docs/photos/watertower-sep-10-26.png)

*Water Tower, Chicago. Project screenshot from 10 September 2026. Nearby
buildings were still partly reconstructed; this image is not an accuracy check.*

## What exists today

The **1 October 2026 world snapshot** records 1,755 installed 512 m tiles:
1,714 base tiles and 41 tiles with scan or roof upgrades. Chicagoland coverage
is partial. Installation records show what reached the save; building accuracy
still needs independent checks. See [release evidence](docs/PUBLIC_RELEASE.md).

The complete generated save is backed up in a **private Hugging Face dataset**:
111 geographic archives, 2.74 GB compressed. Uploads and selected downtown and
Elmhurst restores passed checksum checks. The restored save has not been
launched in Minecraft. [Restore instructions and access](docs/WORLD_DATASET.md).

The explorer shows a separate Chicago generation record with **256 m cells**.
Source data, generated geometry, surface detail, and Minecraft checks have
separate counts. Green cells show generated terrain and buildings; they do not
establish that those cells were imported into the game or checked for accuracy.

Street-level appearance is the next step. The imagery adapter exports
coordinate-checked candidates, but it has not colorized the live buildings.
The [appearance plan](docs/CV_BUILDING_POLISH.md) covers registered photos,
wall color, windows, and entrances for an Elmhurst and downtown pilot.

## How it fits together

[Arnis](https://github.com/louis-e/arnis) is the vendored map-to-block baseline.
Earthcraft adds coordinates, source records, tile coverage, navigation, and
imports into one Minecraft save. Scan-based building and landmark detail can
then refine the baseline.

That shared path is still being connected. Elmhurst and the generic location
builder retain their own map-to-block code, and the live importer currently
accepts only the Chicago coordinate frame. Arnis output enters a staging area
before import. See the [architecture and implementation status](docs/SPARSE_EARTH_ARCHITECTURE.md).

The default pipeline uses local, frozen inputs and deterministic geometry.
Optional computer-vision experiments are disabled by default. Whole-Earth
generation remains an on-demand design: places are built as needed. The
[global generation notes](docs/GLOBAL_GENERATION.md),
[projection atlas](docs/GLOBAL_PROJECTION_ATLAS.md), and
[Chicago performance measurements](docs/FAST_GENERATION.md) describe that work.

## Run the tests

Use Python 3.11. The offline suite needs neither a Minecraft installation nor
geographic downloads.

```sh
git clone https://github.com/EricSpencer00/earthcraft.git
cd earthcraft
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements-test.txt
PYTHONPATH=scripts .venv/bin/python -m unittest discover -s tests -v
```

The suite checks coordinates, source handling, world writing, imports, replay,
and appearance observations. GitHub also checks the explorer in a browser at
phone, tablet, and desktop sizes and saves screenshots with the test results.

Geographic generation needs macOS, a separate Minecraft Java installation,
and local input manifests. Start with the [smallest world experiment](docs/MVP.md)
or the [geometry-only KML importer](docs/GOOGLE_EARTH_KML.md). The
[Chicago supervisor](scripts/earthcraft_supervisor.py) supports unattended runs
on the external data volume; the generation workflow requires the configured
`earthcraft-lacie` runner. Runtime compatibility is recorded with each snapshot.

## Contribute

Keep code, tests, synthetic fixtures, and measured findings in this repository.
Worlds, raw scans, source photographs, archives, credentials, and machine-local
paths stay out of source history. Playable snapshots have their own artifact
and attribution workflow. See [CONTRIBUTING.md](CONTRIBUTING.md).

Before adding a data provider, record its source, date, terms, transformations,
and distribution rights. [Source lossiness](docs/SOURCE_LOSSINESS.md) explains
what can change between a source measurement and a Minecraft block.

## License and attribution

Earthcraft code uses the [Apache License 2.0](LICENSE). Third-party software,
fonts, maps, imagery, LiDAR, Minecraft files, and generated worlds retain their
own terms. See [NOTICE](NOTICE) before redistributing those materials.

Earthcraft is an independent experiment, unaffiliated with Arnis, Mojang,
Microsoft, OpenStreetMap, the USGS, Cook County, or other data providers.
