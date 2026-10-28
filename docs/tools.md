# Tools and examples

Paths passed to MCP tools must be absolute and within a configured project root. All operations work on saved files. Use the returned `sha256` for subsequent writes; existing files are never overwritten merely because a name matches.

## Discover installed templates

`list_schematic_templates()` and `get_schematic_template(name)` are read-only and work before any workspace or simulator configuration. The supported names are `voltage_divider` and `mixer`. The result includes a description, model requirements, suggested result variable/frequency, and a `specification` containing the builder's `components`, `connections` and `harmonics`. Add an absolute target `path` to that specification when calling `build_schematic`.

The same resources are available from the installed CLI:

```powershell
usimmics-bridge examples list
usimmics-bridge examples show voltage_divider
usimmics-bridge examples export voltage_divider --output build.json --schematic-path "C:/CircuitProjects/demo_prj/divider.sch"
```

`show` prints metadata and the specification; `export` writes the specification itself, optionally with the supplied destination path, as a ready-to-use JSON arguments file. Exports require a new output filename. No source checkout is needed. The canonical JSON resources are in `usimmics_bridge/data/examples`; the repository's `examples/` directory documents this migration.

## Structured voltage divider

1. Call `create_project` with `{"name":"demo"}`.
2. Call `get_schematic_template` with `{"name":"voltage_divider"}`, add a `path` field pointing to `<returned project_path>/divider.sch` to its `specification`, and call `build_schematic` with that object. This step saves the circuit. `preview_schematic(path)` already works without a native solver.
3. Call `run_schematic` with that path. A 1 V peak, 1 MHz source and equal resistors produce 0.5 V peak at `out.Vb`.
4. Call `preview_schematic` with the path and returned `dataset` as `dataset_path`. It returns a PNG and metadata; `spectrum_markers` lists the labelled spectral lines, their dBm rounded to two decimals, and mixing product. `adjusted_labels` names any label the preview moved off a collision. Use `compare_results` when the comparison needs more precision than the preview markers.
5. Optionally call `publish_results` with the path, dataset path and returned `source_sha256` to update the normal GUI `.dat` file.

For CLI transport, put the object in a JSON file and run `usimmics-mcp-call build_schematic --arguments-file build.json`. Use the same pattern for every tool.

Components use `type`, `name`, `position:[x,y]`, `orientation:0..3`, and semantic `params`. Connections use `from` and `to` endpoints `{component,pin}` plus optional orthogonal `via` points and a net `label`. Pin indices are zero-based. Default Vac/Vdc pin 0 is top, passive/filter/diode pin 0 is left, GND pin 0 is its center. Native diode orientation 0 puts its cathode at the left. Rotation 1 maps `(dx,dy)` to `(dy,-dx)`. Builder-generated diagrams are empty; the independent preview still displays the circuit and optional spectrum.

The `mixer` template describes a two-tone BAT54 mixer. It names the model but contains no vendor model coefficients. The bridge reads BAT54 from the user's installed library when building the circuit. A missing model does not prevent building the divider.

## Portable workspace without a simulator

Template discovery needs no configuration. For a complete divider build and preview without a native installation, create an explicit configuration with an existing writable project root and a placeholder installation path. For example, in PowerShell:

```powershell
$workspace = Join-Path (Get-Location) "portable-demo"
New-Item -ItemType Directory -Force $workspace | Out-Null
@{
  installation_root = (Join-Path $workspace "no-simulator")
  project_roots = @($workspace)
} | ConvertTo-Json | Set-Content -Encoding utf8 portable-config.json
$env:USIMMICS_BRIDGE_CONFIG = (Resolve-Path portable-config.json).Path
```

Run the [first-example commands](../README.md#first-example-from-an-installed-package) with this configuration. `doctor` reports the solver as unavailable and returns a nonzero status; that does not prevent portable workspace operations. Native `run_schematic` and BAT54 model loading still require a real installation. Use `configure --installation-root ... --project-root ...` when setting up native simulation.

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

`parameter_sweep` returns JSON/CSV paths under `.usimmics-sweeps`, while the original source stays unchanged. Inspect `status` (`success`, `partial` or `failed`) rather than assuming every requested point succeeded. `rows` contains successful comparisons; `points` records every point's state. `completed`, `failed` and `remaining` summarize progress, and `source_snapshot` identifies the input snapshot. Summaries are checkpointed after each point, so completed work and failure details remain available if a later point fails.

The default `fail_fast: true` stops at the first failed point. Use `fail_fast: false` to attempt subsequent points. To recover interrupted work, pass `resume_from` with the absolute path of its `summary.json`, together with the same sweep request; previous progress is checked before reuse. Cancellation records a `cancelled` summary. A sweep changing the actual comparison frequency must choose a sample that exists in every result, or use independent runs and `read_results` instead. Ordinary CLI calls accept these options in the JSON arguments file.

`compare_results(dataset_paths,variable,frequency_hz,impedance)` compares up to 20 datasets at an actual frequency. It reports voltage magnitude and dBm with the specified reference impedance. There is no interpolation. Zero power is represented by null dBm rather than nonstandard JSON infinity.

## Background jobs and cancellation

Use these tools through a **persistent MCP session**. `start_job` accepts a discriminated `request`: set `operation` to `run_schematic` or `parameter_sweep`, followed by that operation's arguments. For example:

```json
{
  "request": {
    "operation": "parameter_sweep",
    "path": "C:/CircuitProjects/demo_prj/divider.sch",
    "component": "R1",
    "parameter": "resistance",
    "values": ["1 kOhm", "2 kOhm", "3 kOhm"],
    "variable": "out.Vb",
    "frequency_hz": 1000000,
    "timeout": 30,
    "fail_fast": false
  }
}
```

Keep the returned `job_id` and use `job_status(job_id)` to read progress and the eventual result. `cancel_job(job_id)` requests cancellation of that job; for an executing run it terminates the native solver, and for a sweep it preserves a partial summary. It does not merely stop waiting for an MCP response.

The server allows at most four active jobs, retains up to 64 jobs in memory, and serializes native solver execution to one process at a time per server. Job IDs belong to the running server session; they are not a durable queue across server restarts. Sweep checkpoints are stored on disk and can be supplied to a new sweep with `resume_from`.

`usimmics-mcp-call` creates a new server process for each invocation, so background job commands are unavailable through that one-shot helper. Use the ordinary `run_schematic`/`parameter_sweep` calls there, or use a persistent MCP client for job lifecycle control. Windows termination behavior requires the native verification described in [CONTRIBUTING.md](../CONTRIBUTING.md); portable tests do not establish it.

## Results and provenance

Successful `run_schematic` results include `run_id`, `manifest`, `source_snapshot`, `duration_seconds` and `validation`, alongside the dataset and log paths. The bridge validates the supported dataset structure before treating a run as successful; empty results, inconsistent dimensions and nonfinite samples fail explicitly.

`publish_results` requires the dataset and schematic provenance to match. It copies the verified dataset to the project's `.dat` destination and preserves a sidecar named `<file>.dat.manifest.json`. Keep that sidecar with a published dataset when moving project files. Legacy isolated-run manifests remain readable, but a standalone dataset without verified provenance should not be treated as proof that it matches the current schematic.

See [results and provenance](results.md) for validation limits, provenance states, interrupted publication, and the distinction between rejected invalid results and warnings for external or stale datasets.

## Failure handling

- **Configuration absent:** run `configure`, then `doctor`.
- **Wrong version or unsupported component:** use a supported 5.9 circuit; preserve the original file rather than guessing a conversion.
- **Stale SHA256:** read/inspect the file again and reconcile the change.
- **Malformed Diagram:** remove invalid records or repair the closing tag through read/save; blank diagram records are rejected.
- **Dangling pin or crossing route:** correct endpoints/waypoints before saving.
- **Solver error/timeout:** inspect the returned log path. Source and prior datasets remain intact.
- **Stale dataset:** rerun the current source; publishing old results is rejected.
- **Native GUI appears unchanged:** reload its saved file. The bridge does not access unsaved window state.
