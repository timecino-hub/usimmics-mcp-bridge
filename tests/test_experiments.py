import json
from pathlib import Path
import pytest
from usimmics_bridge.experiments import compare_results,parameter_sweep
from usimmics_bridge.core import BridgeError
from test_builder import fixture,design
from usimmics_bridge.builder import build_schematic


class DatasetStub:
    def read_results(self,path,max_points):
        return {'variables':[{'name':'frequency','data':[0,1e6]},
            {'name':'out.Vb','type':'complex','unit':'V','dependencies':['frequency'],
             'data':[{'magnitude':1.0},{'magnitude':1.0}]}]}


def test_compare_exact_and_dc():
    b=DatasetStub()
    assert compare_results(b,['fake'],'out.Vb',1e6)['rows'][0]['power_dbm']==pytest.approx(10)
    assert compare_results(b,['fake'],'out.Vb',0)['rows'][0]['power_dbm']==pytest.approx(13.0103)
    with pytest.raises(BridgeError,match='actual sample'):
        compare_results(b,['fake'],'out.Vb',2e6)


def test_isolated_sweep(tmp_path,monkeypatch):
    b=fixture(tmp_path);comps,routes=design();source=tmp_path/'mixer.sch'
    build_schematic(b,str(source),comps,routes);before=source.read_bytes()
    from types import SimpleNamespace
    from test_results_integrity import hb_binary
    b.simulator.parent.mkdir(parents=True)
    b.simulator.write_bytes(b'placeholder for the controlled solver hook')
    def execute(netpath, dataset, log, timeout):
        dataset.write_bytes(hb_binary())
        log.write_text('Synthetic sweep fixture')
        return SimpleNamespace(returncode=0, output_tail='')
    monkeypatch.setattr(b, '_execute_solver', execute)
    out=parameter_sweep(b,str(source),'V1','frequency',['1 MHz','2 MHz'],variable='out.Vb')
    assert len(out['rows'])==2 and out['source_unchanged']
    assert source.read_bytes()==before and Path(out['csv_path']).exists()
    assert '2 MHz' in Path(out['rows'][1]['source']).read_text()
    with pytest.raises(BridgeError): parameter_sweep(b,str(source),'V1','frequency',['1 MHz']*21)
