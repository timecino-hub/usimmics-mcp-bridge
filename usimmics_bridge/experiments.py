"""Bounded independent parameter runs and exact frequency comparisons."""
import csv
import io
import json
import math
from pathlib import Path
from uuid import uuid4
from .core import BridgeError
from .numeric import voltage_power_dbm


def compare_results(workbench, dataset_paths, variable, frequency_hz, impedance=50):
    if not 1<=len(dataset_paths)<=20: raise BridgeError('Compare 1..20 datasets')
    if (isinstance(frequency_hz, bool) or not isinstance(frequency_hz, (int, float))
            or isinstance(impedance, bool) or not isinstance(impedance, (int, float))
            or not math.isfinite(frequency_hz) or frequency_hz < 0
            or not math.isfinite(impedance) or impedance <= 0):
        raise BridgeError('Frequency must be nonnegative and impedance positive')
    rows=[]
    warnings=[]
    for path in dataset_paths:
        selected_reader = getattr(workbench, '_read_results_data', None)
        if callable(selected_reader):
            # A single-trace measurement must not materialize every unrelated
            # spectrum. The decoder still validates every sample in the file.
            _, data = selected_reader(path, 100000, selected_names={'frequency', variable})
        else:
            # Retain the small duck-typed reader interface used by consumers.
            data = workbench.read_results(path, 100000)
        if data.get('truncated'):
            raise BridgeError('Dataset is too large for comparison (100000 points per variable)')
        provenance = data.get('provenance', {'state': 'unverified', 'verified': False,
                                           'reason': 'Dataset provenance is not recorded'})
        if provenance['state'] in ('incomplete', 'failed', 'invalid_provenance'):
            raise BridgeError('Cannot compare untrusted dataset: ' + provenance['reason'])
        if not provenance['verified']:
            warnings.append({'dataset': path, 'state': provenance['state'], 'message': provenance['reason']})
        vals={v['name']:v for v in data['variables']}
        if 'frequency' not in vals or variable not in vals: raise BridgeError('Missing frequency axis or requested variable')
        freq=vals['frequency']['data'];v=vals[variable]
        if v['type']!='complex' or v['unit']!='V' or v['dependencies']!=['frequency'] or len(freq)!=len(v['data']):
            raise BridgeError('Comparison requires a complex voltage frequency spectrum')
        # Prefer an actual exact match before tolerance, so adjacent RF lines
        # do not hide an exact requested sample. AC and DC are never conflated.
        matches = [i for i, f in enumerate(freq) if f == frequency_hz]
        if not matches:
            matches = [i for i, f in enumerate(freq) if f is not None
                       and (f == 0) == (frequency_hz == 0)
                       and math.isclose(f, frequency_hz, rel_tol=1e-9, abs_tol=1e-6)]
        if len(matches)!=1: raise BridgeError('Requested frequency must match one actual sample; no interpolation')
        mag=v['data'][matches[0]]['magnitude']
        if mag is None:raise BridgeError('Sample magnitude is not finite')
        # DC voltage is RMS already; AC HB amplitudes are peak values.
        actual_frequency = freq[matches[0]]
        rows.append({'dataset':path,'frequency_hz':actual_frequency,'magnitude_v':mag,
                     'power_dbm':voltage_power_dbm(mag, actual_frequency, impedance),
                     'reference_impedance_ohm': impedance,
                     'dataset_sha256': data.get('dataset_sha256'), 'provenance': provenance})
    return {'variable':variable,'impedance_ohm':impedance,'rows':rows,'interpolated':False,
            'warnings': warnings}


def parameter_sweep(workbench,path,component,parameter,values,variable='IF_out.Vb',frequency_hz=1e6,timeout=30):
    if not isinstance(values,list) or not 1<=len(values)<=20 or any(not isinstance(v,str) or not v for v in values):
        raise BridgeError('Supply 1..20 nonempty parameter strings')
    if type(timeout) is not int or not 1<=timeout<=30: raise BridgeError('Per-run timeout must be 1..30 seconds (maximum total solver time 600 seconds)')
    original=workbench.read_schematic(path)
    folder=workbench.target(str(Path(original['path']).parent/'.usimmics-sweeps'))
    folder.mkdir(exist_ok=True)
    folder=folder/uuid4().hex;folder.mkdir()
    rows=[]
    for index,value in enumerate(values):
        variant=folder/f'point_{index:03d}.sch'
        saved=workbench.save_schematic(str(variant),original['content'])
        workbench.update_parameters(str(variant),[{'component':component,'parameter':parameter,'value':value}],saved['sha256'])
        run=workbench.run_schematic(str(variant),timeout)
        result=compare_results(workbench,[run['dataset']],variable,frequency_hz)
        rows.append({'index':index,'value':value,**result['rows'][0],'source':str(variant)})
    report={'source':original['path'],'source_sha256':original['sha256'],'component':component,'parameter':parameter,
            'variable':variable,'rows':rows,'source_unchanged':workbench.read_schematic(path)['sha256']==original['sha256']}
    if not report['source_unchanged']:raise BridgeError('Source was changed externally during sweep')
    json_path=folder/'summary.json';csv_path=folder/'summary.csv'
    workbench.atomic_write(json_path,(json.dumps(report,indent=2,allow_nan=False)+'\n').encode())
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    workbench.atomic_write(csv_path,stream.getvalue().encode('utf-8'))
    return {**report,'json_path':str(json_path),'csv_path':str(csv_path)}
