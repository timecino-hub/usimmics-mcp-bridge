import asyncio
import json
import os
from pathlib import Path
import sys
import time
import threading

import pytest

from usimmics_bridge.core import BridgeError
from usimmics_bridge.execution import ExecutionCancelled, ExecutionControl, run_async, run_process
from usimmics_bridge.jobs import JobManager


async def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Condition did not become true")
        await asyncio.sleep(0.02)


def process_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.restype = wintypes.HANDLE
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not stat.exists() or stat.read_text().split(")", 1)[1].split()[0] != "Z"


def sleeping_command(tmp_path, child=False):
    pid_file = tmp_path / "processes.json"
    code = ("import json,os,time,sys,subprocess; from pathlib import Path; "
            "pids=[os.getpid()]; ")
    if child:
        code += "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); pids.append(p.pid); "
    code += "Path(sys.argv[1]).write_text(json.dumps(pids)); print('started',flush=True); time.sleep(30)"
    return [sys.executable, "-u", "-c", code, str(pid_file)], pid_file


def test_worker_yields_and_request_cancellation_reaps_process_tree(tmp_path):
    async def exercise():
        command, pid_file = sleeping_command(tmp_path, child=True)
        task = asyncio.create_task(run_async(lambda control: run_process(command, tmp_path, tmp_path / "run.log", 30)))
        await wait_until(lambda: pid_file.exists() and pid_file.stat().st_size > 0)
        pids = json.loads(pid_file.read_text())
        assert all(process_alive(p) for p in pids)
        # Reaching here while the worker is sleeping proves event-loop responsiveness.
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        await wait_until(lambda: not any(process_alive(p) for p in pids))
        assert "CANCELLED" in (tmp_path / "run.log").read_text()
    asyncio.run(exercise())


def test_timeout_reaps_process_and_keeps_disk_log(tmp_path):
    command, pid_file = sleeping_command(tmp_path)
    with pytest.raises(BridgeError, match="timed out"):
        run_process(command, tmp_path, tmp_path / "run.log", 1.0)
    assert not any(process_alive(p) for p in json.loads(pid_file.read_text()))
    assert "TIMEOUT" in (tmp_path / "run.log").read_text()


def test_queued_solver_cancels_without_starting(tmp_path):
    async def exercise():
        first, pid_file = sleeping_command(tmp_path)
        task = asyncio.create_task(run_async(lambda control: run_process(first, tmp_path, tmp_path / "first.log", 30)))
        await wait_until(lambda: pid_file.exists() and pid_file.stat().st_size > 0)
        queued_control = ExecutionControl()
        queued = asyncio.create_task(run_async(lambda control: run_process(first, tmp_path, tmp_path / "second.log", 30), control=queued_control))
        await asyncio.sleep(0.1)
        queued_control.cancel()
        with pytest.raises(ExecutionCancelled):
            await asyncio.wait_for(queued, 3)
        assert not (tmp_path / "second.log").exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(exercise())


def test_background_jobs_progress_cancel_limit_and_shutdown(tmp_path):
    async def exercise():
        jobs = JobManager()
        def work(control):
            control.progress(1, 2, "First point complete")
            while not control.cancelled.wait(0.02):
                pass
            control.check()
        started = [await jobs.start("test", work) for _ in range(4)]
        with pytest.raises(BridgeError, match="four"):
            await jobs.start("test", work)
        first = started[0]["job_id"]
        await wait_until(lambda: jobs.status(first)["progress"]["completed"] == 1)
        assert jobs.cancel(first)["status"] == "cancelling"
        await wait_until(lambda: jobs.status(first)["status"] == "cancelled")
        assert jobs.cancel(first)["status"] == "cancelled"
        await asyncio.wait_for(jobs.close(), 5)
        assert all(jobs.status(j["job_id"])["status"] == "cancelled" for j in started)
    asyncio.run(exercise())


def test_repeated_request_cancel_waits_for_worker_cleanup():
    async def exercise():
        started, cleanup, finish = threading.Event(), threading.Event(), threading.Event()
        control = ExecutionControl()
        def work(control):
            started.set()
            control.cancelled.wait(5)
            cleanup.set()
            finish.wait(5)
            control.check()
        task = asyncio.create_task(run_async(work, control=control))
        await wait_until(started.is_set)
        task.cancel()
        await wait_until(cleanup.is_set)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        from usimmics_bridge.execution import _ACTIVE
        assert control not in _ACTIVE
    asyncio.run(exercise())


def test_stuck_progress_consumer_does_not_hold_completed_worker():
    async def exercise():
        class Context:
            async def report_progress(self, *args):
                await asyncio.Event().wait()
        control = ExecutionControl()
        def work(control):
            control.progress(message="Running")
            return {"status": "success"}
        result = await asyncio.wait_for(run_async(work, control=control, context=Context()), 2)
        assert result["status"] == "success"
        from usimmics_bridge.execution import _ACTIVE
        assert control not in _ACTIVE
    asyncio.run(exercise())


def test_failed_process_cleanup_still_releases_solver_slot(tmp_path, monkeypatch):
    import usimmics_bridge.execution as execution
    control = ExecutionControl()
    slot = threading.BoundedSemaphore(1)
    class Process:
        pid = 123
        def poll(self):
            return None
    def start(*args, **kwargs):
        control.cancel()
        return Process()
    def denied(process):
        raise PermissionError("Controlled cleanup failure")
    monkeypatch.setattr(execution, "_SOLVER_SLOT", slot)
    monkeypatch.setattr(execution.subprocess, "Popen", start)
    monkeypatch.setattr(execution, "_terminate_tree", denied)
    with pytest.raises(PermissionError, match="Controlled"):
        run_process(["unused"], tmp_path, tmp_path / "log", 1, control)
    assert slot.acquire(blocking=False)
    slot.release()
