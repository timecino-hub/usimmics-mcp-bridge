---
name: usimmics-control
description: Build, save, clone, validate and preview supported uSimmics 5.9 circuits through local MCP tools; run harmonic-balance simulations, parameter sweeps and frequency comparisons. Use for uSimmics/QucsStudio 5.9, not original Qucs or Qucs-S.
---

# uSimmics circuit automation

Prefer the local `usimmics` MCP tools. Begin with `environment_status` and inspect the requested project. The integration operates on saved files; it cannot inspect unsaved native GUI buffers.

## If tools are not yet discovered

Use `usimmics-mcp-call list` or `usimmics-mcp-call <tool> --arguments-file <json-file>`. This invokes the actual stdio MCP server and returns JSON plus image paths, without a chat restart. Use the local image viewer for returned PNG paths. Do not print image base64. If the console command is not on PATH, use the configured MCP Python executable with `-m usimmics_bridge.client`. If `local-installation.md` exists beside this skill, it records this machine's runtime and configuration paths.

## Workflows

- For new circuits, use `create_project` and `build_schematic`. Components have type/name/position/orientation/params; connections reference component pin indices and explicit orthogonal waypoints. Read the tool schema and examples before introducing an unfamiliar component. BAT54 is loaded from the user's installation.
- For complete-file edits, call `read_schematic`, modify the returned source, then `save_schematic` with the inspected SHA256. New targets omit the hash. Existing targets are backed up. Use `clone_schematic` for independent experiments.
- For parameter edits, use semantic names or zero-based indices from `inspect_schematic`. `update_parameters` synchronizes HB source-frequency keys, including splits/merges.
- Call `validate_schematic`, then `run_schematic`, then `read_results` or `preview_schematic` using the returned dataset. Inspect PNGs for layout defects and note freshness/warnings.
- `publish_results` saves a verified dataset to the normal `.dat` location for native plotting. Requires matching source/result provenance and `DataSet=*.dat`. Native windows need reloading before their next save.
- `parameter_sweep` creates isolated variants (maximum 20 points, 30 seconds per solver call) and JSON/CSV comparisons without modifying the source. Select a frequency actually present in every compared dataset. `compare_results` never interpolates.

## Important interpretation limits

The native solver target is Windows/uSimmics 5.9. Supported components: R/C/L, Vac/Vdc, Diode, Filter, GND and one HB block. Vdc is a component within HB; DC operating-point, AC/SP/transient commands and arbitrary native features are not implemented. Unsupported formats/types must fail rather than be guessed.

Preview is bridge-rendered from saved coordinates, not a native screenshot. Paintings are omitted with a warning. Preview spectra use a labelled -140 dBm floor and 50 ohms; raw data is unchanged. AC voltages are peak values, DC values RMS: use the correct factor when converting power. A null dBm represents zero power.

Use background tools for saving, setup inspection and simulation. GUI automation is only needed when the request specifically concerns actual native-window behavior. Coordinate concurrent GUI editing before disk writes; cooperative locks only serialize bridge clients.

Configuration is selected by `USIMMICS_BRIDGE_CONFIG` or the platform user config directory. `usimmics-bridge configure` sets installation/project paths; `doctor` verifies them. Change only paths required by the task. Do not alter unrelated host or client settings.
