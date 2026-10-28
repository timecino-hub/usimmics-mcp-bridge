import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest
from usimmics_bridge.config import config_path,configure,doctor
from usimmics_bridge.core import Bridge, BridgeError


def test_config_without_personal_paths(tmp_path, monkeypatch):
    target=tmp_path/'settings/config.json'
    monkeypatch.setenv('USIMMICS_BRIDGE_CONFIG',str(target))
    assert config_path()==target
    assert doctor()['configured'] is False
    install=tmp_path/'program with spaces';(install/'bin').mkdir(parents=True)
    (install/'bin/simulator.exe').write_bytes(b'test fixture, not executed')
    project=tmp_path/'projects with spaces'
    configure(str(install),[str(project)])
    assert json.loads(target.read_text(encoding='utf-8'))['project_roots']==[str(project)]
    with pytest.raises(BridgeError):configure(str(install),[str(project)])
    configure(str(install),[str(project)],force=True)
    assert list(target.parent.glob('config.json.bak.*'))


def test_config_explicit_precedes_environment(tmp_path,monkeypatch):
    monkeypatch.setenv('USIMMICS_BRIDGE_CONFIG',str(tmp_path/'env.json'))
    assert config_path(tmp_path/'explicit.json')==tmp_path/'explicit.json'


@pytest.mark.parametrize('roots', ['ABSOLUTE_STRING', [], [''], ['relative'], [None], [1], {'path': '/tmp'}, None])
def test_invalid_roots_fail_before_becoming_an_access_policy(tmp_path, roots):
    if roots == 'ABSOLUTE_STRING':
        roots = str(tmp_path/'intended')
    cfg = tmp_path/'config.json'
    cfg.write_text(json.dumps({'installation_root': str(tmp_path), 'project_roots': roots}))
    with pytest.raises(BridgeError, match='Invalid bridge configuration'):
        Bridge(cfg)


@pytest.mark.parametrize('installation', ['', '.', None, 1])
def test_configuration_installation_must_be_absolute_string(tmp_path, installation):
    cfg = tmp_path/'config.json'
    cfg.write_text(json.dumps({'installation_root': installation, 'project_roots': [str(tmp_path)]}))
    with pytest.raises(BridgeError, match='Invalid bridge configuration'):
        Bridge(cfg)


def test_unknown_configuration_field_is_not_ignored(tmp_path):
    cfg = tmp_path/'config.json'
    cfg.write_text(json.dumps({'installation_root': str(tmp_path), 'project_roots': [str(tmp_path)], 'project_root': '/'}))
    with pytest.raises(BridgeError, match='project_root'):
        Bridge(cfg)


def test_configure_rejects_mistyped_root_collection_without_writing(tmp_path):
    install = tmp_path/'install'
    (install/'bin').mkdir(parents=True)
    (install/'bin/simulator.exe').write_bytes(b'fixture')
    cfg = tmp_path/'config.json'
    for roots in [str(tmp_path/'project'), [], ['']]:
        with pytest.raises(BridgeError):
            configure(str(install), roots, cfg)
    assert not cfg.exists()
    assert not (tmp_path/'project').exists()


def windows_doctor_fixture(tmp_path, monkeypatch, **status):
    """Only platform/version execution is mocked; filesystem readiness checks are real."""
    import usimmics_bridge.config as config_module
    root = tmp_path/'projects'
    root.mkdir()
    cfg = tmp_path/'config.json'
    cfg.write_text(json.dumps({'installation_root': str(tmp_path/'install'), 'project_roots': [str(root)]}))
    monkeypatch.setattr(config_module, 'os', SimpleNamespace(name='nt', access=os.access, W_OK=os.W_OK, X_OK=os.X_OK))
    report = {'simulator_exists': True, 'simulator_banner': 'uSimmics simulator 5.9',
              'simulator_returncode': 0, 'bat54_available': False, **status}
    monkeypatch.setattr('usimmics_bridge.workspace.Workbench.environment_status', lambda self: report)
    return cfg, root


def test_doctor_separates_optional_bat54_from_base_readiness(tmp_path, monkeypatch):
    cfg, root = windows_doctor_fixture(tmp_path, monkeypatch)
    report = doctor(cfg)
    assert report['ready'] and report['workspace_ready'] and report['solver_ready']
    assert report['bat54_ready'] is False
    assert report['checks']['bat54']['required_for_base_workflow'] is False
    assert report['checks']['workspace']['roots'][0]['path'] == str(root)
    assert not list(root.iterdir())  # doctor checks permissions without writing probe files.


def test_doctor_missing_or_unwritable_project_root_is_not_ready(tmp_path, monkeypatch):
    import usimmics_bridge.config as config_module
    cfg, root = windows_doctor_fixture(tmp_path, monkeypatch)
    root.rmdir()
    report = doctor(cfg)
    assert report['ready'] is False and report['workspace_ready'] is False
    assert report['checks']['workspace']['roots'][0]['exists'] is False
    root.mkdir()
    monkeypatch.setattr(config_module.os, 'access', lambda *args: False)
    report = doctor(cfg)
    assert report['ready'] is False and report['checks']['workspace']['roots'][0]['writable'] is False


@pytest.mark.parametrize('banner,exit_code,version,ready', [
    ('uSimmics simulator 5.9', 0, '5.9', True),
    ('QucsStudio version 5.9\nQt version 6.8', 0, '5.9', True),
    ('uSimmics simulator 15.9', 0, '15.9', False),
    ('uSimmics simulator 5.90', 0, '5.90', False),
    ('uSimmics simulator 5.9oops', 0, None, False),
    ('uSimmics simulator 5.9', 2, '5.9', False),
    ('unknown', 0, None, False),
    ('versions 5.9 and 6.0', 0, None, False),
])
def test_doctor_requires_exact_supported_version_and_successful_version_exit(tmp_path, monkeypatch, banner, exit_code, version, ready):
    cfg, _ = windows_doctor_fixture(tmp_path, monkeypatch, simulator_banner=banner, simulator_returncode=exit_code)
    report = doctor(cfg)
    assert report['ready'] is ready
    assert report['checks']['solver']['version'] == version
    assert report['checks']['solver']['version_exit_code'] == exit_code


def test_environment_status_preserves_version_exit_code(tmp_path, monkeypatch):
    (tmp_path/'bin').mkdir()
    (tmp_path/'bin/simulator.exe').write_bytes(b'not executed')
    cfg = tmp_path/'config.json'
    cfg.write_text(json.dumps({'installation_root': str(tmp_path), 'project_roots': [str(tmp_path)]}))
    monkeypatch.setattr('usimmics_bridge.core.subprocess.run', lambda args, **kwargs: subprocess.CompletedProcess(args, 7, b'simulator 5.9', b''))
    result = Bridge(cfg).environment_status()
    assert result['simulator_returncode'] == 7
    assert result['simulator_banner'] == 'simulator 5.9'
