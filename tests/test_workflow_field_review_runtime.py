"""Synthetic reviewers test field-accountability gates, never scientific accuracy."""

from __future__ import annotations

import json
import sys

import pytest

from test_workflow_extraction_runtime import FakeWorker, source_case
from genomics_discovery.curation.workflow_extraction.models import Workflow, digest
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute


class FieldWorker(FakeWorker):
    def __init__(self, case, *, field_verdicts=(), field_fault=None,
                 target="operation:wash", pointer="/operations/1/action", adjudicate=False):
        super().__init__(case)
        self.field_verdicts = field_verdicts
        self.field_fault = field_fault
        self.target = target
        self.pointer = pointer
        self.adjudicate = adjudicate

    async def run(self, prompt, attempt_dir):
        result = await super().run(prompt, attempt_dir)
        path = attempt_dir / "field_review.json"
        if not path.exists():
            return result
        if self.field_fault == "missing":
            path.unlink()
            return result
        data = json.loads(path.read_text())
        check = next(row for row in data["checks"] if row["group_id"] == self.target)
        verdict = (self.field_verdicts[self.reviews - 1]
                   if self.reviews <= len(self.field_verdicts) else "supported")
        check["verdict"] = verdict
        if verdict in {"unsupported", "not_established"}:
            check["affected_fields"] = [self.pointer]
            check["rationale"] = "Synthetic reviewer identifies an unsupported field assertion."
        if self.field_fault == "stale_value":
            check["field_bindings"][0]["value_sha256"] = "0" * 64
        elif self.field_fault == "stale_workflow":
            data["workflow_sha256"] = "0" * 64
        elif self.field_fault == "omitted_group":
            data["checks"].pop()
        elif self.field_fault == "duplicate_group":
            data["checks"].append(data["checks"][0])
        elif self.field_fault == "no_evidence":
            check["evidence"] = []
        elif self.field_fault == "changed_scaffold":
            scaffold = attempt_dir / "field_review_scaffold.json"
            scaffold.write_text("{}")
        if self.reviews == 2 and self.adjudicate:
            prior = json.loads((attempt_dir / "previous_field_review.json").read_text())
            old = next(row for row in prior["checks"] if row["group_id"] == self.target)
            binding = next(row for row in old["field_bindings"]
                           if row["field_pointer"] == self.pointer)
            data["adjudications"] = [{
                "prior_group_id": self.target, "prior_field_binding": binding,
                "rationale": "Synthetic explicit reconsideration; not an empirical result.",
                "inspected_passages": check["inspected_passages"], "evidence": check["evidence"],
            }]
        path.write_text(json.dumps(data))
        return result


def no_supported_export(run):
    assert not (run / "graph_candidate.json").exists()
    assert not (run / "protocol.txt").exists()


@pytest.mark.asyncio
async def test_default_pipeline_requires_and_archives_field_accountability(source_case, tmp_path):
    run = tmp_path / "fields"
    worker = FieldWorker(source_case)
    state = await execute(source_case[1], run, mode="baseline", worker=worker)
    assert state["status"] == "supported"
    assert state["require_field_review"] is True
    assert state["field_review_outcome"] == "supported"
    assert [call["role"] for call in worker.calls] == ["integrator", "reviewer"]
    reviewer = worker.calls[-1]
    scaffold = json.loads((reviewer["directory"] / "field_review_scaffold.json").read_text())
    assert all(row["verdict"] == "unassessed" for row in scaffold["checks"])
    assert {"workflow", "module:preparation"} <= {row["group_id"] for row in scaffold["checks"]}
    assert scaffold["workflow_sha256"] == digest(Workflow.model_validate(worker.workflow))
    assert state["attempts"][-1]["inputs"]["field_review_scaffold"]["sha256"]
    assert state["artifacts"]["field_review"]["document_sha256"]
    assert "field_review.json" in reviewer["prompt"]
    assert "--kind field_review" in reviewer["prompt"]
    assert str(run / "schemas" / "Workflow.schema.json") in reviewer["prompt"]
    assert "shared field definitions" in reviewer["prompt"]
    config = json.loads((run / "config.json").read_text())
    assert config["require_field_review"] is True
    assert any(item["path"].endswith("/curation/validation.py")
               for item in config["implementation"])


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "stale_value", "stale_workflow", "omitted_group",
                                  "duplicate_group", "no_evidence", "changed_scaffold"])
async def test_incomplete_or_unbound_field_review_never_exports(source_case, tmp_path, fault):
    run = tmp_path / fault
    state = await execute(source_case[1], run, mode="baseline", max_revisions=0,
                          worker=FieldWorker(source_case, field_fault=fault))
    assert state["status"] == "failed"
    no_supported_export(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict,status", [("unsupported", "needs_revision"),
                                           ("not_established", "needs_revision"),
                                           ("unassessed", "failed")])
async def test_field_review_vetoes_positive_record_review(source_case, tmp_path, verdict, status):
    run = tmp_path / verdict
    state = await execute(source_case[1], run, mode="baseline", max_revisions=0,
                          worker=FieldWorker(source_case, field_verdicts=[verdict]))
    assert state["status"] == status
    assert state["field_review_outcome"] == verdict
    if verdict == "unassessed":
        assert "unfinished review, not missing scientific evidence" in state["error"]
    no_supported_export(run)


@pytest.mark.asyncio
async def test_revision_receives_negatives_and_reviews_current_values(source_case, tmp_path):
    worker = FieldWorker(source_case, field_verdicts=["unsupported", "supported"])
    state = await execute(source_case[1], tmp_path / "repaired", mode="baseline", worker=worker)
    assert state["status"] == "supported" and state["revision"] == 1
    assert [call["role"] for call in worker.calls] == ["integrator", "reviewer", "integrator", "reviewer"]
    assert (worker.calls[2]["directory"] / "previous_field_review.json").exists()
    second = worker.calls[3]["directory"]
    current = json.loads((second / "field_review.json").read_text())
    assert current["prior_review_sha256"] and current["prior_workflow_sha256"]
    assert (second / "previous_field_candidate.json").exists()
    assert "explicit source-backed adjudication" in worker.calls[3]["prompt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("adjudicate", [False, True])
async def test_unchanged_negative_cannot_be_silently_supported(source_case, tmp_path, adjudicate):
    worker = FieldWorker(source_case, field_verdicts=["not_established", "supported"],
                         target="state:nuclei", pointer="/states/0/description", adjudicate=adjudicate)
    run = tmp_path / "unchanged"
    state = await execute(source_case[1], run, mode="baseline", worker=worker)
    assert state["status"] == ("supported" if adjudicate else "failed")
    if not adjudicate:
        assert "unchanged prior negative field" in state["error"]
        no_supported_export(run)


@pytest.mark.asyncio
async def test_legacy_optout_is_explicit_and_recorded(source_case, tmp_path):
    worker = FieldWorker(source_case, field_fault="missing")
    run = tmp_path / "legacy"
    state = await execute(source_case[1], run, mode="baseline", worker=worker,
                          require_field_review=False)
    assert state["status"] == "supported" and state["require_field_review"] is False
    assert "field_review" not in state["artifacts"]
    assert json.loads((run / "config.json").read_text())["require_field_review"] is False


@pytest.mark.asyncio
async def test_positive_field_review_does_not_clear_blocking_unknown(source_case, tmp_path):
    worker = FakeWorker(source_case, blocking=True)
    run = tmp_path / "blocking"
    state = await execute(source_case[1], run, mode="baseline", worker=worker)
    assert state["field_review_outcome"] == "supported"
    assert state["status"] == "research_needed"
    no_supported_export(run)


@pytest.mark.asyncio
async def test_field_review_self_check_tool_exposes_integrity_not_acceptance(
        source_case, tmp_path, monkeypatch, capsys):
    from genomics_discovery.curation.workflow_extraction.__main__ import main

    worker = FieldWorker(source_case, field_verdicts=["not_established"])
    run = tmp_path / "self_check"
    await execute(source_case[1], run, mode="baseline", max_revisions=0, worker=worker)
    directory = worker.calls[-1]["directory"]
    monkeypatch.setattr(sys, "argv", ["workflow-extraction", "check",
        str(directory / "field_review.json"), "--kind", "field_review",
        "--packet", str(source_case[1]), "--candidate", str(directory / "candidate.json")])
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True
    assert "no scientific support verdict" in output["boundary"]
    no_supported_export(run)


@pytest.mark.asyncio
async def test_field_review_profile_rejects_non_boolean_before_run(source_case, tmp_path):
    run = tmp_path / "invalid_policy"
    with pytest.raises(ValueError, match="require_field_review must be a boolean"):
        await execute(source_case[1], run, require_field_review="yes")
    assert not run.exists()
