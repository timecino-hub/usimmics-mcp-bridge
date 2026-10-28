"""Strict, bounded decoding of the supported QucsData v2 result records.

The output point limit never limits validation: every sample is checked before
any result is returned, including samples omitted from the JSON response.
"""
from __future__ import annotations

import math
from pathlib import Path
import struct

from .core import BridgeError, _sha


MAX_DATASET_BYTES = 64 * 1024 * 1024
MAX_VARIABLES = 4096
MAX_RETURNED_SAMPLES = 1_000_000


def read_dataset_bytes(path: Path) -> bytes:
    # Bound the actual read as well as the stat check if another process grows
    # the file between them. The caller resolves/authorizes the path first.
    if path.stat().st_size > MAX_DATASET_BYTES:
        raise BridgeError('Dataset exceeds the 64 MiB reader limit')
    with path.open('rb') as stream:
        data = stream.read(MAX_DATASET_BYTES + 1)
    if len(data) > MAX_DATASET_BYTES:
        raise BridgeError('Dataset exceeds the 64 MiB reader limit')
    return data


def parse_dataset(data: bytes, max_points: int = 2000, *, require_hb: bool = False,
                  selected_names: set[str] | None = None) -> dict:
    """Validate all records/samples, returning at most max_points per variable.

    All nonfinite values are rejected explicitly. No NaN/Inf is silently
    converted to JSON null. A general read can inspect other real axes; native
    HB runs/publication additionally require a frequency-dependent voltage.
    Internal consumers can select returned variables without skipping validation
    of any other record or sample. The response budget applies only to output.
    """
    if isinstance(max_points, bool) or not isinstance(max_points, int) or not 1 <= max_points <= 100000:
        raise BridgeError('max_points must be from 1 to 100000')
    if len(data) > MAX_DATASET_BYTES:
        raise BridgeError('Dataset exceeds the 64 MiB reader limit')
    if len(data) < 16 or data[:12] != b'QucsData\x00\x00\x02\x03':
        raise BridgeError('Only the verified uSimmics binary QucsData v2 format is supported')
    end = 16 + struct.unpack_from('<I', data, 12)[0]
    if end > len(data):
        raise BridgeError('Dataset header is truncated')
    cursor = 16
    records = []
    names = set()

    def cstring():
        nonlocal cursor
        zero = data.find(b'\0', cursor, end)
        if zero < 0:
            raise BridgeError('Dataset header string is not terminated')
        try:
            value = data[cursor:zero].decode('utf-8')
        except UnicodeError as exc:
            raise BridgeError('Dataset header has invalid text') from exc
        cursor = zero + 1
        if not value or any(ord(ch) < 32 for ch in value):
            raise BridgeError('Dataset variable names and dependencies must be nonempty text')
        return value

    while cursor < end:
        if len(records) >= MAX_VARIABLES:
            raise BridgeError(f'Dataset exceeds {MAX_VARIABLES} variables')
        if cursor + 8 > end:
            raise BridgeError('Dataset variable header is truncated')
        kind = data[cursor]
        if kind not in (3, 5):
            raise BridgeError(f'Unsupported dataset variable type {kind}')
        try:
            unit = data[cursor + 1:cursor + 4].rstrip(b'\0').decode('ascii')
        except UnicodeError as exc:
            raise BridgeError('Dataset unit has invalid text') from exc
        if '\0' in unit or any(ord(ch) < 32 for ch in unit):
            raise BridgeError('Dataset unit has invalid text')
        count = struct.unpack_from('<I', data, cursor + 4)[0]
        cursor += 8
        name = cstring()
        if name in names:
            raise BridgeError(f'Duplicate dataset variable: {name}')
        names.add(name)
        if count == 0:
            raise BridgeError(f'Dataset variable has no samples: {name}')
        dependencies = [cstring()] if kind == 3 else []
        records.append(dict(name=name, unit=unit, dependencies=dependencies,
                            type='complex' if kind == 3 else 'real', total_points=count))
    if not records:
        raise BridgeError('Dataset has no variables')

    by_name = {record['name']: record for record in records}
    for record in records:
        for dependency in record['dependencies']:
            axis = by_name.get(dependency)
            if axis is None:
                raise BridgeError(f"Missing dependency {dependency} for {record['name']}")
            if axis['type'] != 'real' or axis['dependencies']:
                raise BridgeError(f'Dataset dependency must be a real independent axis: {dependency}')
            if axis['total_points'] != record['total_points']:
                raise BridgeError(f"Dataset sample count does not match {dependency}: {record['name']}")
    frequency = by_name.get('frequency')
    if frequency is not None and (frequency['type'] != 'real' or frequency['unit'] != 'Hz'
                                  or frequency['dependencies']):
        raise BridgeError('Frequency axis must be independent real data in Hz')
    hb_voltages = [record for record in records if record['type'] == 'complex'
                   and record['unit'] == 'V' and record['dependencies'] == ['frequency']]
    if require_hb and (frequency is None or not hb_voltages):
        raise BridgeError('HB results require a frequency axis and a complex voltage spectrum')
    selected_records = [record for record in records
                        if selected_names is None or record['name'] in selected_names]
    if sum(min(record['total_points'], max_points) for record in selected_records) > MAX_RETURNED_SAMPLES:
        raise BridgeError('Dataset response exceeds one million samples; lower max_points')

    cursor = end
    sample_count = 0
    for record in records:
        complex_ = record['type'] == 'complex'
        sample_size = 16 if complex_ else 8
        count = record['total_points']
        selected = selected_names is None or record['name'] in selected_names
        stop = cursor + sample_size * count
        if stop > len(data):
            raise BridgeError('Dataset sample payload is truncated')
        values = []
        samples = struct.iter_unpack('<dd' if complex_ else '<d', memoryview(data)[cursor:stop])
        for index, parts in enumerate(samples):
            if not all(math.isfinite(value) for value in parts):
                raise BridgeError(f"Nonfinite dataset sample: {record['name']}[{index}]")
            if record['name'] == 'frequency' and parts[0] < 0:
                raise BridgeError(f'Frequency axis must be nonnegative: sample {index}')
            if complex_:
                magnitude = math.hypot(*parts)
                if not math.isfinite(magnitude):
                    raise BridgeError(f"Nonfinite dataset magnitude: {record['name']}[{index}]")
                if selected and index < max_points:
                    values.append(dict(real=parts[0], imag=parts[1], magnitude=magnitude))
            elif selected and index < max_points:
                values.append(parts[0])
        cursor = stop
        sample_count += count
        record.update(truncated=count > max_points, data=values)
    if cursor != len(data):
        raise BridgeError('Unsupported trailing dataset payload')
    return dict(format='QucsData-v2', dataset_sha256=_sha(data), variables=selected_records,
                truncated=any(record['truncated'] for record in selected_records),
                validation={'structure': 'valid', 'semantics': 'valid', 'numeric_quality': 'finite',
                            'all_samples_checked': True, 'samples_checked': sample_count,
                            'hb_spectrum': frequency is not None and bool(hb_voltages)})
