# Result validation and provenance

`read_results` checks the complete binary dataset, including samples omitted by
`max_points`. The response limit only controls returned data. The supported
QucsData v2 records must contain nonempty, uniquely named variables; dependencies
must refer to real axes with matching sample counts. A `frequency` axis must be
real, independent, in Hz, finite and nonnegative. All NaN/Inf values and
nonfinite complex magnitudes are rejected with the variable and sample index.
Native HB runs and publication additionally require a complex voltage spectrum
that depends on the frequency axis. This checks file and numeric validity; it
does not independently prove physical accuracy or solver convergence.

The 64 MiB input limit applies to the actual read. There may be at most 4096
variables, and a single decoded response is limited to one million samples;
lower `max_points` when that response limit is reached. A truncated response
cannot be used by `compare_results` or `preview_schematic` as a complete spectrum.
Comparison internally retains only the frequency axis and requested voltage,
while validating every variable and sample. Unrelated traces therefore do not
consume its response budget or conceal invalid values elsewhere in the file.

## Run artifacts

Each isolated run stores the exact input bytes as `source.sch`, the compiled
`circuit.net`, `result.dat`, `simulation.log`, and `manifest.json`. The dataset
also has a dedicated `result.dat.manifest.json`. Schema version 1 records:

- `status`: running, success, failed or timed_out;
- source, dataset and netlist paths and SHA256 digests;
- the source snapshot path and hash;
- run ID, start/end timestamps and elapsed seconds;
- bridge version and simulator identity;
- exit code, full-dataset validation summary and structured failure information.

The simulator version reuses a successful `environment_status`/doctor query in
the same process. If it has not been queried, `simulator.version` is null and
`version_source` is `not_queried`; starting a run never adds a version subprocess.
Invalid solver output is a failed run even when the process exited with code 0.
Failed and timed-out runs retain their input snapshot and failure manifest.

## Publication

`publish_results` validates and copies the same captured dataset bytes, then
retains provenance beside the GUI dataset, for example
`divider.dat.manifest.json`. Publishing still requires a current source hash,
matching provenance and `DataSet=*.dat`. Existing outputs retain backups.

Two separate files cannot be replaced in one filesystem operation. Publication
first commits a sidecar with `status: publishing`, replaces the dataset, and
finally commits `status: success`. If interrupted, the pending state remains
explicitly untrusted. Rerun publication from the original successful run to
recover. A dataset-specific sidecar always takes precedence over a legacy
`manifest.json`, even if the sidecar is invalid or incomplete.

Old run directories containing only the original four-field `manifest.json`
remain readable and publishable when their source and dataset hashes match.

## Consumer behavior

`read_results`, previews and comparisons share provenance resolution. States
include `matches_source`, `unverified`, `stale_or_modified`,
`invalid_provenance`, `incomplete` and `failed`.

- Reading returns provenance and explicit warnings with the decoded data.
- A preview marks unverified, stale, invalid, incomplete or failed provenance in
  the image and metadata; it never presents those states as a verified result.
- Comparison refuses invalid, incomplete and failed provenance. External or
  stale datasets may still be compared, with warnings and provenance in every
  row. Every row includes its dataset hash and reference impedance.
- Publication accepts only provenance matching the current source and dataset.

Frequency comparison first prefers an exact sample, then a unique match within
the documented numerical tolerance (relative 1e-9, absolute 1e-6 Hz). DC only
matches DC. Power always uses the actual selected sample's frequency: DC voltage
is RMS, while nonzero-frequency HB voltage is peak amplitude.

These hashes detect stale or accidentally altered files. They are not signatures
and cannot authenticate results against someone who can rewrite both datasets
and their manifests. Portable tests use synthetic binary fixtures and a fake
solver hook; native simulation remains covered by the separate integration
suite requiring a local uSimmics installation.
