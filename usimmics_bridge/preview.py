"""Deterministic saved-file circuit preview and discrete HB spectrum, using Pillow.

Components and wires keep their saved coordinates. A text label that would collide
with a symbol, wire or another label moves to the nearest free spot and is reported
in ``adjusted_labels``. Geometry is drawn supersampled and box-reduced, so it is
anti-aliased; text is drawn afterwards at the final size, so it stays sharp.
"""
import itertools
import math
import re
from PIL import Image, ImageDraw, ImageFont

from .core import _parse, NAMES, BridgeError
from .numeric import voltage_power_dbm

PAGE, CARD, BORDER, TAG = '#f6f6f3', '#ffffff', '#e2e1da', '#cfcec6'
INK, INK2, MUTED = '#16171a', '#4f4e4a', '#6b6a65'
GRID, AXIS = '#ecebe5', '#bdbcb3'
SYMBOL, WIRE, INACTIVE, ACCENT, FRAME = '#1d2939', '#3f679e', '#b6bac2', '#c2410c', '#8fa3bf'
CRITICAL, FRAME_FILL = '#b42318', '#f3f6fb'
# Categorical slots in a CVD-validated order. A trace keeps its slot in the plot and on its net tag.
SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']
FLOOR, MAX_PANELS, MAX_MARKERS = -140, len(SERIES), 8
PANEL_TITLE, HEADROOM, PANEL_GAP, AXIS_BAND = 22, 42, 14, 62
SHORT = {'resistance': 'R', 'frequency': 'f', 'amplitude': 'V', 'order': 'N', 'bandwidth': 'BW',
         'capacitance': 'C', 'inductance': 'L', 'voltage': 'V'}
SI = {'T': 1e12, 'G': 1e9, 'M': 1e6, 'k': 1e3, 'K': 1e3, '': 1, 'm': 1e-3, 'u': 1e-6, 'µ': 1e-6,
      'n': 1e-9, 'p': 1e-12, 'f': 1e-15}
FONT_FILES = {
    'regular': ['segoeui.ttf', 'DejaVuSans.ttf', 'Arial.ttf', 'Helvetica.ttc'],
    'semibold': ['seguisb.ttf', 'segoeuib.ttf', 'DejaVuSans-Bold.ttf', 'Arial Bold.ttf', 'arialbd.ttf'],
    'cjk': ['msyh.ttc', 'NotoSansCJK-Regular.ttc', 'NotoSansSC-Regular.otf', 'wqy-microhei.ttc',
            'PingFang.ttc', 'Hiragino Sans GB.ttc'],
    'cjk-semibold': ['msyhbd.ttc', 'NotoSansCJK-Bold.ttc', 'NotoSansSC-Bold.otf'],
}
FONT_FALLBACK = {'semibold': 'regular', 'cjk': 'regular', 'cjk-semibold': 'cjk'}
_FONTS = {}


def _font(style, size, text=''):
    """System sans in the requested weight; text with CJK characters uses a CJK face if present."""
    if any(ord(ch) >= 0x2E80 for ch in text):
        style = 'cjk-semibold' if style == 'semibold' else 'cjk'
    if (style, size) not in _FONTS:
        for name in FONT_FILES[style]:
            try:
                _FONTS[style, size] = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        else:
            fallback = FONT_FALLBACK.get(style)
            _FONTS[style, size] = _font(fallback, size) if fallback else ImageFont.load_default(size=size)
    return _FONTS[style, size]


def _measure(row):
    font = _font(row[2], row[3], row[0])
    ascent, descent = font.getmetrics()
    return font.getlength(row[0]), ascent + descent


def _block(rows):
    sizes = [_measure(row) for row in rows]
    return max(w for w, _ in sizes), sum(h for _, h in sizes)


def _fit(text, style, size, width):
    while len(text) > 1 and _measure((text, None, style, size))[0] > width:
        text = text[:-2] + '…'
    return text


def _minus(value):
    return str(value).replace('-', '−')


class Canvas:
    """Geometry drawn at 2-3x and box-reduced for anti-aliasing; text added at 1x afterwards."""

    def __init__(self, width, height):
        self.scale = next(s for s in (3, 2, 1) if width * height * s * s <= 24_000_000 or s == 1)
        self.image = Image.new('RGB', (width * self.scale, height * self.scale), PAGE)
        self.draw = ImageDraw.Draw(self.image)
        self.texts = []

    def _xy(self, points):
        return [(x * self.scale, y * self.scale) for x, y in points]

    def _width(self, width):
        return max(1, round(width * self.scale))

    def line(self, points, fill, width=2):
        self.draw.line(self._xy(points), fill=fill, width=self._width(width), joint='curve')

    def circle(self, center, r, fill=None, outline=None, width=2):
        (x, y), r = self._xy([center])[0], r * self.scale
        self.draw.ellipse((x - r, y - r, x + r, y + r), fill=fill, outline=outline,
                          width=self._width(width) if outline else 0)

    def box(self, box, fill=None, outline=None, width=1, radius=0):
        self.draw.rounded_rectangle([v * self.scale for v in box], radius=radius * self.scale, fill=fill,
                                    outline=outline, width=self._width(width) if outline else 0)

    def text(self, xy, text, style='regular', size=12, fill=INK, anchor='la'):
        self.texts.append((xy, text, style, size, fill, anchor))

    def rows(self, x, y, rows, width=None):
        """Stack (text, color, style, size) rows from a top-left corner, optionally centered in width."""
        for row in rows:
            w, h = _measure(row)
            self.text((x + ((width - w) / 2 if width else 0), y), row[0], row[2], row[3], row[1])
            y += h

    def save(self, output):
        image = self.image.reduce(self.scale) if self.scale > 1 else self.image
        draw = ImageDraw.Draw(image)
        for xy, text, style, size, fill, anchor in self.texts:
            draw.text(xy, text, fill=fill, font=_font(style, size, text), anchor=anchor)
        image.save(output, format='PNG')


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


def _hz(text):
    """Hertz from a plain uSimmics value such as '11 MHz' or '1e6'; None for anything symbolic."""
    m = re.fullmatch(r'\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([TGMkKmuµnpf]?)(?:Hz)?\s*', text)
    return float(m[1]) * SI[m[2]] if m else None


def _hb_info(c, comps):
    """Mixing order and tones of an HB block, with the Vac sources that drive each tone."""
    order, *parts = c['properties'][0].split(';')
    tones = []
    for part in filter(None, parts):
        count, text = part.split('@', 1)
        tones.append({'text': text, 'hz': _hz(text), 'count': int(count),
                      'sources': [s['name'] for s in comps if s['type'] == 'Vac' and s['active']
                                  and s['properties'][1] == text]})
    return {'name': c['name'], 'order': int(order[:-1]), 'tones': tones}


def _product_name(ms):
    text = ''
    for m, i in sorted(((m, i) for i, m in enumerate(ms) if m), key=lambda t: (t[0] < 0, t[1])):
        text += ('−' if m < 0 else '+' if text else '') + (str(abs(m)) if abs(m) > 1 else '') + f'f{i + 1}'
    return text


def mixing_products(hb, frequencies):
    """Name each nonzero frequency by its lowest-order mixing product of the HB tones ('f1−f2')."""
    tones = hb['tones'] if hb else []
    if not tones or any(t['hz'] is None or t['hz'] <= 0 for t in tones):
        return {}
    reach = min(16, max(hb['order'], *(t['count'] for t in tones)))
    if (2 * reach + 1) ** len(tones) > 50000:
        return {}
    wanted = {round(f * 1e3): f for f in frequencies if f > 0}
    best = {}
    for ms in itertools.product(range(-reach, reach + 1), repeat=len(tones)):
        f = sum(m * t['hz'] for m, t in zip(ms, tones))
        if f <= 0:
            continue
        for key in (round(f * 1e3) - 1, round(f * 1e3), round(f * 1e3) + 1):
            target = wanted.get(key)
            if target is not None and abs(target - f) <= 1e-9 * target + 1e-3:
                rank = (sum(map(abs, ms)), sum(m < 0 for m in ms))
                if target not in best or rank < best[target][0]:
                    best[target] = (rank, ms)
    return {f: _product_name(ms) for f, (_, ms) in best.items()}


def _symbol(c):
    """Strokes, circles and polarity accents of a component in local (unrotated) coordinates."""
    kind, props = c['type'], c['properties']
    circles, accents = [], []
    if kind == 'GND':
        strokes = [[(-11, 0), (11, 0)], [(-7, 5), (7, 5)], [(-3, 10), (3, 10)]]
    elif kind in ('Vac', 'Vdc'):
        strokes = [[(0, -30), (0, -16)], [(0, 16), (0, 30)]]
        circles = [((0, 0), 16)]
        if kind == 'Vac':
            strokes.append([(i / 2, 6 * math.sin(i * math.pi / 20)) for i in range(-20, 21)])
        else:
            strokes += [[(-8, -4), (8, -4)], [(-4, 4), (4, 4)]]
        accents = [[(-13, -24), (-7, -24)], [(-10, -27), (-10, -21)]]
    elif kind == 'R':
        strokes = [[(-30, 0), (-18, 0)], [(18, 0), (30, 0)], [(-18, -8), (18, -8), (18, 8), (-18, 8), (-18, -8)]]
    elif kind == 'C':
        strokes = [[(-30, 0), (-5, 0)], [(5, 0), (30, 0)], [(-5, -13), (-5, 13)], [(5, -13), (5, 13)]]
    elif kind == 'L':
        strokes = [[(-30, 0), (-18, 0)], [(18, 0), (30, 0)]]
        strokes += [[(cx - 6 * math.cos(k * math.pi / 12), -6 * math.sin(k * math.pi / 12)) for k in range(13)]
                    for cx in (-12, 0, 12)]
    elif kind == 'Diode':
        # Native uSimmics orientation 0 places the cathode bar on the left; property 30 names the style.
        strokes = [[(-30, 0), (-10, 0)], [(10, 0), (30, 0)], [(10, -10), (-10, 0), (10, 10), (10, -10)],
                   [(-10, -11), (-10, 11)]]
        style = props[30] if len(props) > 30 else ''
        if style == 'Schottky':
            strokes += [[(-10, -11), (-6, -11), (-6, -7)], [(-10, 11), (-14, 11), (-14, 7)]]
        elif style == 'Zener':
            strokes += [[(-10, -11), (-6, -11)], [(-10, 11), (-14, 11)]]
    elif kind == 'Filter':
        strokes = [[(-30, 0), (-16, 0)], [(16, 0), (30, 0)], [(-16, -16), (16, -16), (16, 16), (-16, 16), (-16, -16)]]
        strokes += [[(i / 2, y - 2.5 * math.sin(i * math.pi / 16)) for i in range(-16, 17)] for y in (-8, 0, 8)]
        # IEC bands: top = high, bottom = low frequencies; a slash marks a stopped band.
        stopped = {'lowpass': (-8,), 'highpass': (8,), 'bandpass': (-8, 8), 'bandstop': (0,)}
        strokes += [[(-4, y + 3.5), (4, y - 3.5)] for y in stopped.get(props[2] if len(props) > 2 else '', ())]
    else:
        raise BridgeError('Unsupported preview symbol: ' + kind)
    return strokes, circles, accents


def _rotate(c, dx, dy):
    for _ in range(c['orientation']):
        dx, dy = dy, -dx
    return c['x'] + dx, c['y'] + dy


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _bbox(points):
    return (min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points))


def _grow(box, d):
    return box[0] - d, box[1] - d, box[2] + d, box[3] + d


def _shift(box, dx, dy):
    return box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy


_OFFSETS = sorted(((dx, dy) for dx in range(-48, 49, 4) for dy in range(-48, 49, 4)),
                  key=lambda d: (d[0] ** 2 + d[1] ** 2, abs(d[0]), d[1]))


def _nearest(box, p):
    return min(max(p[0], box[0]), box[2]), min(max(p[1], box[1]), box[3])


def _crosses(a, b, box):
    """Whether segment a-b passes through the interior of box (Liang-Barsky clipping)."""
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - box[0]), (dx, box[2] - a[0]), (-dy, a[1] - box[1]), (dy, box[3] - a[1])):
        if p == 0:
            if q <= 0:
                return False
        elif p < 0:
            t0 = max(t0, q / p)
        else:
            t1 = min(t1, q / p)
        if t0 >= t1:
            return False
    return True


def _free_offset(box, obstacles, reach, ok=lambda box: True):
    """Nearest offset, saved position first, at which `box` touches no obstacle; None if none."""
    near = [o for o in obstacles if _overlaps(o, _grow(box, reach))]
    for dx, dy in _OFFSETS:
        if dx * dx + dy * dy > reach * reach:
            return None
        moved = _shift(box, dx, dy)
        if not any(_overlaps(moved, o) for o in near) and ok(moved):
            return dx, dy
    return None


def _component_rows(c, ink, ink2):
    names = NAMES.get(c['type'], [])
    rows = [(c['name'], ink, 'semibold', 13)]
    for i, (value, flag) in enumerate(zip(c['properties'], c['flags'])):
        if flag == '1':
            name = names[i] if i < len(names) else None
            rows.append(((SHORT.get(name, name) + '=' if name else '') + value, ink2, 'regular', 12))
    return rows


def _hb_rows(info, ink2):
    tones = [f"f{i + 1} = {t['text']}" for i, t in enumerate(info['tones'])]
    rows = [' · '.join(tones[i:i + 2]) for i in range(0, len(tones), 2)]
    counts = '/'.join(str(n) for n in sorted({t['count'] for t in info['tones']}))
    rows.append((counts + ' harmonics · ' if counts else '') + 'order ' + str(info['order']))
    return [(row, ink2, 'regular', 12) for row in rows]


def _scene(lines, comps, wires, net_colors):
    """Schematic primitives in saved coordinates, plus collision-free label positions."""
    strokes, circles, frames, bodies, labels, tags = [], [], [], [], [], []
    wire_boxes = [_grow(_bbox([a, b]), 2) for a, b, _ in wires]
    for c in comps:
        active = bool(c['active'])
        color, ink, ink2 = (SYMBOL, INK, INK2) if active else (INACTIVE, MUTED, MUTED)
        fields = c['prefix'].split()
        tx, ty = int(fields[5]), int(fields[6])
        if c['type'] == '.HB':
            width = max(150, math.ceil(_measure(('Harmonic balance', '', 'semibold', 12))[0]) + 24)
            frames.append(((c['x'], c['y'] - 30, c['x'] + width, c['y']), active))
            bodies.append(frames[-1][0])
            rows = [(c['name'], ink, 'semibold', 13)] + _hb_rows(_hb_info(c, comps), ink2)
        else:
            local, rings, accents = _symbol(c)
            points = []
            for poly in local:
                strokes.append(([_rotate(c, *p) for p in poly], color, 2))
                points += strokes[-1][0]
            for center, r in rings:
                x, y = _rotate(c, *center)
                circles.append(((x, y), r, color))
                points += [(x - r, y - r), (x + r, y + r)]
            strokes += [([_rotate(c, *p) for p in poly], ACCENT if active else INACTIVE, 1.5) for poly in accents]
            bodies.append(_grow(_bbox(points), 1))
            if c['type'] == 'GND':
                continue
            rows = _component_rows(c, ink, ink2)
        labels.append({'name': c['name'], 'x': c['x'] + tx, 'y': c['y'] + ty, 'rows': rows})
    junctions = junction_points(comps, wires)
    obstacles = bodies + wire_boxes + [(x - 4, y - 4, x + 4, y + 4) for x, y in junctions]
    for line in lines[lines.index('<Wires>') + 1:lines.index('</Wires>')]:
        m = re.match(r'(-?\d+) (-?\d+) (-?\d+) (-?\d+) "([^"\n]+)" (-?\d+) (-?\d+) (-?\d+)', line)
        if m:
            x1, y1, x2, y2, lx, ly, delta = (int(m[i]) for i in (1, 2, 3, 4, 6, 7, 8))
            # The saved delta locates the labelled node along the wire, measured from its first point.
            ax, ay = (x1 + delta, y1) if y1 == y2 else (x1, y1 + delta)
            anchor = (min(max(ax, min(x1, x2)), max(x1, x2)), min(max(ay, min(y1, y2)), max(y1, y2)))
            tags.append({'name': m[5], 'x': lx, 'y': ly, 'anchor': anchor, 'color': net_colors.get(m[5]),
                         'rows': [(m[5], INK, 'semibold', 12)]})
    placed, leaders, adjusted = [], [], []
    # Net tags go first: their leader lines must reach the wire without crossing symbols or text.
    # Component labels then take the nearest spot clear of every tag and leader.
    for item in tags + labels:
        w, h = _block(item['rows'])
        x, y = item['x'], item['y']
        if 'anchor' in item:
            anchor = item['anchor']
            box = (x - 6 - (12 if item['color'] else 0), y - 2, x + w + 6, y + h + 2)
            blockers = [b for b in bodies if not (b[0] <= anchor[0] <= b[2] and b[1] <= anchor[1] <= b[3])]
            def ok(moved):
                end = _nearest(moved, anchor)
                return not any(_crosses(anchor, end, b) for b in blockers + placed)
            reach = 48
        else:
            box = (x - 2, y + 2, x + w + 2, y + h - 1)
            def ok(moved):
                return not any(_crosses(a, b, moved) for a, b in leaders)
            reach = 36
        dx, dy = _free_offset(box, obstacles + placed, reach, ok) or (0, 0)
        if (dx, dy) != (0, 0) and item['name'] not in adjusted:
            adjusted.append(item['name'])
        item.update(x=x + dx, y=y + dy, box=_shift(box, dx, dy))
        placed.append(item['box'])
        if 'anchor' in item:
            leaders.append((item['anchor'], _nearest(item['box'], item['anchor'])))
    warnings = []
    items = labels + tags
    for i, a in enumerate(items):
        for b in items[:i]:
            A, B = a['box'], b['box']
            if min(A[2], B[2]) - max(A[0], B[0]) > 3 and min(A[3], B[3]) - max(A[1], B[1]) > 3:
                warnings.append('Label overlap: ' + a['rows'][0][0] + ' / ' + b['rows'][0][0])
    boxes = obstacles + placed
    return {'wires': wires, 'strokes': strokes, 'circles': circles, 'frames': frames, 'junctions': junctions,
            'labels': labels, 'tags': tags, 'warnings': warnings, 'adjusted': adjusted,
            'bounds': _bbox([p for b in boxes for p in (b[:2], b[2:])]) if boxes else (0, 0, 1, 1)}


def _draw_scene(cv, scene, ox, oy):
    def at(p):
        return p[0] + ox, p[1] + oy
    for a, b, _ in scene['wires']:
        cv.line([at(a), at(b)], WIRE, 2)
        for p in (a, b):
            cv.circle(at(p), 1, fill=WIRE)
    for points, color, width in scene['strokes']:
        cv.line([at(p) for p in points], color, width)
    for center, r, color in scene['circles']:
        cv.circle(at(center), r, outline=color, width=2)
    for box, active in scene['frames']:
        x0, y0, x1, y1 = _shift(box, ox, oy)
        cv.box((x0, y0, x1, y1), FRAME_FILL if active else CARD, FRAME if active else INACTIVE, 1.5, 4)
        cv.text((x0 + 12, y0 + 6), 'Harmonic balance', 'semibold', 12, INK2 if active else MUTED)
    for p in scene['junctions']:
        cv.circle(at(p), 3.5, fill=WIRE)
    for tag in scene['tags']:
        x0, y0, x1, y1 = _shift(tag['box'], ox, oy)
        ax, ay = at(tag['anchor'])
        near = _nearest((x0, y0, x1, y1), (ax, ay))
        if near != (ax, ay):
            cv.line([(ax, ay), near], MUTED, 1.25)
        cv.circle((ax, ay), 2.5, fill=MUTED)
        cv.box((x0, y0, x1, y1), CARD, TAG, 1, 4)
        if tag['color']:
            cv.circle((x0 + 10, (y0 + y1) / 2), 4, fill=tag['color'])
    for item in scene['labels'] + scene['tags']:
        cv.rows(item['x'] + ox, item['y'] + oy, item['rows'])


def _spectrum(dataset):
    vals = {v['name']: v for v in dataset['variables']}
    frequency = vals.get('frequency', {}).get('data', [])
    if not frequency:
        raise BridgeError('Spectrum preview needs a frequency axis')
    if any(v is None or not math.isfinite(v) for v in frequency):
        raise BridgeError('Spectrum has invalid frequencies')
    traces = []
    for name, v in vals.items():
        if v['type'] == 'complex' and v['unit'] == 'V' and v['dependencies'] == ['frequency']:
            if len(v['data']) != len(frequency):
                raise BridgeError('Spectrum/axis length mismatch')
            traces.append((name, [voltage_power_dbm(s['magnitude'], f, 50) for f, s in zip(frequency, v['data'])]))
    if not traces:
        raise BridgeError('No supported complex voltage spectrum')
    traces.sort(key=lambda t: t[0].lower())
    return {'frequency': frequency, 'traces': traces[:MAX_PANELS], 'omitted': [n for n, _ in traces[MAX_PANELS:]]}


def _plot_height(n):
    return 230 if n == 1 else 170 if n == 2 else 140


def _spectrum_height(spec, hb, note):
    n = len(spec['traces'])
    header = 20 + 24 + 18 + (18 if hb and hb['tones'] else 0) + (18 if note else 0) + 6
    return header + n * (PANEL_TITLE + HEADROOM + _plot_height(n)) + (n - 1) * PANEL_GAP + AXIS_BAND


def _axis(fmax):
    """Display unit and a 1-2-2.5-5 tick step giving roughly eight intervals."""
    if fmax <= 0:
        return 1, 'Hz', 1, 1
    scale, unit = next(((s, u) for s, u in ((1e9, 'GHz'), (1e6, 'MHz'), (1e3, 'kHz')) if fmax >= s), (1, 'Hz'))
    raw = fmax / scale / 8
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if raw <= m * magnitude * (1 + 1e-9))
    top = max(step, math.ceil(fmax / scale * 1.02 / step - 1e-9) * step)
    return scale, unit, step, top


def _annotate(cv, stems, region, names, scale, unit):
    """Direct-label the strongest stems where a label fits without covering another mark."""
    dots = [(x - 6, y - 6, x + 6, y + 6) for x, y, _, _ in stems]
    placed, chosen = [], []
    for x, y, v, f in sorted(stems, key=lambda s: (-s[2], s[3])):
        if len(chosen) == MAX_MARKERS:
            break
        freq = 'DC' if f == 0 else f'{f / scale:.6g} {unit}'
        head = f'{names[f]} · {freq}' if names.get(f) else freq
        rows = [(head, INK, 'semibold', 11), (_minus(f'{v:.1f}') + ' dBm', INK2, 'regular', 11)]
        w, h = _block(rows)
        spots = [(x - w / 2, y - 9 - h), (x - 8, y - 9 - h), (x - w + 8, y - 9 - h), (x + 10, y - h / 2),
                 (x - 10 - w, y - h / 2), (x + 10, y - h - 2), (x - 10 - w, y - h - 2), (x + 10, y + 2),
                 (x - 10 - w, y + 2)]
        best = None
        for rank, (bx, by) in enumerate(spots):
            box = (bx - 2, by, bx + w + 2, by + h)
            if box[0] < region[0] or box[2] > region[2] or box[1] < region[1] or box[3] > region[3]:
                continue
            if any(_overlaps(box, other) for other in placed + dots):
                continue
            crossings = sum(1 for sx, sy, _, _ in stems if box[0] <= sx <= box[2] and sy < box[3])
            if best is None or (crossings, rank) < best[0]:
                best = ((crossings, rank), bx, by, box)
        if best:
            cv.rows(best[1], best[2], rows, w)
            placed.append(best[3])
            chosen.append((v, f))
    return chosen


def _draw_spectrum(cv, x0, y0, x1, spec, hb, note):
    """Small multiples of discrete HB spectra: one panel per node voltage on a shared frequency axis."""
    frequency, traces = spec['frequency'], spec['traces']
    n, y = len(traces), y0 + 20
    cv.text((x0 + 20, y), 'Harmonic-balance spectrum', 'semibold', 15, INK)
    y += 24
    cv.text((x0 + 20, y), f'Power into 50 Ω (AC peak, DC RMS) · display floor {_minus(FLOOR)} dBm',
            'regular', 12, INK2)
    y += 18
    if hb and hb['tones']:
        tones = ' · '.join(f"f{i + 1} = {t['text']}" + (f" ({', '.join(t['sources'])})" if t['sources'] else '')
                           for i, t in enumerate(hb['tones']))
        cv.text((x0 + 20, y), _fit(f"Tones: {tones} · order {hb['order']}", 'regular', 12, x1 - x0 - 40),
                'regular', 12, INK2)
        y += 18
    if note:
        cv.text((x0 + 20, y), _fit('Warning: ' + note, 'semibold', 12, x1 - x0 - 40), 'semibold', 12, CRITICAL)
        y += 18
    y += 6
    left, right = x0 + 64, x1 - 26
    scale, unit, step, top = _axis(max(frequency))
    def px(f):
        return left + (f / scale + 0.025 * top) / (1.025 * top) * (right - left)
    peak = max((v for _, values in traces for v in values if v is not None), default=FLOOR)
    ceiling = max(20, 20 * math.ceil(peak / 20))
    plot_h, names, markers = _plot_height(n), mixing_products(hb, frequency), []
    ticks = [k * step for k in range(int(top / step + 1e-9) + 1)]
    for i, (name, values) in enumerate(traces):
        color = SERIES[i]
        panel = y + i * (PANEL_TITLE + HEADROOM + plot_h + PANEL_GAP)
        cv.line([(x0 + 26, panel + 16), (x0 + 26, panel + 7)], color, 2)
        cv.circle((x0 + 26, panel + 7), 3.5, fill=color)
        cv.text((x0 + 36, panel), name, 'semibold', 13, INK)
        plot_top = panel + PANEL_TITLE + HEADROOM
        bottom = plot_top + plot_h
        def py(db):
            return bottom - (db - FLOOR) / (ceiling - FLOOR) * plot_h
        label_step = 20 if plot_h * 20 / (ceiling - FLOOR) >= 19 else 40
        for level in range(FLOOR, ceiling + 1, 20):
            cv.line([(left, py(level)), (right, py(level))], AXIS if level == FLOOR else GRID, 1)
            if level % label_step == 0:
                cv.text((left - 10, py(level)), _minus(level), 'regular', 11, MUTED, 'rm')
        cv.text((left - 10, plot_top - 12), 'dBm', 'regular', 11, MUTED, 'rs')
        for t in ticks:
            cv.line([(px(t * scale), bottom), (px(t * scale), bottom + 4)], AXIS, 1)
        stems = [(px(f), py(v), v, f) for f, v in zip(frequency, values) if v is not None and v >= FLOOR]
        for sx, sy, _, _ in stems:
            cv.line([(sx, bottom), (sx, sy)], color, 2)
        for sx, sy, _, _ in stems:
            cv.circle((sx, sy), 6, fill=CARD)
            cv.circle((sx, sy), 4, fill=color)
        if not stems:
            cv.text(((left + right) / 2, (plot_top + bottom) / 2),
                    f'Every sample is below the {_minus(FLOOR)} dBm display floor', 'regular', 12, MUTED, 'mm')
        for v, f in _annotate(cv, stems, (left, panel + PANEL_TITLE, right, bottom - 2), names, scale, unit):
            markers.append({'trace': name, 'frequency_hz': f, 'power_dbm': round(v, 2),
                            'product': 'DC' if f == 0 else names.get(f)})
    for t in ticks:
        cv.text((px(t * scale), bottom + 8), f'{t:g}', 'regular', 11, MUTED, 'ma')
    cv.text(((left + right) / 2, bottom + 27), f'Frequency ({unit})', 'regular', 12, INK2, 'ma')
    return markers


def render(content, output, title, dataset=None, note=None):
    """Draw the saved schematic and, with a dataset, its spectrum; `note` flags doubtful data."""
    lines, comps, wires = _parse(content)
    spectrum = _spectrum(dataset) if dataset else None
    hb = next((_hb_info(c, comps) for c in comps if c['type'] == '.HB' and c['active']), None)
    colors = {}
    for i, (name, _) in enumerate(spectrum['traces'] if spectrum else []):
        colors.setdefault(name.rsplit('.', 1)[0], SERIES[i])
    scene = _scene(lines, comps, wires, colors)
    minx, miny, maxx, maxy = scene['bounds']
    if maxx - minx > 5000 or maxy - miny > 5000:
        raise BridgeError('Layout too large for a readable preview; move components closer')
    margin, pad = 24, 22
    width = max(1000, math.ceil(maxx - minx) + 2 * (margin + pad))
    card_top, card_h = margin + 60, math.ceil(maxy - miny) + 2 * pad
    spec_top = card_top + card_h + 16
    height = (spec_top + _spectrum_height(spectrum, hb, note) if spectrum else card_top + card_h) + margin
    cv = Canvas(width, height)
    cv.text((margin, margin - 2), _fit(title, 'semibold', 20, width - 2 * margin), 'semibold', 20, INK)
    cv.text((margin, margin + 30), 'Saved-file preview · bridge renderer, not a native screenshot · uSimmics 5.9',
            'regular', 12, MUTED)
    cv.box((margin, card_top, width - margin, card_top + card_h), CARD, BORDER, 1, 10)
    _draw_scene(cv, scene, round((width - (maxx - minx)) / 2 - minx), card_top + pad - math.floor(miny))
    warnings, markers = scene['warnings'], []
    if spectrum:
        cv.box((margin, spec_top, width - margin, height - margin), CARD, BORDER, 1, 10)
        markers = _draw_spectrum(cv, margin, spec_top, width - margin, spectrum, hb, note)
        if spectrum['omitted']:
            warnings.append(f'Spectrum shows {MAX_PANELS} voltages; omitted: ' + ', '.join(spectrum['omitted']))
    cv.save(output)
    return {'width': width, 'height': height, 'warnings': warnings,
            'junction_points': [list(p) for p in scene['junctions']],
            'adjusted_labels': scene['adjusted'],
            'spectrum_traces': [name for name, _ in spectrum['traces']] if spectrum else [],
            'spectrum_markers': markers,
            'spectrum_impedance_ohm': 50 if dataset else None,
            'spectrum_floor_dbm': FLOOR if dataset else None}
