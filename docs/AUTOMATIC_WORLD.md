# Automatic populated world

Latest: [Chicago MVP extension](MVP_EXTENSION.md). Open
**Earthcraft-Chicago-MVP-Explorer-v5** in standard Java 1.21.10 Singleplayer.
Press **G** for native player-size, walking-speed and fast-flight controls.
The older Fabric-specific instructions below describe the preserved v4 setup.

Double-click `Build Earthcraft.command` to build and install a new 512 m Chicago
pilot. It reads frozen source data (downloads missing pilot sources), populates
native Minecraft chunks, verifies blocks, loads/saves/reloads an isolated copy
with Mojang's 1.21.10 server, and copies a new save into Minecraft. Open that save
from Singleplayer. There is no build function for the player to execute.

Requires the project's environment (`requirements-metric.txt`), pinned Mojang
server jar, cached regional OSM/ESA source data and existing metadata templates.
These are present on this Mac. This is not yet a portable bootstrap installer.

The pilot uses a local Transverse Mercator projection with unit scale at its
origin. East is +X, south is +Z, and all vertical distances use one constant
offset. A one-metre sampling grid is not a claim of one-metre source accuracy.
USGS terrain is sampled on that grid; Cook County's 2022 footprint data supplies
building footprints. Cook 2022 LiDAR surface elevations now provide per-cell roof
levels and setbacks instead of a flat extrusion to each building's maximum.
The surface service explicitly declares NAVD88 heights in US survey feet; these
are converted to metres. An extended-height datapack preserves tall structures.

On this Mac, select the **Earthcraft — Geographic Explorer** launcher profile,
then Singleplayer → **Earthcraft-Chicago-LiDAR-Roofs-v4**. The world is already
populated. New builds install into this isolated profile automatically.

The profile contains Fly Mod 3D, Fabric API, Cloth Config and Mod Menu, with
download checksums verified against their distribution metadata. Double-tap
Space for creative flight; B toggles Fly Mod. Its settings are available through
Mods → Fly Mod → Configure. The configured flight multiplier is 4×; base player
speed is reset to vanilla to avoid compounding boosts. Walking speed and the
geographic block scale are unchanged. Client startup and these controls still
need an in-game check: the launcher currently presents a blank window.
Without the isolated profile, the builder retains vanilla 4× creative flight.

Every world includes source checksums, per-building dimensions, known limitations
and a block verification report. Source materials are translated to a small
Minecraft palette; side walls derived from the roof heightfield are geometric
approximations, not observed façades. The DSM can include vegetation and source
interpolation artifacts within footprints. Interiors,
façades, individual trees and unresolved features are not measured by this data.
Suspect ESA water classifications are neutral stone unless mapped water confirms
them. The pilot does not yet supply continuous whole-Earth generation or mountain
validation. Keep expansion gated on those tests and actual client visual QA.

Sources: USGS 3DEP public elevation; OpenStreetMap contributors (ODbL); ESA
WorldCover 2021 v200 (CC BY 4.0); Cook County GIS Building Footprints 2022
([service](https://gis.cookcountyil.gov/traditional/rest/services/buildingFootprint_2022/MapServer/0),
[county terms](https://www.cookcountyil.gov/terms-use)). Generated worlds stay local.
