"""Installed, read-only circuit templates; JSON resources are the single source of truth."""
from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path
from typing import Literal

from .core import BridgeError


TemplateName = Literal['voltage_divider', 'mixer']

_CATALOG = {
    'voltage_divider': {
        'title': '1 MHz voltage divider',
        'description': '1 V peak source and two equal 1 kOhm resistors; output is out.Vb.',
        'requires_model': None,
        'result_variable': 'out.Vb',
        'result_frequency_hz': 1_000_000,
    },
    'mixer': {
        'title': 'Two-tone BAT54 mixer',
        'description': '11 MHz RF and 10 MHz LO with a BAT54 diode and a 1 MHz IF output.',
        'requires_model': 'BAT54 from the separately installed uSimmics library',
        'result_variable': 'IF_out.Vb',
        'result_frequency_hz': 1_000_000,
    },
}


def list_schematic_templates() -> dict:
    """Describe templates available in the installed package, without configuration or disk writes."""
    return {'templates': [{'name': name, **metadata} for name, metadata in _CATALOG.items()]}


def get_schematic_template(name: TemplateName) -> dict:
    """Return a fresh builder specification and requirements for an installed template."""
    if name not in _CATALOG:
        raise BridgeError('Unknown template; choose ' + ', '.join(_CATALOG))
    resource = files('usimmics_bridge').joinpath('data', 'examples', name + '.json')
    specification = json.loads(resource.read_text(encoding='utf-8'))
    return {'name': name, **_CATALOG[name], 'specification': specification}


def export_schematic_template(name: TemplateName, output: Path, schematic_path: str | None = None) -> dict:
    """Export builder JSON to a new CLI-selected file; never overwrite an existing file."""
    template = get_schematic_template(name)
    specification = template['specification']
    if schematic_path is not None:
        path = Path(schematic_path).expanduser()
        if not path.is_absolute() or path.suffix.lower() != '.sch':
            raise BridgeError('--schematic-path must be an absolute .sch path')
        specification = {'path': str(path), **specification}
    destination = Path(output).expanduser().resolve()
    payload = json.dumps(specification, ensure_ascii=False, indent=2) + '\n'
    # Explicit CLI export is independent of workspace policy. The exclusive open
    # protects a pre-existing destination, including concurrent exports.
    with destination.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(payload)
    return {'name': name, 'exported_path': str(destination),
            'requires_model': template['requires_model'],
            'includes_schematic_path': schematic_path is not None}


def register_template_tools(mcp) -> None:
    """Register config-free template discovery on an existing FastMCP server."""
    from mcp.types import ToolAnnotations

    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                  idempotentHint=True, openWorldHint=False)
    mcp.tool(annotations=annotations)(list_schematic_templates)
    mcp.tool(annotations=annotations)(get_schematic_template)
