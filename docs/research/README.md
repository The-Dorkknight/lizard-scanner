# Research

**Start here:** [Down the Lizard Hole](../paper/down-the-lizard-hole.pdf), the paper that tells all of
this as one story, with the structured-light background and references. Its source, figures and data are
in [its own repository](https://github.com/The-Dorkknight/down-the-lizard-hole).

| Document | What it is |
|---|---|
| [lizard-engine/RESEARCH.md](lizard-engine/RESEARCH.md) | How comparable cameras are controlled, and what the two USB recordings of Creality's app showed (15 October 2026) |
| [lizard-engine/PLAN.md](lizard-engine/PLAN.md) | The open questions at hand-over: ring brightness, camera B, calibration, engine work |
| [lizard-engine/HANDOFF.md](lizard-engine/HANDOFF.md) | The hand-over of the Lizard engine from Who are yoUSB? to the multi-scanner app. It describes `lizard_engine.py`, which became this repository's `lizard_camera.py` |
| [real-scanner/README.md](real-scanner/README.md) | **The real scanner, 16 October 2026**: what was seen, the six light / gain test reports, what they mean, what is open |
| [real-scanner/2026-10-18-stripes.md](real-scanner/2026-10-18-stripes.md) | **The first real wall test, 18 October 2026**: the pattern is vertical stripes with wobbling beads, why 0.4's depth was noise, shift maps, the refit (walls within 2 mm), depth of a mug |
| [lizard-engine/reports/](lizard-engine/reports/) | The two recordings' analysis reports and every camera request in time order (serials and the Windows user name removed) |

What this app adds on top (the wall calibration and its measurements) is in
[../../DESIGN_NOTES.md](../../DESIGN_NOTES.md) and [../../HOW_IT_WORKS.md](../../HOW_IT_WORKS.md).
The tool that made the reports is `tools/lizard_capture.py` (from Who are yoUSB?).
