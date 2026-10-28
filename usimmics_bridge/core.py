"""Validated subset of the uSimmics 5.9 schematic and binary dataset formats.

This deliberately is not a general QUCS compiler. Unsupported components,
versions and dataset types are rejected instead of guessed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
from .locking import locked_write
from .component_specs import COMPONENT_SPECS, SUPPORTED, NAMES
from .schemas import BridgeConfig, normalize_edits


class BridgeError(ValueError):
    """A request cannot be performed within the supported interface."""


@dataclass(frozen=True)
class SolverOutcome:
    returncode: int
    output_tail: str


PROP = re.compile(r'"([^"\r\n]*)"([01])')
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    return value


def _parse(text: str):
    if not isinstance(text, str) or len(text.encode('utf-8')) > 2*1024*1024 or len(text.splitlines()) > 10000:
        raise BridgeError('Schematic exceeds input limits')
    if any(ord(ch) < 32 and ch not in '\r\n\t' for ch in text):
        raise BridgeError('Control characters are not supported')
    if not text.splitlines() or text.splitlines()[0] != "<QucsStudio Schematic 5.9>":
        raise BridgeError("Only QucsStudio/uSimmics schematic format 5.9 is supported")
    lines = text.splitlines()
    last = 0
    for section in ['Properties','Symbol','Components','Wires','Diagrams','Paintings']:
        a,b='<'+section+'>','</'+section+'>'
        if lines.count(a)!=1 or lines.count(b)!=1:
            raise BridgeError('Missing or duplicate section: '+section)
        i,j=lines.index(a),lines.index(b)
        if i<last or j<=i: raise BridgeError('Invalid section order: '+section)
        last=j+1
    if any(row.strip() for row in lines[last:]):
        raise BridgeError('Unexpected text after Paintings section')
    # The 5.9 GUI treats an empty diagram record as a premature terminator.
    # A solver-only check misses this because diagrams are absent from netlists.
    if lines.count('<Diagrams>') != 1 or lines.count('</Diagrams>') != 1:
        raise BridgeError('Missing or duplicate Diagrams section')
    ds, de = lines.index('<Diagrams>'), lines.index('</Diagrams>')
    if ds >= de:
        raise BridgeError('Diagrams section is not closed correctly')
    current = None
    for row in lines[ds + 1:de]:
        stripped = row.strip()
        if not stripped:
            raise BridgeError('Blank diagram record is incompatible with the uSimmics 5.9 GUI')
        if current is None:
            match = re.fullmatch(r'<Rect ([^"<>]+) "([^"<>]*)" "([^"<>]*)" "([^"<>]*)">', stripped)
            if not match:
                raise BridgeError('Unsupported or malformed Rect diagram opening record')
            fields=match[1].split()
            if len(fields)!=23 or not re.fullmatch(r'#[0-9a-fA-F]{6}',fields[5]):
                raise BridgeError('Malformed Rect header fields')
            try:
                numbers=[float(v) for i,v in enumerate(fields) if i!=5]
                if not all(math.isfinite(v) for v in numbers): raise ValueError()
                if int(fields[2])<=0 or int(fields[3])<=0: raise ValueError()
            except ValueError as exc:
                raise BridgeError('Invalid Rect geometry or axis field') from exc
            current = 'Rect'
        elif stripped.startswith('</'):
            if stripped != '</' + current + '>':
                raise BridgeError('Mismatched diagram closing record')
            current = None
        elif re.fullmatch(r'<Legend -?\d+ -?\d+ [01]>', stripped):
            pass
        elif re.fullmatch(r'<"[^"<>]+" "[^"<>]*" #[0-9a-fA-F]{6} (?:-?\d+ ){5}-?\d+ "[^"<>]*">', stripped):
            pass
        else:
            raise BridgeError('Unsupported or malformed diagram record: '+stripped[:80])
    if current is not None:
        raise BridgeError('Diagram field is not closed: ' + current)
    try:
        start, end = lines.index("<Components>"), lines.index("</Components>")
        ws, we = lines.index("<Wires>"), lines.index("</Wires>")
    except ValueError as exc:
        raise BridgeError("Missing schematic Components or Wires section") from exc
    components = []
    seen = set()
    for line_no in range(start + 1, end):
        line = lines[line_no]
        if not line.strip():
            continue
        fields = line.split(maxsplit=8)
        if len(fields) < 8:
            raise BridgeError(f"Malformed component at line {line_no + 1}")
        kind, name = fields[:2]
        try:
            active, x, y, tx, ty, orientation = map(int, fields[2:8])
        except ValueError as exc:
            raise BridgeError(f"Malformed geometry for {name}") from exc
        if active not in (0, 1):
            raise BridgeError(f"Unsupported component state for {name}: {active}")
        if kind != "GND" and (not IDENT.fullmatch(name) or name in seen):
            raise BridgeError(f"Invalid or duplicate component name: {name}")
        seen.add(name)
        tail = fields[8] if len(fields) == 9 else ""
        props = list(PROP.finditer(tail))
        if PROP.sub("", tail).strip():
            raise BridgeError(f"Unsupported property encoding for {name}")
        spec = COMPONENT_SPECS.get(kind)
        if spec is not None:
            try:
                spec.validate_properties(kind, [m[1] for m in props])
            except ValueError as exc:
                raise BridgeError(str(exc)) from exc
        if any('\\' in m[1] or any(ord(ch)<32 for ch in m[1]) for m in props):
            raise BridgeError('Unsupported property escape/control character')
        if orientation not in (0,1,2,3):
            raise BridgeError('Unsupported component orientation')
        components.append(dict(type=kind, name=name, active=active, x=x, y=y,
                               orientation=orientation, properties=[m[1] for m in props],
                               line=line_no, prefix=" ".join(fields[:8]),
                               flags=[m[2] for m in props]))
    wires = []
    for line_no in range(ws + 1, we):
        line = lines[line_no]
        if not line.strip():
            continue
        fields = line.split(maxsplit=4)
        if len(fields)<4: raise BridgeError('Malformed wire coordinates')
        try:
            x1, y1, x2, y2 = map(int, fields[:4])
        except (ValueError, TypeError) as exc:
            raise BridgeError(f"Malformed wire at line {line_no + 1}") from exc
        if x1 != x2 and y1 != y2 or (x1, y1) == (x2, y2):
            raise BridgeError("Only nonzero orthogonal wire segments are supported")
        labels = re.findall(r'"([^"\r\n]*)"', fields[4]) if len(fields) > 4 else []
        if len(fields)>4 and not re.fullmatch(r'"[^"\r\n]*" -?\d+ -?\d+ -?\d+ "[^"\r\n]*"', fields[4]):
            raise BridgeError('Malformed wire label record')
        label = labels[0] if labels else ""
        if label and not IDENT.fullmatch(label):
            raise BridgeError(f"Unsupported net label: {label}")
        wires.append(((x1, y1), (x2, y2), label))
    if len(components)+len(wires)>2000:
        raise BridgeError('Schematic exceeds 2000 components plus wires')
    return lines, components, wires


def _compile(text: str) -> str:
    _, components, wires = _parse(text)
    active = [c for c in components if c["active"]]
    if not active:
        raise BridgeError("No active components")
    unknown = sorted({c["type"] for c in active} - SUPPORTED)
    if unknown:
        raise BridgeError(f"Unsupported components: {', '.join(unknown)}")
    if sum(c["type"] == ".HB" for c in active) != 1:
        raise BridgeError("Exactly one active Harmonic Balance block is required")
    points, pins, ground = set(), {}, set()
    for a, b, _ in wires:
        points.update((a, b))
    for c in active:
        kind, name, x, y, rot = (c[k] for k in ("type", "name", "x", "y", "orientation"))
        if rot not in (0, 1, 2, 3):
            raise BridgeError(f"Unsupported rotation/mirror for {name}: {rot}")
        if kind == ".HB":
            pins[name] = []
            continue
        if kind == "GND":
            ground.add((x, y)); points.add((x, y)); continue
        result = COMPONENT_SPECS[kind].pins(x, y, rot)
        pins[name] = result
        points.update(result)
    parent = {p: p for p in points}
    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p
    def union(a, b):
        parent[find(a)] = find(b)
    def on(p, a, b):
        return (min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and
                min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))
    for a, b, _ in wires:
        for p in points:
            if on(p, a, b):
                union(a, p)
    label_points = {}
    for a, _, label in wires:
        if label:
            if label in label_points:
                union(a, label_points[label])
            label_points[label] = a
    if not ground:
        raise BridgeError("A ground reference is required")
    first_ground = next(iter(ground))
    for p in ground:
        union(first_ground, p)
    labels = {find(first_ground): "gnd"}
    for label, p in label_points.items():
        root = find(p)
        if root in labels and labels[root] != label:
            raise BridgeError(f"Conflicting net labels: {labels[root]} and {label}")
        labels[root] = label
    counts = {}
    for comp_pins in pins.values():
        for p in comp_pins:
            counts[find(p)] = counts.get(find(p), 0) + 1
    for name, comp_pins in pins.items():
        for p in comp_pins:
            root = find(p)
            if counts[root] < 2 and root != find(first_ground):
                raise BridgeError(f"Dangling pin on {name} at {p}")
    net_index = 0
    output = ["# Generated by usimmics-bridge; supported uSimmics 5.9 subset"]
    for c in active:
        if c["type"] == "GND":
            continue
        nets = []
        for p in pins[c["name"]]:
            root = find(p)
            if root not in labels:
                while f"_bridge_net{net_index}" in label_points:
                    net_index += 1
                labels[root] = f"_bridge_net{net_index}"; net_index += 1
            nets.append(labels[root])
        params = ' '.join('"' + v + '"' for v in c["properties"])
        output.append(' '.join([c["type"] + ':' + c["name"], *nets, params]).rstrip())
    return '\n'.join(output) + '\n'


class Bridge:
    def __init__(self, config_path: Path):
        self.config_path = Path(config_path).resolve()
        try:
            config = BridgeConfig.model_validate(json.loads(self.config_path.read_text(encoding="utf-8-sig")))
            self.installation = Path(config.installation_root).resolve()
            self.roots = [Path(p).resolve() for p in config.project_roots]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise BridgeError(f"Invalid bridge configuration: {exc}") from exc
        if not self.roots:
            raise BridgeError("At least one project root is required")
        self.simulator = self.installation / "bin" / "simulator.exe"

    def _path(self, value, suffix=None):
        p = Path(value).expanduser()
        if not p.is_absolute():
            raise BridgeError("An absolute path is required")
        p = p.resolve()
        if not any(p.is_relative_to(root) for root in self.roots):
            raise BridgeError("Path is outside configured project roots")
        if suffix and p.suffix.lower() != suffix:
            raise BridgeError(f"Expected a {suffix} file")
        if not p.is_file():
            raise BridgeError(f"File not found: {p}")
        if p.suffix.lower()=='.sch' and p.stat().st_size>2*1024*1024:
            raise BridgeError('Schematic exceeds 2 MiB')
        return p

    def environment_status(self) -> dict:
        lib = self.installation / "library" / "B_Schottky.lib"
        status = dict(installation_root=str(self.installation), simulator=str(self.simulator),
                      simulator_exists=self.simulator.is_file(),
                      gui_exists=(self.installation / "bin" / "uSimmics.exe").is_file(),
                      project_roots=[str(p) for p in self.roots],
                      bat54_available=lib.is_file() and "<Component BAT54>" in lib.read_text(errors="replace"),
                      supported_schematic_version="5.9", supported_components=sorted(SUPPORTED))
        if self.simulator.is_file():
            try:
                p = subprocess.run([str(self.simulator), "--version"], capture_output=True,
                                   timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                status["simulator_banner"] = (p.stdout + p.stderr).decode("utf-8", errors="replace").strip()
                status["simulator_returncode"] = p.returncode
                if p.returncode == 0:
                    from .provenance import remember_simulator_banner
                    remember_simulator_banner(self.simulator, status["simulator_banner"])
            except (OSError, subprocess.TimeoutExpired) as exc:
                status["simulator_error"] = str(exc)
        return status

    def list_projects(self) -> dict:
        files = set()
        for root in self.roots:
            if root.is_dir():
                for p in root.rglob("*.sch"):
                    if p.resolve().is_relative_to(root) and not {".usimmics-runs", ".usimmics-sweeps", ".usimmics-previews"}.intersection(p.parts):
                        files.add(str(p.resolve()))
        return {"project_roots": [str(p) for p in self.roots], "schematics": sorted(files)}

    def inspect_schematic(self, path: str) -> dict:
        p = self._path(path, ".sch")
        data = p.read_bytes()
        _, components, wires = _parse(data.decode("utf-8-sig"))
        result = []
        for c in components:
            known = NAMES.get(c["type"], [])
            result.append(dict(name=c["name"], type=c["type"], active=bool(c["active"]),
                               supported=c["type"] in SUPPORTED, position=[c["x"], c["y"]],
                               orientation=c["orientation"], properties=[dict(index=i, name=known[i] if i < len(known) else None, value=v)
                               for i, v in enumerate(c["properties"])]))
        return dict(path=str(p), sha256=_sha(data), components=result, wire_count=len(wires))

    @locked_write
    def update_parameters(self, path: str, edits: list[dict], expected_sha256: str) -> dict:
        p = self._path(path, ".sch")
        original = p.read_bytes()
        if _sha(original) != expected_sha256:
            raise BridgeError("Schematic changed since inspection; inspect again")
        try:
            edits = normalize_edits(edits)
        except ValueError as exc:
            raise BridgeError(f'Invalid parameter edits: {exc}') from exc
        lines, components, _ = _parse(original.decode("utf-8-sig"))
        by_name = {c["name"]: c for c in components if c["type"] != "GND"}
        original_source_freqs={c['name']:c['properties'][1] for c in components if c['type']=='Vac' and c['active']}
        touched, changes, frequencies = set(), [], {}
        for edit in edits:
            try:
                c = by_name[edit["component"]]
                param = edit.get("parameter", edit.get("index"))
                if isinstance(param, str):
                    param = NAMES.get(c["type"], []).index(param)
                if isinstance(param, bool) or not isinstance(param, int) or not 0 <= param < len(c["properties"]):
                    raise BridgeError("Invalid property index")
                value = edit["value"]
                if not isinstance(value, str) or not value or any(x in value for x in ('"', '\n', '\r', '\x00', '\\')):
                    raise BridgeError("Parameter must be a nonempty single-line string without quotes or backslashes")
                if c["type"] not in SUPPORTED:
                    raise BridgeError("Cannot edit unsupported component")
                key = (c["name"], param)
                if key in touched:
                    raise BridgeError("Duplicate parameter edit")
                touched.add(key)
                old = c["properties"][param]
                c["properties"][param] = value
                changes.append(dict(component=c["name"], index=param, before=old, after=value))
                if c["type"] == "Vac" and param == 1:
                    frequencies[old] = value
            except (KeyError, ValueError, TypeError) as exc:
                if isinstance(exc, BridgeError):
                    raise
                raise BridgeError(f"Invalid parameter edit: {edit}") from exc
        new_source_freqs={c['name']:c['properties'][1] for c in components if c['type']=='Vac' and c['active']}
        for c in components:
            if c["type"] == ".HB" and frequencies:
                if (c["name"], 0) in touched:
                    raise BridgeError("Edit source frequency and HB harmonics in separate requests")
                parts=c['properties'][0].split(';')
                counts={s.split('@',1)[1]:int(s.split('@',1)[0]) for s in parts if '@' in s and s.split('@',1)[1]}
                merged={}
                for name,new_freq in new_source_freqs.items():
                    count=counts.get(original_source_freqs[name],16)
                    merged[new_freq]=max(merged.get(new_freq,0),count)
                c['properties'][0]=parts[0]+';'+''.join(str(n)+'@'+f+';' for f,n in merged.items())
            lines[c["line"]] = c["prefix"] + (' ' if c["properties"] else '') + ''.join(
                '"' + v + '"' + flag for v, flag in zip(c["properties"], c["flags"]))
        updated = ('\n'.join(lines).rstrip() + '\n').encode('utf-8')
        _compile(updated.decode('utf-8'))
        if p.read_bytes() != original:
            raise BridgeError("Schematic changed while preparing update")
        backup = p.with_name(p.name + '.bak.' + uuid4().hex)
        backup.write_bytes(original)
        fd, temp = tempfile.mkstemp(prefix=p.name + '.', suffix='.tmp', dir=p.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(updated); stream.flush(); os.fsync(stream.fileno())
            if p.read_bytes() != original:
                raise BridgeError("Schematic changed while saving update")
            os.replace(temp, p)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return dict(path=str(p), backup=str(backup), sha256=_sha(updated), changes=changes,
                    hb_frequency_keys_synchronized=bool(frequencies), gui_reload_required=True)

    def _execute_solver(self, netpath: Path, dataset: Path, log: Path, timeout: int):
        """Run without blocking MCP, retaining logs and terminating cancelled processes."""
        from .execution import run_process
        return run_process([str(self.simulator), str(netpath), str(dataset)],
                           netpath.parent, log, timeout)

    def run_schematic(self, path: str, timeout: int = 60) -> dict:
        from . import __version__
        from .execution import ExecutionCancelled, ExecutionTimeout, current_control
        from .provenance import SCHEMA_VERSION, dataset_sidecar, simulator_metadata, write_manifest

        p = self._path(path, ".sch")
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
            raise BridgeError("timeout must be an integer from 1 to 300 seconds")
        if not self.simulator.is_file():
            raise BridgeError("Configured uSimmics simulator.exe is missing")
        source = p.read_bytes()
        net = _compile(source.decode('utf-8-sig'))
        runs = p.parent / '.usimmics-runs'
        runs.mkdir(exist_ok=True)
        if not any(runs.resolve().is_relative_to(root) for root in self.roots):
            raise BridgeError("Run directory resolves outside configured project roots")
        run = runs / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8])
        run.mkdir()
        netpath, dataset, log = run / 'circuit.net', run / 'result.dat', run / 'simulation.log'
        snapshot = run / 'source.sch'
        snapshot.write_bytes(source)
        netpath.write_bytes(net.encode('utf-8'))
        manifest = run / 'manifest.json'
        sidecar = dataset_sidecar(dataset)
        started_clock = time.monotonic()
        info = {'schema_version': SCHEMA_VERSION, 'kind': 'simulation', 'run_id': run.name,
                'status': 'running', 'source': str(p), 'source_sha256': _sha(source),
                'source_snapshot': str(snapshot), 'source_snapshot_sha256': _sha(source),
                'dataset': str(dataset), 'dataset_sha256': None,
                'netlist': str(netpath), 'netlist_sha256': _sha(netpath.read_bytes()),
                'log': str(log), 'bridge_version': __version__,
                'simulator': simulator_metadata(self.simulator),
                'started_at': datetime.now(timezone.utc).isoformat(),
                'completed_at': None, 'duration_seconds': None, 'exit_code': None,
                'validation': None, 'error': None}
        write_manifest(manifest, info)
        write_manifest(sidecar, info)
        current_control().progress(message="Preparing simulation", manifest_path=str(manifest), log_path=str(log))

        def finish(status, *, error=None, exit_code=None, result=None):
            info.update(status=status, completed_at=datetime.now(timezone.utc).isoformat(),
                        duration_seconds=time.monotonic() - started_clock,
                        exit_code=exit_code, error=error)
            if result is not None:
                info.update(dataset_sha256=result['dataset_sha256'], validation=result['validation'])
            # Until the dataset-bound sidecar is replaced, readers still see
            # running/failed rather than falling back to this run manifest.
            write_manifest(manifest, info)
            write_manifest(sidecar, info)

        proc = None
        try:
            proc = self._execute_solver(netpath, dataset, log, timeout)
            if proc.returncode != 0 or not dataset.is_file() or dataset.stat().st_size == 0:
                raise BridgeError(f"Simulation failed (exit {proc.returncode}); log: {log}: " + proc.output_tail)
            # max_points bounds response size only: the reader validates every
            # value, including invalid samples later in an otherwise valid file.
            _, result = self._read_results_data(str(dataset), max_points=1, require_hb=True)
            current_control().check()
        except ExecutionCancelled as exc:
            finish('cancelled', error={'code': 'cancelled', 'message': str(exc)})
            raise
        except (subprocess.TimeoutExpired, ExecutionTimeout) as exc:
            finish('timed_out', error={'code': 'timeout', 'message': str(exc)})
            raise BridgeError(f"Simulation timed out; log: {log}") from exc
        except OSError as exc:
            finish('failed', error={'code': 'io_error', 'message': str(exc)},
                   exit_code=proc.returncode if proc is not None else None)
            raise BridgeError(f"Simulation I/O failed; log: {log}: {exc}") from exc
        except BridgeError as exc:
            finish('failed', error={'code': 'invalid_results' if proc is not None and proc.returncode == 0
                                   else 'solver_failed', 'message': str(exc)},
                   exit_code=proc.returncode if proc is not None else None)
            raise
        finish('success', exit_code=proc.returncode, result=result)
        return dict(status='success', source=str(p), source_sha256=_sha(source),
                    netlist=str(netpath), dataset=str(dataset), log=str(log),
                    dataset_sha256=result['dataset_sha256'], run_id=run.name,
                    manifest=str(manifest), dataset_manifest=str(sidecar), source_snapshot=str(snapshot),
                    duration_seconds=info['duration_seconds'], started_at=info['started_at'],
                    completed_at=info['completed_at'], validation=result['validation'],
                    simulator=info['simulator'],
                    exit_code=proc.returncode, variables=[v['name'] for v in result['variables']],
                    scope='uSimmics 5.9 supported schematic subset')

    def _read_results_data(self, path: str, max_points: int = 2000, *, require_hb: bool = False,
                           selected_names: set[str] | None = None):
        from .datasets import parse_dataset, read_dataset_bytes
        from .provenance import inspect_provenance, provenance_summary

        p = self._path(path, '.dat')
        data = read_dataset_bytes(p)
        result = parse_dataset(data, max_points, require_hb=require_hb, selected_names=selected_names)
        provenance = inspect_provenance(self, p, result['dataset_sha256'])
        return data, {'path': str(p), **result, 'provenance': provenance_summary(provenance),
                      'warnings': [] if provenance['verified'] else [provenance['reason']]}

    def read_results(self, path: str, max_points: int = 2000) -> dict:
        return self._read_results_data(path, max_points)[1]
