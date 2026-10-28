"""Cooperative bridge-client locks; native editors do not participate."""
from contextlib import contextmanager
from functools import wraps
import hashlib
from pathlib import Path
import tempfile
import threading
from filelock import FileLock, Timeout

_guard = threading.Lock()
_locks = {}

@contextmanager
def file_lock(path, timeout=15):
    canonical = str(Path(path).resolve()).casefold() if __import__('os').name == 'nt' else str(Path(path).resolve())
    with _guard:
        lock = _locks.get(canonical)
        if lock is None:
            folder = Path(tempfile.gettempdir())/'usimmics-bridge-locks'
            folder.mkdir(exist_ok=True)
            lock = FileLock(folder/(hashlib.sha256(canonical.encode()).hexdigest()+'.lock'))
            _locks[canonical] = lock
    with lock.acquire(timeout=timeout):
        yield

def locked_write(fn):
    @wraps(fn)
    def wrapped(self, path, *args, **kwargs):
        from .core import BridgeError
        # Scope resolution precedes all lock-file or project writes.
        resolved = self.target(str(path)) if hasattr(self, 'target') else self._path(str(path))
        try:
            with file_lock(resolved):
                return fn(self, path, *args, **kwargs)
        except Timeout as exc:
            raise BridgeError('Timed out waiting for another bridge writer') from exc
    return wrapped
