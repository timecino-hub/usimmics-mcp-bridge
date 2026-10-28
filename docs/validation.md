# Validation contracts

Configuration files require `installation_root` to be a nonempty absolute path
and `project_roots` to be a nonempty JSON array of nonempty absolute paths for
the current operating system. Unknown fields, relative paths, a string in place
of the root array, and non-string entries are rejected before any access policy
is constructed. The `configure` command resolves its CLI path arguments to
absolute paths before writing this format.

`doctor` reports three independent capabilities:

- `workspace_ready`: every project root exists, is a directory, and passes the
  operating system's write/search permission check. This check does not create
  probe files; permissions or disk space can still change before a later write.
- `solver_ready`: Windows, an existing simulator, a successful `--version`
  invocation, and a parsed 5.9 version. Text such as `15.9` or `5.90` is not 5.9.
- `bat54_ready`: the installed library contains the BAT54 component. This is
  optional for RLC/source circuits and required when requesting that model.

The existing `ready` field and CLI exit status describe the base workspace and
solver workflow (`workspace_ready and solver_ready`). A missing BAT54 library
appears separately with a suggested action instead of blocking unrelated circuits.

`component_specs.py` contains the supported native property counts, known
semantic names, default values, and pin geometry. Both structured authoring and
the raw-file parser/compiler use this contract. C and L each require all six
native properties; only their first field is named because the bridge does not
claim verified semantics for the other fields. Existing positional edits remain
available. Diode model fields retain the same explicit 32-field contract.

HB order is limited to 1–16 and each frequency's harmonic count to 1–32 in every
entry point: build, save, parameter update, saved-file validation, and simulation.
Malformed or duplicate frequency keys are rejected. These are bounds and format
checks, not guarantees of numerical convergence or native-model correctness.

MCP component, connection, endpoint, edit, and harmonic objects now have nested
JSON schemas. Unknown fields and mistyped integers (including booleans) fail
before editing. The Python/CLI builder still accepts dictionaries, and parameter
edits continue to accept the legacy `index` alias. Public tool names and argument
names are unchanged. Typed outputs are supplied for project creation and saved
schematic reads/writes; other dictionary outputs also have structured content.
Tool annotations describe side effects and are hints, not a permission system.
