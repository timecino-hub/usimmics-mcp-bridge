# Contributing

Create a virtual environment with `uv sync --extra dev --locked`. Run `uv run --locked ruff check usimmics_bridge tests scripts`, then `uv run --locked pytest -m "not integration" --basetemp .testtmp-unit -q`.

Native tests require Windows and a separately installed uSimmics 5.9. Set `USIMMICS_HOME` if discovery does not find it, then run `uv run --locked pytest -m integration --basetemp .testtmp-native -q -rs`. Tests create temporary circuits and never require a user's private project. Do not commit simulator binaries or extracted vendor libraries. A skip means the solver has not been tested on that machine; it is not a successful native verification.

For new component support, verify native property counts and pin/orientation order, add an independent circuit with an analytic or native reference, check parsing and preview, and make unsupported inputs fail explicitly. Do not accept a visually plausible symbol as proof of correct electrical connectivity.

New mutations must use configured project roots, preserve existing data, cooperate with file locks, and reject stale hashes. MCP stdout is reserved for protocol messages; diagnostics belong on stderr. Keep unit/protocol tests runnable without a simulator.

## Installed examples

Edit only `usimmics_bridge/data/examples/*.json`. These package resources ship in the wheel and source distribution and are loaded with `importlib.resources`, so they work outside a repository checkout. Tests use the same resource loader. Do not reintroduce duplicate JSON files under `examples/`; that directory explains `usimmics-bridge examples export` for users who need a standalone request file. The mixer template must reference the installed model by name without embedding vendor model coefficients.

## Package and dependency verification

```powershell
uv build
uv export --locked --no-dev --no-emit-project --no-hashes --output-file .ci-runtime-requirements.txt
python scripts/smoke_wheel.py --wheel dist --requirements .ci-runtime-requirements.txt
```

The smoke driver uses the standard library to create a fresh venv with no system site packages. It installs the built wheel and exact exported runtime dependencies, copies its check to a temporary directory outside the checkout, removes inherited Python import overrides, and verifies that `usimmics_bridge` resolves inside the new venv. It then checks the installed CLI/resources, real MCP tool catalog/schema, create → build → preview → read/save, overwrite protection, and image decoding. It never creates a fake solver executable or claims a native simulation passed.

The locked CI matrix runs on Windows/Linux with Python 3.11/3.12. The separate `compatible-latest` job installs the latest dependencies allowed by `pyproject.toml` and runs the same tests and clean wheel smoke without the lock export. This keeps reproducible verification distinct from compatibility with new dependency releases. Both jobs build the source distribution and wheel; `uv build` also builds the wheel from its generated source distribution.

## Before a release

Use the exact commit intended for the release. Require both portable CI jobs and the clean wheel workflow to pass. When a release changes the compiler, dataset parser, native execution or model handling, also run the opt-in native suite on Windows with uSimmics 5.9 and record the commit, bridge version, simulator banner, passed tests and skipped tests in the release notes. Recheck the installed wheel's native build → run → preview → publish → sweep flow on that same machine. If native execution has not been performed, state that explicitly instead of treating portable CI or skips as native verification. Keep the supported subset and remaining limitations visible in every Alpha release.
