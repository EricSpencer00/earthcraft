# Earthcraft field atlas

## Direction

Editorial typography meets a technical atlas. The wide map and narrow build
record reflect how a contributor checks a running world. A warm paper surface
stays readable on a daylight screen. The field notes use an actual Water Tower
capture, labeled with its date and early-build state.

## Type

Fraunces, weights 400–600, sets the wordmark and headings. Libre Franklin,
weights 400–700, sets body copy and controls. Both are bundled WOFF2 files with
SIL Open Font License notices in dashboard/assets/fonts. No external font
request is needed. Native monospace handles dates, scale, and coordinates.

Use a 44–80 px main heading and 32–39 px section headings. Limit prose to
65 characters per line. At phone width, controls and important map details
use 14 px type or larger. Small metadata remains secondary.

## Color and spacing

CSS custom properties in dashboard/style.css are the source of truth:

| Role | OKLCH | Use |
| --- | --- | --- |
| paper | .941 .019 88 | Page |
| surface | .978 .009 88 | Controls and queued cells |
| ink | .275 .027 152 | Text |
| muted | .475 .024 150 | Supporting text |
| line | .806 .025 100 | Rules and boundaries |
| green | .446 .09 153 | Generated cells and focus |
| gray | .73 .029 143 | Source-ready cells |
| amber | .655 .122 73 | Active builds and installed overlays |
| rust | .545 .15 31 | Failed cells and unreadable snapshots |
| outside | .916 .02 94 | Unlisted map squares |

Spacing follows 4, 8, 12, 16, 24, 32, 48, and 64 px steps. Use thin rules to
separate sections. Controls have square corners and at least 44 px touch
height. Hover and focus are visible; reduced motion disables transitions.

## Layout and behavior

The introduction pairs a large place-led heading with a short explanation.
The atlas uses a flexible map and a 284 px ledger. At 800 px the ledger moves
below the map; at 540 px the introduction, toolbar, and ledger become single
columns. Field notes pair the real capture with open rows for ground,
buildings, and planned street detail.

Native selects, checkboxes, and disclosure controls keep interactions familiar.
Click, tap, or arrow keys select a cell; Escape clears it. Zoom is capped at
4× and Fit returns to the full extent. Refresh retains a selection when the
grid is unchanged. Published progress refreshes once a minute while visible;
local views refresh every ten seconds. A failed request keeps the last map
and offers an inline retry. Empty snapshots say that no cells were recorded.

## Copy and exclusions

Keep geography, geometry, appearance, import, and verification distinct. Use
“Not recorded” for missing counts. Do not claim that a snapshot represents
complete coverage or independent building accuracy. Date project photographs.
Use paper, ink, and green without gradients, glass panels, invented geography,
or a repeated marketing card grid. Avoid slogans where a specific status or
instruction would help the reader more.
