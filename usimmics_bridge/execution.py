"""Bounded worker execution and cancellable native processes.

The MCP event loop never waits synchronously for a solver. One solver runs at a
time per server; queued requests remain cancellable. Logs go straight to disk.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

import anyio

from .core import BridgeError


class ExecutionCancelled(BridgeError):
    """A cancelled operation, optionally with a durable partial result."""

    def __init__(self, message="Operation cancelled", result=None):
        super().__init__(message)
        self.result = result


class ExecutionTimeout(BridgeError):
    """A native process exceeded its execution deadline and was reaped."""


class ExecutionControl:
    def __init__(self, on_progress=None):
        self.cancelled = threading.Event()
        self.on_progress = on_progress
        self._progress = {"completed": 0, "total": 1, "message": "Queued"}

    def cancel(self):
        self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise ExecutionCancelled()

    def progress(self, completed=None, total=None, message="Running", **details):
        self._progress.update(message=message, **details)
        if completed is not None:
            self._progress["completed"] = completed
        if total is not None:
            self._progress["total"] = total
        if self.on_progress:
            self.on_progress(dict(self._progress))

    @contextmanager
    def bind(self):
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)


_CURRENT = ContextVar("usimmics_execution_control", default=None)
_SOLVER_SLOT = threading.BoundedSemaphore(1)
_WORKERS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="usimmics")
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = set()


def current_control():
    return _CURRENT.get() or ExecutionControl()


@dataclass(frozen=True)
class ProcessOutcome:
    returncode: int
    output_tail: str
    elapsed: float


def _terminate_tree(process):
    """Terminate this invocation and its descendants, then reap the parent."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_process(command, cwd, log_path, timeout, control=None):
    """Run a bounded native invocation; timeout excludes waiting for the slot."""
    control = control or current_control()
    control.progress(message="Waiting for solver", stage="queued")
    while not _SOLVER_SLOT.acquire(timeout=0.05):
        control.check()
    process = None
    started = time.monotonic()
    try:
        control.check()
        log_path = Path(log_path)
        with log_path.open("wb") as log:
            options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)
                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if os.name == "nt" else {"start_new_session": True}
            process = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, **options)
            control.progress(message="Solver running", stage="running", pid=process.pid)
            while process.poll() is None:
                if control.cancelled.wait(0.05):
                    _terminate_tree(process)
                    log.write(b"\nCANCELLED\n")
                    raise ExecutionCancelled(f"Simulation cancelled; log: {log_path}")
                if time.monotonic() - started >= timeout:
                    _terminate_tree(process)
                    log.write(b"\nTIMEOUT\n")
                    raise ExecutionTimeout(f"Simulation timed out; log: {log_path}")
            control.check()
        with log_path.open("rb") as stream:
            stream.seek(max(0, log_path.stat().st_size - 4000))
            tail = stream.read().decode("utf-8", errors="replace")
        return ProcessOutcome(process.returncode, tail, time.monotonic() - started)
    finally:
        try:
            if process is not None and process.poll() is None:
                _terminate_tree(process)
        finally:
            _SOLVER_SLOT.release()


async def _finish_waiting(future):
    """Repeated cancellation must not abandon a running native worker."""
    with anyio.CancelScope(shield=True):
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            future.result()
        except (Exception, asyncio.CancelledError):
            pass


async def run_async(function, *, control=None, context=None):
    """Offload a callable(control), forward progress, and wait for cancellation cleanup."""
    control = control or ExecutionControl()
    with _ACTIVE_LOCK:
        if len(_ACTIVE) >= 8:
            raise BridgeError("Too many active operations (limit 8); wait or cancel a job")
        _ACTIVE.add(control)
    loop = asyncio.get_running_loop()
    updates = asyncio.Queue()
    previous = control.on_progress

    def progress(update):
        if previous:
            previous(update)
        if context is not None:
            loop.call_soon_threadsafe(updates.put_nowait, update)

    control.on_progress = progress

    async def report():
        while (update := await updates.get()) is not None:
            try:
                await asyncio.wait_for(context.report_progress(
                    update["completed"], update["total"], update["message"]), timeout=2)
            except Exception:
                # A disconnected client must not prevent native process cleanup.
                pass

    reporter = asyncio.create_task(report()) if context is not None else None

    def work():
        with control.bind():
            control.check()
            return function(control)

    worker = loop.run_in_executor(_WORKERS, work)
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        control.cancel()
        await _finish_waiting(worker)
        raise
    finally:
        try:
            control.on_progress = previous
            if reporter is not None:
                reporter.cancel()
                await _finish_waiting(reporter)
        finally:
            with _ACTIVE_LOCK:
                _ACTIVE.discard(control)


def cancel_active():
    with _ACTIVE_LOCK:
        for control in _ACTIVE:
            control.cancel()
