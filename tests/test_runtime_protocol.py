"""Real stdio behavior using a controlled Python child, never a native solver."""
import asyncio
import json
import os
from pathlib import Path
import sys
import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import McpError
from mcp.types import ClientNotification, CancelledNotification, CancelledNotificationParams

from usimmics_bridge.builder import build_schematic
from test_builder import fixture, design
from test_execution import process_alive, wait_until


def test_stdio_background_job_remains_responsive_and_cleans_up_on_disconnect(tmp_path):
    work = fixture(tmp_path)
    work.simulator.parent.mkdir(parents=True)
    work.simulator.write_bytes(b"Controlled hook placeholder")
    source = tmp_path / "divider.sch"
    components, connections = design()
    build_schematic(work, str(source), components, connections)
    pid_file = tmp_path / "solver.pid"
    child = "import os,sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
    wrapper = tmp_path / "controlled_server.py"
    wrapper.write_text("\n".join([
        "import sys",
        "from usimmics_bridge.workspace import Workbench",
        "from usimmics_bridge.execution import run_process",
        "def execute(self, netpath, dataset, log, timeout):",
        f"    return run_process([sys.executable, '-c', {child!r}, {str(pid_file)!r}], netpath.parent, log, timeout)",
        "Workbench._execute_solver = execute",
        "from usimmics_bridge.server import main",
        "main()",
    ]))

    async def exercise():
        env = {**os.environ, "USIMMICS_BRIDGE_CONFIG": str(work.config_path),
               "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        params = StdioServerParameters(command=sys.executable, args=[str(wrapper)], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                async def call(name, args):
                    result = await asyncio.wait_for(session.call_tool(name, args), 5)
                    assert not result.isError, result
                    return json.loads(next(item.text for item in result.content if item.type == "text"))
                started = await call("start_job", {"request": {"operation": "run_schematic",
                                      "path": str(source), "timeout": 30}})
                await wait_until(lambda: pid_file.exists() and pid_file.stat().st_size > 0)
                pid = int(pid_file.read_text())
                assert process_alive(pid)
                assert (await call("list_projects", {}))["schematics"] == [str(source)]
                assert (await call("cancel_job", {"job_id": started["job_id"]}))["status"] in {"cancelling", "cancelled"}
                for _ in range(100):
                    status = await call("job_status", {"job_id": started["job_id"]})
                    if status["status"] == "cancelled":
                        break
                    await asyncio.sleep(0.05)
                assert status["status"] == "cancelled"
                assert not process_alive(pid)
                manifest = Path(status["progress"]["manifest_path"])
                assert json.loads(manifest.read_text())["status"] == "cancelled"

                # A standard MCP cancellation notification must also stop an awaited run.
                pid_file.unlink()
                request_id = session._request_id
                progress = []
                async def on_progress(value, total, message):
                    progress.append((value, total, message))
                direct = asyncio.create_task(session.call_tool("run_schematic", {"path": str(source)},
                                                              progress_callback=on_progress))
                await wait_until(lambda: pid_file.exists() and pid_file.stat().st_size > 0)
                direct_pid = int(pid_file.read_text())
                await wait_until(lambda: bool(progress))
                await call("list_projects", {})
                await session.send_notification(ClientNotification(CancelledNotification(
                    params=CancelledNotificationParams(requestId=request_id, reason="Regression test"))))
                with pytest.raises(McpError, match="cancelled"):
                    await asyncio.wait_for(direct, 10)
                await wait_until(lambda: not process_alive(direct_pid), timeout=10)

                pid_file.unlink()
                await call("start_job", {"request": {"operation": "run_schematic", "path": str(source)}})
                await wait_until(lambda: pid_file.exists() and pid_file.stat().st_size > 0)
                disconnected_pid = int(pid_file.read_text())
                assert process_alive(disconnected_pid)
        await wait_until(lambda: not process_alive(disconnected_pid), timeout=10)
    asyncio.run(exercise())
