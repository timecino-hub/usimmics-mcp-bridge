import json
from pathlib import Path
import pytest
from usimmics_bridge.builder import build_schematic
from usimmics_bridge.workspace import Workbench
from usimmics_bridge.core import BridgeError


def fixture(tmp_path):
    cfg=tmp_path/'config.json';cfg.write_text(json.dumps({'installation_root':str(tmp_path/'missing'),'project_roots':[str(tmp_path)]}))
    return Workbench(cfg)


def design():
    comps=[{'type':'Vac','name':'V1','position':[100,130]},
           {'type':'R','name':'R1','position':[250,100]},
           {'type':'R','name':'R2','position':[400,130],'orientation':3},
           {'type':'GND','name':'G1','position':[100,250]},
           {'type':'GND','name':'G2','position':[400,250]}]
    def conn(a,i,b,j,**kwargs):return {'from':{'component':a,'pin':i},'to':{'component':b,'pin':j},**kwargs}
    routes=[conn('V1',0,'R1',0),conn('R1',1,'R2',0,label='out'),conn('V1',1,'G1',0),conn('R2',1,'G2',0)]
    return comps,routes


def test_build_and_reject_invalid_route(tmp_path):
    work=fixture(tmp_path);comps,routes=design();target=tmp_path/'new.sch'
    result=build_schematic(work,str(target),comps,routes)
    assert result['saved'] and work.validate_schematic(str(target))['valid']
    assert '4@1 MHz;' in target.read_text()
    routes[0]['via']=[[200,170]]
    with pytest.raises(BridgeError,match='orthogonal'):
        build_schematic(work,str(tmp_path/'bad.sch'),comps,routes)
    assert not (tmp_path/'bad.sch').exists()


def test_reject_unknown_pin(tmp_path):
    work=fixture(tmp_path);comps,routes=design();routes[0]['from']['pin']=9
    with pytest.raises(BridgeError,match='Unknown connection pin'):
        build_schematic(work,str(tmp_path/'bad.sch'),comps,routes)
