---
description: Sample Earthcraft chunks from the Hugging Face dataset, render them, and judge whether they look like the real place
argument-hint: "[count=24] [revision=main] [region=X,Z]"
---

Run a visual QA pass over the published world. Arguments: $ARGUMENTS

1. Run `node scripts/qa_sample.mjs --count <count> --revision <revision> --out runs/scratch/qa/<run-id>`
   (add `--region X,Z` when given; it needs `@playwright/test`, and `--chromium PATH` when the cached
   browser build doesn't match). It serves `dashboard/` itself, renders each sample through
   `blocks.html?qa=1` straight from the dataset, and is capped at 200 MB and the free-space floor.
   Most of the metro is suburban, so add a few `--region` samples downtown (around 0,0 and -1,4).
2. Open every PNG in the run directory with the Read tool and look at it. Judge each sample on
   all three views together, 1 (broken) to 5 (convincingly real):
   - **terrain**: continuous ground, no holes, voids, floating blocks, or terraces that don't exist.
   - **buildings**: closed walls, believable roofs (not spiky noise), massing that matches a real
     street, facades that aren't one flat block.
   - **streets**: continuous roads, curbs and sidewalks, markings, intersections that line up.
   - **vegetation**: trees and lawns where a real place would have them.
   - **color**: materials and colors read as the real place, not saturated Minecraft defaults.
   - **seams**: no chunk or tile seams, stripes, missing chunks, or misaligned tiles.
   - **overall**: would someone who knows the place recognize it?
3. For every score ≤ 3, record a defect: category, which view, what is wrong, and the chunk
   coordinates. Be concrete ("roofs on the east half are single-block pillars"), never vague.
4. Write `runs/scratch/qa/<run-id>/verdicts.json`:
   `{"schema":"earthcraft.qa-verdicts.v1","run":..., "revision":..., "samples":[{"id","region","chunk",
   "scores":{...},"defects":[...]}],"summary":{"mean":{...},"worst":[ids],"patterns":[...]}}`.
   `patterns` lists defects that recur across samples. They are what the next pipeline change should fix.
5. Upload the run directory to `qa/runs/<run-id>/` with
   `scripts/hf_store.py push-files --src runs/scratch/qa/<run-id> --dest qa/runs/<run-id>`, then delete
   the local run directory.
6. Report the mean scores, the three worst samples with their defects, and the recurring patterns.
