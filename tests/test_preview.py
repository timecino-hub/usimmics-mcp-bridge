import math
from PIL import Image
import pytest
from usimmics_bridge.core import BridgeError
from usimmics_bridge.numeric import voltage_power_dbm
from usimmics_bridge.preview import junction_points,render
from test_core import circuit


def test_voltage_power_dc_ac_zero_and_extremes():
    assert voltage_power_dbm(1,0)==pytest.approx(13.0102999566)
    assert voltage_power_dbm(1,1e6)==pytest.approx(10)
    assert voltage_power_dbm(0,0) is None
    assert math.isfinite(voltage_power_dbm(1e300,1e6))
    assert math.isfinite(voltage_power_dbm(1e-300,1e6))
    for args in [(None,0),(float('inf'),0),(1,-1),(1,0,0),(-1,1)]:
        with pytest.raises(BridgeError):voltage_power_dbm(*args)


def test_tjunction_marked_bare_crossing_not_marked():
    assert junction_points([], [((0,0),(100,0),''),((50,0),(50,30),'')])==[(50,0)]
    assert junction_points([], [((0,0),(100,0),''),((50,-30),(50,30),'')])==[]
    assert junction_points([], [((0,0),(50,0),''),((50,0),(100,0),'')])==[]


def test_render_tjunction_and_dc_spectrum(tmp_path):
    text=circuit().replace('0 -30 70 -30','0 -30 70 -30\n35 -30 35 -60 "branch" 35 -70 0 ""')
    data={'variables':[{'name':'frequency','type':'real','unit':'Hz','dependencies':[],'data':[0,1e6]},
        {'name':'out.Vb','type':'complex','unit':'V','dependencies':['frequency'],
         'data':[{'magnitude':1.0},{'magnitude':1.0}]}]}
    path=tmp_path/'preview.png';meta=render(text,path,'Independent fixture',data)
    assert [35,-30] in meta['junction_points']
    assert meta['spectrum_traces']==['out.Vb']
    with Image.open(path) as image:
        image.verify()
