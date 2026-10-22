"""Clean launches reuse the engine and cannot receive prior scientific answers."""

import asyncio
import json
from pathlib import Path

import pytest

from scripts.benchmark import run_clean_extraction as runner
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.sources import build_packet


@pytest.fixture
def plan(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Prepare cells, measure molecules and decode reads.")
    packet = build_packet(method_id="fresh", title="Fresh assay", scope="Native cells to reads",
        variant="Original", sources=[{"source_id": "primary", "title": "Primary paper",
        "role": "primary_paper", "url": "https://example.org/paper", "path": str(source)}])
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet))
    return {"format_version": "clean_extraction_plan/1.0", "model": "gpt-6.1-sol",
        "effort": "xhigh", "methods": [{"id": "fresh", "packet": str(path),
        "packet_sha256": sha(path)}]}


@pytest.mark.parametrize("injection", ["workflow", "review", "source_obligations", "reference", "feedback"])
def test_prior_artifact_roles_not_admitted_at_plan_or_method_level(plan, injection):
    plan[injection] = "/historical/answer.json"
    with pytest.raises(ValueError, match="admits only"):
        runner.checked_plan(plan)
    del plan[injection]
    plan["methods"][0][injection] = "/historical/answer.json"
    with pytest.raises(ValueError, match="admits only"):
        runner.checked_plan(plan)


def test_changed_packet_or_original_source_is_rejected(plan, tmp_path):
    packet_path = Path(plan["methods"][0]["packet"])
    original = packet_path.read_bytes()
    packet_path.write_bytes(original + b' ')
    with pytest.raises(ValueError, match="changed registered"):
        runner.checked_plan(plan)
    packet_path.write_bytes(original)
    (tmp_path / "source.txt").write_text("Changed original source.")
    with pytest.raises(ValueError):
        runner.checked_plan(plan)


@pytest.mark.parametrize("change", ["model", "effort", "format", "duplicate", "id", "empty", "three"])
def test_unrequested_or_ambiguous_launch_contracts_rejected(plan, change):
    if change in {"model", "effort"}:
        plan[change] = "other"
    elif change == "format":
        plan["format_version"] = "unknown"
    elif change == "duplicate":
        plan["methods"] *= 2
    elif change == "id":
        plan["methods"][0]["id"] = "another-method"
    elif change == "empty":
        plan["methods"] = []
    else:
        plan["methods"] *= 3
    with pytest.raises(ValueError):
        runner.checked_plan(plan)


@pytest.mark.asyncio
async def test_clean_call_passes_only_source_packet_and_fixed_gates(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "test-only")
    calls = []
    monkeypatch.setattr(runner, "CodexWorker", lambda **kw: kw)
    async def execute(packet, output, **kwargs):
        calls.append((packet, output, kwargs))
        return {"status": "supported", "run": str(output)}
    monkeypatch.setattr(runner, "execute", execute)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    result = await runner.run(path, tmp_path / "run")
    assert result["status"] == "completed" and not result["prior_artifacts_supplied"]
    assert not result["canonical_promotion"]
    assert len(calls) == 1
    assert set(calls[0][2]) == {"worker", "mode", "max_revisions", "require_coverage",
                              "require_source_coverage", "require_field_review"}
    assert calls[0][2]["mode"] == "specialists" and calls[0][2]["max_revisions"] == 1
    assert all(calls[0][2][key] for key in ("require_coverage", "require_source_coverage", "require_field_review"))
    assert calls[0][2]["worker"] == {"model": "gpt-6.1-sol", "effort": "xhigh",
        "timeout_seconds": None, "web_search": False, "network_access": False}
    before = (tmp_path / "run/state.json").read_bytes()
    with pytest.raises(FileExistsError):
        await runner.run(path, tmp_path / "run")
    assert (tmp_path / "run/state.json").read_bytes() == before


@pytest.mark.asyncio
async def test_operational_failure_and_nonpositive_review_never_report_success(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "test-only")
    monkeypatch.setattr(runner, "CodexWorker", lambda **kw: object())
    async def fail(*args, **kwargs):
        raise ValueError("Synthetic worker failure")
    monkeypatch.setattr(runner, "execute", fail)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    result = await runner.run(path, tmp_path / "failed")
    assert result["status"] == "failed"
    assert "Synthetic worker failure" in result["methods"]["fresh"]["error"]
    async def withheld(*args, **kwargs):
        return {"status": "needs_revision"}
    monkeypatch.setattr(runner, "execute", withheld)
    result = await runner.run(path, tmp_path / "withheld")
    assert result["status"] == "needs_attention"


@pytest.mark.asyncio
async def test_no_slurm_does_not_claim_output(plan, tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="Slurm"):
        await runner.run(tmp_path / "unused", tmp_path / "run")
    assert not (tmp_path / "run").exists()


@pytest.mark.asyncio
async def test_input_mutation_after_execution_is_persisted_as_failure(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "test-only")
    monkeypatch.setattr(runner, "CodexWorker", lambda **kw: object())
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    async def mutate(*args, **kwargs):
        path.write_text(json.dumps(plan) + ' ')
        return {"status": "supported"}
    monkeypatch.setattr(runner, "execute", mutate)
    with pytest.raises(ValueError, match="Frozen clean plan"):
        await runner.run(path, tmp_path / "run")
    state = json.loads((tmp_path / "run/state.json").read_text())
    assert state["status"] == "failed" and state["finished_at"]


@pytest.mark.asyncio
async def test_two_distinct_methods_are_kept_in_distinct_directories(plan, tmp_path, monkeypatch):
    packet = json.loads(Path(plan["methods"][0]["packet"]).read_text())
    packet["method_id"] = "second"
    other = tmp_path / "second_packet.json"
    other.write_text(json.dumps(packet))
    plan["methods"].append({"id": "second", "packet": str(other), "packet_sha256": sha(other)})
    monkeypatch.setenv("SLURM_JOB_ID", "test-only")
    monkeypatch.setattr(runner, "CodexWorker", lambda **kw: object())
    paths = []
    async def execute(packet, output, **kwargs):
        paths.append(output)
        return {"status": "supported", "run": str(output)}
    monkeypatch.setattr(runner, "execute", execute)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    result = await runner.run(path, tmp_path / "run")
    assert set(result["methods"]) == {"fresh", "second"}
    assert set(paths) == {tmp_path / "run/methods/01", tmp_path / "run/methods/02"}
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_cancellation_settles_owned_task_then_preserves_cancelled_state(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "test-only")
    monkeypatch.setattr(runner, "CodexWorker", lambda **kw: object())
    entered = asyncio.Event()
    async def wait(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(runner, "execute", wait)
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    task = asyncio.create_task(runner.run(path, tmp_path / "run"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = json.loads((tmp_path / "run/state.json").read_text())
    assert state["status"] == "cancelled"
    assert state["methods"]["fresh"]["status"] == "cancelled"
    assert state["finished_at"]
