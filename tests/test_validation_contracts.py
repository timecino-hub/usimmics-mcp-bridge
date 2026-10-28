"""The same invalid circuit must fail before mutation, regardless of its entry point."""
import asyncio
import copy
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from usimmics_bridge import server
from usimmics_bridge.builder import build_schematic
from usimmics_bridge.core import BridgeError, _compile, _parse, _sha
from test_builder import design, fixture


@pytest.mark.parametrize('kind,value', [('C', '1 nF'), ('L', '1 mH')])
def test_reactive_six_field_contract_at_every_entry_point(tmp_path, monkeypatch, kind, value):
    work = fixture(tmp_path)
    components, routes = design()
    components[2]['type'] = kind
    components[2]['params'] = {'capacitance' if kind == 'C' else 'inductance': value}
    good = tmp_path/'good.sch'
    build_schematic(work, str(good), components, routes)
    source = good.read_text()
    record = next(c for c in _parse(source)[1] if c['name'] == 'R2')
    assert len(record['properties']) == 6
    assert _compile(source).count(f'"{value}"') == 1

    # A short native record was previously accepted and emitted as an invalid netlist.
    lines = source.splitlines()
    lines[record['line']] = record['prefix'] + f' "{value}"1'
    invalid = '\n'.join(lines) + '\n'
    bad = tmp_path/'bad.sch'
    bad.write_text(invalid)
    with pytest.raises(BridgeError, match='property count'):
        _compile(invalid)
    assert work.validate_schematic(str(bad))['valid'] is False
    with pytest.raises(BridgeError, match='property count'):
        work.save_schematic(str(tmp_path/'saved.sch'), invalid)
    with pytest.raises(BridgeError, match='property count'):
        work.update_parameters(str(bad), [{'component': 'R2', 'parameter': 0, 'value': value}], _sha(bad.read_bytes()))
    work.simulator.parent.mkdir(parents=True)
    work.simulator.write_bytes(b'not executed')
    def must_not_launch(*args, **kwargs):
        raise AssertionError('Invalid source reached native solver')
    monkeypatch.setattr('usimmics_bridge.core.subprocess.run', must_not_launch)
    with pytest.raises(BridgeError, match='property count'):
        work.run_schematic(str(bad))
    components[2]['properties'] = [value]
    with pytest.raises(BridgeError, match='property count'):
        build_schematic(work, str(tmp_path/'built.sch'), components, routes)
    assert bad.read_text() == invalid
    assert not list(tmp_path.glob('*.bak.*'))
    assert not (tmp_path/'.usimmics-runs').exists()
    assert not (tmp_path/'built.sch').exists()
    assert not (tmp_path/'saved.sch').exists()


@pytest.mark.parametrize('setting', ['0@;4@1 MHz;', '17@;4@1 MHz;', '3@;0@1 MHz;', '3@;33@1 MHz;'])
def test_harmonic_bounds_apply_to_raw_save_update_validate_and_run(tmp_path, monkeypatch, setting):
    work = fixture(tmp_path)
    components, routes = design()
    target = tmp_path/'good.sch'
    build_schematic(work, str(target), components, routes)
    original = target.read_bytes()
    text = original.decode().replace('3@;4@1 MHz;', setting)
    with pytest.raises(BridgeError, match='HB (order|count)'):
        work.save_schematic(str(tmp_path/'invalid.sch'), text)
    with pytest.raises(BridgeError, match='HB (order|count)'):
        work.update_parameters(str(target), [{'component': 'HB1', 'parameter': 'harmonics', 'value': setting}], _sha(original))
    raw = tmp_path/'raw.sch'
    raw.write_text(text)
    assert work.validate_schematic(str(raw))['valid'] is False
    work.simulator.parent.mkdir(parents=True)
    work.simulator.write_bytes(b'not executed')
    def must_not_launch(*args, **kwargs):
        raise AssertionError('Invalid HB settings reached native solver')
    monkeypatch.setattr('usimmics_bridge.core.subprocess.run', must_not_launch)
    with pytest.raises(BridgeError, match='HB (order|count)'):
        work.run_schematic(str(raw))
    assert target.read_bytes() == original
    assert not list(tmp_path.glob('*.bak.*'))
    assert not (tmp_path/'invalid.sch').exists()


@pytest.mark.parametrize('mutation', ['unknown_field', 'bool_coordinate', 'float_rotation', 'bad_param', 'bad_connection'])
def test_builder_rejects_malformed_nested_input_before_writing(tmp_path, mutation):
    work = fixture(tmp_path)
    components, routes = design()
    if mutation == 'unknown_field':
        components[0]['orientaton'] = 1
    elif mutation == 'bool_coordinate':
        components[0]['position'][0] = True
    elif mutation == 'float_rotation':
        components[0]['orientation'] = 1.0
    elif mutation == 'bad_param':
        components[1]['params'] = {'capacitance': '1 pF'}
    else:
        routes[0]['from']['pin'] = False
    with pytest.raises(BridgeError):
        build_schematic(work, str(tmp_path/'bad.sch'), components, routes)
    assert not (tmp_path/'bad.sch').exists()


@pytest.mark.parametrize('settings', [{'order': 17}, {'count': 33}, {'order': True}, {'count': 4.0}, {'ordr': 3}])
def test_builder_harmonic_schema_is_strict(tmp_path, settings):
    work = fixture(tmp_path)
    components, routes = design()
    with pytest.raises(BridgeError):
        build_schematic(work, str(tmp_path/'bad.sch'), components, routes, settings)
    assert not (tmp_path/'bad.sch').exists()


def test_dict_update_api_keeps_positional_index_compatibility(tmp_path):
    work = fixture(tmp_path)
    components, routes = design()
    path = tmp_path/'circuit.sch'
    saved = build_schematic(work, str(path), components, routes)
    result = work.update_parameters(str(path), [{'component': 'R1', 'index': 0, 'value': '75 Ohm'}], saved['sha256'])
    assert result['changes'][0]['after'] == '75 Ohm'
    before = path.read_bytes()
    with pytest.raises(BridgeError):
        work.update_parameters(str(path), [{'component': 'R1', 'parameter': 0, 'value': '90 Ohm', 'typo': True}], result['sha256'])
    assert path.read_bytes() == before


def test_real_fastmcp_schemas_and_nested_validation(tmp_path, monkeypatch):
    work = fixture(tmp_path)
    monkeypatch.setattr(server, 'CONFIG', work.config_path)
    async def exercise():
        tools = {tool.name: tool for tool in await server.mcp.list_tools()}
        tool = tools['build_schematic']
        schema = tool.inputSchema
        component = schema['$defs']['ComponentInput']
        assert set(component['required']) == {'type', 'name', 'position'}
        assert component['additionalProperties'] is False
        assert component['properties']['orientation']['maximum'] == 3
        assert 'resistance' in component['properties']['params']['propertyNames']['enum']
        assert schema['properties']['components']['maxItems'] == 100
        assert schema['$defs']['Connection']['properties']['from']['$ref'].endswith('/Endpoint')
        assert schema['$defs']['HarmonicSettings']['properties']['count']['maximum'] == 32
        assert tools['parameter_sweep'].inputSchema['properties']['values']['maxItems'] == 20
        assert tools['parameter_sweep'].inputSchema['properties']['timeout']['maximum'] == 30
        assert tools['read_schematic'].outputSchema['required'] == ['path', 'sha256', 'content', 'state']
        assert tools['read_schematic'].annotations.readOnlyHint is True
        assert tools['preview_schematic'].annotations.readOnlyHint is False
        assert tool.annotations.openWorldHint is False
        with pytest.raises(ToolError, match='frequency_hz'):
            await server.mcp.call_tool('compare_results', {'dataset_paths': ['unused.dat'], 'variable': 'out.Vb', 'frequency_hz': True})
        components, routes = design()
        broken = copy.deepcopy(components)
        broken[0]['orientation'] = True
        with pytest.raises(ToolError):
            await server.mcp.call_tool('build_schematic', {'path': str(tmp_path/'bad.sch'), 'components': broken, 'connections': routes})
        assert not (tmp_path/'bad.sch').exists()
        content, structured = await server.mcp.call_tool('build_schematic', {'path': str(tmp_path/'good.sch'), 'components': components, 'connections': routes})
        assert structured['saved'] is True
        assert json.loads(content[0].text)['sha256'] == structured['sha256']
        await server.mcp.call_tool('update_parameters', {'path': str(tmp_path/'good.sch'), 'edits': [{'component': 'R1', 'parameter': 'resistance', 'value': '75 Ohm'}], 'expected_sha256': structured['sha256']})
    asyncio.run(exercise())
