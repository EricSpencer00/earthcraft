# Bounded world acceptance and facade importer

The October 2 owner-task continuation verified a frozen Elmhurst and Water Tower
excerpt in Minecraft Java 1.21.10. It did not complete whole-world acceptance,
install new photo colors, deploy a generator, publish changes, or start paid
compute. The existing save, coordinates, scan receipts and experimental photo
layer remained intact. The dirty owner checkout was left alone; implementation
is on `codex/playable-pilot-20261002`.

## Measured results

| Check | Result |
| --- | --- |
| Frozen excerpt | Six native regions, 433,265,770 source bytes; SHA-verified before and after native tests |
| Installed receipts at capture | 8,313 regional tile receipts; 291 installed scan-upgrade tiles |
| Native server | Two clean load/save/reload cycles on the existing free NUC, using the installed Fabric importer binary |
| Supported checkpoints | Five in Elmhurst and five near Water Tower retained dry support and two air blocks of headroom; these can be roofs and do not prove a walkable street route |
| Current importer controls | Matching cells written; conflicting diamond block, chest data and horizontal log state preserved; unowned chunk rejected; replay recognized existing targets |
| Shared appearance compiler | One **synthetic** existing-wall recolor in an isolated copy saved and replayed correctly; native and closed-writer output matched 262,144 block cells per cycle, 524,288 total |
| Travel functions | Scale 10 and movement speed 2 executed and persisted on a test armor stand; no player gameplay claim |
| Player files | Stored player files remained byte-identical without a player joining |
| Bounded pilot | Ten nearest source footprints near each fixed anchor, selected before photo coverage ranking; all twenty retained |
| Existing wall audit | 606 exposed block anchors near the twenty footprints; no missing sampled native columns; proximity alone does not establish measured building identity |
| Targeted tests | 39 passed, covering selection, snapshot bounds, registration admission, geometry preservation and the existing CAS codec |
| New photo colors in owner save | **0** |
| Client render / actual player walkthrough | **Unverified**; desktop UI tools returned `Transport closed` |

The pinned Mojang server SHA-1 is
`95495a7f485eedd84ce928cef5e223b757d2f764`. The installed importer tested here has
SHA-256 `4cc0f470e67860a446dfadf421ec0cc98e4d1cbd6f6c4738a944c5363e59b1e0`.
Older native proofs for a different importer binary are not substituted for
this evidence.

The inherited frame remains X east / Z south, west −32, north 33,
vertical offset −116, min Y −64 and height 1,024. The exact Elmhurst anchor
`[-87.9403418, 41.8994745]` maps to approximately
`[-26182.7019, -267.8957]`. The Water Tower anchor maps to `[32, 33]`.
The live save binding remains
`5afbda2f3609e88927f9de195622545185ad988891a7271f6ea1c629e782999b`.

## Appearance and resource gaps

The source save already contains **386** tagged photo displays where its
resource manifest declares **193** panels. Both native cycles preserved those
386 displays; the test did not create the duplication. This is an appearance
acceptance issue, not a passed 193-panel check. The owner save was not repaired
or deduplicated under this continuation.

Nearby cached photo metadata supplies 8–34 positional candidates per selected
Elmhurst building. The selected downtown buildings near Water Tower have zero
cached candidates within 75 m. Existing downtown photo experiments were taken
elsewhere and are not evidence of coverage for this selection. Metadata
proximity is not wall visibility. Existing calibrated projection outputs are
still unregistered candidates: no independent alignment, depth visibility or
static-surface ownership pass has admitted them to these twenty buildings.

LaCie is mounted on the Mac mini with about 574 GiB free. The original
supervisor auto-resumed and retained its existing base and scan workers; no
second pipeline was launched. The original publisher is running. Whole-area
queue declarations and delivery receipts do not imply game acceptance for
every tile.

The earlier cloud batch retained 24 accepted scan results and terminated its
compute and temporary identity/network resources. Its original-source archive
download subsequently failed with `ExpiredToken`; `collection.json` still
shows the stale `collecting_originals` state. The approximately 19.8 GB original
archive has not been confirmed retained on LaCie, and storage cleanup is not
claimed complete. This continuation did not refresh credentials, restart paid
compute, or delete the remaining private storage.

## Implemented importer boundary

`facade_importer.py` is an offline staging endpoint for both street-photo and
Orthofacade adapters once they produce
`earthcraft.registered-facade-observations.v1`. Existing raw projection and
Orthofacade candidate files cannot be passed directly as registered world
observations.

The compiler recomputes registration errors from at least six distinct held-out
measured controls across at least two images. Fit images and held-out images
must be separate; fit cameras need at least a two-metre baseline. The transform
must retain metre scale and orientation. Median error must be at most 0.5 m and
95th-percentile error at most 1 m. Candidate, registration and inherited frame
hashes are bound together. Staging checks the retained photo and metadata bytes
and capture attribution, rather than accepting a checksum string alone.

An admitted observation must have explicit observed/static flags, an acceptable
depth residual and facing angle, source/building association, and an existing
exposed default solid block near the selected footprint. Missing neighbor
chunks are not air. Existing photo anchors, block entities and state properties
are preserved. The adapter supplies the measured visibility/ownership evidence;
the importer itself does not recover cameras or perform photogrammetry.

Color is mapped to the average textures of supported solid Minecraft materials
from the existing client jar. This version recolors whole blocks. It cannot
independently texture four faces of one vanilla voxel, and conflicting face
colors abstain. It neither creates geometry nor fills unknown surfaces. Packets
use the existing `building_delta` compare-and-set codec shared by Fabric and
the closed writer.

Staging writes only a new immutable artifact directory, never a save or a live
watched inbox. The real pilot currently produces a `registration_pending`
manifest with zero packets and zero applied colors. The one native color
fixture is explicitly synthetic, with no photo-accuracy claim. Production
delivery integration remains pending a real admitted stage with matching native
and closed-writer proof; a building-layer manifest is not fabricated to bypass
the current delivery contract.

## Reproduce using existing resources

Use a new output directory for each attempt. Capture only while the owner save
is closed; the snapshot helper takes a short save fence, retains byte hashes,
rejects symlinks, and enforces a 512 MiB limit. Transfer the excerpt privately to
an already-authorized compute host. The MacBook remains the coordinator.

```text
python scripts/playable_acceptance_snapshot.py --world <closed-save> --binding <live-binding> --output <new-excerpt>
python scripts/facade_pilot.py --index <existing-footprints.sqlite> --frame <inherited-frame.json> --output <new-pilot.json>
python scripts/facade_pilot.py --pilot <frozen-pilot.json> --photo-cache <retained-cache> --output <new-pilot-with-discovery.json>
python scripts/facade_pilot.py --pilot <frozen-pilot.json> --audit-world <excerpt/world> --output <new-wall-audit.json>
python scripts/facade_importer.py --excerpt <excerpt> --pilot <frozen-pilot.json> --output <new-stage>
python scripts/playable_world_acceptance.py --excerpt <excerpt> --work <new-native-test> --assets <existing-assets-root> --java <verified-java-executable>
```

The native checker binds loopback only, uses a 1.5 GiB heap and view/simulation
distance 2, and stops its server on success or failure. It reuses the pinned
server, installed importer/API jars and existing Fabric caches. Its optional
`--stage` accepts only a matching, bounded **synthetic** material fixture for
compatibility testing; it is not a production publisher.

Private evidence for this run is retained under the owner task's
`runs/playable-pilot-20261003/`: `snapshot.json` inside `excerpt/`, `pilot.json`,
`pilot-wall-audit.json`, `appearance-stage-001/manifest.json`,
`material-fixture-001/`, and `native-003/verification.json` with both server
logs. Save bytes, player files and source-photo artifacts are not committed.
