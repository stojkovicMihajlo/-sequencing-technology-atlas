"""Control scoring and the real execute path using injected transport only."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.benchmark.review_full_workflow_control import run_control, score_control
from test_workflow_extraction_runtime import source_case
from test_workflow_source_obligations import SourceWorker, obligations_for
from test_workflow_verification_controls import definitions
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
from genomics_discovery.curation.workflow_extraction.models import Workflow, digest
from genomics_discovery.curation.workflow_extraction.workflow_mutation_controls import seed_workflow_controls


@pytest.mark.parametrize("fault", [None, "supported", "wrong_field", "unselected", "uncertain",
                                  "transport", "export", "status"])
def test_exact_negative_field_and_missing_obligation_are_both_required(fault):
    receipt = {"field_mutations": [{"target_id": "state:s", "candidate_field_pointer": "/states/0/description"}]}
    field = SimpleNamespace(checks=[SimpleNamespace(group_id="state:s",
        verdict="supported" if fault == "supported" else "not_established",
        affected_fields=["/states/0/kind" if fault == "wrong_field" else "/states/0/description"])])
    coverage = SimpleNamespace(checks=[SimpleNamespace(obligation_id="source_required",
        selected=fault != "unselected", disposition="uncertain" if fault == "uncertain" else "missing")])
    result = score_control(receipt, {"status": "supported" if fault == "status" else "needs_revision"},
        field, coverage, ["source_required"], native_complete=fault != "transport",
        supported_export=fault == "export")
    assert result["passed"] is (fault is None)
    if fault == "transport":
        assert result["field_controls"][0]["detected"] is False
        assert result["omission_controls"][0]["detected"] is False


class ControlWorker(SourceWorker):
    async def run(self, prompt, directory):
        result = await super().run(prompt, directory)
        path = Path(directory) / "field_review.json"
        review = json.loads(path.read_text())
        workflow = json.loads((Path(directory) / "candidate.json").read_text())
        index = next(i for i, op in enumerate(workflow["operations"]) if op["id"] == "seq_a")
        row = next(item for item in review["checks"] if item["group_id"] == "operation:seq_a")
        row.update(verdict="not_established", affected_fields=[f"/operations/{index}/action"],
                   rationale="Synthetic fixture: the added disposal claim is unsupported.")
        path.write_text(json.dumps(review))
        return result


@pytest.mark.parametrize("transport_failure", [False, True])
async def test_real_review_only_path_and_failed_transport_preservation(source_case, tmp_path, monkeypatch,
                                                                      transport_failure):
    monkeypatch.setenv("SLURM_JOB_ID", "offline-test-no-model")
    packet, packet_path, raw = source_case
    workflow = Workflow.model_validate(raw)
    specs = definitions(workflow, packet)
    specs[0].update(field_pointer="/operations/2/action",
                    original_value_sha256=digest(workflow.operations[2].action),
                    changed_value=workflow.operations[2].action + " Discard all products.")
    candidate, receipt = seed_workflow_controls(workflow, packet, specs, ["field", "coverage"])
    candidate_path, receipt_path, obligation_path = (tmp_path / name for name in
        ("candidate.json", "host_expected_controls.json", "original_obligations.json"))
    candidate_path.write_text(candidate.model_dump_json())
    receipt.update(candidate_raw_sha256=sha(candidate_path), source_packet_raw_sha256=sha(packet_path))
    receipt_path.write_text(json.dumps(receipt))
    obligation_path.write_text(json.dumps(obligations_for(packet)))
    worker = ControlWorker((packet, packet_path, candidate.model_dump(mode="json")))
    if transport_failure:
        worker.fault = "unfinished_turn"
    calls = []
    async def spy(*args, **kwargs):
        calls.append(kwargs)
        assert "host_expected_controls" not in str(kwargs)
        return await execute(*args, **kwargs)
    output = tmp_path / "control_run"
    report = await run_control(candidate_path, packet_path, obligation_path, receipt_path, output,
        ["wash_required"], expected_receipt_sha256=sha(receipt_path),
        expected_obligations_sha256=sha(obligation_path), executor=spy, worker=worker)
    assert len(calls) == 1 and calls[0]["mode"] == "review_only" and calls[0]["max_revisions"] == 0
    assert calls[0]["require_field_review"] and calls[0]["require_source_coverage"]
    assert len(worker.calls) == 1 and worker.calls[0]["role"] == "reviewer"
    assert report["passed"] is (not transport_failure)
    assert (output / "returned_state.json").exists() and (output / "execution/state.json").exists()
    assert not (output / "execution/graph_candidate.json").exists()
    if transport_failure:
        assert report["actual_extraction_status"] == "failed"
        assert not any(row["detected"] for row in report["field_controls"] + report["omission_controls"])


async def test_requires_slurm_before_claiming_output(tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="Sherlock Slurm"):
        await run_control("unused", "unused", "unused", "unused", tmp_path / "never-created", ["x"],
                          expected_receipt_sha256="0" * 64, expected_obligations_sha256="0" * 64)
    assert not (tmp_path / "never-created").exists()
