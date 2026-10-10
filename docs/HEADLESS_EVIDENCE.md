# Actual block evidence

The [evidence view](https://ericspencer.us/earthcraft/headless/) renders actual decoded cells from four previously archived LQ tiles. It contains an orthographic cube image, topmost-block map, counts and a sanitized receipt. It does not increase generated coverage or establish independent survey accuracy.

The current sample is pinned to source revision `f87e9f2154a011a05524e343fcbe831c82db41d5` and archive SHA-256 `14d0f3d72a01ddded2c77b0884e191d82fc045c639e624556c47dd6fb5d527e1`. The private source archive and world files are excluded from this repository. Existing source credits remain in the receipt and the page. The public bundle contains only two derived PNGs and JSON metadata.

The decoder checks the archive and decompressed region hashes, full 1,024-chunk coverage per tile, sector bounds, actual chunk coordinates, generated-content privacy constraints, palette indices, section bounds, and native `WORLD_SURFACE` heightmaps against decoded blocks. It counts non-air blocks rather than inferring them from manifest declarations. The selected chunk-aligned crop maximizes stone-brick/brick top cells; that heuristic is not a surveyed building identification.

Datum agreement, independent geometry accuracy and measured surface color remain unresolved. Display colors identify block types and are illustrative; game textures, blockstate meshes, liquid heights and photography are not used. LQ buildings can be inferred or extruded. No Minecraft visual acceptance is claimed. The observation timestamp records this analysis of an existing sample, not a new generation or installation.

## Reproduce locally without Minecraft

Use Python 3.11 and the pinned headless dependencies. Obtain the named sealed archive through an already authorized private source path; no download credentials or private archive are supplied to public CI.

```sh
python -m pip install -r requirements-headless.txt
python scripts/headless_blocks.py analyze \
  --archive /path/to/authorized/archive.tar \
  --sha256 14d0f3d72a01ddded2c77b0884e191d82fc045c639e624556c47dd6fb5d527e1 \
  --revision f87e9f2154a011a05524e343fcbe831c82db41d5 \
  --observed-utc 2026-10-10T00:00:00+00:00 \
  --crop-size 96 --output progress/headless
python scripts/headless_blocks.py validate --directory progress/headless
python scripts/headless_site.py --bundle progress/headless --output preview/headless
python -m unittest discover -s tests -p 'test_headless_blocks.py' -v
```

Identical archive, pinned renderer/dependencies, crop and observation timestamp yield an identical bundle. Change the timestamp to the actual analysis time for a new observation. Renderer changes require rerendering: the receipt pins its source-code hash. Original source archives are read only. The parser expands at most 64 MiB per compressed member, 128 MiB per region and 16 MiB per chunk; a render has at most 250,000 visible faces. These are explicit application bounds, not a native process sandbox.

## CI and publication

Relevant pushes and pull requests run synthetic actual-NBT decoding tests, sealed public-bundle validation, static page assembly and the public-tree check. The private archive is not fetched in CI. Pages runs the existing atlas browser checks against the assembled site and checks the evidence page headlessly before publication. Relevant validated changes on `main` automatically publish the existing GitHub Pages destination. There is no recurring generation job, self-committing bot, model inference, or perpetual rerun loop. Source-storage and receipt-access gates on the manual regional generator remain in force.

The repository is public. These jobs use standard Ubuntu/macOS labels, with public-repository guards, finite timeouts and cancellation of superseded runs. Standard hosted execution is [free in public repositories](https://docs.github.com/en/actions/reference/runners/github-hosted-runners). [Artifact storage has separate accounting](https://docs.github.com/en/billing/concepts/product-billing/github-actions); this change adds no separate retained headless artifact or dependency cache. The existing Pages bundle is capped at 20 MiB and retained for one day. The existing browser proof also uses one-day retention. No repository visibility, billing limit, paid runner or private-data access is changed.

For a new authorized archived sample, run the decoder locally with its actual archive hash and immutable revision, inspect the saved image and receipt, and commit the small derived bundle. CI verifies those changes and updates the site. A failed evidence or browser check prevents the Pages job from deploying; the last published page remains available.
