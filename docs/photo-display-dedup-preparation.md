# Photo display diagnosis and reversible preparation

The owner-task preparation starts from commit
`2aa14fe1ad0d0299c8f82dd4c2cfa77ee07d73f3` on
`codex/playable-pilot-20261002`. Its isolated local checkout is
`/Users/eric/earthcraft-worktrees/playable-pilot-20261002`.
The original save and the dirty owner checkout were preserved.

## Saved display inventory

The original save contains 386 tagged `minecraft:item_display` entities for
193 declared `earthcraft_skin:face_*` models. Each model occurs exactly twice.
All 193 pairs have identical full typed saved NBT except their top-level UUID.
Both copies match the declared model and anchor. There are 189 distinct anchor
positions: different face models legitimately share a position, so deduplicating
by position alone would discard valid panels.

The only nonempty entity region is `entities/r.0.0.mca`, 36,864 bytes, SHA-256
`5a4976e12cd88232675a70bbf7e0ae78f80f32320d76c6473b0a87ebb08cdddf`.
The current inventory has no unrelated entities in that region and no candidate
UUID references in the inspected entity NBT, level data, player data or saved
data files. Twenty-three metadata, receipt, player/data and datapack files are
also guarded by checksums. A future change must inventory again; this observation
does not establish that arbitrary future references or edits are safe to remove.

The retained `runs/city-stream-replay-001` baseline contains one panel per model.
All 193 historical UUIDs are present in the current pairs. The final plan keeps
those UUIDs with their **current** payloads; a unique saved UUID reference takes
precedence over a historical preference. Payload differences or ambiguous
references block the entire plan.

The generated photo build function first kills matching **loaded** displays,
then unconditionally summons 193 panels. A repeated build with older displays
not loaded could produce this second full set. That is a plausible mechanism;
the exact historical invocation causing duplication was not established.

## Prepared artifact, not a world repair

The authoritative private artifact is
`runs/photo-dedup-preparation-20261003-002/` in the owner checkout. The earlier
preparation directory remains intact and is superseded by this historical-UUID
selection. The final directory contains:

- `inventory.json`: saved identity, model, anchor, typed payload and guard hashes.
- `patch.json`: 193 proposed duplicate removals, with retained historical UUIDs.
- `backup/entities/r.0.0.mca`: exact original region bytes, verified against the save.
- `preparation.json`: checksums binding the inventory, plan and backup.
- `owner-guard-verification.json`: all 24 source files (the entity region plus
  23 guard files) and the entity backup match.

`photo_display_dedup.py` exposes only `prepare` and `verify`. It has no apply mode
or region writer. It takes a brief existing save lock, inventories a fixed
bounded input, and writes an immutable output **outside** the save. The only
`.mca` it writes is the unchanged backup. Removal and restoration are simulated
on copied NBT in memory. The measured hypothesis is 386 → 193 across two entity
chunks, with exact typed-NBT reversal. **Zero world entities were deleted.**

Verification passed locally against the owner save and independently on the
existing free NUC against the transferred artifact. The NUC artifact check does
not check owner guards because the original save is not on that host. Sixteen
regression tests passed, including payload edits, typed differences, shared
anchors, historical identities, references, backup corruption, guard drift,
save locking, symlink scope and exact in-memory reversal.

Any later authorized writer must revalidate a closed save and record its
post-write checksum. Restoring a whole backup region over newer edits is
forbidden. Applying this plan and testing changed-world output remain outside
this preparation. No deduplication was applied even to a client test copy.

```text
python scripts/photo_display_dedup.py prepare --world <closed-original-save> --keeper-baseline <matching-historical-save> --output <new-private-preparation>
python scripts/photo_display_dedup.py verify --world <same-original-save> --output <private-preparation>
python -m unittest discover -s tests -p test_photo_display_dedup.py -v
```

## Twenty-building photo input blocker

The frozen pilot remains ten Elmhurst and ten Water Tower buildings, SHA-256
`d5a612e0637d10445f2e0de244a2b1dccb81a331c8f8ef7d1761ca58535f144e`.
The 608 cached image metadata records provide 8–34 nearby candidates per Elmhurst
building and none within 75 m of the selected downtown buildings. Metadata
discovery is not retained, visible, registered wall imagery.

The actual two retained photos used by prior calibrated projection proofs are
427.727 m (Elmhurst) and 907.484 m (downtown) from their nearest selected pilot
footprints; neither has a selected building within 75 m. Their projection outputs
remain in an unregistered reconstruction frame. No pilot has a real
`world_from_source` transform bound to the inherited world frame and independent
held-out controls, static facade ownership/depth-occlusion receipts, or the
normalized observation/source-attribution bundle required by `facade_importer`.
The private input audit is `runs/photo-dedup-pilot-inputs-20261003.json`.

The shared importer and synthetic native compatibility test described in
[the acceptance report](playable-pilot-acceptance.md) remain available. The real
stage is still `registration_pending`, with zero packets and **zero newly
applied real photo colors**. No independent per-face photo textures are claimed.

## Client boundary and resource state

On `Eric-MB64.local`, `/Applications/Minecraft.app` and the installed 1.21.10
client are present. The existing launcher has a pre-existing crash-report modal.
Native System Events inspection and window capture work; the desktop tool still
returns `Transport closed`. However, supported activation does not make the
launcher frontmost, its accessibility tree exposes window decorations rather
than the modal's web controls, and a bounded process-scoped click returned
`missing value` without changing the dialog. Escape/Tab also left it unchanged.
The modal does not expose the underlying exception in the captured state. No
sign-in requirement or entitlement failure was established.

Client attempts stopped at that control boundary. No game client was launched,
no world was opened, no report was shared, and no sign-in or settings change was
made. Render/player walkthrough therefore remains **unverified**. The frozen
excerpt to copy for a future supported check is:
`/Users/eric/earthcraft/runs/playable-pilot-20261003/excerpt/world`.

The original Mac mini supervisor is running with existing base/scan worker PIDs
17498 and 17499. LaCie is mounted with 573.816 GiB free. No generation job or
publisher was duplicated. AWS original-source retrieval remains blocked by the
previous `ExpiredToken`; archive retention and storage cleanup are not claimed.
No retrieval restart, paid compute, credentials, publication or deployment was
attempted under this preparation.
