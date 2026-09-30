# Distant rendering in the existing Earthcraft world

Distant Horizons 3.3.3 displays simplified blocks beyond Minecraft's full-detail
view. The pinned Fabric 1.21.10 installation also includes Sodium 0.7.3 to improve
nearby chunk rendering. Distant detail is cached between sessions and becomes
coarser with distance. The first visit requires time to build the cache.

The configured target radius is 2,048 chunks (32,768 blocks, approximately 32.8 km
at Earthcraft's one-block-per-metre scale). It is a best-effort distance rather
than infinite coverage. Only regions already present in the save can appear.

Run `python scripts/install_distant_rendering.py` using Python 3.11 or later.
The installer verifies the existing launcher profile and both publisher SHA-512
checksums, adds the two mods, and installs `configs/distant-horizons.toml` as
`runtime/traversal/config/DistantHorizons.toml`. It is safe to stage the installation
while Minecraft is open, but activating the mods requires a normal restart.
Existing different versions or conflicting settings are rejected without replacement.
The installer also accepts the quoted decimal values DH writes when expanding its
configuration, so a safe rerun preserves the effective settings after first launch.

The configuration must retain schema `_version = 5`,
`common.worldGenerator.generatorPlan = "CHUNKS_ONLY"`, and
`common.worldGenerator.chunkGeneratorMode = "PRE_EXISTING_ONLY"`.
Surface generation would invent terrain that does not match Earthcraft's real
geography. Automatic updates are disabled to keep the binary and schema paired.
The renderer reads the existing world coordinates; installation does not modify
the geographic frame, region files, player data, launcher settings, or world.

Performance settings use low horizontal quality, two-block maximum LOD resolution,
medium vertical detail, single-pass transitions, and no distant ambient occlusion.
One background worker runs at a 50% duty cycle and pauses under server load or
fast travel. The cache gradually fills in; this is not a full-speed regional
precomputation on the coordination Mac.

After saving and quitting, relaunch **Earthcraft — Geographic Explorer** and open
the existing **Earthcraft** save. Confirm the Mods screen lists Distant Horizons
3.3.3 and Sodium 0.7.3. For lower nearby rendering load, set Minecraft's full-detail
render distance to 12 chunks in **Options → Video Settings**; DH keeps its
separate 2,048-chunk distant radius. The installer preserves the current full-detail
distance and memory allocation by default because an open game/launcher can
overwrite them. Once both have closed, add `--tune-closed-profile` to set nearby
rendering to 12 chunks and this profile's maximum heap to 8 GiB. The tuning step
checks processes and Minecraft's session lock, backs up the prior settings under
`runs/distant-rendering-before-tuning`, and preserves all other profiles and options.

Installation proof is saved locally in `runs/distant-rendering-install.json`.
Installation checks do not prove OpenGL compatibility, frame rate, or actual distant
coverage. Those require checking the restarted client and its log. Do not label
the renderer active until the new process loads both mods successfully.

Official projects: [Distant Horizons](https://modrinth.com/mod/distanthorizons)
and [Sodium](https://modrinth.com/mod/sodium).
