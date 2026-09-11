# Chicago MVP extension

Current save: **Earthcraft-Chicago-MVP-Explorer-v5**, installed in standard
Minecraft Java 1.21.10 Singleplayer and in the older Geographic Explorer profile.
Previous saves are unchanged. Future automatic builds use the standard profile;
the native travel menu requires no Fabric mod or typed player commands.

## Travel

Press **G**, or Pause → **Travel**.

- Player size slider: 1× to 10×, then Apply size. Blocks stay one metre.
- Walking speed slider: 1× to 20×, independent of body size.
- Fast flight: the photo demo's spectator mode; scroll to adjust flight speed.
- Creative mode: return to normal creative interaction at the current position.
- Human at spawn: reset size/speed and return to the known safe starting point.

Grow outdoors. A ten-times-larger body cannot fit through human-sized openings.
Sliders currently reopen at their default positions, not the current settings;
they change nothing until applied. Menu registration and attribute functions pass
the exact game server checks. An actual player/UI playtest remains outstanding.

## Appearance router

A deterministic local router reads explicit OSM material and colour tags,
projects their polygons into the LiDAR grid, respects mapped part height ranges,
and maps supported appearance to installed Minecraft textures. Smaller parts take
precedence over broader outlines. It only changes existing building cells.

In v5, 16 source regions produced 483,055 material changes with **zero occupancy
changes** against v4. Material/colour approximation and source tags are recorded
in `appearance-routing.json`. No LLM, generated windows or generic floor patterns.

The photo-detail pipeline remains separate. Its small textured surface models
can supplement LiDAR only after photos are georegistered, metric scale is checked,
and visible surfaces are associated with the actual building. The existing
ETH3D photo reconstruction is not Chicago imagery and is not pasted onto Chicago.
CV-to-LiDAR photo routing, detail LOD and regional caching remain extension work.

## Five geographic extras

1. Elevation-alignment report: county ground versus USGS consistency; independent
   vertical-datum alignment is still unverified.
2. Water-surface audit: connected bodies and height spans. Current fountains have
   uneven cells flagged; actual water elevations and gradients are unresolved.
3. Chunk-boundary audit: 31,744 neighboring comparisons, zero unexplained jumps
   against the canonical terrain/roof field.
4. Relief-preserving height preflight: high-altitude terrain keeps one constant
   offset. Excessive vertical range fails instead of compressing mountains.
   Synthetic high-altitude/oversize tests pass; a real mountain site is pending.
5. Coverage report: observed terrain/roof coverage and missing photo, tree and
   bathymetric layers stay explicit. One-metre output is not one-metre accuracy.

These are safeguards and diagnostics, not a claim of 100% correct geography.
Global chart navigation, reliable hydrology, real mountain validation, and
georegistered photo detail remain required before broader fidelity claims.

## Verification

v5 contains 1,024 populated chunks. All 262,144 ground/heightmap cells and 128,142
sampled roof cells pass block read-back. Java 1.21.10 loads, saves and reopens it.
An isolated temporary-entity test confirms the scale function sets 10 and the
movement-speed function sets 2.0 (20× normal player base). This checks commands,
not human gameplay. Logs: `runs/mvp-v5-travel-probe-direct/`.
