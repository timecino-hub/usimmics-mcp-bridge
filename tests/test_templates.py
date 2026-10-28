import asyncio
import json
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from usimmics_bridge.builder import build_schematic
from usimmics_bridge.cli import main
from usimmics_bridge.core import BridgeError
from usimmics_bridge.templates import (
    export_schematic_template, get_schematic_template,
    list_schematic_templates, register_template_tools,
)
from usimmics_bridge.workspace import Workbench


def test_installed_templates_are_config_free_and_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('USIMMICS_BRIDGE_CONFIG', str(tmp_path / 'absent.json'))
    catalog = list_schematic_templates()['templates']
    assert {entry['name'] for entry in catalog} == {'voltage_divider', 'mixer'}
    first = get_schematic_template('voltage_divider')
    first['specification']['components'].clear()
    assert get_schematic_template('voltage_divider')['specification']['components']
    assert get_schematic_template('mixer')['requires_model']
    with pytest.raises(BridgeError, match='Unknown template'):
        get_schematic_template('../config')


def test_export_builds_without_source_checkout_or_simulator(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'installation_root': str(tmp_path / 'no-simulator'),
                               'project_roots': [str(tmp_path)]}), encoding='utf-8')
    bridge = Workbench(cfg)
    project = Path(bridge.create_project('installed')['project_path'])
    request = tmp_path / 'request.json'
    path = project / 'divider.sch'
    export_schematic_template('voltage_divider', request, str(path))
    arguments = json.loads(request.read_text(encoding='utf-8'))
    saved = build_schematic(bridge, **arguments)
    assert saved['saved'] and bridge.validate_schematic(str(path))['valid']
    preview = bridge.preview_schematic(str(path))
    assert Path(preview['image_path']).is_file()
    before = request.read_bytes()
    with pytest.raises(FileExistsError):
        export_schematic_template('mixer', request)
    assert request.read_bytes() == before
    with pytest.raises(BridgeError, match='absolute .sch'):
        export_schematic_template('voltage_divider', tmp_path / 'bad.json', 'relative.sch')
    assert not (tmp_path / 'bad.json').exists()


def test_cli_examples_need_no_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('USIMMICS_BRIDGE_CONFIG', str(tmp_path / 'absent.json'))
    monkeypatch.setattr('sys.argv', ['usimmics-bridge', 'examples', 'list'])
    assert main() == 0
    assert len(json.loads(capsys.readouterr().out)['templates']) == 2
    monkeypatch.setattr('sys.argv', ['usimmics-bridge', 'examples', 'show', 'mixer'])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['specification']['components']
    output = tmp_path / 'request.json'
    monkeypatch.setattr('sys.argv', ['usimmics-bridge', 'examples', 'export',
                                   'voltage_divider', '--output', str(output)])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['exported_path'] == str(output)
    assert main() == 1
    assert 'error' in json.loads(capsys.readouterr().err)


def test_template_mcp_catalog_is_read_only_and_names_are_bounded():
    server = FastMCP('template-test')
    register_template_tools(server)
    catalog = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    assert set(catalog) == {'list_schematic_templates', 'get_schematic_template'}
    for tool in catalog.values():
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.openWorldHint is False
    schema = catalog['get_schematic_template'].inputSchema
    assert schema['required'] == ['name']
    assert set(schema['properties']['name']['enum']) == {'voltage_divider', 'mixer'}
