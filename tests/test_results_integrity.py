"""Synthetic binary QucsData fixtures and fake-solver boundary tests.

These exercise the real decoder and filesystem workflow. They do not stand in
for the separately marked tests of the proprietary native uSimmics executable.
"""
import json
import math
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace

import pytest

from usimmics_bridge.builder import build_schematic
from usimmics_bridge.core import BridgeError, _sha
from usimmics_bridge.datasets import parse_dataset
from usimmics_bridge.experiments import compare_results
from usimmics_bridge.provenance import dataset_sidecar, remember_simulator_banner, simulator_metadata
from usimmics_bridge.workspace import Workbench
from test_builder import design


def binary_records(records):
    """Author actual little-endian records; no decoded-result stubs."""
    header = bytearray()
    samples = bytearray()
    for name, unit, values, dependency in records:
        complex_ = dependency is not None
        header.extend(bytes([3 if complex_ else 5]) + unit.encode('ascii').ljust(3, b'\0'))
        header.extend(struct.pack('<I', len(values)) + name.encode() + b'\0')
        if complex_:
            header.extend(dependency.encode() + b'\0')
        for value in values:
            samples.extend(struct.pack('<dd', *value) if complex_ else struct.pack('<d', value))
    return b'QucsData\0\0\x02\x03' + struct.pack('<I', len(header)) + header + samples


def hb_binary(frequencies=(0.0, 1e6, 2e6), voltages=((0.0, 0.0), (0.5, 0.0), (0.0, 0.0))):
    return binary_records([('frequency', 'Hz', frequencies, None),
                           ('out.Vb', 'V', voltages, 'frequency')])


@pytest.fixture
def circuit(tmp_path):
    install = tmp_path / 'install'
    (install / 'bin').mkdir(parents=True)
    (install / 'bin' / 'simulator.exe').write_bytes(b'fake solver placeholder')
    cfg = tmp_path / 'config.json'
    cfg.write_text(json.dumps({'installation_root': str(install), 'project_roots': [str(tmp_path)]}))
    work = Workbench(cfg)
    components, connections = design()
    source = tmp_path / 'divider.sch'
    saved = build_schematic(work, str(source), components, connections)
    return work, source, saved


def fake_solver(monkeypatch, work, data, *, returncode=0):
    def execute(netpath, dataset, log, timeout):
        dataset.write_bytes(data)
        log.write_text('Synthetic solver fixture\n')
        return SimpleNamespace(returncode=returncode, output_tail='Synthetic solver fixture')
    monkeypatch.setattr(work, '_execute_solver', execute)


def test_binary_fixture_decodes_and_checks_samples_beyond_response_limit():
    fixture = Path(__file__).with_name('fixtures') / 'hb_voltage.dat'
    raw = fixture.read_bytes()
    assert raw == hb_binary()
    parsed = parse_dataset(raw, max_points=1, require_hb=True)
    assert parsed['truncated']
    assert parsed['dataset_sha256'] == _sha(raw)
    assert parsed['validation']['all_samples_checked']
    assert parsed['validation']['samples_checked'] == 6
    assert [len(variable['data']) for variable in parsed['variables']] == [1, 1]


@pytest.mark.parametrize(('data', 'message'), [
    (binary_records([]), 'no variables'),
    (binary_records([('frequency', 'Hz', [0], None), ('frequency', 'Hz', [0], None)]), 'Duplicate'),
    (binary_records([('frequency', 'Hz', [], None)]), 'no samples'),
    (binary_records([('frequency', 'kHz', [0], None)]), 'real data in Hz'),
    (binary_records([('frequency', 'Hz', [(0, 0)], 'axis'), ('axis', 'Hz', [0], None)]), 'real data in Hz'),
    (binary_records([('out.Vb', 'V', [(1, 0)], 'missing')]), 'Missing dependency'),
    (hb_binary(voltages=((1, 0),)), 'sample count'),
    (hb_binary(frequencies=(0, 1e6, -1)), 'nonnegative'),
    (hb_binary(frequencies=(0, 1e6, float('nan'))), 'Nonfinite'),
    (hb_binary(voltages=((0, 0), (1, 0), (float('inf'), 0))), 'Nonfinite'),
    (hb_binary(voltages=((0, 0), (1, 0), (0, float('nan')))), 'Nonfinite'),
    (hb_binary(voltages=((0, 0), (1, 0), (1.7e308, 1.7e308))), 'Nonfinite'),
    (hb_binary()[:-1], 'payload is truncated'),
    (hb_binary() + b'junk', 'trailing dataset payload'),
])
def test_reject_invalid_binary_semantics_even_with_one_output_point(data, message):
    with pytest.raises(BridgeError, match=message):
        parse_dataset(data, max_points=1)


def test_reject_truncated_header_and_unknown_record():
    with pytest.raises(BridgeError, match='header is truncated'):
        parse_dataset(hb_binary()[:20])
    raw = bytearray(hb_binary())
    raw[16] = 99
    with pytest.raises(BridgeError, match='variable type 99'):
        parse_dataset(bytes(raw))


def test_non_hb_data_can_be_inspected_but_cannot_be_an_hb_result():
    raw = binary_records([('time', 's', [0, 1], None)])
    assert parse_dataset(raw)['validation']['hb_spectrum'] is False
    with pytest.raises(BridgeError, match='HB results require'):
        parse_dataset(raw, require_hb=True)


@pytest.mark.parametrize('data', [binary_records([]), hb_binary(voltages=((1, 0),)),
                                 hb_binary(voltages=((0, 0), (1, 0), (float('inf'), 0)))])
def test_publish_checks_numeric_quality_even_when_legacy_hashes_match(circuit, tmp_path, data):
    work, source, saved = circuit
    folder = tmp_path / 'legacy-run'
    folder.mkdir()
    dataset = folder / 'result.dat'
    dataset.write_bytes(data)
    (folder / 'manifest.json').write_text(json.dumps({
        'source': str(source), 'source_sha256': saved['sha256'],
        'dataset_sha256': _sha(data), 'netlist_sha256': '0' * 64}))
    with pytest.raises(BridgeError):
        work.publish_results(str(source), str(dataset), saved['sha256'])
    assert not source.with_suffix('.dat').exists()


@pytest.mark.parametrize('data', [binary_records([]), hb_binary(voltages=((1, 0),)),
                                 hb_binary(voltages=((0, 0), (1, 0), (float('nan'), 0)))])
def test_zero_exit_invalid_solver_output_fails_and_records_snapshot(circuit, monkeypatch, data):
    work, source, _ = circuit
    original = source.read_bytes()
    fake_solver(monkeypatch, work, data)
    with pytest.raises(BridgeError):
        work.run_schematic(str(source))
    run = next((source.parent / '.usimmics-runs').iterdir())
    info = json.loads((run / 'manifest.json').read_text())
    assert info['status'] == 'failed'
    assert info['exit_code'] == 0
    assert info['error']['code'] == 'invalid_results'
    assert info['duration_seconds'] >= 0
    assert Path(info['source_snapshot']).read_bytes() == original
    assert json.loads(dataset_sidecar(run / 'result.dat').read_text())['status'] == 'failed'


@pytest.mark.parametrize(('error', 'status'), [
    (subprocess.TimeoutExpired('synthetic solver', 1), 'timed_out'),
    (OSError('synthetic launch failure'), 'failed'),
])
def test_solver_failure_manifest_survives(circuit, monkeypatch, error, status):
    work, source, _ = circuit
    def execute(*args):
        raise error
    monkeypatch.setattr(work, '_execute_solver', execute)
    with pytest.raises(BridgeError):
        work.run_schematic(str(source))
    run = next((source.parent / '.usimmics-runs').iterdir())
    info = json.loads((run / 'manifest.json').read_text())
    assert info['status'] == status
    assert info['completed_at'] is not None
    assert Path(info['source_snapshot']).is_file()


def test_run_publish_preview_compare_keep_verified_provenance(circuit, monkeypatch):
    work, source, saved = circuit
    fake_solver(monkeypatch, work, hb_binary())
    remember_simulator_banner(work.simulator, 'uSimmics simulator 5.9')
    run = work.run_schematic(str(source))
    info = json.loads(Path(run['manifest']).read_text())
    assert info['schema_version'] == 1 and info['status'] == 'success'
    assert info['bridge_version'] and info['simulator']['version'] == '5.9'
    assert info['dataset_sha256'] == run['dataset_sha256'] == _sha(hb_binary())
    assert Path(run['source_snapshot']).read_bytes() == source.read_bytes()
    assert work.preview_schematic(str(source), run['dataset'])['dataset_freshness'] == 'matches_source'

    published = work.publish_results(str(source), run['dataset'], saved['sha256'])
    assert published['provenance']['verified']
    assert Path(published['dataset_manifest']).is_file()
    preview = work.preview_schematic(str(source), published['path'])
    assert preview['dataset_freshness'] == 'matches_source'
    compared = compare_results(work, [published['path']], 'out.Vb', 1e6)
    assert compared['warnings'] == []
    assert compared['rows'][0]['dataset_sha256'] == run['dataset_sha256']
    assert compared['rows'][0]['provenance']['verified']
    assert compared['rows'][0]['reference_impedance_ohm'] == 50


def test_legacy_run_manifest_remains_readable_and_publishable(circuit, monkeypatch):
    work, source, saved = circuit
    fake_solver(monkeypatch, work, hb_binary())
    run = work.run_schematic(str(source))
    info = json.loads(Path(run['manifest']).read_text())
    legacy = {key: info[key] for key in ('source', 'source_sha256', 'dataset_sha256', 'netlist_sha256')}
    Path(run['manifest']).write_text(json.dumps(legacy))
    Path(run['dataset_manifest']).unlink()
    read = work.read_results(run['dataset'])
    assert read['provenance']['verified'] and read['provenance']['legacy']
    published = work.publish_results(str(source), run['dataset'], saved['sha256'])
    assert published['provenance']['verified'] and not published['provenance']['legacy']


def test_source_edit_is_reported_by_comparison_and_blocks_publication(circuit, monkeypatch):
    work, source, _ = circuit
    fake_solver(monkeypatch, work, hb_binary())
    run = work.run_schematic(str(source))
    source.write_bytes(source.read_bytes() + b'\n')
    comparison = compare_results(work, [run['dataset']], 'out.Vb', 1e6)
    assert comparison['rows'][0]['provenance']['state'] == 'stale_or_modified'
    assert comparison['warnings'][0]['state'] == 'stale_or_modified'
    with pytest.raises(BridgeError, match='provenance'):
        work.publish_results(str(source), run['dataset'], _sha(source.read_bytes()))


@pytest.mark.parametrize(('tamper', 'state'), [
    ('bad_json', 'invalid_provenance'), ('hash', 'stale_or_modified'),
    ('path', 'invalid_provenance'), ('pending', 'incomplete'),
])
def test_sidecar_tampering_does_not_fall_back_to_successful_run_manifest(circuit, monkeypatch, tamper, state):
    work, source, saved = circuit
    fake_solver(monkeypatch, work, hb_binary())
    run = work.run_schematic(str(source))
    sidecar = Path(run['dataset_manifest'])
    info = json.loads(sidecar.read_text())
    if tamper == 'bad_json':
        sidecar.write_text('{')
    else:
        if tamper == 'hash':
            info['dataset_sha256'] = '0' * 64
        elif tamper == 'path':
            info['dataset'] = str(sidecar.parent / 'other.dat')
        else:
            info['status'] = 'publishing'
        sidecar.write_text(json.dumps(info))
    read = work.read_results(run['dataset'])
    assert read['provenance']['state'] == state
    assert not read['provenance']['verified']
    with pytest.raises(BridgeError, match='provenance'):
        work.publish_results(str(source), run['dataset'], saved['sha256'])
    assert work.preview_schematic(str(source), run['dataset'])['dataset_freshness'] == state


def test_interrupted_publication_leaves_explicit_untrusted_state(circuit, monkeypatch):
    work, source, saved = circuit
    fake_solver(monkeypatch, work, hb_binary())
    run = work.run_schematic(str(source))
    dest = source.with_suffix('.dat')
    sidecar = dataset_sidecar(dest)
    original_write = work.atomic_write
    def interrupted_write(path, data, expected_sha256=None):
        if Path(path) == sidecar and json.loads(data)['status'] == 'success':
            raise OSError('simulated interruption before provenance commit')
        return original_write(path, data, expected_sha256)
    monkeypatch.setattr(work, 'atomic_write', interrupted_write)
    with pytest.raises(BridgeError, match='Publication did not finish'):
        work.publish_results(str(source), run['dataset'], saved['sha256'])
    assert dest.read_bytes() == hb_binary()
    assert json.loads(sidecar.read_text())['status'] == 'publishing'
    # Even a matching successful legacy manifest cannot override the pending
    # dataset-specific sidecar.
    old = json.loads(Path(run['manifest']).read_text())
    old['dataset'] = str(dest)
    (dest.parent / 'manifest.json').write_text(json.dumps(old))
    assert work.read_results(str(dest))['provenance']['state'] == 'incomplete'
    assert work.preview_schematic(str(source), str(dest))['dataset_freshness'] == 'incomplete'
    with pytest.raises(BridgeError, match='untrusted'):
        compare_results(work, [str(dest)], 'out.Vb', 1e6)


def test_comparison_keeps_dc_distinct_and_prefers_exact_rf_sample(circuit):
    work, source, _ = circuit
    dataset = source.with_suffix('.dat')
    dataset.write_bytes(hb_binary(frequencies=(0, 1e9, 1e9 + 0.5), voltages=((1, 0), (1, 0), (2, 0))))
    result = compare_results(work, [str(dataset)], 'out.Vb', 0)
    assert result['rows'][0]['power_dbm'] == pytest.approx(13.0102999566)
    assert result['warnings'][0]['state'] == 'unverified'
    with pytest.raises(BridgeError, match='actual sample'):
        compare_results(work, [str(dataset)], 'out.Vb', 1e-7)
    result = compare_results(work, [str(dataset)], 'out.Vb', 1e9)
    assert result['rows'][0]['magnitude_v'] == 1
    assert result['rows'][0]['power_dbm'] == pytest.approx(10)
    assert math.isfinite(result['rows'][0]['power_dbm'])


def large_multitrace_binary(*, invalid_unselected=False):
    """1.1M total samples fit the binary limit; one selected trace fits the output budget."""
    count = 100000
    values = [(1.0, 0.0)] * count
    records = [('frequency', 'Hz', range(count), None)]
    for index in range(10):
        trace = values
        if invalid_unselected and index == 9:
            trace = values.copy()
            trace[-1] = (float('nan'), 0.0)
        records.append((f'out{index}.Vb', 'V', trace, 'frequency'))
    return binary_records(records)


def test_comparison_selects_one_trace_without_exceeding_response_budget(circuit):
    work, source, _ = circuit
    dataset = source.with_suffix('.dat')
    dataset.write_bytes(large_multitrace_binary())
    with pytest.raises(BridgeError, match='one million samples'):
        work.read_results(str(dataset), 100000)
    result = compare_results(work, [str(dataset)], 'out0.Vb', 99999.0)
    assert result['rows'][0]['frequency_hz'] == 99999.0
    assert result['rows'][0]['magnitude_v'] == 1.0
    assert result['rows'][0]['power_dbm'] == pytest.approx(10.0)


def test_comparison_still_rejects_late_nan_in_an_unselected_trace(circuit):
    work, source, _ = circuit
    dataset = source.with_suffix('.dat')
    dataset.write_bytes(large_multitrace_binary(invalid_unselected=True))
    with pytest.raises(BridgeError, match=r'Nonfinite dataset sample: out9\.Vb\[99999\]'):
        compare_results(work, [str(dataset)], 'out0.Vb', 1.0)


@pytest.mark.parametrize('banner,expected', [
    ('Qt version 6.8\nuSimmics simulator 5.9', '5.9'),
    ('versions 5.9 and 6.0', None),
])
def test_provenance_uses_the_same_version_parser_as_doctor(circuit, banner, expected):
    work, _, _ = circuit
    remember_simulator_banner(work.simulator, banner)
    assert simulator_metadata(work.simulator)['version'] == expected
