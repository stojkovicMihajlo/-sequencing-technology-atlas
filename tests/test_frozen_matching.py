"""Deterministic policy tests; fixture evidence is not a scientific gold standard."""

import json
from dataclasses import asdict

import pytest
from pydantic import ValidationError

from test_workflow_transformations import cases as cases
from test_operation_registry import assignment, review, commit, admission_for, decision_for
from test_operation_registry_live import prepare
from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.operation_registry.store import catalogue
from genomics_discovery.curation.operation_registry.frozen_matching import (
    CandidateAssessment, ExtensionRequest, MatchingReport, make_plan, validate_report, reconcile_reports,
)
from genomics_discovery.curation.workflow_extraction.models import digest
from playground.workflow_concepts_live import execute


def setup(cases, tmp_path):
    manifest, registry = prepare(cases, tmp_path)
    initial = assignment(cases, registry.snapshot())
    commit(registry, initial, review(initial, cases), *cases[:2])
    return manifest, registry, initial


def report_for(cases, snapshot, plan, verdict="supported"):
    rows = []
    for pair in plan["pairs"]:
        index = list(cases[0]).index(pair["occurrence_id"])
        proposal = assignment(cases, snapshot, index=index)
        checked = review(proposal, cases)
        fields = list(admission_for(decision_for(proposal), checked).definition_checks)
        if verdict != "supported":
            fields[0] = fields[0].model_copy(update={"verdict": verdict})
        rows.append(CandidateAssessment(**pair, inspected_passages=checked.checks[0].inspected_passages,
                                        criteria=tuple(fields)))
    return MatchingReport(plan_sha256=digest(plan), assessments=tuple(rows))


def test_plan_invariant_to_presentation_order(cases, tmp_path):
    _, registry, _ = setup(cases, tmp_path)
    snap = catalogue(registry.snapshot())
    forward = make_plan(snap, cases[0])
    reverse = make_plan(snap, dict(reversed(list(cases[0].items()))))
    assert forward == reverse
    assert len(forward["pairs"]) == len(cases[0]) * len(snap["concepts"])


@pytest.mark.parametrize("verdict,expected", [("supported", "member"), ("unsupported", "non_member"),
                                              ("unresolved", "unknown")])
def test_verdict_derived_from_criteria(cases, tmp_path, verdict, expected):
    _, registry, _ = setup(cases, tmp_path)
    snap = catalogue(registry.snapshot())
    plan = make_plan(snap, cases[0])
    report = report_for(cases, snap, plan, verdict)
    assert {r["status"] for r in validate_report(report, plan, snap, *cases[:2])} == {expected}


@pytest.mark.parametrize("fault", ["missing_pair", "duplicate_pair", "missing_field", "duplicate_field",
                                  "wrong_quote", "unknown_passage", "changed_catalogue", "changed_plan"])
def test_incomplete_or_changed_reports_fail(cases, tmp_path, fault):
    _, registry, _ = setup(cases, tmp_path)
    snap = catalogue(registry.snapshot())
    plan = make_plan(snap, cases[0])
    report = report_for(cases, snap, plan).model_dump(mode="json")
    row = report["assessments"][0]
    if fault == "missing_pair": report["assessments"].pop()
    elif fault == "duplicate_pair": report["assessments"].append(row)
    elif fault == "missing_field": row["criteria"].pop()
    elif fault == "duplicate_field": row["criteria"].append(row["criteria"][0])
    elif fault == "wrong_quote": row["criteria"][0]["evidence"][0]["quote"] = "A fabricated sentence"
    elif fault == "unknown_passage": row["inspected_passages"][0]["passage_id"] = "not_a_passage"
    elif fault == "changed_catalogue": next(iter(snap["concepts"].values()))["scope_note"] = "changed"
    elif fault == "changed_plan": plan["write_policy"] = "write_anything"
    with pytest.raises(ValueError):
        validate_report(MatchingReport.model_validate(report), plan, snap, *cases[:2])


def test_worker_cannot_author_overall_verdict(cases, tmp_path):
    _, registry, _ = setup(cases, tmp_path)
    snap = catalogue(registry.snapshot())
    plan = make_plan(snap, cases[0])
    report = report_for(cases, snap, plan).model_dump(mode="json")
    report["assessments"][0]["membership"] = "member"
    with pytest.raises(ValidationError): MatchingReport.model_validate(report)


@pytest.mark.parametrize("second,expected", [("unsupported", "disagreement"), ("unresolved", "unknown")])
def test_conflict_never_becomes_positive(cases, tmp_path, second, expected):
    _, registry, _ = setup(cases, tmp_path)
    snap = catalogue(registry.snapshot())
    plan = make_plan(snap, cases[0])
    result = reconcile_reports(report_for(cases, snap, plan), report_for(cases, snap, plan, second),
                               plan, snap, *cases[:2])
    assert {r["status"] for r in result["decisions"]} == {expected}
    assert not result["registry_mutated"]


class FrozenWorker:
    model, effort = "fixture", "fixture"

    def __init__(self, cases, snapshot, extension=False):
        self.cases, self.snapshot, self.calls, self.extension = cases, snapshot, 0, extension

    async def run(self, prompt, path):
        self.calls += 1
        plan = json.loads((path.parent / "plan.json").read_text())
        report = report_for(self.cases, self.snapshot, plan)
        if self.extension:
            example = assignment(self.cases, self.snapshot)
            request = ExtensionRequest(occurrence_id=plan["pairs"][0]["occurrence_id"],
                definition=example.definition.model_copy(update={"id": "pending_new_label"}),
                relation=example.relation, closest_concept_ids=(example.definition.id,),
                distinction="Synthetic pending proposal; this does not establish usefulness",
                properties_to_preserve=("Keep source conditions",), evidence=example.members[0].evidence)
            report = report.model_copy(update={"extension_requests": (request,)})
        (path / "artifact.json").write_text(report.model_dump_json())
        (path / "prompt.txt").write_text(prompt)
        thread, usage = f"fixture-{self.calls}", {"input_tokens": 1, "output_tokens": 1}
        events = [{"type": "thread.started", "thread_id": thread}, {"type": "turn.started"},
                  {"type": "turn.completed", "usage": usage}]
        (path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        result = WorkerResult(0, thread, usage, .01, None)
        (path / "result.json").write_text(json.dumps(asdict(result)))
        (path / "final.txt").write_text("Fixture complete")
        return result


async def test_default_live_mode_cannot_write_even_with_extension(cases, tmp_path):
    manifest, registry, _ = setup(cases, tmp_path)
    before = registry.path.read_bytes()
    worker = FrozenWorker(cases, catalogue(registry.snapshot()), extension=True)
    state = await execute(manifest, registry.path, tmp_path / "run", worker=worker)
    assert state["status"] == "completed_frozen" and worker.calls == 2
    assert registry.path.read_bytes() == before
    assert state["pending_extensions"] == 2
    result = json.loads((tmp_path / "run/matching_results.json").read_text())
    assert result["decisions"][0]["status"] == "member"
    assert len(result["execution"]["threads"]) == 2
    assert all(r["status"] == "pending_curation" for r in result["pending_extensions"])


async def test_supplied_proposal_cannot_silently_enable_curation(cases, tmp_path):
    manifest, registry, _ = setup(cases, tmp_path)
    with pytest.raises(ValueError, match="explicit curation"):
        await execute(manifest, registry.path, tmp_path / "run", supplied_proposal=tmp_path / "anything")
