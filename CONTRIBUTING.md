# Contributing

Create a virtual environment with `uv sync --extra dev --locked`. Run `uv run ruff check usimmics_bridge tests`, then `uv run pytest -m "not integration" --basetemp .testtmp-unit -q`.

Native tests require Windows and a separately installed uSimmics 5.9. Set `USIMMICS_HOME` if discovery does not find it, then run `uv run pytest -m integration --basetemp .testtmp-native -q`. Tests create temporary circuits and never require a user's private project. Do not commit simulator binaries or extracted vendor libraries.

For new component support, verify native property counts and pin/orientation order, add an independent circuit with an analytic or native reference, check parsing and preview, and make unsupported inputs fail explicitly. Do not accept a visually plausible symbol as proof of correct electrical connectivity.

New mutations must use configured project roots, preserve existing data, cooperate with file locks, and reject stale hashes. MCP stdout is reserved for protocol messages; diagnostics belong on stderr. Keep unit/protocol tests runnable without a simulator.

Build with `uv build`. Check the wheel and source distribution in a fresh environment before tagging a release. A public Alpha should clearly state its tested subset and remaining limitations.
