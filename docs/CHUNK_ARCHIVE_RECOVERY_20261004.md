# Chunk archive recovery — 4 October 2026

The repaired archive route uses the existing MacBook-owned controller and private
HF dataset, with generator-only shards retained on the Mac mini's LaCie volume.
No save publication, generation restart, credential change or deletion is part
of this repair.

## Owner and progress baseline

Observation: 2026-10-04 21:45:25 UTC. Controller PID **92872** was running under
the existing launchd label `com.eric.earthcraft.hf-generated` in `gui/501`.
The original mini supervisor/base/scan PIDs remain **41811 / 41813 / 41814**.
PIDs are observations, not permanent identities: inspect the launchd job and
task process arguments before acting.

Control directory:
`/Users/eric/earthcraft/runs/chicagoland-scans-20260929/hf-generated`.
Read `status.local.json`, `state.local.json`, and the matching
`<sha256>.proof.local.json`. The baseline is **47 verified batches / 434 unique
archived tiles**. Batches can advance without new unique tiles when scan upgrades
replace earlier archived receipt identities.

The first recovered chunk has four scan updates, 2,672,640 bytes, SHA-256
`472cd7b9a3405434897848e265a8426557b9a501d0321d02942105bf01a641f1`,
at HF revision `92b4ccf5199d15f6452616bc4fd1787d6588cce6`.
Its retained LaCie archive and remote shard checksum matched. The proof also
binds the remote index, frozen plan and exclusion of personal save state.

## Watchdog checks and restart boundary

1. Read the existing launchd job, process arguments and archive checkpoint.
   Do not create another controller or generation worker while an owner is live.
2. Compare `state.local.json.batches` with the previous observation. An increase
   follows a verified upload/index proof and persisted receipt checkpoint.
   Verify that the new proof says `state=verified`, matches its shard hash and
   frozen plan, and excludes personal save contents.
3. If the counter is unchanged, inspect the existing pending shard and current
   process. A live bounded export/upload is not evidence that a restart is safe.
   Transport retries are finite; SSH/export commands are bounded to 300 seconds,
   and the uploader to 600 seconds. Authentication, host-key, checksum and schema
   failures are not fixed by blind retries.
4. Restart only the existing launchd label after verifying its controller is
   stopped and the task archive lock has no live holder. Preserve the pending
   shard and checkpoint so the same immutable transfer can resume. Never remove
   the checkpoint or start a second owner. Do not restart generation.
5. Before each SSH operation, resolve the documented `mini-ts` alias from the
   MacBook. Current route: hostname `100.117.97.102`, user `ericspencer`, no jump,
   identity `~/.ssh/id_ed25519_mac_mini`. A changed route requires review.

The existing `config.local.json` uses `memory_upload=true`, four tiles per batch,
and at most 64 MiB of archive bytes in RAM. It preserves the original deadline
`1794624479.7155452`, frozen plan and dataset. Local bulk archives are not created
or removed. Proof/index/checkpoint metadata are the only local archive writes.
Existing local staging and LaCie archives remain retained.

The configured MacBook reserve is still **50 GiB**. Disk-staged archive mode and
the saved-world publisher enforce that reserve; RAM archive mode avoids bulk
MacBook staging. LaCie retains its **150 GiB** generation reserve. Do not lower
either boundary or remove originals to make a watchdog pass.

No additional watchdog was created. Validation: 12 archive regressions,
4 retry regressions, mini transport/socket checks, Python/Node syntax and Git
whitespace checks passed. The first recovered batch took 24.9 seconds and its
timed maximum RSS was 76,103,680 bytes. First proof plus index metadata totaled
291,575 bytes; local tar staging remained 2,826,240 bytes.

## Direct LaCie world delivery assessment

`regional_install.py --world <closed-world> --candidate <verified-tile-world>
--receipt <receipt> --backups <backup-directory>` accepts a filesystem world
path, guards the save/session, verifies candidate/frame/region receipts, creates
region backups and preserves existing/edited chunks. `regional_merge.py` can
merge a verified bounded bundle into a private `task/current` replica.

The current `regional_publish.mjs` instead fences and writes the configured
MacBook world, and requires its local 50 GiB reserve. LaCie is not mounted on the
MacBook. The archive shards alone are not a complete playable save.

A direct LaCie delivery plan therefore needs a verified closed candidate world
on the mini, explicit original/target identities, retained backup/guard hashes,
the existing receipt-bound merge path, and post-merge load/save verification.
Changing the authoritative save, moving a full world, or enabling that route
has not been done. Current saved-world publication remains storage-blocked.
