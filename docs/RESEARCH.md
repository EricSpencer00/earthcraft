# Research and decision record

Checked 2026-09-09. Primary sources only. This is a planning survey, not a benchmark. Project-specific architecture choices and error hypotheses are proposals in [PLAN.md](PLAN.md) and [SOURCE_LOSSINESS.md](SOURCE_LOSSINESS.md).

| Source | Verified fact relevant to this plan | What it does not establish |
|---|---|---|
| [Arnis repository](https://github.com/louis-e/arnis) | Generates geographic worlds from OSM/elevation; Java and Bedrock support; CLI and Apache-2.0 code | Our selected game version, façade fidelity, local runtime, or exact custom coordinate contract |
| [Google Maps Platform terms](https://cloud.google.com/maps-platform/terms) | §3.2.3 restricts scraping, caching, and creation of content from Maps content | Permission for the proposed Google scraper or offline derivative world |
| [Google Earth terms](https://maps.google.com/intl/en_all/help/terms_maps-earth/) | Separate Earth terms include product/redistribution restrictions | A reusable Earth imagery/mesh dataset license |
| [OSM copyright](https://www.openstreetmap.org/copyright) | ODbL data and attribution requirements | Accuracy, completeness, or permission to bulk-fetch rendered tiles |
| [Overture buildings](https://docs.overturemaps.org/guides/buildings/) | Buildings/parts, mixed source lineage, roofprint distinction, ODbL theme | Independent corroboration of OSM-derived features or complete building heights |
| [USGS 3DEP products](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services?qt-science_support_page_related_con=0) | Point clouds and terrain products, open access, product distinctions | Coverage/accuracy/date of an unselected AOI |
| [Mapillary license help](https://help.mapillary.com/hc/en-us/articles/115001770409-CC-BY-SA-license-for-open-data) | Describes imagery licensing and attribution | Full downstream obligation/access review for our artifact |
| [MLX-VLM](https://github.com/Blaizzy/mlx-vlm) | Local Mac VLM inference and documented Qwen3-VL usage | Our measured throughput, quantized checkpoint quality, or memory use |
| [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL) | Open model family and inference documentation | Reliable geometric reconstruction of the pilot |
| [Depth Pro](https://github.com/apple/ml-depth-pro) | Metric monocular depth model and reference implementation | Survey accuracy or the paper's GPU latency on M1 Max |
| [PyTorch MPS](https://docs.pytorch.org/docs/2.14/notes/mps.html) | Apple GPU backend documentation | Every Depth Pro operator working in our environment |
| [COLMAP installation](https://colmap.github.io/install.html) | Mac installation, CPU/GPU build considerations | An accelerated end-to-end dense reconstruction pipeline on Apple Silicon |

## Local observations

Read-only shell inspection found:

- `sysctl -n machdep.cpu.brand_string hw.memsize`: Apple M1 Max; 68,719,476,736 bytes = 64 GiB.
- `df -h /Users/eric`: about 39 GiB available on the data volume at inspection time. Recheck before downloading.
- Minecraft version directory: `1.21.10`, `1.21.11-pre1`.
- Python shim, uv, Cargo, and Git executables present. No dependency/model compatibility tests were run.

## Decisions supported by the evidence

1. Reuse an existing world generator for the first comparator rather than spending the first milestone on file-format code.
2. Design independent source adapters. Google content restrictions are material to acquisition; open sources and user-owned imagery can support a prototype without assuming a Google license.
3. Use a small local vision model first. Treat depth and multi-view reconstruction as separately gated additions.
4. Preserve source geometry, dates, and lineage before fusion. A formatted data schema and two agreeing providers do not guarantee accurate independent measurements.
5. Avoid up-front city downloads on the current disk. Use a bounded pilot and an explicit storage forecast.

## Remaining empirical questions

- Where is the pilot, and which sources actually cover it?
- Does Arnis preserve our coordinate contract and load correctly in installed Java 1.21.10?
- Which exact quantized checkpoint fits the storage budget and produces useful accepted predictions?
- Does Depth Pro run correctly and usefully on the installed Mac runtime?
- How much visible detail can be recovered from permitted imagery beyond the no-AI baseline?
- What fraction of the selected area remains unknown after all admitted sources?

These questions are assigned concrete tests in [the experiment protocol](../experiments/PROTOCOL.md). No model weights, geographic payloads, or existing Minecraft saves were modified during planning.
