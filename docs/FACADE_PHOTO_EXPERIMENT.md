# Water Tower photo skin — experimental, not a reconstructed facade

2026-09-10. The user approved a programmatic photo-to-LiDAR experiment after
accepting the atlas LiDAR appearance. This scope is one Water Tower facade,
not global texture acquisition or a completed building. Geographic processing
uses local OpenCV/NumPy/SciPy, no LLM, learned segmentation or hosted inference.

## Outcome and evidence

`Earthcraft-Water-Tower-Photo-Skin-v3` adds a measured-photo skin to an upper-shaft
patch, 25–44 m above the county ground reference. It does **not** complete the
wall or move, add or remove blocks. Its 16 region chunks are byte-identical to
`Earthcraft-Water-Tower-3D`; all 1,980 building voxels and 4,096 ground/heightmap
cells pass readback. Each skin panel sits 2 mm outside an existing exposed block
face; this small rendering offset avoids z-fighting and does not change collision.
The source is a real 2025 photograph, not an AI-created or hand-drawn texture.

There are 193 bounded display panels and 35,239 opaque texture texels at 16 pixels
per metre-block edge. Unsupported texels are transparent over the old material.
Native world resource pack and datapack create the panels on entry; state prevents
duplicates, reload clears an interrupted pending state, and the initial creative
flight camera faces the patch. Attribution is embedded in the resource pack.

The v2 isolated Minecraft 1.21.10 check created and retained 193 panels across two
server cycles. v3 repeats this with a ready-to-view initial player camera and
per-cycle entity-count checks. Authoritative final results live in
`runs/water-tower-photo-skin-v3-server-check/verification.json` and the world
`server-verification.json`; do not infer success from this description alone.
The selected regression suite has 39 tests, including camera handedness, projection,
UV orientation, occlusion, bounded gap rejection and install-profile selection.

Final v3 server result: both cycles passed, 193 panels in each, no reported errors;
travel attribute probe also passed. Installed into
`runtime/traversal/saves/Earthcraft-Water-Tower-Photo-Skin-v3`, the actual Geographic
Explorer profile, preserving every existing save. The installed assets and level
metadata were checked against the project output. No client rendering claim follows
from these server/copy checks.

`runs/Earthcraft-Water-Tower-Photo-Skin-v3/before-after-diagnostic.png` is a software
diagnostic from exported textures and block positions, **not** a game screenshot.
It shows that photo transfer adds stone/window detail but also inherits the metre
block staircase. Recoloring a coarse shell does not reconstruct a smooth wall.
Client rendering remains unverified: the computer-use app inventory exposes only
Minecraft Launcher, not the Java game. Do not interrupt the user's running world.

## Frozen inputs and source rights

- `runs/water-tower-facade-photos/`: three original Commons photographs, ~11.9 MB,
  SHA-1 checked against the source and SHA-256 recorded. Raw API metadata includes
  author, camera claims, capture dates and licenses. The west photo is by Dough4872,
  2025-08-19, CC BY-SA 4.0. South A/B are Ken Lund, 2013-11-22, CC BY-SA 2.0.
- `runs/water-tower-facade-validation-photo/`: one separate 2.5 MB photograph by
  Sergei Gussev, 2016-09-18, CC BY 2.0. Its claimed camera location alone does not
  establish which facade is visible. No collection-wide imagery download occurred.
- `runs/water-tower-points-2022/`: unchanged county airborne LiDAR, 2022-04-05
  through 2022-06-29, NAVD88/Geoid18 with explicit publisher-CRS provenance.
- No Google Maps/Earth imagery was scraped. No images, meshes, worlds or source
  data were published. Future redistribution needs the recorded attribution and
  applicable licenses. Photographic illumination is not recovered reflectance.

Source pages:
[West photograph](https://commons.wikimedia.org/wiki/File:Chicago_Water_Tower_view_from_west.jpeg),
[south A](https://commons.wikimedia.org/wiki/File:Chicago_Water_Tower,_Chicago,_Illinois_(11004311056).jpg),
[south B](https://commons.wikimedia.org/wiki/File:Chicago_Water_Tower,_Chicago,_Illinois_(11004439483).jpg),
[separate photo](https://commons.wikimedia.org/wiki/File:Chicago_-_-i---i-_(29496640250).jpg).

## Competing hypotheses and findings

1. **GPS/EXIF + silhouette alignment.** `facade_registration.py` initializes a
   pinhole camera from supplied metadata and optimizes it locally, with no manual
   image correspondences. Colour-seeded classical GrabCut is a central buff-stone
   landmark hypothesis, not a general semantic detector. Shaft-only fitting
   misplaced the roof. Including its dark silhouette improved the west training
   overlap to 0.8975; that is not geographic or texture accuracy. Full candidates,
   initializations and overlays are preserved in the registration run directories.
   Different starts still give different cameras. Registration is not admitted as
   a generally solved or independently verified problem.
2. **Direct bounded plane completion.** `facade_surface.py` fits vertical planes to
   shaft returns. A conservative 1.25 m triangle-edge / 0.5 m nearest-support bound
   produced only 1,894 coloured samples: a patchy surface, not a complete wall.
   That candidate was not exported into the playable world. Infinite-plane strip
   testing (932 train / 388 held-out points, 7 planes) gives median 0.0904 m and
   p95 0.3568 m nearest-plane distance; this normal-consistency test does not prove
   openings, coverage or a fully observed surface. No thresholds were relaxed to
   call it accurate. `runs/water-tower-facade-evidence-001/report.json` is authoritative.
3. **Photo skin on unchanged blocks.** `facade_skin.py` is the reversible renderer
   experiment actually exported. It separates photo-transfer feasibility from
   geometry completion. Its existing-voxel-face positions can differ from the real
   wall, so color placement inherits that error. Mask erosion, one-metre nearest
   LiDAR support and sparse-depth rejection reduce leakage but cannot certify the
   absence of all occluders. No hidden-side textures or window patterns invented.
4. **Independent photo checks.** West→separate photo has only 10 dominant-homography
   inliers from 48 ratio matches; west→south A has none. Neither validates the west
   texture. South A/B have 3,274 inliers but are near-duplicates and do not establish
   new wall coverage. Independent facade validation remains failed, not waived.

## Reproduction and next gate

Scripts: `facade_photos.py`, `facade_registration.py`, `facade_surface.py`,
`facade_evidence_check.py`, `facade_skin.py`, `facade_skin_check.py`.
Acquisition refuses existing outputs and checks a 40 MiB image-batch ceiling;
local experiments refuse existing worlds/run directories. Retain failed artifacts.
No background job or recurring automation is established by this experiment.

Next gate: acquire a genuinely overlapping, reusable facade view or calibrated
user-owned capture, then demonstrate cross-view geometric/appearance alignment.
After that, test a smooth supported visual surface separately from collision to
avoid the staircase texture distortion. Do not expand the existing silhouette
heuristic to every building or increase unverified fill merely to remove holes.
Keep the accepted atlas save and its source observations untouched.
