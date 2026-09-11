# Critique of the original plan

Reviewed 2026-09-09 against the repository and attached hardware. This review changes the plan; it does not claim implementation results.

The strongest parts are the metre-scale coordinate contract, source-by-source loss analysis, explicit unknowns, and separate depth gate. Preserve them. The main weakness is execution sequencing: a comprehensive research architecture risks delaying the first usable release.

| Finding | Impact | Revision |
|---|---|---|
| Storage treated as one 15 GiB pool | Ignores LaCie and hides cache/copy costs | Separate internal/external budgets and filesystem-aware placement |
| External storage mentioned as a future SSD | Actual drive is exFAT; media and speed unknown | Record observations, benchmark before choosing hot-data placement |
| 64 GiB capacity used without concurrency policy | Multiple runtimes and game can compete for unified memory | One model, bounded image batches, four CPU workers, process-tree and system-pressure gates |
| Full evidence stack precedes product packaging | GeoParquet, point clouds and fusion can delay proof of value | First implement synthetic fixture and Arnis wrapper; introduce formats only as needed |
| Public repository not planned | No license decision, history review or contributor path | Separate public planning, v0.1 baseline and v0.2 AI release gates |
| Arnis integration left open-ended | Could consume the project in exporter work | Time-box baseline integration to two focused sessions; document failed contract before extending writer |
| AOI appears to block all work | Synthetic tests and storage checks need no location | Start E0 independently; select a permitted public demo AOI for live data |
| Numeric quality targets look precise before source selection | Readers could mistake aspiration for measured fidelity | Keep proposed labels; freeze gates only after coverage and independent controls are identified |
| Public story leads with many source candidates | Could be read as a Google-to-Minecraft scraper promise | Describe open geodata baseline and optional local visual refinement |
| Atomic export lacks filesystem boundary handling | External disconnect or cross-volume copy can leave partial worlds | Checksums, completion manifests and internal recovery state |

## Revised critical path

1. Portable configuration, storage preflight and synthetic geometry/export fixture.
2. Pin Arnis; make one bounded no-AI world load with measured scale and honest limitations.
3. Prepare public v0.1 with offline synthetic checks, attribution, install instructions and a permitted demo.
4. Evaluate 20 local vision crops; expand only when useful. Add the smallest scene/observation schema needed to preserve evidence.
5. Publish v0.2 only when accepted semantics improve the fixed comparator. Depth, broader adapters and kilometre tiling follow their own gates.

If Arnis cannot satisfy the scale contract within the integration time box, retain it as a comparator and document the smallest writer extension needed. Do not call an approximate baseline 1:1. No calendar completion estimate is defensible until E0/E2 are measured.
