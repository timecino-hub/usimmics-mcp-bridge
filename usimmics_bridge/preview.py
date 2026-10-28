"""Deterministic saved-file circuit preview and discrete HB spectrum, using Pillow."""
import math
from pathlib import Path
import re
from PIL import Image, ImageDraw, ImageFont

from .core import _parse, NAMES, BridgeError
from .numeric import voltage_power_dbm


def junction_points(comps, wires):
    """Mark actual branch candidates; bare midsegment crossings stay disconnected."""
    candidates={p for a,b,_ in wires for p in (a,b)}
    pin_rays={}
    def direction(a,b):
        return ((b[0]>a[0])-(b[0]<a[0]),(b[1]>a[1])-(b[1]<a[1]))
    for c in comps:
        if not c['active'] or c['type']=='.HB':continue
        x,y=c['x'],c['y']
        offsets=[(0,0)] if c['type']=='GND' else ([(0,-30),(0,30)] if c['type'] in ('Vac','Vdc') else [(-30,0),(30,0)])
        for dx,dy in offsets:
            for _ in range(c['orientation']):dx,dy=dy,-dx
            p=(x+dx,y+dy);candidates.add(p)
            ray=(0,1) if c['type']=='GND' else direction(p,(x,y))
            pin_rays.setdefault(p,set()).add(ray)
    result=[]
    for p in candidates:
        rays=set(pin_rays.get(p,set()))
        for a,b,_ in wires:
            if min(a[0],b[0])<=p[0]<=max(a[0],b[0]) and min(a[1],b[1])<=p[1]<=max(a[1],b[1]):
                for q in (a,b):
                    if q!=p:rays.add(direction(p,q))
        if len(rays)>=3:result.append(p)
    return sorted(result)


def render(content, output, title, dataset=None):
    lines, comps, wires = _parse(content)
    font_path = Path('C:/Windows/Fonts/segoeui.ttf')
    font = ImageFont.truetype(str(font_path), 15) if font_path.exists() else ImageFont.load_default(size=15)
    small = ImageFont.truetype(str(font_path), 12) if font_path.exists() else ImageFont.load_default(size=12)
    big = ImageFont.truetype(str(font_path), 20) if font_path.exists() else ImageFont.load_default(size=20)
    probe = ImageDraw.Draw(Image.new('RGB',(1,1)))
    labels, points = [], []
    for c in comps:
        x,y=c['x'],c['y']
        points.extend([(x-35,y-35),(x+35,y+35)])
        if c['type']=='GND': continue
        if c['type']=='.HB':
            points.extend([(x,y-30),(x+190,y+30)])
            labels.append((x,y+9,c['name']))
            continue
        fields=c['prefix'].split()
        tx,ty=int(fields[5]),int(fields[6])
        text=[c['name']]
        short={'resistance':'R','frequency':'f','amplitude':'V','order':'N','bandwidth':'BW',
               'capacitance':'C','inductance':'L','voltage':'V'}
        names=NAMES.get(c['type'],[])
        for i,(value,flag) in enumerate(zip(c['properties'],c['flags'])):
            if flag=='1':
                name=names[i] if i<len(names) else 'p'+str(i)
                text.append(short.get(name,name)+'='+value)
        labels.append((x+tx,y+ty,'\n'.join(text)))
    for a,b,_ in wires: points.extend([a,b])
    for line in lines[lines.index('<Wires>')+1:lines.index('</Wires>')]:
        m=re.match(r'(-?\d+) (-?\d+) (-?\d+) (-?\d+) "([^"\n]+)" (-?\d+) (-?\d+)',line)
        if m: labels.append((int(m[6]),int(m[7]),m[5]))
    boxes=[]
    for x,y,text in labels:
        box=probe.multiline_textbbox((x,y),text,font=font,spacing=3)
        boxes.append(box);points.extend([(box[0],box[1]),(box[2],box[3])])
    minx=min(x for x,y in points)-30; miny=min(y for x,y in points)-20
    maxx=max(x for x,y in points)+30; maxy=max(y for x,y in points)+25
    if maxx-minx>5000 or maxy-miny>5000:
        raise BridgeError('Layout too large for a readable preview; move components closer')
    width=max(1000,math.ceil(maxx-minx)); circuit_h=math.ceil(maxy-miny)
    height=circuit_h+110+(360 if dataset else 0)
    im=Image.new('RGB',(width,height),'#f7f9fc');d=ImageDraw.Draw(im)
    d.text((24,15),title,font=big,fill='#152a46')
    d.text((24,43),'Saved-file preview | bridge renderer | uSimmics 5.9',font=small,fill='#53657a')
    def xy(p): return (p[0]-minx,p[1]-miny+80)
    blue='#193f85'; ink='#152a46'
    def line(coords,fill=blue,width_=2): d.line([xy(p) for p in coords],fill=fill,width=width_)
    for a,b,label in wires: line([a,b])
    for c in comps:
        x,y,rot=c['x'],c['y'],c['orientation'];kind=c['type']
        def p(dx,dy):
            for _ in range(rot): dx,dy=dy,-dx
            return (x+dx,y+dy)
        def seg(coords): line([p(a,b) for a,b in coords],blue if c['active'] else '#a0a9b4')
        if kind=='.HB':
            a=xy((x,y-30));b=xy((x+190,y))
            d.rounded_rectangle([a,b],radius=4,outline=blue,width=2,fill='#e8effa')
            d.text((a[0]+8,a[1]+4),'Harmonic balance',font=font,fill=ink)
        elif kind=='GND':
            for yy,half in [(0,12),(5,8),(10,3)]: seg([(-half,yy),(half,yy)])
        elif kind in ('Vac','Vdc'):
            seg([(0,-30),(0,-16)]);seg([(0,16),(0,30)])
            q=xy((x,y));d.ellipse([q[0]-16,q[1]-16,q[0]+16,q[1]+16],outline=blue,width=2)
            if kind=='Vac': seg([(i,6*math.sin(i*math.pi/10)) for i in range(-10,11)])
            else: seg([(-7,-5),(7,-5)]);seg([(-3,5),(3,5)])
            d.text(xy(p(-13,-31)),'+',font=small,fill='#b44343')
        elif kind=='R':
            seg([(-30,0),(-18,0)]);seg([(18,0),(30,0)])
            seg([(-18,-8),(18,-8),(18,8),(-18,8),(-18,-8)])
        elif kind=='C':
            seg([(-30,0),(-5,0)]);seg([(5,0),(30,0)])
            seg([(-5,-14),(-5,14)]);seg([(5,-14),(5,14)])
        elif kind=='L':
            seg([(-30,0),(-18,0)])
            seg([(-18+i, -8*abs(math.sin(i*math.pi/12))) for i in range(37)])
            seg([(18,0),(30,0)])
        elif kind=='Diode':
            seg([(-30,0),(-10,0)]);seg([(10,0),(30,0)])
            # Native uSimmics orientation 0 places the cathode on the left.
            # At orientation 3 (the supplied lab), that cathode is at the top.
            seg([(10,-10),(-10,0),(10,10),(10,-10)])
            seg([(-10,-11),(-10,11)])
            seg([(-6,-11),(-10,-11)]);seg([(-10,11),(-14,11)])
        elif kind=='Filter':
            seg([(-30,0),(-16,0)]);seg([(16,0),(30,0)])
            seg([(-16,-16),(16,-16),(16,16),(-16,16),(-16,-16)])
            for yy in [-8,0,8]: seg([(i,yy+2*math.sin(i/3)) for i in range(-10,11)])
        else: raise BridgeError('Unsupported preview symbol: '+kind)
    # Use junctions implied by wire endpoints and component pin locations.
    junctions=junction_points(comps,wires)
    for p0 in junctions:
        q=xy(p0);d.ellipse([q[0]-3,q[1]-3,q[0]+3,q[1]+3],fill=blue)
    for x,y,text in labels:
        d.multiline_text(xy((x,y)),text,font=font,spacing=3,fill=ink)
    warnings=[]
    for i,a in enumerate(boxes):
        for j,b in enumerate(boxes[:i]):
            if min(a[2],b[2])-max(a[0],b[0])>3 and min(a[3],b[3])-max(a[1],b[1])>3:
                warnings.append('Label overlap: '+labels[i][2].split('\n')[0]+' / '+labels[j][2].split('\n')[0])
    traces=[]
    if dataset:
        vals={v['name']:v for v in dataset['variables']}
        frequency=vals.get('frequency',{}).get('data',[])
        if not frequency: raise BridgeError('Spectrum preview needs a frequency axis')
        if any(v is None or not math.isfinite(v) for v in frequency):
            raise BridgeError('Spectrum has invalid frequencies')
        top=circuit_h+125;left=75;right=width-30;bottom=height-45
        floor=-140;ceiling=20;fmax=max(frequency)/1e6 or 1
        for name,v in vals.items():
            if v['type']=='complex' and v['unit']=='V' and v['dependencies']==['frequency']:
                if len(v['data'])!=len(frequency): raise BridgeError('Spectrum/axis length mismatch')
                dbm=[]
                for f,sample in zip(frequency,v['data']):
                    mag=sample['magnitude']
                    dbm.append(voltage_power_dbm(mag,f,50))
                traces.append((name,dbm))
        if not traces: raise BridgeError('No supported complex voltage spectrum')
        d.text((24,top-35),'Discrete HB spectrum · dBm, 50 Ω · display floor −140 dBm',font=font,fill=ink)
        def px(f): return left+f/1e6/fmax*(right-left)
        def py(db): return bottom-(db-floor)/(ceiling-floor)*(bottom-top)
        for level in range(floor,ceiling+1,20):
            yy=py(level);d.line([(left,yy),(right,yy)],fill='#d7e0eb')
            d.text((20,yy-8),str(level),font=small,fill='#53657a')
        for t in range(8):
            f=t*fmax/7;xx=px(f*1e6)
            d.text((xx-10,bottom+8),f'{f:.1f}',font=small,fill='#53657a')
        d.text((right-25,bottom+25),'MHz',font=small,fill=ink)
        colors=['#235dc1','#ce4e36','#298659']
        for j,(name,values) in enumerate(traces):
            color=colors[j%len(colors)]
            d.text((left+j*260,top-17),name,font=small,fill=color)
            for f,v in zip(frequency,values):
                xx=px(f);yy=py(max(floor,min(ceiling,v if v is not None else floor)))
                d.line([(xx,bottom),(xx,yy)],fill=color,width=2)
                d.ellipse([xx-3,yy-3,xx+3,yy+3],fill=color)
    im.save(output,format='PNG')
    return {'width':width,'height':height,'warnings':warnings,
            'junction_points':[list(p) for p in junctions],
            'spectrum_traces':[name for name,_ in traces],
            'spectrum_impedance_ohm':50 if dataset else None,
            'spectrum_floor_dbm':-140 if dataset else None}
