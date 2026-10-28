import math
from PIL import Image
import pytest
from usimmics_bridge.core import BridgeError
from usimmics_bridge.numeric import voltage_power_dbm
from usimmics_bridge.preview import junction_points,render,mixing_products,_hz
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


def test_tone_parsing_and_mixing_product_names():
    assert (_hz('11 MHz'),_hz('2.4 GHz'),_hz('1e6'),_hz('fRF'))==(11e6,2.4e9,1e6,None)
    hb={'order':3,'tones':[{'hz':11e6,'count':4},{'hz':10e6,'count':4}]}
    assert mixing_products(hb,[0,1e6,9e6,10e6,11e6,21e6,33e6])=={
        1e6:'f1−f2',9e6:'2f2−f1',10e6:'f2',11e6:'f1',21e6:'f1+f2',33e6:'3f1'}
    assert mixing_products({'order':3,'tones':[{'hz':None,'count':4}]},[1e6])=={}


def test_spectrum_panels_markers_and_data_note(tmp_path):
    data={'variables':[{'name':'frequency','type':'real','unit':'Hz','dependencies':[],'data':[0,1e7,2e7,3e7]},
        {'name':'b.Vb','type':'complex','unit':'V','dependencies':['frequency'],
         'data':[{'magnitude':0},{'magnitude':10.0},{'magnitude':1e-9},{'magnitude':0.1}]},
        {'name':'a.Vb','type':'complex','unit':'V','dependencies':['frequency'],'data':[{'magnitude':0}]*4}]}
    plain=render(circuit(),tmp_path/'a.png','Spectrum',data)
    noted=render(circuit(),tmp_path/'b.png','Spectrum',data,'dataset does not match')
    # One panel per voltage in name order; +30 dBm is labelled, -170 dBm stays under the floor.
    assert plain['spectrum_traces']==['a.Vb','b.Vb']
    assert [(m['trace'],m['frequency_hz'],m['power_dbm'],m['product']) for m in plain['spectrum_markers']]==[
        ('b.Vb',1e7,30.0,'f1'),('b.Vb',3e7,-10.0,'3f1')]
    assert noted['height']==plain['height']+18


def test_labels_leave_symbols_and_unresolvable_overlap_warns(tmp_path):
    # R1's saved label offset (0, 0) covers its own body, so the preview moves it clear.
    meta=render(circuit(),tmp_path/'a.png','混频器.sch')
    assert 'R1' in meta['adjusted_labels'] and meta['warnings']==[]
    crowded=circuit().replace('Vac V2 1 200 0 18 -26 0','Vac V2 1 200 0 -182 -26 0')
    assert 'Label overlap: V2 / V1' in render(crowded,tmp_path/'b.png','Crowded')['warnings']
