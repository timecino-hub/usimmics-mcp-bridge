# Tools and examples

Paths passed to MCP tools must be absolute and within a configured project root. All operations work on saved files. Use the returned `sha256` for subsequent writes; existing files are never overwritten merely because a name matches.

## Structured voltage divider

1. Call `create_project` with `{"name":"demo"}`.
2. Read `examples/voltage_divider.json`, add a `path` field pointing to `<returned project_path>/divider.sch`, and call `build_schematic` with that object.
3. Call `run_schematic` with that path. A 1 V peak, 1 MHz source and equal resistors produce 0.5 V peak at `out.Vb`.
4. Call `preview_schematic` with the path and returned `dataset` as `dataset_path`. It returns a PNG and metadata.
5. Optionally call `publish_results` with the path, dataset path and returned `source_sha256` to update the normal GUI `.dat` file.

For CLI transport, put the object in a JSON file and run `usimmics-mcp-call build_schematic --arguments-file build.json`. Use the same pattern for every tool.

Components use `type`, `name`, `position:[x,y]`, `orientation:0..3`, and semantic `params`. Connections use `from` and `to` endpoints `{component,pin}` plus optional orthogonal `via` points and a net `label`. Pin indices are zero-based. Default Vac/Vdc pin 0 is top, passive/filter/diode pin 0 is left, GND pin 0 is its center. Native diode orientation 0 puts its cathode at the left. Rotation 1 maps `(dx,dy)` to `(dy,-dx)`. Builder-generated diagrams are empty; the independent preview still displays the circuit and optional spectrum.

`examples/mixer.json` describes a two-tone BAT54 mixer. It names the model but contains no vendor model coefficients. The bridge reads BAT54 from the user's installed library.

## Editing complete files

`read_schematic` returns full source, including malformed files for diagnosis. Modify the source and call `save_schematic(path,content,expected_sha256)`. It validates supported format and connectivity, backs up existing content, and writes UTF-8 with LF endings. For new files omit `expected_sha256`. `clone_schematic` creates variants without changing the source; the optional hash is for an existing destination.

For simple component edits, use `inspect_schematic` and `update_parameters`. Each edit contains `component`, `parameter` (the semantic name or zero-based index returned by inspection), and a string `value`. Source frequency edits synchronize HB keys, including shared-frequency splits/merges.

## Parameter scan

After building the divider, call:

```json
{
  "path": "C:/CircuitProjects/demo_prj/divider.sch",
  "component": "R1",
  "parameter": "resistance",
  "values": ["1 kOhm", "2 kOhm", "3 kOhm"],
  "variable": "out.Vb",
  "frequency_hz": 1000000,
  "timeout": 30
}
```

`parameter_sweep` returns rows and JSON/CSV paths under `.usimmics-sweeps`, while the original source stays unchanged. It stops on a failed point; completed isolated runs remain available for diagnosis. A sweep changing the actual comparison frequency must choose a sample that exists in every result, or use independent runs and `read_results` instead.

`compare_results(dataset_paths,variable,frequency_hz,impedance)` compares up to 20 datasets at an actual frequency. It reports voltage magnitude and dBm with the specified reference impedance. There is no interpolation. Zero power is represented by null dBm rather than nonstandard JSON infinity.

## Failure handling

- **Configuration absent:** run `configure`, then `doctor`.
- **Wrong version or unsupported component:** use a supported 5.9 circuit; preserve the original file rather than guessing a conversion.
- **Stale SHA256:** read/inspect the file again and reconcile the change.
- **Malformed Diagram:** remove invalid records or repair the closing tag through read/save; blank diagram records are rejected.
- **Dangling pin or crossing route:** correct endpoints/waypoints before saving.
- **Solver error/timeout:** inspect the returned log path. Source and prior datasets remain intact.
- **Stale dataset:** rerun the current source; publishing old results is rejected.
- **Native GUI appears unchanged:** reload its saved file. The bridge does not access unsaved window state.
