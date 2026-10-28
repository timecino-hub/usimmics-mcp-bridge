import json
from pathlib import Path
import pytest
from usimmics_bridge.config import config_path,configure,doctor
from usimmics_bridge.core import BridgeError


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
