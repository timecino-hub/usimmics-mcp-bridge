"""Structured, explicitly routed authoring for the supported 5.9 subset."""
import re
from .core import BridgeError, IDENT, NAMES, PROP
from .component_specs import COMPONENT_SPECS, DEFAULTS
from .schemas import normalize_build


def build_schematic(workbench, path, components, connections, harmonics=None, expected_sha256=None):
    """Save a structured circuit. Explicit routes must not cross unrelated nets."""
    try:
        components, connections, harmonics = normalize_build(components, connections, harmonics)
    except ValueError as exc:
        raise BridgeError(f'Invalid circuit description: {exc}') from exc
    items={};records=[];pins={};freqs=[]
    for c in components:
        kind=c['type'];name=c['name'];pos=c['position'];rot=c.get('orientation',0)
        if not isinstance(name,str) or not IDENT.fullmatch(name) or name in items or name=='HB1':
            raise BridgeError('Invalid or duplicate component name')
        if len(pos)!=2 or any(type(n) is not int or abs(n)>4000 for n in pos) or type(rot) is not int or rot not in range(4):
            raise BridgeError('Positions must be integer pairs; orientations are 0..3')
        if kind not in (*DEFAULTS,'Diode','GND'): raise BridgeError('Unsupported builder component: '+kind)
        props=list(c.get('properties',DEFAULTS.get(kind,[])))
        if kind=='Diode' and c.get('model')=='BAT54':
            library=workbench.installation/'library/B_Schottky.lib'
            if not library.is_file(): raise BridgeError('BAT54 library is unavailable')
            match=re.search(r'<Component BAT54>.*?<Model>\s*(.*?)\s*</Model>',library.read_text(encoding='utf-8',errors='replace'),re.S)
            if not match: raise BridgeError('BAT54 model not found')
            props=[m[1] for m in PROP.finditer(match[1])]
            # Native 5.9 schematic model-display identifier is separate from the model values.
            if len(props)==31: props.append('BAT54')
        if kind=='Diode' and not props: raise BridgeError('Diode needs model=BAT54 or explicit properties')
        for key,value in c.get('params',{}).items():
            try: idx=NAMES.get(kind,[]).index(key)
            except ValueError as exc: raise BridgeError('Unknown parameter: '+key) from exc
            if idx>=len(props): raise BridgeError('Parameter exceeds property list')
            props[idx]=value
        try:
            COMPONENT_SPECS[kind].validate_properties(kind, props)
        except ValueError as exc:
            raise BridgeError(str(exc)) from exc
        x,y=pos
        for i, pin in enumerate(COMPONENT_SPECS[kind].pins(x, y, rot)):
            pins[(name,i)] = pin
        items[name]=c
        if kind=='Vac': freqs.append(props[1])
        visible={'Vac':{0,1},'Filter':{0,1,5},'Diode':set()}.get(kind,{0})
        fields=''.join('"'+v+'"'+('1' if i in visible else '0') for i,v in enumerate(props))
        default_label=(-26,16) if kind in ('R','C','L') and rot%2==0 else ((-30,-85) if kind=='Filter' else (20,-26))
        label=c.get('label_offset',default_label)
        if len(label)!=2 or any(type(n) is not int or abs(n)>4000 for n in label):
            raise BridgeError('Label offset must be an integer pair')
        tx,ty=label
        records.append(f'{kind} {"*" if kind=="GND" else name} 1 {x} {y} {tx} {ty} {rot}'+(' '+fields if fields else ''))
    order=harmonics['order'];count=harmonics['count']
    if not freqs: raise BridgeError('Builder requires at least one Vac source for HB')
    spec=str(order)+'@;'+''.join(str(count)+'@'+f+';' for f in dict.fromkeys(freqs))
    hb_y=max(c['position'][1] for c in components)+100
    records.append(f'.HB HB1 1 100 {hb_y} 0 9 0 '+''.join('"'+v+'"0' for v in [spec,'no','1 GHz','lin','1 kHz','10 kHz','10','0.001','0.1 uA','500']))
    parent={key:key for key in pins}
    def find(k):
        while parent[k]!=k:k=parent[k]
        return k
    def endpoint(d):
        if type(d.get('pin')) is not int: raise BridgeError('Pin must be an integer index')
        key=(d['component'],d['pin'])
        if key not in pins: raise BridgeError('Unknown connection pin: '+str(key))
        return key
    grounds=[(name,0) for name,c in items.items() if c['type']=='GND']
    for key in grounds[1:]: parent[find(key)]=find(grounds[0])
    routes=[]
    for conn in connections:
        a=endpoint(conn['from']);b=endpoint(conn['to']);parent[find(a)]=find(b)
        via=conn.get('via',[])
        if any(len(p)!=2 or any(type(n) is not int or abs(n)>4000 for n in p) for p in via):raise BridgeError('Invalid waypoint')
        route=[pins[a],*[tuple(p) for p in via],pins[b]]
        for p,q in zip(route,route[1:]):
            if p==q or p[0]!=q[0] and p[1]!=q[1]: raise BridgeError('Supply nonzero orthogonal waypoints')
        label=conn.get('label','')
        if label and not IDENT.fullmatch(label): raise BridgeError('Invalid net label')
        routes.append((a,route,label))
    def intersects(a,b,c,d):
        return max(min(a[0],b[0]),min(c[0],d[0]))<=min(max(a[0],b[0]),max(c[0],d[0])) and max(min(a[1],b[1]),min(c[1],d[1]))<=min(max(a[1],b[1]),max(c[1],d[1]))
    segments=[]
    for key,route,label in routes:
        for a,b in zip(route,route[1:]):
            for other,c,d in segments:
                if find(key)!=find(other) and intersects(a,b,c,d): raise BridgeError('Unrelated routes cross; change waypoints')
            for other,p in pins.items():
                if find(key)!=find(other) and intersects(a,b,p,p): raise BridgeError('Route touches an unrelated component pin')
            segments.append((key,a,b))
    wires=[]
    for key,route,label in routes:
        for i,(a,b) in enumerate(zip(route,route[1:])):
            tail=f' "{label}" {a[0]+10} {a[1]-25} 0 ""' if label and i==0 else ''
            wires.append(f'{a[0]} {a[1]} {b[0]} {b[1]}'+tail)
    text='\n'.join(['<QucsStudio Schematic 5.9>','<Properties>','View=0,0,1600,1000,1,0,0','Grid=10,10,1','DataSet=*.dat','DataDisplay=*.dpl','OpenDisplay=0','showFrame=0','</Properties>','<Symbol>','</Symbol>','<Components>',*records,'</Components>','<Wires>',*wires,'</Wires>','<Diagrams>','</Diagrams>','<Paintings>','</Paintings>'])+'\n'
    return workbench.save_schematic(path,text,expected_sha256)
