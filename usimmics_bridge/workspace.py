"""Disk workspace and preview operations; no GUI state or UI automation."""
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from .core import Bridge, BridgeError, _parse, _compile, _sha
from .locking import locked_write


class Workbench(Bridge):
    def environment_status(self):
        return {**super().environment_status(), 'bridge_version': __import__('usimmics_bridge').__version__,
                'document_state': 'saved_on_disk', 'preview_renderer': 'Pillow bridge renderer',
                'capabilities': ['inspect','parameters','headless_simulation','results',
                                 'create_project','read','save','clone','validate','preview','publish_results',
                                 'build_schematic','parameter_sweep','compare_results']}

    def target(self, path, suffix=None):
        p = Path(path).expanduser()
        if not p.is_absolute():
            raise BridgeError('An absolute path is required')
        p = p.resolve()
        if not any(p.is_relative_to(root) for root in self.roots):
            raise BridgeError('Path is outside configured project roots')
        if suffix and p.suffix.lower() != suffix:
            raise BridgeError('Expected a ' + suffix + ' file')
        return p

    def create_project(self, name, root=None):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
            raise BridgeError('Use an alphanumeric project name with underscores or hyphens')
        base = Path(root).resolve() if root else self.roots[0]
        if base not in self.roots:
            raise BridgeError('Choose one of the configured project roots')
        path = self.target(str(base / (name if name.endswith('_prj') else name + '_prj')))
        if not base.is_dir():
            raise BridgeError('Project root does not exist')
        path.mkdir(exist_ok=False)
        return {'project_path': str(path)}

    def read_schematic(self, path):
        p = self._path(path, '.sch')
        raw = p.read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise BridgeError('Schematic exceeds 2 MiB')
        # Raw read is intentional: it also permits diagnosing a malformed document.
        return {'path': str(p), 'sha256': _sha(raw),
                'content': raw.decode('utf-8-sig'), 'state': 'saved_on_disk'}

    @staticmethod
    def check_text(content):
        if len(content.encode('utf-8')) > 2 * 1024 * 1024:
            raise BridgeError('Schematic exceeds 2 MiB')
        lines = content.splitlines()
        if len(lines) > 10000:
            raise BridgeError('Schematic exceeds 10000 lines')
        sections = ['Properties', 'Symbol', 'Components', 'Wires', 'Diagrams', 'Paintings']
        last = 0
        for section in sections:
            a, b = '<'+section+'>', '</'+section+'>'
            if lines.count(a) != 1 or lines.count(b) != 1:
                raise BridgeError('Missing or duplicate section: '+section)
            i, j = lines.index(a), lines.index(b)
            if i < last or j <= i:
                raise BridgeError('Invalid section order: '+section)
            last = j + 1
        if any(x.strip() for x in lines[last:]):
            raise BridgeError('Unexpected text after Paintings section')
        _, comps, wires = _parse(content)
        if len(comps) + len(wires) > 2000:
            raise BridgeError('Preview/compiler limit: 2000 components plus wires')
        netlist = _compile(content)
        warnings = []
        active_freqs = {c['properties'][1] for c in comps
                        if c['type'] == 'Vac' and c['active'] and len(c['properties']) > 1}
        for c in comps:
            if c['type'] == '.HB' and c['active']:
                keys = {s.split('@', 1)[1] for s in c['properties'][0].split(';') if '@' in s}
                missing = active_freqs - keys
                if missing:
                    warnings.append('HB has no explicit harmonic count for: '+', '.join(sorted(missing)))
        painting = content.split('<Paintings>', 1)[1].split('</Paintings>', 1)[0].strip()
        if painting:
            warnings.append('Preview omits Paintings annotations; original source is preserved')
        return {'valid': True, 'component_count': len(comps), 'wire_count': len(wires),
                'netlist': netlist, 'warnings': warnings,
                'validation_scope': 'supported 5.9 syntax, diagram closure, connectivity; not native GUI rendering'}

    def validate_schematic(self, path):
        read = self.read_schematic(path)
        try:
            result = self.check_text(read['content'])
        except (ValueError, IndexError) as exc:
            result = {'valid': False, 'errors': [str(exc)]}
        return {**result, 'path': read['path'], 'sha256': read['sha256']}

    @locked_write
    def atomic_write(self, path, data, expected_sha256=None):
        p = self.target(str(path))
        if not p.parent.is_dir():
            raise BridgeError('Parent directory does not exist; create a project first')
        existed = p.exists()
        if existed and not p.is_file():
            raise BridgeError('Target is not a file')
        original = p.read_bytes() if existed else None
        if existed and (expected_sha256 is None or _sha(original) != expected_sha256):
            raise BridgeError('Existing destination requires its current SHA256; read it again')
        if not existed and expected_sha256 is not None:
            raise BridgeError('Destination does not exist; omit expected_sha256')
        backup = None
        fd, temp = tempfile.mkstemp(prefix=p.name+'.', suffix='.tmp', dir=p.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); stream.flush(); os.fsync(stream.fileno())
            if original is not None:
                if not p.exists() or p.read_bytes() != original:
                    raise BridgeError('Destination changed during save')
                backup = p.with_name(p.name+'.bak.'+uuid4().hex)
                backup.write_bytes(original)
                os.replace(temp, p)
            else:
                # rename fails if the destination appears concurrently (Windows).
                if os.name == 'nt':
                    os.rename(temp, p)
                else:
                    os.link(temp, p); os.unlink(temp)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        return {'path': str(p), 'sha256': _sha(data),
                'backup': str(backup) if backup else None, 'saved': True}

    def save_schematic(self, path, content, expected_sha256=None):
        p = self.target(path, '.sch')
        # Normalize transport encoding, but reject malformed diagram records instead of hiding them.
        text = content.lstrip('\ufeff').replace('\r\n','\n').replace('\r','\n').rstrip()+'\n'
        check = self.check_text(text)
        result = self.atomic_write(p, text.encode('utf-8'), expected_sha256)
        return {**result, 'warnings': check['warnings'], 'gui_reload_required': True}

    def clone_schematic(self, source, destination, expected_sha256=None):
        source_data = self.read_schematic(source)
        if self.target(destination, '.sch') == Path(source_data['path']):
            raise BridgeError('Clone destination must differ from source')
        result = self.save_schematic(destination, source_data['content'], expected_sha256)
        return {**result, 'source': source_data['path'], 'source_sha256': source_data['sha256']}

    @locked_write
    def publish_results(self, path, dataset_path, expected_sha256):
        p = self._path(path, '.sch')
        if _sha(p.read_bytes()) != expected_sha256:
            raise BridgeError('Schematic changed; cannot publish stale results')
        dataset = self._path(dataset_path, '.dat')
        manifest = self._path(str(dataset.parent/'manifest.json'), '.json')
        info = json.loads(manifest.read_text(encoding='utf-8'))
        if (info.get('source_sha256') != expected_sha256 or
                Path(info.get('source','')).resolve() != p or
                info.get('dataset_sha256') != _sha(dataset.read_bytes())):
            raise BridgeError('Dataset provenance does not match this schematic')
        self.read_results(str(dataset), 1)
        text = p.read_text(encoding='utf-8-sig')
        if not re.search(r'^DataSet=\*\.dat$', text, re.M):
            raise BridgeError('Publishing currently requires DataSet=*.dat')
        dest = self.target(str(p.with_suffix('.dat')), '.dat')
        old_hash = _sha(dest.read_bytes()) if dest.is_file() else None
        if _sha(p.read_bytes()) != expected_sha256:
            raise BridgeError('Schematic changed during publication')
        result = self.atomic_write(dest, dataset.read_bytes(), old_hash)
        return {**result, 'source_sha256': expected_sha256, 'gui_reload_required': True}

    def preview_schematic(self, path, dataset_path=None):
        from .preview import render
        p = self._path(path, '.sch')
        source = self.read_schematic(str(p))
        check = self.check_text(source['content'])
        result = None
        freshness = 'no_dataset'
        if dataset_path:
            datafile = self._path(dataset_path, '.dat')
            result = self.read_results(str(datafile), 10000)
            if result['truncated']:
                raise BridgeError('Dataset too large for preview (10000 points per variable)')
            freshness = 'unverified'
            manifest = datafile.parent/'manifest.json'
            if manifest.is_file():
                manifest = self._path(str(manifest), '.json')
                info = json.loads(manifest.read_text(encoding='utf-8'))
                freshness = 'matches_source' if (info.get('source_sha256') == source['sha256'] and
                    info.get('dataset_sha256') == _sha(datafile.read_bytes())) else 'stale_or_modified'
            if freshness != 'matches_source':
                check['warnings'].append('Dataset freshness: '+freshness)
        folder = self.target(str(p.parent/'.usimmics-previews'))
        folder.mkdir(exist_ok=True)
        output = folder/(p.stem+'-'+uuid4().hex[:10]+'.png')
        notes = {'unverified': 'dataset provenance is not recorded; it may not belong to this schematic',
                 'stale_or_modified': 'dataset does not match the saved schematic; rerun before trusting it'}
        rendered = render(source['content'], output, p.name, result, notes.get(freshness))
        return {'path': str(p), 'source_sha256': source['sha256'], 'image_path': str(output),
                'renderer': 'bridge schematic renderer, not a native GUI screenshot',
                'state': 'saved_on_disk', 'dataset_freshness': freshness,
                **rendered, 'warnings': check['warnings']+rendered['warnings']}
