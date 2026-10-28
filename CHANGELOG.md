# Changelog

## Unreleased

- Preview redesign: anti-aliased symbols, one spectrum panel per node voltage instead of stems drawn over each other, round-number frequency ticks with automatic Hz/kHz/MHz/GHz units, and a power axis that grows instead of clipping above 20 dBm.
- Spectral lines are labelled with their mixing product (`f1−f2`), frequency and level; the labelled values are returned as `spectrum_markers`. Samples under the display floor are no longer drawn as dots on the floor.
- Net labels are drawn as tags with a leader to their node, colour-matched to their spectrum panel. Colliding labels are nudged clear (`adjusted_labels`). The HB block shows its tones and order. Diode and filter symbols follow their Schottky/Zener style and pass band. CJK file names use a CJK font.
- Stale or unverified datasets are flagged inside the image, not only in warnings.

## 0.3.0a1

First public Alpha candidate.

- Portable package, console commands, configuration discovery and environment diagnostics.
- Sixteen MCP tools, including structured circuit authoring and bounded parameter sweeps.
- Complete-file save/clone, supported-format validation, PNG circuit/spectrum preview and verified result publication.
- Cooperative cross-process writer locks and shared-frequency HB synchronization.
- Correct DC/AC power conversion and T-junction markers in previews.
- Self-contained tests/examples, optional native solver checks, Windows/Linux CI and release packages.

Native execution remains limited to the verified uSimmics 5.9 HB subset; previews are bridge-rendered, and native unsaved GUI state is not accessible.
