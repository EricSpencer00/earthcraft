# Public-readiness audit

This is a source-tree audit, not a release or rights clearance.

## Current checkout

- The repository public-tree checker passes for the tracked source tree.
- The tracked tree contains no detected home-directory paths, credentials,
  private keys, bot attribution, generated worlds, raw map downloads, or model
  weights.
- `roofer.log.json` is ignored explicitly because local run logs are not public
  artifacts.
- `docs/SPARSE_EARTH_ARCHITECTURE.md`, the progress snapshot, and the Pages
  workflow contain public architecture and aggregate state only.

## Reachable history

The reachable history scan found no detected credentials or absolute home paths.
An older commit still contains a generated Minecraft screenshot at
`docs/photos/watertower-sep-10-26.png`. It shows a public Chicago landmark and
does not expose a private address. It remains a separate source-rights review
item, not a private-data finding; the current checkout removes that file and
ignores the photo directory.

## Publication state

The public repository has been created at
`https://github.com/EricSpencer00/earthcraft`, but this checkout still needs
its audited source pushed and its Pages deployment verified. The Pages
workflow publishes the dashboard at the repository Pages URL; the desired
`ericspencer.us/earthcraft/` route still requires the site's existing hosting
configuration to map that path.
