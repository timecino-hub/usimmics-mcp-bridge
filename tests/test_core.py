import json
from pathlib import Path
import pytest
from usimmics_bridge.core import Bridge, BridgeError, _compile, _parse, _sha


def circuit():
    comps='''Vac V1 1 0 0 18 -26 0 "1 V"1"10 MHz"1"0"0"0"0"con_2"0
Vac V2 1 200 0 18 -26 0 "1 V"1"10 MHz"1"0"0"0"0"con_2"0
R R1 1 100 -30 0 0 0 "50 Ohm"1"26.85"0"0"0"0"0"26.85"0"european"0
GND * 1 0 30 0 0 0
GND * 1 200 30 0 0 0
.HB HB1 1 0 100 0 0 0 "3@;4@10 MHz;"0"no"0"1 GHz"0"lin"0"1 kHz"0"10 kHz"0"10"0"0.001"0"0.1 uA"0"500"0'''
    return '<QucsStudio Schematic 5.9>\n<Properties>\nDataSet=*.dat\n</Properties>\n<Symbol>\n</Symbol>\n<Components>\n'+comps+'\n</Components>\n<Wires>\n0 -30 70 -30\n130 -30 200 -30\n</Wires>\n<Diagrams>\n</Diagrams>\n<Paintings>\n</Paintings>\n'


def test_frequency_split_and_merge(tmp_path):
    config=tmp_path/'config.json';config.write_text(json.dumps({'installation_root':str(tmp_path),'project_roots':[str(tmp_path)]}))
    b=Bridge(config);p=tmp_path/'test.sch';p.write_text(circuit())
    first=b.update_parameters(str(p),[{'component':'V1','parameter':'frequency','value':'12 MHz'}],_sha(p.read_bytes()))
    hb=next(c for c in _parse(p.read_text())[1] if c['type']=='.HB')['properties'][0]
    assert '4@10 MHz;' in hb and '4@12 MHz;' in hb
    assert Path(first['backup']).read_text()==circuit()
    b.update_parameters(str(p),[{'component':'V2','parameter':'frequency','value':'12 MHz'}],first['sha256'])
    hb=next(c for c in _parse(p.read_text())[1] if c['type']=='.HB')['properties'][0]
    assert hb=='3@;4@12 MHz;'


@pytest.mark.parametrize('text',[circuit().replace('"con_2"0',''),circuit().replace('3@;4@10 MHz;','garbage'),circuit()+'unexpected\n',circuit().replace('0 -30 70 -30','0 -30 70 -20')])
def test_malformed_rejected(text):
    with pytest.raises(BridgeError): _compile(text)


def test_rotation_and_ground_order():
    # Rotate a horizontal resistor into a vertical load: first pin is bottom.
    text=circuit().replace('R R1 1 100 -30 0 0 0','R R1 1 100 -30 0 0 1').replace('0 -30 70 -30','0 -30 0 0\n0 0 100 0').replace('130 -30 200 -30','100 -60 200 -60\n200 -60 200 -30')
    net=_compile(text)
    assert 'R:R1 _bridge_net0 _bridge_net1' in net


def test_crossing_without_endpoint_remains_separate():
    # A vertical extension from the right terminal crosses the left terminal's
    # horizontal extension strictly inside both segments, without a junction endpoint.
    net=_compile(circuit().replace('0 -30 70 -30','0 -30 0 -80\n0 -80 150 -80\n150 -80 150 -50\n70 -50 150 -50\n70 -50 70 -30').replace('130 -30 200 -30','130 -30 130 -100\n130 -100 200 -100\n200 -100 200 -30'))
    r=next(x for x in net.splitlines() if x.startswith('R:R1')).split()
    assert r[1]!=r[2]


def test_t_junction_endpoint_connects():
    text=circuit().replace('0 -30 70 -30','0 -30 70 -30\n35 -30 35 -60 "branch" 35 -70 0 ""')
    net=_compile(text)
    assert 'Vac:V1 branch gnd' in net
    assert 'R:R1 branch ' in net


RECT='<Rect 180 740 600 220 31 #c0c0c0 1 00 1 0 5e6 35e6 1 -100 20 20 1 -0.1 0.5 1.1 -1 -1 -1 "Frequency (Hz)" "Power (dBm)" "">'


def test_verified_rect_records():
    text=circuit().replace('<Diagrams>','<Diagrams>\n'+RECT+'\n<Legend 10 -100 0>\n<"dBm(out.Vb)" "" #0000ff 2 3 0 0 0 0 "">\n</Rect>')
    _parse(text)


@pytest.mark.parametrize('record',['NOT_A_VALID_NATIVE_RECORD','<Rect 0 0 ...>','<Legend x y z>','<"out.Vb" "" #bogus 2 3 0 0 0 0 "">'])
def test_invalid_rect_records_rejected(record):
    text=circuit().replace('<Diagrams>','<Diagrams>\n'+RECT+'\n'+record+'\n</Rect>')
    with pytest.raises(BridgeError): _parse(text)


def test_vdc_native_three_fields():
    text=circuit().replace('Vac V1 1 0 0 18 -26 0 "1 V"1"10 MHz"1"0"0"0"0"con_2"0', 'Vdc V1 1 0 0 18 -26 0 "1 V"1"battery"0"SIL-2"0')
    _compile(text)
