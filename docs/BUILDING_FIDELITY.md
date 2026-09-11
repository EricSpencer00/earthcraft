# Buildings first: accuracy and appearance

The first unit of success is one recognizable building and the ground immediately around it. A correct road map with anonymous boxes is insufficient. Keep geometry accuracy and visual quality as separate scores.

## What to acquire

| Attribute | Preferred evidence | Minimum treatment |
|---|---|---|
| Footprint and separate wings | OSM ways/relations/parts; municipal GIS; Overture where helpful | Keep actual polygon and courtyard holes; do not replace with a rectangle |
| Ground and roof elevations | Public LiDAR, surveyed city models, explicit height metadata | Retain vertical datum; levels are a height estimate |
| Roof silhouette | Building parts, roof tags, permitted aerial views or measured drawings | Preserve ridge direction, setbacks and separate roof volumes |
| Wall material and colour | Explicit tags, architectural descriptions, licensed/user-owned façade photos | Use a small cohesive palette per building; label Minecraft material mapping as artistic approximation |
| Window/door rhythm | Rectified permitted photographs, measured elevations | Align floors, bays and entrances; unknown back walls remain unknown |
| Pavement, curbs and planting | Mapped paths, permitted aerial/street observations | Preserve usable paths and building entrances; vegetation must not cover mistakes |

Fetch structured source records through documented APIs or download endpoints before scraping rendered pages. OSM supports outlines, parts, heights, roof forms and material tags, but coverage varies. A building ID lets us acquire only the relevant object and its context. [OSM building tags](https://wiki.openstreetmap.org/wiki/Key:building), [Simple 3D Buildings](https://wiki.openstreetmap.org/wiki/Simple_3D_Buildings).

For unusual landmarks, building-specific public archives can outperform generic AI. The Library of Congress has a Chicago Water Tower record with photographs and documentation. Check the individual collection's rights statement before reusing images or drawings. Architectural descriptions can establish materials even when photographs cannot be redistributed. The Chicago Architecture Center identifies Joliet limestone and castellated Gothic design. [Library of Congress record](https://www.loc.gov/item/il0097/), [Chicago Architecture Center](https://www.architecture.org/online-resources/buildings-of-chicago/chicago-water-tower).

Google content is still governed by the existing source policy. Local inference does not confer scraping or redistribution rights. This MVP uses OSM and public elevation; no Google scraper or photo collection was added.

## Order of visual refinement

1. Footprint, major masses, relative heights and roof silhouette.
2. Wall and roof material families, tonal contrast and weathering restrained to observed evidence.
3. Structural façade rhythm: floor lines, piers, window bays and entrance positions.
4. Roof trim, steps, railings and a few identity-defining details that fit the metre grid.
5. Adjacent pavement and measured vegetation; review from pedestrian height as well as above.

Use approximately three to five purposeful wall/trim/roof/glass materials per building where the evidence permits. Avoid per-block random noise, repeated glowing windows, decorative clutter or uniform generic window patterns. Use slabs and stairs for half-block detail without enlarging the building. A metre-wide cell cannot retain every carved ornament; simplify consistently. A good-looking unknown wall is still an inferred wall.

For Water Tower, warm buff limestone and the distinctive narrow shaft matter more than adding generic balconies or random moss. Do not recolour the whole neighborhood to match the landmark. The original OSM observation remains immutable; any supported correction belongs in a named overlay with source and reason. No arbitrary height change may be disguised as a style improvement.

## Smallest AI experiment

One building, two to four permitted façade views, one compact observation JSON. Ask a local vision model only for visible material classes, bay layout and occlusion. Human-check these against the inputs. Deterministic code maps accepted observations to block choices. No depth model, training, mesh reconstruction, generalized fusion engine or city batch is required.

First test a manually verified observation record. If that cannot produce a better building, adding AI only automates an ineffective representation. AI starts after the renderer can use a reliable record.

## Review gate

Use one overhead, one three-quarter and two pedestrian views under the same daylight conditions. Check footprint, total height, roof silhouette, entrance, window rhythm, palette and surroundings separately. Record unknowns and concrete defects. Require one independently checked dimension and one independently checked visible façade attribute before calling the building accurate. A generator's scale setting or a pleasing screenshot does not pass that gate.

Arnis 3.1.0 explicitly synthesizes heights when tags are absent and chooses façade styles/material defaults. It prioritizes recognized material tags over colour tags. Those generated defaults need evidence labels; merely injecting a colour tag may not change a stone-tagged building. [Pinned building renderer](https://github.com/louis-e/arnis/blob/v3.1.0/src/element_processing/buildings.rs).
