"""Local stdio MCP entry point for the uSimmics 5.9 bridge."""
from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations
from usimmics_bridge.workspace import Workbench as Bridge
from .config import config_path, doctor
from .builder import build_schematic as _build
from .experiments import parameter_sweep as _sweep, compare_results as _compare
from .schemas import (
    ComponentList, ConnectionList, DatasetPaths, EditList, Frequency, HarmonicSettings,
    Impedance, ProjectResult, ReadSchematicResult, ResultPointLimit, RunTimeout,
    SaveResult, SweepTimeout, SweepValues,
)

CONFIG = config_path()
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
CREATE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
mcp = FastMCP(
    "usimmics",
    instructions=(
        "Control local uSimmics 5.9 projects without GUI automation. Inspect the environment "
        "and schematic before editing. Pass the inspected SHA256 to update_parameters. "
        "run_schematic compiles only its explicitly supported component types and rejects "
        "unknown circuits. Runs use separate output folders; existing datasets are preserved. "
        "Use read_schematic/save_schematic for complete files, clone_schematic for variants, "
        "save operations validate before writing; validate_schematic checks saved files. Use preview_schematic for an image of the saved layout "
        "and optional discrete spectrum. publish_results saves verified run data for native GUI plots. "
        "Preview images come from the bridge renderer, not the native GUI. "
        "On-disk edits do not replace unsaved edits in an open GUI."
    ),
)


@mcp.tool(annotations=READ)
def environment_status() -> dict[str, Any]:
    """Check local installation, simulator, BAT54 library, configured project roots and capabilities."""
    return doctor(CONFIG)


@mcp.tool(annotations=READ)
def list_projects() -> dict[str, Any]:
    """List existing uSimmics projects in the configured local roots, without creating projects."""
    return Bridge(CONFIG).list_projects()


@mcp.tool(annotations=READ)
def inspect_schematic(path: str) -> dict[str, Any]:
    """Inspect a .sch file, component parameters and SHA256 before a background edit or run."""
    return Bridge(CONFIG).inspect_schematic(path)


@mcp.tool(annotations=WRITE)
def update_parameters(path: str, edits: EditList, expected_sha256: str) -> dict[str, Any]:
    """Atomically edit component parameters with backup and conflict detection.

    Each edit: {"component": "V1RF", "parameter": "frequency", "value": "12 MHz"}.
    Use parameter names/indices returned by inspect_schematic. Frequency changes also
    update matching harmonic-balance frequency keys. All edits validate before writing.
    Reload the file in uSimmics to see these disk changes.
    """
    return Bridge(CONFIG).update_parameters(path, edits, expected_sha256)


@mcp.tool(annotations=CREATE)
def run_schematic(path: str, timeout: RunTimeout = 60) -> dict[str, Any]:
    """Compile a supported 5.9 schematic and run simulator.exe headlessly in an isolated run folder.

    Supports the documented R/C/L, Vac/Vdc, BAT54/Diode, Filter, GND and HB subset.
    Unknown components or ambiguous wiring fail explicitly. Returns unique output paths
    and solver status; does not overwrite the GUI's datasets or open a window.
    """
    return Bridge(CONFIG).run_schematic(path, timeout)


@mcp.tool(annotations=READ)
def read_results(path: str, max_points: ResultPointLimit = 2000) -> dict[str, Any]:
    """Read verified QucsData v2 real/complex simulation results, with a bounded number of points."""
    return Bridge(CONFIG).read_results(path, max_points)


@mcp.tool(annotations=CREATE)
def create_project(name: str, root: str | None = None) -> ProjectResult:
    """Create a new _prj directory in a configured project root. Fails if it already exists."""
    return Bridge(CONFIG).create_project(name, root)


@mcp.tool(annotations=READ)
def read_schematic(path: str) -> ReadSchematicResult:
    """Read complete saved .sch source plus SHA256, including malformed files for diagnosis."""
    return Bridge(CONFIG).read_schematic(path)


@mcp.tool(annotations=WRITE)
def save_schematic(path: str, content: str, expected_sha256: str | None = None) -> SaveResult:
    """Save a complete supported 5.9 schematic atomically, including components, wires and plots.

    Supply full source from read_schematic with requested edits. Existing files require their
    current SHA256 and are backed up; omit the hash for new files. Syntax and connectivity
    validate before any write. Saves UTF-8/LF, never an unsaved GUI buffer.
    """
    return Bridge(CONFIG).save_schematic(path, content, expected_sha256)


@mcp.tool(annotations=WRITE)
def clone_schematic(source: str, destination: str, expected_sha256: str | None = None) -> SaveResult:
    """Save a copy of a schematic as an independent variant. Existing destination needs its hash."""
    return Bridge(CONFIG).clone_schematic(source, destination, expected_sha256)


@mcp.tool(annotations=READ)
def validate_schematic(path: str) -> dict[str, Any]:
    """Check saved-file sections, plot closure, supported component connections and HB settings.

    Returns valid/errors/warnings and a compiled netlist. This is supported-format validation,
    not a claim that every native GUI feature can be rendered or checked.
    """
    return Bridge(CONFIG).validate_schematic(path)


@mcp.tool(annotations=WRITE)
def publish_results(path: str, dataset_path: str, expected_sha256: str) -> dict[str, Any]:
    """Save this bridge's verified run dataset to the schematic's normal .dat path for GUI plots.

    Requires matching run provenance and current schematic hash. Backs up existing .dat.
    Refuses stale results and custom DataSet paths. Reload in the GUI to view changes there.
    """
    return Bridge(CONFIG).publish_results(path, dataset_path, expected_sha256)


@mcp.tool(annotations=CREATE)
def preview_schematic(path: str, dataset_path: str | None = None) -> list:
    """Return a PNG image of the saved circuit layout and optionally its discrete HB spectrum.

    Renders original component positions, labels, wires and supported symbols using the bridge
    renderer. The PNG is returned as native MCP image content and saved locally. It is NOT a
    screenshot of the native GUI and omits Paintings annotations with a warning. Colliding labels
    are nudged clear and named in adjusted_labels. The optional spectrum draws one panel per node
    voltage (peak-voltage dBm/50 Ohm, labelled display floor) and labels the strongest lines with
    mixing product, frequency and level; spectrum_markers returns those exact values. Data whose
    provenance does not match the schematic is flagged in the image.
    """
    result = Bridge(CONFIG).preview_schematic(path, dataset_path)
    return [json.dumps(result, ensure_ascii=False), Image(path=result['image_path'])]


@mcp.tool(annotations=WRITE)
def build_schematic(path: str, components: ComponentList, connections: ConnectionList,
                    harmonics: HarmonicSettings | None = None, expected_sha256: str | None = None) -> SaveResult:
    """Build and save a circuit from component/pin descriptions without writing .sch syntax.

    Components: {type,name,position:[x,y],orientation:0..3,params:{parameter:value}}.
    Diode additionally requires model='BAT54' (loaded locally) or explicit properties.
    Connections: {from:{component,pin},to:{component,pin},via:[[x,y]],label:optional}.
    Pins are zero-based; default R/filter/diode pin0 left, Vac pin0 top, GND pin0 center.
    All routes must be orthogonal. Different-net crossings/touches are rejected.
    Harmonics: {order:3,count:4}. Source frequencies are inserted automatically.
    """
    return _build(Bridge(CONFIG),path,components,connections,harmonics,expected_sha256)


@mcp.tool(annotations=CREATE)
def parameter_sweep(path: str, component: str, parameter: str, values: SweepValues,
                    variable: str = 'IF_out.Vb', frequency_hz: Frequency = 1e6, timeout: SweepTimeout = 30) -> dict[str, Any]:
    """Simulate 1..20 independent parameter variants, preserve source, return JSON/CSV comparison.

    Each run is limited to 1..30 seconds. Comparison frequency must exist as an actual sample.
    Results record the source hash and variant paths; no GUI data is overwritten.
    """
    return _sweep(Bridge(CONFIG),path,component,parameter,values,variable,frequency_hz,timeout)


@mcp.tool(annotations=READ)
def compare_results(dataset_paths: DatasetPaths, variable: str, frequency_hz: Frequency,
                    impedance: Impedance = 50) -> dict[str, Any]:
    """Compare a complex voltage at one actual frequency across 1..20 datasets (no interpolation).

    Includes magnitude and impedance-qualified dBm; AC samples are peak voltages, DC uses RMS.
    """
    return _compare(Bridge(CONFIG),dataset_paths,variable,frequency_hz,impedance)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
