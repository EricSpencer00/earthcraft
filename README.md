# Earthcraft

Turn a real place into a Minecraft world at one block per metre, with inference on an Apple Silicon Mac.

**Status: planned, not implemented.** This repository contains the architecture, source-by-source loss analysis, experiment gates, and initial configuration. No map downloads, model inference, or world generation have run yet. The name is a local working name; there is no remote repository or public release.

The intended pipeline is:

```text
Map vectors + elevation + aerial imagery + street imagery
                         ↓
              Source-specific observations
                         ↓
        Metric scene with evidence and uncertainty
                         ↓
       Local vision inference for missing attributes
                         ↓
           Deterministic Minecraft block export
                         ↓
             Playable world + quality report
```

Start with [the implementation plan](docs/PLAN.md). Read [source lossiness](docs/SOURCE_LOSSINESS.md) before adding a data provider, and [the experiments](experiments/PROTOCOL.md) before making a quality or performance claim. [Research sources](docs/RESEARCH.md) distinguish verified upstream capabilities from our proposals.

The first deliverable will be a 256 × 256 metre exterior reconstruction in Java Edition. Expand to a kilometre only after the smaller world passes geometry and game-loading checks. A full selected area can be generated; a lossless reconstruction of hidden reality cannot be promised.

Arnis is the initial baseline and candidate export backend. The new work is source comparison, local inference, evidence-aware fusion, and measurable reconstruction quality.

Google Maps/Earth/Street View remain explicit source candidates, but their standard access and derivative-content restrictions prevent treating a scraper as the default acquisition backend. Open map data, public elevation, suitably licensed imagery, and user-owned captures provide an executable starting route. See [the source analysis](docs/SOURCE_LOSSINESS.md).

Configuration in [configs/pilot.json](configs/pilot.json) is a **design contract**, not a runnable command interface. The pilot location is pending. Code and dataset licensing are separate; no project-wide redistribution license has been selected.
