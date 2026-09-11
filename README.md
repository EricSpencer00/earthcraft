# Earthcraft

Earthcraft turns a bounded real-world place into a Minecraft Java world. The
project keeps the geographic measurements, source provenance, and uncertainty
visible instead of hiding them behind a screenshot that merely looks right.

The public baseline is deterministic and does not require AI. It works from
local, frozen inputs and uses ordinary geometry, source data, and Minecraft
world files. There are separate experiments for local computer vision, but
they are optional, disabled by default, and cannot invent missing geography.

This is still an experimental project. A small 64 × 64 block Water Tower test
and larger Chicago experiments exist, but game loading and real-world accuracy
are not accepted as finished until they are checked independently. The current
roadmap is in [docs/PLAN.md](docs/PLAN.md); the evidence and release limits are
in [docs/PUBLIC_RELEASE.md](docs/PUBLIC_RELEASE.md).

## Start here

The quickest useful check is the offline test suite. It does not download a
map, call a hosted service, or need a Minecraft installation.

```sh
git clone https://github.com/EricSpencer00/earthcraft.git
cd earthcraft
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-test.txt
PYTHONPATH=scripts .venv/bin/python -m unittest discover -s tests -v
```

The checked-in tests exercise coordinate transforms, source handling, metric
world writing, replay, façade observations, and the small KML importer. They
are the required check for every pull request.

The larger geographic runs need macOS, Python 3.11, public source data, and a
separate Minecraft Java installation. They also need local input manifests
that are intentionally not included here. See [docs/MVP.md](docs/MVP.md) for
the smallest generated-world experiment and
[docs/GOOGLE_EARTH_KML.md](docs/GOOGLE_EARTH_KML.md) for the inspectable
geometry-only route.

## What belongs in this repository

Code, tests, synthetic fixtures, configuration contracts, and notes about
what has actually been measured belong here. Generated worlds, raw geographic
downloads, photographs, model weights, archives, run logs, and machine-local
paths do not. The `.gitignore` is deliberately strict; please do not work
around it by committing a convenient copy of a local dataset.

Source and derived-data licenses are separate questions. Before adding a
provider, read [docs/SOURCE_LOSSINESS.md](docs/SOURCE_LOSSINESS.md) and record
the source ID, date, terms, transformation, and distribution decision. Map or
imagery access is not automatically permission to redistribute a generated
world.

## Working with other people

Start with [CONTRIBUTING.md](CONTRIBUTING.md). A small branch, a focused pull
request, a plain explanation of the change, and a test result are more useful
here than a large rewrite. If a result is not measured, say that directly.

Earthcraft is maintained by human contributors. Do not add model or bot
co-authors, `Co-authored-by` trailers, or generated filler to commits, issues,
pull requests, or documentation. If a tool helped with an edit, a human still
owns the review, wording, and commit.

## License

The project code is available under the [Apache License 2.0](LICENSE). Third-
party programs, map data, imagery, elevation data, LiDAR, Minecraft files,
and generated worlds keep their own terms. See [NOTICE](NOTICE) before
redistributing anything beyond the source code.
