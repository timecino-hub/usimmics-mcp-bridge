"""Typed background run/sweep tools for a persistent MCP server session."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Literal

import anyio
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from .schemas import RunTimeout, SweepTimeout, SweepValues, Frequency

from .execution import cancel_active
from .experiments import parameter_sweep
from .jobs import JobManager
from .workspace import Workbench


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["run_schematic"]
    path: str
    timeout: RunTimeout = 60


class SweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["parameter_sweep"]
    path: str
    component: str
    parameter: str
    values: SweepValues
    variable: str = "IF_out.Vb"
    frequency_hz: Frequency = 1e6
    timeout: SweepTimeout = 30
    fail_fast: StrictBool = True
    resume_from: str | None = None


JobRequest = Annotated[RunRequest | SweepRequest, Field(discriminator="operation")]
JOBS = JobManager()


@asynccontextmanager
async def server_lifespan(server):
    try:
        yield {}
    finally:
        cancel_active()
        with anyio.CancelScope(shield=True):
            await JOBS.close()


def register_runtime_tools(mcp, config):
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                          idempotentHint=False, openWorldHint=False))
    async def start_job(request: JobRequest) -> dict:
        """Start a background run or sweep; poll job_status and use cancel_job to stop it.

        Requires a persistent MCP session. Job IDs are session-local (latest 64 retained),
        with at most four active jobs and one native solver at a time. Completed artifacts
        and sweep summaries survive server restarts. Closing the server cancels active jobs.
        """
        arguments = request.model_dump(exclude={"operation"})

        def execute(control):
            bridge = Workbench(config)
            if request.operation == "run_schematic":
                return bridge.run_schematic(**arguments)
            return parameter_sweep(bridge, **arguments, control=control)

        return await JOBS.start(request.operation, execute)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=False))
    def job_status(job_id: str) -> dict:
        """Read a session-local job's status, latest progress, result or error."""
        return JOBS.status(job_id)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                          idempotentHint=True, openWorldHint=False))
    def cancel_job(job_id: str) -> dict:
        """Request cancellation, then poll until cancelled; finished jobs are unchanged.

        The worker terminates the native process tree, saves partial sweep results and
        releases its solver slot before reporting cancelled. Repeated cancellation is safe.
        """
        return JOBS.cancel(job_id)
