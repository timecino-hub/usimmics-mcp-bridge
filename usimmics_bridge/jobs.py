"""Session-scoped background jobs; durable artifacts remain in project folders."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import threading
from uuid import uuid4

from .core import BridgeError
from .execution import ExecutionCancelled, ExecutionControl, run_async


def _now():
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    def __init__(self):
        self._jobs = {}
        self._tasks = {}
        self._controls = {}
        self._lock = threading.Lock()

    def _update(self, job_id, **fields):
        with self._lock:
            self._jobs[job_id].update(fields)

    async def start(self, operation, function):
        with self._lock:
            active = sum(j["status"] in {"queued", "running", "cancelling"} for j in self._jobs.values())
            if active >= 4:
                raise BridgeError("At most four background jobs may be active")
            while len(self._jobs) >= 64:
                old = next((key for key, value in self._jobs.items()
                            if value["status"] in {"success", "partial", "failed", "cancelled"}), None)
                if old is None:
                    raise BridgeError("Job history is full")
                del self._jobs[old]
                self._tasks.pop(old, None)
                self._controls.pop(old, None)
            job_id = uuid4().hex
            self._jobs[job_id] = {"job_id": job_id, "operation": operation, "status": "queued",
                                  "created_at": _now(), "progress": {"completed": 0, "total": 1,
                                                                       "message": "Queued"}}
        control = ExecutionControl(lambda update: self._update(job_id, progress=update))
        self._controls[job_id] = control

        async def run():
            self._update(job_id, status="running", started_at=_now())
            try:
                result = await run_async(function, control=control)
                status = result.get("status", "success")
                if status not in {"success", "partial", "failed", "cancelled"}:
                    status = "success"
                self._update(job_id, status=status, result=result)
            except ExecutionCancelled as exc:
                self._update(job_id, status="cancelled", error=str(exc), result=exc.result)
            except asyncio.CancelledError:
                control.cancel()
                self._update(job_id, status="cancelled", error="Server stopped")
            except Exception as exc:
                self._update(job_id, status="failed", error=str(exc))
            finally:
                self._update(job_id, finished_at=_now())

        self._tasks[job_id] = asyncio.create_task(run(), name=f"usimmics-job-{job_id}")
        return self.status(job_id)

    def status(self, job_id):
        with self._lock:
            if job_id not in self._jobs:
                raise BridgeError("Unknown job_id; jobs belong to the current server session")
            return deepcopy(self._jobs[job_id])

    def cancel(self, job_id):
        current = self.status(job_id)
        if current["status"] in {"queued", "running", "cancelling"}:
            self._controls[job_id].cancel()
            self._update(job_id, status="cancelling")
        return self.status(job_id)

    async def close(self):
        for control in self._controls.values():
            control.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
