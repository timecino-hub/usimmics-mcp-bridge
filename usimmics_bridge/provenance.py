"""Dataset-bound provenance and atomic run metadata.

Hashes detect stale/accidentally changed artifacts. They do not authenticate a
manifest against an actor who can rewrite both the manifest and project files.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
import threading

from .core import BridgeError, _sha


SCHEMA_VERSION = 1
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_banner_guard = threading.Lock()
_banners = {}


def dataset_sidecar(dataset: Path) -> Path:
    return dataset.with_name(dataset.name + '.manifest.json')


def manifest_bytes(info: dict) -> bytes:
    return (json.dumps(info, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def write_manifest(path: Path, info: dict) -> None:
    """Atomically replace metadata in an already-authorized run directory."""
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(manifest_bytes(info))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _simulator_key(path: Path):
    stat = path.stat()
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size


def remember_simulator_banner(path: Path, banner: str) -> None:
    try:
        key = _simulator_key(path)
    except OSError:
        return
    with _banner_guard:
        _banners[key] = banner


def simulator_metadata(path: Path) -> dict:
    """Reuse a doctor/environment query; never launch an extra version process."""
    from .config import parse_simulator_version

    try:
        key = _simulator_key(path)
    except OSError:
        key = None
    with _banner_guard:
        banner = _banners.get(key)
    version = parse_simulator_version(banner) if banner else None
    return {'path': str(path), 'version': version, 'banner': banner,
            'version_source': 'environment_status_cache' if banner else 'not_queried',
            'size_bytes': key[2] if key else None, 'modified_ns': key[1] if key else None}


def inspect_provenance(workbench, dataset: Path, dataset_sha256: str, *,
                       source_path: Path | None = None, source_sha256: str | None = None) -> dict:
    """Resolve a dataset sidecar first, with legacy run-manifest compatibility.

    An existing but invalid/incomplete sidecar always wins over a legacy
    manifest. A reader must never silently fall back to older successful data.
    """
    sidecar = dataset_sidecar(dataset)
    explicit = sidecar.exists() or sidecar.is_symlink()
    candidate = sidecar if explicit else dataset.parent / 'manifest.json'
    result = {'state': 'unverified', 'verified': False, 'manifest_path': None,
              'legacy': False, 'reason': 'Dataset provenance is not recorded', 'manifest': None}
    if not candidate.exists() and not candidate.is_symlink():
        return result
    result['manifest_path'] = str(candidate)
    try:
        candidate = workbench._path(str(candidate), '.json')
        with candidate.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise BridgeError('Provenance manifest exceeds 1 MiB')
        info = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(info, dict):
            raise BridgeError('Provenance manifest must be an object')
        result['manifest'] = info
        legacy = 'schema_version' not in info and not explicit
        result['legacy'] = legacy
        if not legacy and (type(info.get('schema_version')) is not int
                           or info['schema_version'] != SCHEMA_VERSION):
            raise BridgeError('Unsupported provenance schema version')
        status = info.get('status', 'success' if legacy else None)
        if status in ('running', 'publishing'):
            result.update(state='incomplete', reason='Dataset production or publication is incomplete')
            return result
        if status in ('failed', 'timed_out', 'cancelled'):
            result.update(state='failed', reason='Dataset belongs to an unsuccessful run')
            return result
        if status != 'success':
            raise BridgeError('Provenance does not record a successful result')
        for field in ('source_sha256', 'dataset_sha256'):
            if not isinstance(info.get(field), str) or not _HASH.fullmatch(info[field]):
                raise BridgeError(f'Invalid provenance field: {field}')
        declared_source = info.get('source')
        if not isinstance(declared_source, str) or not Path(declared_source).is_absolute():
            raise BridgeError('Provenance source must be an absolute schematic path')
        if not legacy:
            declared_dataset = info.get('dataset')
            if not isinstance(declared_dataset, str) or not Path(declared_dataset).is_absolute():
                raise BridgeError('Provenance dataset must be an absolute path')
            if Path(declared_dataset).resolve() != dataset.resolve():
                raise BridgeError('Provenance is bound to a different dataset path')
        if info['dataset_sha256'] != dataset_sha256:
            result.update(state='stale_or_modified', reason='Dataset hash does not match its provenance')
            return result
        source = workbench._path(declared_source, '.sch')
        if source_path is not None and source != source_path.resolve():
            result.update(state='stale_or_modified', reason='Dataset belongs to a different schematic')
            return result
        actual_source_sha256 = source_sha256 if source_sha256 is not None else _sha(source.read_bytes())
        if actual_source_sha256 != info['source_sha256']:
            result.update(state='stale_or_modified', reason='Schematic has changed since this dataset was produced')
            return result
        result.update(state='matches_source', verified=True, reason=None)
        return result
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        result.update(state='invalid_provenance', reason=str(exc))
        return result


def provenance_summary(result: dict) -> dict:
    info = result.get('manifest') or {}
    return {key: value for key, value in result.items() if key != 'manifest'} | {
        'source': info.get('source'), 'source_sha256': info.get('source_sha256'),
        'run_id': info.get('run_id'), 'status': info.get('status'),
        'schema_version': info.get('schema_version')}
