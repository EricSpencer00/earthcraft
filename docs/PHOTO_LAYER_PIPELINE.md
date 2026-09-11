# Observed photo layers in regional builds

Final integrated deliverable: `Earthcraft-Chicago-Photo-Atlas-v2`, installed in
Geographic Explorer. Two fresh builds matched both geographic blocks and photo
assets/commands/initial view. Both Minecraft 1.21.10 server cycles found 193 panels
and verified their serialized positions/model IDs; full unit discovery passed 57
tests. Actual client rendering remains unverified. All earlier worlds are retained.

The default regional profile is now `chicago-photo-pilot`: accepted 256 m Chicago
point geometry plus the existing experimental Water Tower photo patch. It retains
one block per metre and adds no geography. This is a reusable frozen-layer adapter,
not automatic photo discovery, universal camera recovery or complete facades.

`photo_layer.py` reads a frozen world-local photo artifact, validates its RGBA
texture checksums and attribution, and translates its anchors into another chart.
It requires the same CRS and an integer-metre origin/vertical translation. It
checks the actual source and destination block faces, not only point sidecars.
Missing anchors, covered faces, different CRS, fractional grid shifts, unsupported
texture formats or an existing resource pack cause a failure before integration.

The Chicago transfer is exactly `[160, 0, 96]` in Minecraft X/Y/Z. All 193 faces
remain supported. The original 35,239 opaque texels are copied byte-for-byte;
unobserved pixels stay transparent. No source commands or arbitrary source models
are executed. The adapter emits bounded declarative models and automatic creation
commands, preserving all original region bytes. Only one layer is supported per
world, at most 512 faces across sixteen chunks. No claim of global scalability.

The catalog entry has this optional field:

```json
"photo_layer": {
  "source_world": "project:worlds/Earthcraft-Water-Tower-Photo-Skin-v3",
  "allow_experimental": true,
  "start_at_detail": true
}
```

The explicit experimental option is necessary because the source camera has not
passed independent alignment validation. It never converts that status into an
accuracy claim. `start_at_detail` transfers only the initial camera and creative
flight state, after checking destination body clearance. The safe ground spawn
and native travel controls remain intact. Neutral `chicago-pilot` still exists.

`--replay-check` now compares decoded blocks plus photo textures, models, mapped
anchors, attribution, creation commands and initial camera. The photo source world
is included in input fingerprints. `photo-layer-verification.json` records asset
and placement checks. `photo-skin.json` preserves the original registration/source
limitations. These reports do not certify rendered appearance or source accuracy.

## Server-check correction

The first regional integration saved all 193 correctly positioned entities, but
its runtime probe counted zero: the production layer had intentionally released
its distant chunks before the probe. The failure is preserved in
`runs/Earthcraft-Chicago-Photo-Atlas-v1-server-check/`. It was not installed.

The checker now derives loading bounds from the mapped panel records, reloads
those chunks before counting, and separately reads saved entity positions/model
IDs after shutdown. It does not alter the production layer's release logic.
The corrected check on the same candidate passed two cycles with 193 runtime
panels and 193 exact persisted positions, documented under
`runs/Earthcraft-Chicago-Photo-Atlas-v1-server-check-002/`.

Creation retries remove only entities tagged as belonging to this layer before
recreating them. Normal creation/reload and deterministic output are tested;
arbitrary crashes at every possible instruction are not exhaustively verified.

## Remaining fidelity limits

One upper-shaft patch is not a completely textured tower, much less a textured
city. Texture transfer preserves the existing metre-block staircase distortion,
photo lighting and uncertain camera alignment. Other buildings keep their existing
source-tag material routing or neutral fallback. The next fidelity experiment
must improve observed coverage or independently supported geometry/appearance;
copying this patch elsewhere or adding more neutral exports would not do that.
