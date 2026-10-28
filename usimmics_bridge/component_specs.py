"""One contract for the deliberately supported native 5.9 component records.

Unknown property semantics stay unnamed; positional access remains available.
In particular, the six C/L fields are preserved rather than guessed or dropped.
"""
from dataclasses import dataclass
import re


HB_ORDER_MIN, HB_ORDER_MAX = 1, 16
HB_COUNT_MIN, HB_COUNT_MAX = 1, 32
COORDINATE_LIMIT = 4000


def validate_property(value):
    if not isinstance(value, str) or any(ch in value for ch in ('"', '\\')) or any(ord(ch) < 32 for ch in value):
        raise ValueError('Properties must be single-line strings without quotes, backslashes or control characters')
    return value


def validate_harmonics(value):
    """Validate the same order/count bounds for raw files and structured authoring."""
    if not re.fullmatch(r'\d+@;(?:\d+@[^@;]+;)*', value):
        raise ValueError('Malformed HB harmonic frequency keys')
    order, *entries = value.rstrip(';').split(';')
    if not HB_ORDER_MIN <= int(order[:-1]) <= HB_ORDER_MAX:
        raise ValueError(f'HB order must be {HB_ORDER_MIN}..{HB_ORDER_MAX}')
    frequencies = set()
    for entry in entries:
        count, frequency = entry.split('@', 1)
        if not HB_COUNT_MIN <= int(count) <= HB_COUNT_MAX:
            raise ValueError(f'HB count must be {HB_COUNT_MIN}..{HB_COUNT_MAX}')
        if not frequency.strip() or frequency in frequencies:
            raise ValueError('HB frequency keys must be nonempty and unique')
        frequencies.add(frequency)


@dataclass(frozen=True)
class ComponentSpec:
    property_count: int
    parameter_names: tuple[str | None, ...]
    pin_offsets: tuple[tuple[int, int], ...]
    defaults: tuple[str, ...] | None = None

    def validate_properties(self, kind, values):
        if len(values) != self.property_count:
            raise ValueError(f'Wrong property count for {kind}: expected {self.property_count}')
        for value in values:
            validate_property(value)
        if kind == '.HB':
            validate_harmonics(values[0])

    def pins(self, x, y, rotation):
        if type(rotation) is not int or rotation not in range(4):
            raise ValueError('Unsupported component orientation')
        result = []
        for dx, dy in self.pin_offsets:
            for _ in range(rotation):
                dx, dy = dy, -dx
            result.append((x + dx, y + dy))
        return result


PASSIVE_PINS = ((-30, 0), (30, 0))
SOURCE_PINS = ((0, -30), (0, 30))
COMPONENT_SPECS = {
    'R': ComponentSpec(6, ('resistance', 'temperature', 'tc1', 'tc2', 'nominal_temperature', 'symbol'),
                       PASSIVE_PINS, ('50 Ohm', '26.85', '0', '0', '26.85', 'european')),
    'C': ComponentSpec(6, ('capacitance', None, None, None, None, None),
                       PASSIVE_PINS, ('1 pF', '0', '0', '0', '26.85', 'european')),
    'L': ComponentSpec(6, ('inductance', None, None, None, None, None),
                       PASSIVE_PINS, ('1 nH', '0', '0', '0', '26.85', 'european')),
    'Vac': ComponentSpec(5, ('amplitude', 'frequency', 'phase', 'delay', 'symbol'),
                         SOURCE_PINS, ('1 V', '1 MHz', '0', '0', 'con_2')),
    'Vdc': ComponentSpec(3, ('voltage', 'symbol', 'package'), SOURCE_PINS, ('1 V', 'battery', 'SIL-2')),
    'Diode': ComponentSpec(32, ('saturation_current', 'emission_coefficient', 'junction_capacitance') + (None,) * 29,
                           PASSIVE_PINS),
    'Filter': ComponentSpec(6, ('frequency', 'order', 'class', 'type', 'reference_impedance', 'bandwidth'),
                            PASSIVE_PINS, ('1 MHz', '3', 'bandpass', 'butterworth', '50 Ohm', '1 MHz')),
    'GND': ComponentSpec(0, (), ((0, 0),), ()),
    '.HB': ComponentSpec(10, ('harmonics', 'noise', 'noise_frequency', 'noise_sweep', 'noise_start',
                             'noise_stop', 'noise_points', 'relative_tolerance', 'absolute_tolerance', 'iterations'), ()),
}

# Compatibility exports; all values originate in the immutable specifications above.
SUPPORTED = set(COMPONENT_SPECS)
NAMES = {kind: list(spec.parameter_names) for kind, spec in COMPONENT_SPECS.items()}
DEFAULTS = {kind: list(spec.defaults) for kind, spec in COMPONENT_SPECS.items() if spec.defaults is not None}
