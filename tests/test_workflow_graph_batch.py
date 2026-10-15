"""Batch isolation and stage handoffs; injected transports are not scientific evidence."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_workflow_completion_runtime import CompletionWorker
from test_workflow_extraction_runtime import FakeWorker
from test_workflow_extraction_runtime import source_case as source_case

from playground import workflow_graph_batch as driver


def manifest_for(tmp_path, packet, ids=("a", "b", "c")):
    path = tmp_path / "batch.json"
    path.write_text(json.dumps({"jobs": [{"id": ident, "packet": str(packet)}
                                        for ident in ids]}))
    return path


def test_module_entrypoint_and_unsafe_or_duplicate_job_ids(tmp_path):
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "playground.workflow_graph_batch", "--help"],
        cwd=project, env={**os.environ, "PYTHONPATH": str(project / "src"),
                          "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True,
        text=True, check=True)
    assert "--concurrency" in result.stdout and "--no-graph" in result.stdout
    assert "--require-coverage" in result.stdout
    for ids in (("../escape",), ("a", "a")):
        with pytest.raises(ValueError):
            driver.Batch.model_validate_json(manifest_for(tmp_path, "packet", ids).read_bytes())


async def test_real_extraction_is_passed_to_selection_and_comparison(
    tmp_path, source_case, monkeypatch,
):
    _, packet_path, _ = source_case
    seen = {}

    async def select(path, run, *, worker):
        seen["parents"] = json.loads(path.read_text())
        parent = seen["parents"]["a"]
        assert json.loads(Path(parent["workflow"]).read_text())["operations"]
        assert json.loads(Path(parent["review"]).read_text())["checks"]
        assert Path(parent["packet"]).is_file()
        run.mkdir()
        manifest = run / "comparison_manifest.json"
        manifest.write_text("{}")
        return {"status": "completed", "manifest": str(manifest)}

    async def compare(path, run, *, worker):
        seen["comparison"] = path
        return {"status": "completed"}

    monkeypatch.setattr(driver, "select", select)
    monkeypatch.setattr(driver, "compare", compare)
    state = await driver.execute(manifest_for(tmp_path, packet_path, ("a",)), tmp_path / "run",
        worker_factory=lambda: FakeWorker(source_case), mode="baseline", max_revisions=0)
    assert state["status"] == "completed" and state["included_methods"] == ["a"]
    assert state["jobs"]["a"]["status"] == "supported"
    assert seen["comparison"].name == "comparison_manifest.json"


async def test_failure_isolation_concurrency_and_selection_failure(
    tmp_path, source_case, monkeypatch,
):
    _, packet_path, _ = source_case
    active = maximum = 0

    async def extract(packet, run, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(.01)
            if run.name == "b":
                raise ValueError("One method failed")
            return {"status": "supported", "workflow": "workflow", "review": "review"}
        finally:
            active -= 1

    async def select(path, run, *, worker):
        assert set(json.loads(path.read_text())) == {"a", "c"}
        return {"status": "failed", "error": "No completed planner thread"}

    monkeypatch.setattr(driver, "extract", extract)
    monkeypatch.setattr(driver, "select", select)
    state = await driver.execute(manifest_for(tmp_path, packet_path), tmp_path / "run",
                                 worker_factory=object, concurrency=2)
    assert maximum == 2 and active == 0
    assert state["status"] == "partial" and state["nonpositive_methods"] == ["b"]
    assert set(state["included_methods"]) == {"a", "c"}
    assert state["graph"] is None and state["selection"]["status"] == "failed"
    assert state["jobs"]["a"]["status"] == state["jobs"]["c"]["status"] == "supported"


async def test_mutated_queued_packet_prevents_launch_and_graph(tmp_path, source_case, monkeypatch):
    _, packet_path, _ = source_case
    calls = []

    async def extract(packet, run, **kwargs):
        calls.append(run.name)
        packet_path.write_text(packet_path.read_text() + " ")
        return {"status": "supported", "workflow": "workflow", "review": "review"}

    monkeypatch.setattr(driver, "extract", extract)
    state = await driver.execute(manifest_for(tmp_path, packet_path), tmp_path / "run",
                                 worker_factory=object, concurrency=1)
    assert calls == ["a"] and state["status"] == "partial"
    assert "Frozen batch input changed" in state["error"]
    assert state["jobs"]["b"]["status"] == state["jobs"]["c"]["status"] == "failed"
    assert state["graph"] is None and not (tmp_path / "run/parents.json").exists()


async def test_cancellation_reaps_active_jobs_and_preserves_state(
    tmp_path, source_case, monkeypatch,
):
    _, packet_path, _ = source_case
    ready = asyncio.Event()
    stopped = []

    async def extract(packet, run, **kwargs):
        ready.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append(run.name)

    monkeypatch.setattr(driver, "extract", extract)
    task = asyncio.create_task(driver.execute(manifest_for(tmp_path, packet_path), tmp_path / "run",
                                             worker_factory=object, concurrency=1))
    await ready.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = json.loads((tmp_path / "run/state.json").read_text())
    assert state["status"] == "cancelled" and stopped == ["a"]
    assert state["jobs"]["a"]["status"] == "cancelled"


@pytest.mark.parametrize("arguments", [{"concurrency": True}, {"timeout": float("nan")},
    {"max_revisions": True}, {"mode": "repair"}, {"require_coverage": 1}])
async def test_bad_configuration_rejected_before_run_creation(tmp_path, arguments):
    with pytest.raises(ValueError):
        await driver.execute(tmp_path / "absent", tmp_path / "run", **arguments)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("require_coverage", [False, True])
async def test_coverage_flag_propagates_to_every_method_and_saved_configuration(
    tmp_path, source_case, monkeypatch, require_coverage,
):
    calls = []

    async def extract(packet, run, *, worker, mode, max_revisions, require_coverage):
        calls.append((run.name, mode, max_revisions, require_coverage))
        return {"status": "supported", "workflow": "workflow", "review": "review"}

    monkeypatch.setattr(driver, "extract", extract)
    run = tmp_path / "flag_batch"
    state = await driver.execute(
        manifest_for(tmp_path, source_case[1]), run, worker_factory=object,
        with_graph=False, max_revisions=0, require_coverage=require_coverage)
    assert state["status"] == "completed" and state["require_coverage"] is require_coverage
    assert calls == [(ident, "specialists", 0, require_coverage) for ident in ("a", "b", "c")]
    assert json.loads((run / "config.json").read_text())["require_coverage"] is require_coverage
    assert json.loads((run / "state.json").read_text())["require_coverage"] is require_coverage


async def test_coverage_baseline_is_rejected_before_inputs_or_workers(
    tmp_path, monkeypatch,
):
    calls = []

    async def extract(*args, **kwargs):
        calls.append("extract")
        raise AssertionError("An unsupported mode must not launch extraction")

    monkeypatch.setattr(driver, "extract", extract)
    run = tmp_path / "invalid_coverage_batch"
    with pytest.raises(ValueError, match="specialists.*reader lineage"):
        await driver.execute(tmp_path / "absent_manifest.json", run,
                             mode="baseline", require_coverage=True,
                             worker_factory=lambda: calls.append("worker"))
    assert calls == [] and not run.exists()


@pytest.mark.parametrize("require_coverage", [False, True])
def test_cli_propagates_coverage_flag(tmp_path, monkeypatch, capsys, require_coverage):
    seen = {}

    async def execute(manifest, run, **kwargs):
        seen.update(kwargs)
        return {"status": "completed", "jobs": {}, "included_methods": []}

    arguments = ["workflow_graph_batch", str(tmp_path / "batch.json"),
                 "--run", str(tmp_path / "run"), "--no-graph"]
    if require_coverage:
        arguments.append("--require-coverage")
    monkeypatch.setattr(driver, "execute", execute)
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(SystemExit) as stopped:
        driver.main()
    assert stopped.value.code == 0
    assert seen["require_coverage"] is require_coverage
    assert json.loads(capsys.readouterr().out)["require_coverage"] is require_coverage


@pytest.mark.parametrize("verdict, outcome", [
    ("unsupported", "needs_revision"), ("unresolved", "research_needed"),
])
async def test_coverage_negative_parent_is_not_admitted_despite_positive_primary_review(
    tmp_path, source_case, monkeypatch, verdict, outcome,
):
    # Both real extraction jobs use native-archive fixtures; only graph transports are stubbed.
    positive = CompletionWorker(source_case)
    negative = CompletionWorker(source_case, coverage_verdicts=[verdict])
    workers = iter((positive, negative))
    seen = {}

    async def select(path, run, *, worker):
        seen["parents"] = json.loads(path.read_text())
        run.mkdir()
        manifest = run / "comparison_manifest.json"
        manifest.write_text("{}")
        return {"status": "completed", "manifest": str(manifest)}

    async def compare(path, run, *, worker):
        seen["comparison"] = path
        return {"status": "completed"}

    monkeypatch.setattr(driver, "select", select)
    monkeypatch.setattr(driver, "compare", compare)
    run = tmp_path / "mixed_coverage"
    state = await driver.execute(
        manifest_for(tmp_path, source_case[1], ("positive", "coverage_negative")), run,
        worker_factory=lambda: next(workers, object()), concurrency=1,
        max_revisions=0, require_coverage=True)
    assert state["status"] == "partial" and state["require_coverage"] is True
    assert state["jobs"]["positive"]["status"] == "supported"
    assert state["jobs"]["coverage_negative"]["status"] == outcome
    assert state["included_methods"] == ["positive"]
    assert state["nonpositive_methods"] == ["coverage_negative"]
    assert set(seen["parents"]) == {"positive"}
    assert set(json.loads((run / "parents.json").read_text())) == {"positive"}
    assert seen["comparison"].name == "comparison_manifest.json"
    primary = json.loads((negative.calls[3]["directory"] / "artifact.json").read_text())
    assert all(check["verdict"] == "supported" for check in primary["checks"])
    assert (negative.calls[3]["directory"] / "coverage_review.json").is_file()
    assert not (run / "methods/coverage_negative/graph_candidate.json").exists()
