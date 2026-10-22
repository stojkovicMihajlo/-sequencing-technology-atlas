"""Prototype input pins and composite success gates, without model calls."""

import importlib
import json
from pathlib import Path

import pytest

from genomics_discovery.curation.run_state import sha


@pytest.fixture
def runner(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts/benchmark"
    monkeypatch.syspath_prepend(str(scripts))
    return importlib.import_module("validate_workflow_prototype")


@pytest.fixture
def plan(tmp_path):
    inputs = {}
    for role in ("packet", "workflow", "repair_feedback", "source_obligations", "controls"):
        path = tmp_path / (role + ".json")
        path.write_text('{}\n')
        inputs[role] = {"path": str(path), "sha256": sha(path)}
    return {"format_version": "workflow_prototype_plan/1.0", "model": "gpt-6.1-sol",
            "effort": "xhigh", "inputs": inputs}


def test_matching_inputs_retained_and_changed_bytes_rejected(runner, plan):
    assert set(runner.checked_inputs(plan)) == set(plan["inputs"])
    Path(plan["inputs"]["workflow"]["path"]).write_text('{"changed":true}\n')
    with pytest.raises(ValueError, match="Missing or changed prototype input: workflow"):
        runner.checked_inputs(plan)


@pytest.mark.parametrize("change", ["format", "model", "effort", "extra_role", "missing_role", "relative"])
def test_bad_launch_contracts_rejected(runner, plan, change):
    if change in {"model", "effort"}:
        plan[change] = "unrequested"
    elif change == "format":
        plan["format_version"] = "unknown"
    elif change == "extra_role":
        plan["inputs"]["hidden_extra"] = plan["inputs"]["packet"]
    elif change == "missing_role":
        del plan["inputs"]["controls"]
    else:
        plan["inputs"]["workflow"]["path"] = "relative.json"
    with pytest.raises(ValueError):
        runner.checked_inputs(plan)


@pytest.mark.asyncio
async def test_no_slurm_rejected_before_output_creation(runner, tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    output = tmp_path / "never_created"
    with pytest.raises(ValueError, match="Slurm allocation"):
        await runner.run(tmp_path / "absent_plan.json", output)
    assert not output.exists()


def fake_execution(monkeypatch, runner, *, repair_status, with_control_gate):
    monkeypatch.setenv("SLURM_JOB_ID", "synthetic-test-only")
    monkeypatch.setattr(runner, "prepare_controls", lambda args: None)
    monkeypatch.setattr(runner, "CodexWorker", lambda **kwargs: object())
    monkeypatch.setattr(runner, "read_closed", lambda path, model: object())
    monkeypatch.setattr(runner, "control_gate", lambda *args, **kwargs: {"passed": True})

    async def repair(*args, **kwargs):
        return {"status": repair_status}

    async def controls(cases, output, **kwargs):
        attempts = []
        if with_control_gate:
            attempt = output / "attempt"
            attempt.mkdir(parents=True)
            artifact = attempt / "artifact.json"
            artifact.write_text('{}\n')
            attempts.append({"attempt": str(attempt), "configuration": "field_accountable_review",
                             "status": "completed", "transport_complete": True,
                             "artifact_sha256": sha(artifact)})
        return {"status": "completed", "attempts": attempts}

    monkeypatch.setattr(runner, "execute", repair)
    monkeypatch.setattr(runner, "run_diagnostic", controls)


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_status,with_gate", [("supported", False), ("research_needed", True)])
async def test_transport_or_control_success_alone_cannot_pass(
        runner, plan, tmp_path, monkeypatch, repair_status, with_gate):
    fake_execution(monkeypatch, runner, repair_status=repair_status, with_control_gate=with_gate)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    state = await runner.run(plan_path, tmp_path / "run")
    assert state["status"] == "needs_attention"
    assert state["semantic_validation"] == "bounded_prototype_checks_not_passed"
    assert not state["canonical_promotion"]
    assert state["scientific_accuracy"] == "not_established_by_this_small_control_set"


@pytest.mark.asyncio
async def test_prepare_failure_persists_failed_state_and_rethrows(runner, plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "synthetic-test-only")
    def fail(args):
        raise ValueError("Synthetic preparation failure")
    monkeypatch.setattr(runner, "prepare_controls", fail)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="Synthetic preparation failure"):
        await runner.run(plan_path, output)
    state = json.loads((output / "state.json").read_text())
    assert state["status"] == "failed"
    assert "Synthetic preparation failure" in state["error"]
    assert state["finished_at"]
