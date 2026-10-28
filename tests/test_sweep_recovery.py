import json
from pathlib import Path

import pytest

from usimmics_bridge.builder import build_schematic
from usimmics_bridge.core import BridgeError, _sha
from usimmics_bridge.execution import ExecutionCancelled, current_control
from usimmics_bridge.experiments import parameter_sweep
from test_builder import fixture, design


def sweep_fixture(tmp_path, monkeypatch, failures=()):
    work = fixture(tmp_path)
    comps, routes = design()
    path = tmp_path / "divider.sch"
    build_schematic(work, str(path), comps, routes)
    calls = []
    def run(variant, timeout):
        index = int(Path(variant).stem.split("_")[-1])
        calls.append(index)
        if index in failures:
            raise BridgeError("Planned solver failure")
        data = Path(variant).with_suffix(".dat")
        data.write_bytes(b"synthetic-test-data")
        return {"dataset": str(data), "source_sha256": _sha(Path(variant).read_bytes())}
    def compare(work, paths, variable, frequency):
        return {"rows": [{"dataset": paths[0], "dataset_sha256": _sha(Path(paths[0]).read_bytes()),
                           "provenance": {"state": "matches_source", "verified": True,
                               "source": str(Path(paths[0]).with_suffix(".sch")),
                               "source_sha256": _sha(Path(paths[0]).with_suffix(".sch").read_bytes())},
                           "frequency_hz": frequency, "magnitude_v": 0.5, "power_dbm": 3.98}]}
    monkeypatch.setattr(work, "run_schematic", run)
    monkeypatch.setattr("usimmics_bridge.experiments.compare_results", compare)
    return work, path, calls


def test_failed_sweep_checkpoints_partial_results_and_resumes_only_pending(tmp_path, monkeypatch):
    failed = {1}
    work, source, calls = sweep_fixture(tmp_path, monkeypatch, failed)
    args = (work, str(source), "R1", "resistance", ["50 Ohm", "100 Ohm", "200 Ohm"])
    report = parameter_sweep(*args, variable="out.Vb")
    assert report["status"] == "partial"
    assert [p["status"] for p in report["points"]] == ["success", "failed", "pending"]
    disk = json.loads(Path(report["json_path"]).read_text())
    assert disk["completed"] == 1 and disk["failed"] == 1
    assert "Planned solver failure" in Path(report["csv_path"]).read_text()
    failed.clear()
    resumed = parameter_sweep(*args, variable="out.Vb", resume_from=report["json_path"])
    assert resumed["status"] == "success" and resumed["completed"] == 3
    assert calls == [0, 1, 1, 2]
    assert work.list_projects()["schematics"] == [str(source)]


def test_continue_after_failed_point_and_reject_changed_resume_data(tmp_path, monkeypatch):
    work, source, calls = sweep_fixture(tmp_path, monkeypatch, {1})
    args = (work, str(source), "R1", "resistance", ["50 Ohm", "100 Ohm", "200 Ohm"])
    report = parameter_sweep(*args, variable="out.Vb", fail_fast=False)
    assert calls == [0, 1, 2] and report["completed"] == 2
    assert report["remaining"] == 0 and report["failed"] == 1
    Path(report["rows"][0]["dataset"]).write_bytes(b"modified")
    with pytest.raises(BridgeError, match="dataset was modified"):
        parameter_sweep(*args, variable="out.Vb", resume_from=report["json_path"])


def test_cancelled_sweep_keeps_report_and_source_snapshot(tmp_path, monkeypatch):
    work, source, calls = sweep_fixture(tmp_path, monkeypatch)
    def cancel(variant, timeout):
        current_control().cancel()
        current_control().check()
    monkeypatch.setattr(work, "run_schematic", cancel)
    before = source.read_bytes()
    with pytest.raises(ExecutionCancelled) as caught:
        parameter_sweep(work, str(source), "R1", "resistance", ["50 Ohm", "100 Ohm"], variable="out.Vb")
    report = caught.value.result
    assert report["status"] == "cancelled" and report["points"][0]["status"] == "cancelled"
    assert Path(report["source_snapshot"]).read_bytes() == before == source.read_bytes()
    assert json.loads(Path(report["json_path"]).read_text())["status"] == "cancelled"


def test_resume_rejects_source_changes(tmp_path, monkeypatch):
    work, source, calls = sweep_fixture(tmp_path, monkeypatch, {1})
    args = (work, str(source), "R1", "resistance", ["50 Ohm", "100 Ohm"])
    report = parameter_sweep(*args, variable="out.Vb")
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(BridgeError, match="inputs changed"):
        parameter_sweep(*args, variable="out.Vb", resume_from=report["json_path"])


def test_resume_recomputes_values_instead_of_trusting_cached_summary(tmp_path, monkeypatch):
    work, source, calls = sweep_fixture(tmp_path, monkeypatch)
    args = (work, str(source), "R1", "resistance", ["50 Ohm"])
    report = parameter_sweep(*args, variable="out.Vb")
    summary = Path(report["json_path"])
    saved = json.loads(summary.read_text())
    saved["points"][0]["result"]["magnitude_v"] = 999
    summary.write_text(json.dumps(saved))
    resumed = parameter_sweep(*args, variable="out.Vb", resume_from=str(summary))
    assert resumed["rows"][0]["magnitude_v"] == 0.5 and calls == [0]


def test_resume_rejects_valid_dataset_from_a_different_point(tmp_path, monkeypatch):
    work, source, calls = sweep_fixture(tmp_path, monkeypatch)
    args = (work, str(source), "R1", "resistance", ["50 Ohm", "100 Ohm"])
    report = parameter_sweep(*args, variable="out.Vb")
    summary = Path(report["json_path"])
    saved = json.loads(summary.read_text())
    saved["points"][0]["result"] = saved["points"][1]["result"]
    summary.write_text(json.dumps(saved))
    with pytest.raises(BridgeError, match="does not verify this variant"):
        parameter_sweep(*args, variable="out.Vb", resume_from=str(summary))


def test_resume_refuses_point_symlink_before_writing_source(tmp_path, monkeypatch):
    failures = {1}
    work, source, calls = sweep_fixture(tmp_path, monkeypatch, failures)
    args = (work, str(source), "R1", "resistance", ["50 Ohm", "100 Ohm"])
    report = parameter_sweep(*args, variable="out.Vb")
    variant = Path(report["points"][1]["source"])
    variant.unlink()
    try:
        variant.symlink_to(source)
    except OSError:
        pytest.skip("Creating symlinks is unavailable for this test user")
    before = source.read_bytes()
    failures.clear()
    with pytest.raises(BridgeError, match="ordinary files"):
        parameter_sweep(*args, variable="out.Vb", resume_from=report["json_path"])
    assert source.read_bytes() == before


def test_changed_variant_during_run_is_reported_as_failed(tmp_path, monkeypatch):
    work, source, calls = sweep_fixture(tmp_path, monkeypatch)
    real_run = work.run_schematic
    def changed(variant, timeout):
        result = real_run(variant, timeout)
        Path(variant).write_bytes(Path(variant).read_bytes() + b"\n")
        return result
    monkeypatch.setattr(work, "run_schematic", changed)
    report = parameter_sweep(work, str(source), "R1", "resistance", ["50 Ohm"], variable="out.Vb")
    assert report["status"] == "failed" and not report["rows"]
    assert "changed while" in report["points"][0]["error"]


def test_all_points_use_captured_input_when_snapshot_changes(tmp_path, monkeypatch):
    work, source, calls = sweep_fixture(tmp_path, monkeypatch)
    real_run = work.run_schematic
    def changed_snapshot(variant, timeout):
        result = real_run(variant, timeout)
        snapshot = Path(variant).with_name("source.sch")
        if len(calls) == 1:
            work.update_parameters(str(snapshot), [{"component": "R2", "parameter": "resistance", "value": "999 Ohm"}],
                                   work.read_schematic(str(snapshot))["sha256"])
        return result
    monkeypatch.setattr(work, "run_schematic", changed_snapshot)
    report = parameter_sweep(work, str(source), "R1", "resistance", ["50 Ohm", "100 Ohm"], variable="out.Vb")
    assert report["status"] == "success" and report["source_unchanged"]
    assert not report["snapshot_unchanged"] and report["warnings"]
    last = work.inspect_schematic(report["points"][1]["source"])
    r2 = next(c for c in last["components"] if c["name"] == "R2")
    assert r2["properties"][0]["value"] == "50 Ohm"
