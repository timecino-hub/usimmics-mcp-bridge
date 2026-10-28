"""JSON CLI fallback: usable immediately, before the client reloads its MCP catalog."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from usimmics_bridge.workspace import Workbench as Bridge
from usimmics_bridge.core import BridgeError
from .config import config_path, configure, doctor
from .builder import build_schematic
from .experiments import parameter_sweep, compare_results


def main() -> int:
    parser = argparse.ArgumentParser(description="uSimmics 5.9 background control")
    parser.add_argument("--config", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser('doctor')
    setup=sub.add_parser('configure')
    setup.add_argument('--installation-root')
    setup.add_argument('--project-root', action='append')
    setup.add_argument('--force', action='store_true')
    sub.add_parser("projects")
    call = sub.add_parser('call', help='Invoke a named workspace operation with a JSON arguments file')
    call.add_argument('operation', choices=['create_project','read_schematic','save_schematic',
        'clone_schematic','validate_schematic','preview_schematic','publish_results',
        'build_schematic','parameter_sweep','compare_results'])
    call.add_argument('--arguments-file', type=Path, required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("path")
    update = sub.add_parser("update")
    update.add_argument("path")
    update.add_argument("--edits-file", required=True, type=Path)
    update.add_argument("--sha256", required=True)
    run = sub.add_parser("run")
    run.add_argument("path")
    run.add_argument("--timeout", type=int, default=60)
    results = sub.add_parser("results")
    results.add_argument("path")
    results.add_argument("--max-points", type=int, default=2000)
    args = parser.parse_args()
    try:
        if args.command=='configure':
            result=configure(args.installation_root,args.project_root,args.config,args.force)
            print(json.dumps(result,ensure_ascii=False));return 0
        if args.command in ('status','doctor'):
            result=doctor(args.config)
            print(json.dumps(result,ensure_ascii=False));return 0 if result['ready'] else 1
        bridge = Bridge(config_path(args.config))
        if args.command == 'call':
            arguments = json.loads(args.arguments_file.read_text(encoding='utf-8-sig'))
            extra={'build_schematic':build_schematic,'parameter_sweep':parameter_sweep,'compare_results':compare_results}
            result = extra[args.operation](bridge,**arguments) if args.operation in extra else getattr(bridge, args.operation)(**arguments)
        elif args.command == "status":
            result = bridge.environment_status()
        elif args.command == "projects":
            result = bridge.list_projects()
        elif args.command == "inspect":
            result = bridge.inspect_schematic(args.path)
        elif args.command == "update":
            edits = json.loads(args.edits_file.read_text(encoding="utf-8-sig"))
            result = bridge.update_parameters(args.path, edits, args.sha256)
        elif args.command == "run":
            result = bridge.run_schematic(args.path, args.timeout)
        else:
            result = bridge.read_results(args.path, args.max_points)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (BridgeError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
