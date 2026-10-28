"""Checkpointed parameter sweeps with isolated inputs and explicit partial results."""
from __future__ import annotations

import csv
import io
import json
import math
from pathlib import Path
from uuid import uuid4
from filelock import Timeout

from .core import BridgeError, _sha
from .execution import ExecutionCancelled, current_control
from .locking import file_lock


def run_sweep(workbench, path, component, parameter, values, variable="IF_out.Vb",
              frequency_hz=1e6, timeout=30, *, fail_fast=True, resume_from=None, control=None):
    from .experiments import compare_results

    if not isinstance(values, list) or not 1 <= len(values) <= 20 or any(not isinstance(v, str) or not v for v in values):
        raise BridgeError("Supply 1..20 nonempty parameter strings")
    if type(timeout) is not int or not 1 <= timeout <= 30:
        raise BridgeError("Per-run timeout must be 1..30 seconds")
    if type(fail_fast) is not bool:
        raise BridgeError("fail_fast must be boolean")
    if isinstance(frequency_hz, bool) or not isinstance(frequency_hz, (float, int)) or not math.isfinite(frequency_hz) or frequency_hz < 0:
        raise BridgeError("Frequency must be finite and nonnegative")
    control = control or current_control()
    control.check()
    original = workbench.read_schematic(path)
    options = {"component": component, "parameter": parameter, "values": values,
               "variable": variable, "frequency_hz": frequency_hz}
    if resume_from:
        summary_path = workbench._path(resume_from, ".json")
        if summary_path.name != "summary.json" or summary_path.parent.parent.name != ".usimmics-sweeps":
            raise BridgeError("resume_from must name a sweep's summary.json")
        folder = summary_path.parent
    else:
        folder = workbench.target(str(Path(original["path"]).parent / ".usimmics-sweeps"))
        folder.mkdir(exist_ok=True)
        folder = folder / uuid4().hex
        folder.mkdir()
        summary_path = folder / "summary.json"

    # Reject simultaneous resumes instead of waiting behind a potentially long run.
    try:
        with file_lock(summary_path, timeout=0):
            return _execute_sweep(workbench, original, options, timeout, fail_fast,
                                  resume_from, folder, summary_path, control, compare_results)
    except Timeout as exc:
        raise BridgeError("This sweep is already running") from exc


def _check_point_result(row, variant, source_sha256):
    proof = row.get("provenance", {})
    if (not proof.get("verified") or proof.get("state") != "matches_source"
            or proof.get("source") != str(variant)
            or proof.get("source_sha256") != source_sha256):
        raise BridgeError("Sweep dataset does not verify this variant and source hash")


def _internal_path(workbench, folder, path):
    """An internal artifact must not alias another file through a symlink/junction."""
    if path.is_symlink() or workbench.target(str(path)) != path or path.parent != folder:
        raise BridgeError("Sweep artifacts must remain ordinary files in their own directory")
    return path


def _execute_sweep(workbench, original, options, timeout, fail_fast, resume_from,
                   folder, summary_path, control, compare_results):
    snapshot = _internal_path(workbench, folder, folder / "source.sch")
    csv_path = _internal_path(workbench, folder, folder / "summary.csv")
    _internal_path(workbench, folder, summary_path)
    json_hash = csv_hash = None
    if resume_from:
        raw = summary_path.read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise BridgeError("Sweep summary exceeds 2 MiB")
        try:
            report = json.loads(raw)
            if (report["schema_version"] != 1 or report["options"] != options
                    or report["source"] != original["path"]
                    or report["source_sha256"] != original["sha256"]
                    or len(report["points"]) != len(options["values"])):
                raise BridgeError("Sweep inputs changed; start a new sweep")
            snapshot_bytes = workbench._path(str(snapshot), ".sch").read_bytes()
            if snapshot_bytes == b"":
                raise BridgeError("Sweep source snapshot is empty")
            if _sha(snapshot_bytes) != original["sha256"]:
                raise BridgeError("Sweep source snapshot was modified")
            for index, point in enumerate(report["points"]):
                expected = _internal_path(workbench, folder, folder / f"point_{index:03d}.sch")
                if point["index"] != index or point["value"] != options["values"][index] or point["source"] != str(expected):
                    raise BridgeError("Sweep point metadata was modified")
                if point["status"] == "success":
                    if _sha(workbench._path(str(expected), ".sch").read_bytes()) != point["source_sha256"]:
                        raise BridgeError("Completed sweep variant was modified")
                    dataset = workbench._path(point["result"]["dataset"], ".dat")
                    if _sha(dataset.read_bytes()) != point["result"]["dataset_sha256"]:
                        raise BridgeError("Completed sweep dataset was modified")
                    refreshed = compare_results(workbench, [str(dataset)], options["variable"], options["frequency_hz"])["rows"][0]
                    _check_point_result(refreshed, expected, point["source_sha256"])
                    point["result"] = refreshed
                else:
                    point.update(status="pending", error=None)
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, BridgeError):
                raise
            raise BridgeError("Invalid sweep summary") from exc
        json_hash = _sha(raw)
        csv_hash = _sha(csv_path.read_bytes()) if csv_path.is_file() else None
    else:
        raw = workbench._path(original["path"], ".sch").read_bytes()
        if _sha(raw) != original["sha256"]:
            raise BridgeError("Source changed while preparing sweep")
        workbench.atomic_write(snapshot, raw)
        snapshot_bytes = raw
        report = {"schema_version": 1, "sweep_id": folder.name,
                  "source": original["path"], "source_sha256": original["sha256"],
                  "source_snapshot": str(snapshot), "options": options,
                  "component": options["component"], "parameter": options["parameter"],
                  "variable": options["variable"], "points": [
                      {"index": i, "value": value, "status": "pending", "attempts": 0,
                       "source": str(folder / f"point_{i:03d}.sch")}
                      for i, value in enumerate(options["values"])]}
    report.update(status="running", timeout=timeout, fail_fast=fail_fast,
                  json_path=str(summary_path), csv_path=str(csv_path), source_snapshot=str(snapshot))
    snapshot_text = snapshot_bytes.decode("utf-8-sig")

    def checkpoint():
        nonlocal json_hash, csv_hash
        points = report["points"]
        report["rows"] = [{"index": p["index"], "value": p["value"], **p["result"],
                           "source": p["source"]} for p in points if p["status"] == "success"]
        report["completed"] = sum(p["status"] == "success" for p in points)
        report["failed"] = sum(p["status"] == "failed" for p in points)
        report["remaining"] = sum(p["status"] in {"pending", "running", "cancelled"} for p in points)
        try:
            report["source_unchanged"] = workbench.read_schematic(original["path"])["sha256"] == original["sha256"]
        except (OSError, BridgeError):
            report["source_unchanged"] = False
        report["warnings"] = [] if report["source_unchanged"] else ["Source changed externally; results use the saved source snapshot"]
        report["snapshot_unchanged"] = (not snapshot.is_symlink() and snapshot.is_file()
                                         and _sha(snapshot.read_bytes()) == original["sha256"])
        if not report["snapshot_unchanged"]:
            report["warnings"].append("Stored source snapshot changed; active points use the initially captured input")
        _internal_path(workbench, folder, summary_path)
        _internal_path(workbench, folder, csv_path)
        json_hash = workbench.atomic_write(summary_path,
            (json.dumps(report, indent=2, allow_nan=False) + "\n").encode(), json_hash)["sha256"]
        stream = io.StringIO(newline="")
        fields = ["index", "value", "status", "source", "dataset", "frequency_hz", "magnitude_v", "power_dbm", "error"]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for point in points:
            writer.writerow({**point, **point.get("result", {})})
        csv_hash = workbench.atomic_write(csv_path, stream.getvalue().encode("utf-8"), csv_hash)["sha256"]

    checkpoint()
    current = None
    try:
        with control.bind():
            for point in report["points"]:
                control.check()
                if point["status"] == "success":
                    continue
                current = point
                point.update(status="running", error=None, attempts=point.get("attempts", 0) + 1)
                checkpoint()
                control.progress(report["completed"] + report["failed"], len(report["points"]),
                                 f"Running point {point['index'] + 1}/{len(report['points'])}",
                                 point_index=point["index"], summary_path=str(summary_path))
                try:
                    variant = _internal_path(workbench, folder, Path(point["source"]))
                    existing = workbench.read_schematic(str(variant))["sha256"] if variant.exists() else None
                    saved = workbench.save_schematic(str(variant), snapshot_text, existing)
                    workbench.update_parameters(str(variant), [{"component": options["component"],
                        "parameter": options["parameter"], "value": point["value"]}], saved["sha256"])
                    run = workbench.run_schematic(str(variant), timeout)
                    run_source_sha256 = run["source_sha256"]
                    if workbench.read_schematic(str(variant))["sha256"] != run_source_sha256:
                        raise BridgeError("Sweep variant changed while the solver was running")
                    result = compare_results(workbench, [run["dataset"]], options["variable"], options["frequency_hz"])
                    _check_point_result(result["rows"][0], variant, run_source_sha256)
                    point.update(status="success", result=result["rows"][0],
                                 source_sha256=run_source_sha256)
                except ExecutionCancelled:
                    point["status"] = "cancelled"
                    raise
                except Exception as exc:
                    point.update(status="failed", error=str(exc), error_type=type(exc).__name__)
                    if fail_fast:
                        checkpoint()
                        break
                checkpoint()
                control.progress(report["completed"] + report["failed"], len(report["points"]),
                                 "Point finished", summary_path=str(summary_path))
                current = None
            control.check()
    except ExecutionCancelled as exc:
        if current and current["status"] == "running":
            current["status"] = "cancelled"
        report["status"] = "cancelled"
        checkpoint()
        raise ExecutionCancelled(f"Sweep cancelled; summary: {summary_path}", report) from exc
    report["status"] = ("success" if all(p["status"] == "success" for p in report["points"])
                        else "partial" if any(p["status"] == "success" for p in report["points"]) else "failed")
    checkpoint()
    return report
