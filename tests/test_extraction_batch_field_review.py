"""Batch recovery must retain the direct extractor's factual-field veto."""

from pathlib import Path

import pytest

from genomics_discovery.curation.extraction_batch import BatchConfig, plan, run
from genomics_discovery.curation.extraction_batch.storage import read, sha, write
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
from test_extraction_batch import inventory
from test_workflow_extraction_runtime import FakeWorker, source_case
from test_workflow_field_review_runtime import FieldWorker


@pytest.mark.parametrize("verdict,status", [("unsupported", "needs_revision"),
                                           ("not_established", "needs_revision"),
                                           ("unassessed", "failed")])
async def test_field_veto_survives_batch_recovery(source_case, tmp_path, verdict, status):
    directory = tmp_path / "batch"
    plan(inventory(tmp_path, source_case), directory,
         config=BatchConfig(max_revisions=0, transport_retries=0))
    worker = FieldWorker(source_case, field_verdicts=[verdict])
    state = await run(directory, worker_factory=lambda: worker)
    row = state["jobs"]["good"]
    assert row["status"] == status, row.get("error")
    assert read(directory / "parents.json") == {}
    if status == "needs_revision":
        assert row["result"]["primary_outcome"] == "supported"
        assert row["result"]["accepted_field_review"]["sha256"]
        resumed = await run(directory, worker_factory=lambda: pytest.fail("Scientific result is terminal"))
        assert resumed["jobs"]["good"]["status"] == status


async def test_positive_field_repair_replays_immediate_history(source_case, tmp_path):
    directory = tmp_path / "batch"
    plan(inventory(tmp_path, source_case), directory)
    worker = FieldWorker(source_case, field_verdicts=["unsupported", "supported"])
    state = await run(directory, worker_factory=lambda: worker)
    assert state["jobs"]["good"]["status"] == "supported"
    assert set(read(directory / "parents.json")) == {"good"}
    result = state["jobs"]["good"]["result"]
    field = read(Path(result["accepted_field_review"]["path"]))
    assert field["prior_review_sha256"] and field["prior_workflow_sha256"]
    resumed = await run(directory, worker_factory=lambda: pytest.fail("Completed repair must be reused"))
    assert resumed["jobs"]["good"]["status"] == "supported"


async def test_field_review_drift_excludes_a_previously_supported_parent(source_case, tmp_path):
    directory = tmp_path / "batch"
    plan(inventory(tmp_path, source_case), directory)
    state = await run(directory, worker_factory=lambda: FakeWorker(source_case))
    result = state["jobs"]["good"]["result"]
    Path(result["accepted_field_review"]["path"]).write_text("{}")
    resumed = await run(directory, worker_factory=lambda: pytest.fail("Do not replace changed artifacts"))
    assert resumed["jobs"]["good"]["status"] == "pin_drift"
    assert read(directory / "parents.json") == {}


@pytest.mark.parametrize("fault", ["missing_record", "foreign_owner", "missing_history"])
async def test_recovery_requires_successful_reviewer_ownership_and_history(source_case, tmp_path, fault):
    directory = tmp_path / "batch"
    plan(inventory(tmp_path, source_case), directory)
    worker = FieldWorker(source_case, field_verdicts=["unsupported", "supported"])
    state = await run(directory, worker_factory=lambda: worker)
    runtime_state_path = Path(state["jobs"]["good"]["attempts"][0]["run"]) / "state.json"
    runtime_state = read(runtime_state_path)
    reviewer = runtime_state["attempts"][-1]
    if fault == "missing_record":
        reviewer.pop("field_review")
    elif fault == "foreign_owner":
        supplied = Path(reviewer["field_review"]["path"])
        replacement = supplied.parent.parent / "foreign_field_review.json"
        replacement.write_bytes(supplied.read_bytes())
        reviewer["field_review"]["path"] = str(replacement)
    else:
        reviewer["inputs"].pop("previous_field_review")
    write(runtime_state_path, runtime_state)
    resumed = await run(directory, worker_factory=lambda: pytest.fail("Do not replace changed lineage"))
    assert resumed["jobs"]["good"]["status"] == "artifact_drift"
    assert read(directory / "parents.json") == {}


async def test_field_gate_keeps_research_outcome(source_case, tmp_path):
    directory = tmp_path / "batch"
    plan(inventory(tmp_path, source_case), directory,
         config=BatchConfig(max_revisions=0, transport_retries=0))
    worker = FieldWorker(source_case, field_verdicts=["not_established"])
    worker.workflow["unresolved"] = [{
        "id": "missing", "description": "Required condition not established.",
        "blocking": True, "next_evidence": "Inspect the primary supplement.",
    }]
    state = await run(directory, worker_factory=lambda: worker)
    assert state["jobs"]["good"]["status"] == "research_needed"
    assert read(directory / "parents.json") == {}


@pytest.mark.parametrize("fault", ["removed_state_policy", "false_state_policy", "changed_config",
                                    "missing_config_pin"])
async def test_recovery_cannot_downgrade_pinned_field_policy(source_case, tmp_path, fault):
    directory = tmp_path / "batch"
    plan(inventory(tmp_path, source_case), directory,
         config=BatchConfig(max_revisions=0, transport_retries=0))
    state = await run(directory, worker_factory=lambda: FieldWorker(
        source_case, field_verdicts=["unsupported"]))
    assert state["jobs"]["good"]["status"] == "needs_revision"
    runtime = Path(state["jobs"]["good"]["attempts"][0]["run"])
    runtime_state = read(runtime / "state.json")
    if fault == "removed_state_policy":
        runtime_state.pop("require_field_review")
    elif fault == "false_state_policy":
        runtime_state["require_field_review"] = False
    elif fault == "missing_config_pin":
        runtime_state.pop("config_sha256")
    else:
        config = read(runtime / "config.json")
        config["require_field_review"] = False
        write(runtime / "config.json", config)
    write(runtime / "state.json", runtime_state)
    resumed = await run(directory, worker_factory=lambda: pytest.fail("Changed policy cannot trigger workers"))
    expected = "pin_drift" if fault == "changed_config" else "artifact_drift"
    assert resumed["jobs"]["good"]["status"] == expected
    assert read(directory / "parents.json") == {}


def test_interrupted_preparation_needs_no_configuration_pin(source_case, tmp_path):
    from genomics_discovery.curation.extraction_batch.controller import _recover

    runtime = tmp_path / "preparing"
    write(runtime / "state.json", {"status": "preparing", "attempts": [], "artifacts": {}})
    candidate, pair, _, _ = _recover({"attempts": [{"run": str(runtime)}]},
                                     source_case[0], BatchConfig())
    assert candidate is None and pair is None


async def test_interrupted_source_only_preparation_reuses_valid_obligations(
        source_case, tmp_path, monkeypatch):
    from genomics_discovery.curation.workflow_extraction import extraction_pipeline
    from test_workflow_source_obligations import SourceWorker

    directory = tmp_path / "batch"
    plan(inventory(tmp_path, source_case), directory,
         config=BatchConfig(require_source_coverage=True, transport_retries=1))
    original_write = extraction_pipeline.write
    interrupted = False

    def fail_first_config(path, value):
        nonlocal interrupted
        if path.name == "config.json" and not interrupted:
            interrupted = True
            raise OSError("Interrupted after source-only preparation")
        return original_write(path, value)

    monkeypatch.setattr(extraction_pipeline, "write", fail_first_config)
    worker = SourceWorker(source_case)
    state = await run(directory, worker_factory=lambda: worker)
    row = state["jobs"]["good"]
    assert row["status"] == "supported", row.get("error")
    assert len(row["attempts"]) == 2
    first = read(Path(row["attempts"][0]["run"]) / "state.json")
    assert "config_sha256" not in first
    assert [a["role"] for a in first["attempts"]] == ["source_obligations"]
    assert first["attempts"][0]["status"] == "validated"
    assert [call["role"] for call in worker.calls].count("source_obligations") == 1


@pytest.mark.parametrize("omit_profile", [False, True])
async def test_legacy_field_profile_remains_explicitly_historical(source_case, tmp_path, omit_profile):
    directory = tmp_path / "batch"
    plan(inventory(tmp_path, source_case), directory)

    async def historical(packet, destination, **kwargs):
        state = await execute(packet, destination, require_field_review=False, **kwargs)
        if omit_profile:
            state.pop("require_field_review")
            # Represent a historical configuration predating the field policy.
            config_path = destination / "config.json"
            config = read(config_path)
            config.pop("require_field_review")
            write(config_path, config)
            state["config_sha256"] = sha(config_path)
            write(destination / "state.json", state)
        return state

    state = await run(directory, worker_factory=lambda: FakeWorker(source_case), extractor=historical)
    assert state["jobs"]["good"]["status"] == "supported"
    assert "accepted_field_review" not in state["jobs"]["good"]["result"]
    assert set(read(directory / "parents.json")) == {"good"}
