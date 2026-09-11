# Resource limits

Earthcraft keeps acquisition and generation bounded because geographic data,
world archives, and optional local model files can become large quickly. The
limits in `configs/pilot.json` are proposed starting points, not performance
claims.

Keep the repository, virtual environment, active state, logs, and small test
fixtures on the main development volume. Put raw sources, large derived
rasters or point clouds, model archives, and closed world archives in a
separate workspace. Set `EARTHCRAFT_BULK_ROOT` rather than baking a local
volume name into a configuration file.

Before a run:

1. Check free space on each volume independently.
2. Count both compressed and unpacked files, plus temporary copies.
3. Keep one writer per artifact or region.
4. Write partial files beside their final destination, verify checksums, then
   mark them complete.
5. Retain the original until a cross-volume copy has been verified.
6. Stop when a volume, memory, or swap threshold is reached; do not delete
   unrelated files to make room.

The public baseline does not load a model. If an optional local vision
experiment is enabled, run one model and one image at a time first, record the
runtime and input hashes, and keep its cache outside Git. A successful model
load is not evidence of geographic accuracy.

Resource guards and interrupted-run recovery are still being implemented.
Until a command documents otherwise, treat an interrupted output as partial
and unverified.
