# Earthcraft world explorer

## Direction

Start in the world. The map fills the screen below a compact header. Streets,
place names, and the lake give the generated cells a recognizable setting.
A narrow drawer holds build counts and selected-cell details; on phones it
opens as a bottom sheet. The initial phone view keeps the drawer closed.

## Type and color

Space Grotesk, weights 400–700, sets the wordmark and headings. Libre Franklin,
weights 400–700, sets body copy and controls. Both are bundled WOFF2 files with
SIL Open Font License notices in dashboard/assets/fonts. Native monospace
handles coordinates and scale. Headings are 23–26 px; controls are 12–14 px,
with at least 44 px touch targets. Important phone detail text is 14 px.

CSS custom properties in dashboard/style.css are the source of truth. Dark
forest-gray surfaces use light text, restrained green for generated geometry,
amber for active work, and rust for failures. Muted text still needs readable
contrast. Spacing follows 4, 8, 12, 16, 24, and 32 px steps. Opaque surfaces
and thin borders keep controls readable against the geographic map.

## Geography and rendering

MapLibre GL JS is bundled under its BSD license. OpenFreeMap supplies the
public dark street map. OpenFreeMap, OpenMapTiles, and OpenStreetMap credits
remain visible while that map is used. No account key or external font is
needed. Basemap requests contain public geographic coordinates.

Published cells use their recorded latitude, longitude, width, and height.
Their Mercator corners are cached; the canvas overlay shares one transform
per frame. Rotation and pitch are disabled so cells stay aligned while
panning and zooming. Local chunk views retain the Minecraft coordinate grid.
The generation pipeline, installed world, and coordinate frame are unchanged.
If the basemap fails, cell inspection remains available. If WebGL is unavailable,
the generation grid is shown with an explicit notice.

## Interaction

Chicago and Elmhurst shortcuts use the anchors from configs/cities.json.
Select a cell to open its record. Build status and surface detail are separate
layers. Missing appearance is shown as awaiting detail. An area without a
published cell says so rather than selecting a distant cell.

Drag and pinch or scroll to navigate the geographic map. Arrow keys inspect
cells; Shift and arrows pan; Escape clears selection. Fit shows the whole
recorded extent. Refresh preserves the camera and selection when the grid is
unchanged. Published progress refreshes once a minute while visible; local
views refresh every ten seconds. A failed snapshot request keeps the last map
and offers Refresh. Empty snapshots say that no cells were recorded.

## Copy

Describe the place, state, or available action. Keep geometry, appearance,
import, and verification distinct. Use “Not recorded” for missing counts.
Date project captures and label early builds. Put build explanations and the
appearance plan inside the drawer so they do not displace the map.
