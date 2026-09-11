# Public repository and release plan

Status: public-repository preparation, 2026-09-10. This document describes
what may be published; it is not a claim that a generated world is finished.

## Public promise

Earthcraft aims to generate Minecraft terrain and building exteriors at one
block per metre from geographic data, with optional local computer-vision
experiments on Apple Silicon. Coverage, capture age and the block grid limit
fidelity. It does not promise exact hidden interiors, global scale without
projection distortion, or arbitrary Google imagery ingestion.

The default public path uses deterministic code and does not require AI. No
hosted model, subscription, bot author, or generated geography is part of the
baseline. Optional local inference work is research-only, must be explicitly
enabled, and must preserve the source evidence and abstentions. Contributors
should use their own names in Git history; the project does not accept bot
`Co-authored-by` trailers.

The first supported runtime is M1 Max/64 GiB; other hardware remains untested.
Generated worlds require a separately installed compatible game. Publish
compatibility only after a real load test.

## Release stages

| Stage | Required evidence | Public claim allowed |
|---|---|---|
| Planning repository | License decision, clean history review, accurate status and roadmap | Architecture and proposed experiments only; no working product claim |
| v0.1 geographic baseline | Native installation instructions tested, pinned Arnis, bounded fetch, storage preflight, synthetic offline checks, one dimension-checked playable 256 m world | Experimental no-AI baseline with measured limits |
| v0.2 local visual refinement | Exact model/runtime revisions and licenses, held-out semantic results, offline replay, frozen before/after views and attribution | Optional local AI improves the reported visible attributes on tested locations |
| Later scale release | 1,024 m seam/resume checks, second location, measured storage and runtime | Only the area sizes and configurations actually tested |

A public planning repository can precede implementation if clearly labelled. v0.1 need not wait for depth, LiDAR ingestion, a GUI, every provider, or kilometre tiling.

## Code, dependencies and data

Earthcraft source is now available under Apache-2.0, with the full [LICENSE](../LICENSE) and a project [NOTICE](../NOTICE). That license applies to the source code only. Audit actual incorporated files rather than assuming the entire upstream repository has one license: Arnis documents a separately licensed Luanti mapping. The Java-only baseline should avoid importing unrelated backend code. Preserve required upstream notices when distributing their work. [Arnis license information](https://github.com/louis-e/arnis#%EF%B8%8F-license-information)

Keep code licensing separate from provider data, derived databases, model weights, images and world downloads. For every public artifact, create a manifest listing source IDs, capture/retrieval dates, transformations, attribution text, license links and distribution decision. An unresolved distribution decision excludes the artifact from the release, not the entire codebase.

Use synthetic redistributable fixtures in default CI. Do not bundle raw map extracts, street photos, checkpoints, Minecraft assets or generated worlds simply because they are accessible locally. OSM attribution and data obligations require artifact-specific review; free map data does not grant bulk access to the public raster tile service. Prefer bounded vector queries or appropriate extracts. [OSM copyright](https://www.openstreetmap.org/copyright), [tile usage policy](https://operations.osmfoundation.org/policies/tiles/)

Google adapters remain disabled under the current source policy. Do not advertise a bypass scraper. If separately permitted access is later available, document the scope before enabling that adapter. Preserve the source-by-source treatment in SOURCE_LOSSINESS.md.

## Repository contents before v0.1

- README: status, one tested quickstart, source coverage limits, exact supported game/runtime, resource requirements and attribution links.
- LICENSE and third-party notices are present; provider and generated-artifact review remains per artifact.
- CONTRIBUTING.md: local setup, fixture checks, source-adapter contract and small change workflow.
- SECURITY.md: agreed reporting route; do not invent an address or enabled GitHub feature.
- Portable example configuration and ignored machine-local overrides; no personal absolute paths in executable defaults.
- Offline synthetic CI for transforms, boundary occupancy, configuration and world parsing; opt-in local checks for models, live data and actual game loading.
- A compact release report: exact commits, input hashes, reproduction steps, errors and sample counts, measured memory/time/disk, known unsupported cases.

Add contribution and security documents when actual setup/reporting routes exist; do not publish fictional commands. Current proposed CLI commands remain explicitly non-runnable.

## History and publication gate

Review every reachable commit and tracked artifact before the first push, not only the current checkout. Check for credentials, private AOIs, EXIF/GPS, personal machine identifiers, absolute home paths, bulky artifacts and third-party content. `.gitignore` does not remove previously tracked material. Report findings and resolve sensitive history before publishing.

Machine characteristics can remain in public benchmark documentation, but strip serial numbers/UUIDs and private filesystem paths from exported logs. Use a public landmark demonstration rather than a private residence. Review screenshot captions and embedded metadata as well as images.

At publication time, confirm the destination owner/name, selected license, clean release contents and requested visibility, then create/push the repository under that explicit publishing instruction. This request is a plan for publication, not an instruction to publish now. No board tickets are needed for these milestones.

## First release acceptance checklist

- [x] License and dependency notices added for the source tree.
- [x] Proposed public history and tracked tree checked for secrets, private inputs, and large payloads.
- [ ] Clean native installation reproduced from instructions.
- [ ] Offline synthetic checks pass and target game loads the demo world.
- [ ] Volume absence, low disk, incomplete export and resumability checks pass.
- [ ] Every shared demo asset has a documented distribution decision and attribution.
- [x] README distinguishes implemented features, measured results and roadmap.
- [ ] Release tag identifies code, runtime and source/model revisions used.

The remaining boxes are release gates, not documentation tasks. In particular,
the repository checks pass, but native game loading, live-data rights, and
generated-world acceptance still need their own evidence.
