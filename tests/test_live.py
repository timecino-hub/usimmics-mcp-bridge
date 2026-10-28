"""Opt-in native solver checks; fixtures are authored here, not copied from a user's lab."""
import json
import math
from pathlib import Path
import pytest
from usimmics_bridge.config import discover_installation
from usimmics_bridge.workspace import Workbench
from usimmics_bridge.builder import build_schematic
from usimmics_bridge.experiments import compare_results, parameter_sweep
from test_builder import design

pytestmark=pytest.mark.integration


@pytest.fixture
def live(tmp_path):
    install=discover_installation()
    if install is None: pytest.skip('uSimmics 5.9 is not installed')
    cfg=tmp_path/'config.json'
    cfg.write_text(json.dumps({'installation_root':str(install),'project_roots':[str(tmp_path)]}))
    return Workbench(cfg)


def solve(work,path,components,routes):
    build_schematic(work,str(path),components,routes)
    return work.run_schematic(str(path))['dataset']


def test_native_divider_and_sweep(live,tmp_path):
    comps,routes=design()
    path=tmp_path/'divider.sch'
    dataset=solve(live,path,comps,routes)
    result=compare_results(live,[dataset],'out.Vb',1e6)
    assert result['rows'][0]['magnitude_v']==pytest.approx(0.5,rel=1e-6)
    result=parameter_sweep(live,str(path),'R1','resistance',['50 Ohm','100 Ohm'],variable='out.Vb')
    assert [r['magnitude_v'] for r in result['rows']]==pytest.approx([0.5,1/3],rel=1e-6)


@pytest.mark.parametrize('kind,value,expected',[
    ('C','1 nF',1/math.sqrt(1+(2*math.pi*1e6*1000*1e-9)**2)),
    ('L','1 mH',(2*math.pi*1e6*1e-3)/math.sqrt(1000**2+(2*math.pi*1e6*1e-3)**2)),
])
def test_native_reactive_defaults(live,tmp_path,kind,value,expected):
    comps,routes=design();comps[1]['params']={'resistance':'1 kOhm'}
    comps[2]['type']=kind;comps[2]['params']={'capacitance' if kind=='C' else 'inductance':value}
    dataset=solve(live,tmp_path/(kind+'.sch'),comps,routes)
    actual=compare_results(live,[dataset],'out.Vb',1e6)['rows'][0]['magnitude_v']
    assert actual==pytest.approx(expected,rel=1e-5)


def test_native_vdc_default(live,tmp_path):
    comps,routes=design()
    comps[3]['position']=[100,300]
    comps.append({'type':'Vdc','name':'DC1','position':[100,200],'params':{'voltage':'1 V'}})
    routes[2]['to']={'component':'DC1','pin':0}
    routes.append({'from':{'component':'DC1','pin':1},'to':{'component':'G1','pin':0}})
    dataset=solve(live,tmp_path/'dc.sch',comps,routes)
    # HB's native result omits this linear circuit's DC operating point. Verify
    # that the Vdc schema parses and the AC transfer remains the expected half.
    assert compare_results(live,[dataset],'out.Vb',1e6)['rows'][0]['magnitude_v']==pytest.approx(0.5,rel=1e-6)


def test_native_bat54_mixer(live,tmp_path):
    example=Path(__file__).parents[1]/'examples/mixer.json'
    spec=json.loads(example.read_text())
    dataset=solve(live,tmp_path/'mixer.sch',spec['components'],spec['connections'])
    voltage=compare_results(live,[dataset],'IF_out.Vb',1e6)['rows'][0]['magnitude_v']
    assert voltage==pytest.approx(0.06129164,rel=1e-4)
