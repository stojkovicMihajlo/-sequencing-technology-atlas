"""Synthetic completion/lineage controls using real native archive validation.

Fake workers establish runtime gates, not scientific completeness or assay validity.
The source-specific finding count below belongs to this fixture, not a gold protocol.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from test_workflow_extraction_runtime import (
    FakeWorker,
    repair_inputs,
)
from test_workflow_extraction_runtime import (
    source_case as source_case,
)

from genomics_discovery.curation.workflow_coverage import (
    CoverageReview,
    IntegrationCoverage,
    validate_coverage,
    validate_coverage_review,
)
from genomics_discovery.curation.workflow_extraction import extraction_pipeline as runtime
from genomics_discovery.curation.workflow_extraction.models import (
    ReadingReport,
    Workflow,
    WorkflowReview,
    digest,
    review_outcome,
)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def coverage_for(workflow_raw, reports, packet):
    """Bind each synthetic source finding to its declared material/data route."""
    targets = {
        "prepare": ["operation:wash", "state:washed"],
        "branch": ["flow:branch_a", "flow:branch_b"],
        "readout": ["operation:seq_a", "operation:seq_b"],
        "merge": ["operation:merge", "state:combined"],
    }
    raw = {
        "source_packet_sha256": digest(packet),
        "workflow_sha256": digest(Workflow.model_validate(workflow_raw)),
        "readers": [
            {"role": role, "report_sha256": digest(report)}
            for role, report in reports.items()
        ],
        "entries": [
            {
                "reader_role": role,
                "finding_id": finding.id,
                "dispositions": [{
                    "kind": "represented",
                    "covers": finding.description,
                    "targets": targets[finding.id],
                    "rationale": "Account for the stated synthetic source interface.",
                    "evidence": [item.model_dump(mode="json") for item in finding.evidence],
                }],
            }
            for role, report in reports.items()
            for finding in report.findings
        ],
    }
    return IntegrationCoverage.model_validate(raw).model_dump(mode="json")


def coverage_review_for(coverage_raw, primary_raw, verdict):
    coverage = IntegrationCoverage.model_validate(coverage_raw)
    primary = WorkflowReview.model_validate(primary_raw)
    checks = []
    for index, entry in enumerate(coverage.entries):
        evidence = entry.dispositions[0].evidence
        checks.append({
            "reader_role": entry.reader_role,
            "finding_id": entry.finding_id,
            "verdict": verdict if index == 0 else "supported",
            "rationale": "Fresh synthetic source check of finding coverage, not acceptance.",
            "evidence": [item.model_dump(mode="json") for item in evidence],
        })
    inspected = sorted({
        (item["source_id"], item["passage_id"])
        for check in checks for item in check["evidence"]
    })
    return CoverageReview.model_validate({
        "coverage_sha256": digest(coverage),
        "workflow_review_sha256": digest(primary),
        "inspected_passages": [
            {"source_id": sid, "passage_id": pid} for sid, pid in inspected
        ],
        "checks": checks,
        "omissions": [],
        "notes": ["A fake worker cannot certify scientific validity."],
    }).model_dump(mode="json")


class CompletionWorker(FakeWorker):
    """Write both sidecars inside the same archived integrator/reviewer calls."""

    def __init__(self, case, *, coverage_fault=None, coverage_verdicts=(),
                 emit_coverage=True, on_integrator=None, **kwargs):
        super().__init__(case, **kwargs)
        self.coverage_fault = coverage_fault
        self.coverage_verdicts = list(coverage_verdicts)
        self.emit_coverage = emit_coverage
        self.on_integrator = on_integrator
        self.reader_documents = {}
        self.coverage_history = []
        self.coverage_review_history = []
        self.reader_gap_detail.update(
            description="OLD_GAP_OPINION_SENTINEL",
            next_evidence="OLD_GAP_RETRIEVAL_OPINION_SENTINEL",
        )

    async def run(self, prompt, attempt_dir):
        # Read the role from this call, not calls[-1], because readers are parallel.
        role = re.search(r"ROLE:\s*(\w+)", prompt).group(1)
        result = await super().run(prompt, attempt_dir)
        directory = Path(attempt_dir)
        if role in {"chemistry", "information"}:
            raw = read_json(directory / "artifact.json")
            original = raw["findings"][0]["evidence"][0]
            findings = {
                "chemistry": [
                    ("prepare", "Wash nuclei with buffer at 4 C."),
                    ("branch", "Split washed nuclei into aliquots A and B."),
                ],
                "information": [
                    ("readout", "Sequence aliquot A to produce reads A. "
                     "Sequence aliquot B to produce reads B."),
                    ("merge", "Merge reads A and reads B into combined reads."),
                ],
            }[role]
            raw["findings"] = [
                {"id": ident, "description": quote, "evidence": [{
                    **original, "quote": quote,
                    "rationale": "The synthetic source explicitly states this interface.",
                }]}
                for ident, quote in findings
            ]
            write_json(directory / "artifact.json", raw)
            self.reader_documents[role] = ReadingReport.model_validate(raw)
        elif role == "integrator" and self.emit_coverage:
            raw = coverage_for(read_json(directory / "artifact.json"),
                               self.reader_documents, self.packet)
            if self.integrations > 1 and self.coverage_fault == "stale_repair_coverage":
                raw = copy.deepcopy(self.coverage_history[0])
            elif self.coverage_fault == "missing_finding":
                raw["entries"].pop()
            elif self.coverage_fault == "wrong_reader_binding":
                raw["readers"][0]["report_sha256"] = "0" * 64
            if self.coverage_fault != "missing_coverage":
                write_json(directory / "coverage.json", raw)
            self.coverage_history.append(raw)
            if self.on_integrator is not None:
                self.on_integrator(self, directory)
        elif role == "reviewer" and self.emit_coverage:
            verdict = (self.coverage_verdicts[self.reviews - 1]
                       if self.reviews <= len(self.coverage_verdicts) else "supported")
            raw = coverage_review_for(read_json(directory / "coverage.json"),
                                      read_json(directory / "artifact.json"), verdict)
            if self.coverage_fault == "missing_coverage_check":
                raw["checks"].pop()
            elif self.coverage_fault == "wrong_coverage_binding":
                raw["coverage_sha256"] = "0" * 64
            elif self.coverage_fault == "wrong_primary_binding":
                raw["workflow_review_sha256"] = "0" * 64
            if self.coverage_fault != "missing_coverage_review":
                write_json(directory / "coverage_review.json", raw)
            self.coverage_review_history.append(raw)
        return result


async def execute_completion(case, tmp_path, *, max_revisions=0, **kwargs):
    worker = CompletionWorker(case, **kwargs)
    run = tmp_path / "completion"
    state = await runtime.execute(case[1], run, worker=worker,
                                  require_coverage=True, max_revisions=max_revisions)
    return state, worker, run


def assert_no_supported_exports(state, run):
    assert "graph_candidate" not in state["artifacts"]
    assert not (run / "graph_candidate.json").exists()
    assert not (run / "protocol.txt").exists()


async def test_completion_accounts_reader_findings_before_same_turn_review(source_case, tmp_path):
    state, worker, run = await execute_completion(source_case, tmp_path)
    assert state["status"] == "supported" and state["require_coverage"] is True
    assert worker.max_active_readers == 2
    assert [call["role"] for call in worker.calls] == [
        "chemistry", "information", "integrator", "reviewer",
    ]
    assert len({attempt["thread_id"] for attempt in state["attempts"]}) == 4
    assert all(attempt["transport_complete"] for attempt in state["attempts"])
    assert all(attempt["completed_turns"] == 1 for attempt in state["attempts"])
    integrator, reviewer = worker.calls[2:]
    reports = worker.reader_documents
    workflow = Workflow.model_validate(read_json(integrator["directory"] / "artifact.json"))
    coverage = IntegrationCoverage.model_validate(
        read_json(integrator["directory"] / "coverage.json"))
    assert validate_coverage(coverage, workflow, reports, worker.packet) == []
    assert {(entry.reader_role, entry.finding_id) for entry in coverage.entries} == {
        (role, finding.id) for role, report in reports.items() for finding in report.findings
    }
    assert set(state["attempts"][2]["inputs"]) == {"chemistry", "information"}
    assert set(state["attempts"][3]["inputs"]) == {
        "candidate", "coverage", "finding_view", "field_review_scaffold"}
    for role, report in reports.items():
        copied = ReadingReport.model_validate(read_json(integrator["directory"] / f"{role}.json"))
        assert digest(copied) == digest(report)
    primary = WorkflowReview.model_validate(read_json(reviewer["directory"] / "artifact.json"))
    secondary = CoverageReview.model_validate(
        read_json(reviewer["directory"] / "coverage_review.json"))
    assert validate_coverage_review(
        secondary, coverage, primary, workflow, reports, worker.packet) == []
    assert "coverage_review.json" in reviewer["prompt"]
    assert "CoverageReview" in reviewer["prompt"]
    assert digest(coverage) in reviewer["prompt"]
    assert (run / "graph_candidate.json").is_file()
    assert (run / "protocol.txt").is_file()


@pytest.mark.parametrize("fault, error", [
    ("missing_coverage", "coverage.json"),
    ("missing_finding", "Missing finding disposition"),
    ("wrong_reader_binding", "report_sha256"),
])
async def test_invalid_integration_accounting_prevents_reviewer_launch(
    source_case, tmp_path, fault, error,
):
    state, worker, run = await execute_completion(source_case, tmp_path, coverage_fault=fault)
    assert state["status"] == "failed" and error in state["error"]
    assert worker.integrations == 1 and worker.reviews == 0
    assert [call["role"] for call in worker.calls] == ["chemistry", "information", "integrator"]
    assert_no_supported_exports(state, run)


@pytest.mark.parametrize("fault, error", [
    ("missing_coverage_review", "coverage_review.json"),
    ("missing_coverage_check", "check"),
    ("wrong_coverage_binding", "coverage_sha256"),
    ("wrong_primary_binding", "workflow_review_sha256"),
])
async def test_same_turn_secondary_review_is_mandatory_and_bound(
    source_case, tmp_path, fault, error,
):
    state, worker, run = await execute_completion(source_case, tmp_path, coverage_fault=fault)
    assert state["status"] == "failed" and error.lower() in state["error"].lower()
    assert worker.integrations == worker.reviews == 1
    assert_no_supported_exports(state, run)


@pytest.mark.parametrize("verdict, outcome", [
    ("unsupported", "needs_revision"), ("unresolved", "research_needed"),
])
async def test_positive_workflow_review_cannot_export_negative_coverage(
    source_case, tmp_path, verdict, outcome,
):
    state, worker, run = await execute_completion(
        source_case, tmp_path, coverage_verdicts=[verdict])
    assert state["status"] == outcome
    workflow = Workflow.model_validate(read_json(worker.calls[2]["directory"] / "artifact.json"))
    primary = WorkflowReview.model_validate(
        read_json(worker.calls[3]["directory"] / "artifact.json"))
    assert review_outcome(primary, workflow) == "supported"
    assert (worker.calls[3]["directory"] / "coverage_review.json").is_file()
    assert worker.integrations == worker.reviews == 1
    assert_no_supported_exports(state, run)
    # Legacy diagnostic format must not misleadingly imply a negative primary review.
    assert not (run / "graph_diagnostic.json").exists()


async def test_one_coverage_repair_rebinds_ledger_and_both_reviews(source_case, tmp_path):
    state, worker, _ = await execute_completion(
        source_case, tmp_path, max_revisions=1,
        coverage_verdicts=["unsupported", "supported"])
    assert state["status"] == "supported" and state["revision"] == 1
    assert [call["role"] for call in worker.calls] == [
        "chemistry", "information", "integrator", "reviewer", "integrator", "reviewer",
    ]
    assert worker.integrations == worker.reviews == 2
    expected_inputs = {
        "chemistry", "information", "previous_candidate", "previous_review",
        "previous_coverage", "previous_coverage_review",
        "previous_field_review",
    }
    assert set(state["attempts"][4]["inputs"]) == expected_inputs
    assert set(state["attempts"][5]["inputs"]) == {
        "candidate", "coverage", "finding_view", "field_review_scaffold",
        "previous_field_review", "previous_field_candidate"}
    workflow_digests = []
    for integ, review in ((worker.calls[2], worker.calls[3]), (worker.calls[4], worker.calls[5])):
        workflow = Workflow.model_validate(read_json(integ["directory"] / "artifact.json"))
        coverage = IntegrationCoverage.model_validate(
            read_json(integ["directory"] / "coverage.json"))
        primary = WorkflowReview.model_validate(read_json(review["directory"] / "artifact.json"))
        secondary = CoverageReview.model_validate(
            read_json(review["directory"] / "coverage_review.json"))
        assert coverage.workflow_sha256 == primary.workflow_sha256 == digest(workflow)
        assert secondary.coverage_sha256 == digest(coverage)
        assert secondary.workflow_review_sha256 == digest(primary)
        assert validate_coverage_review(
            secondary, coverage, primary, workflow, worker.reader_documents, worker.packet) == []
        workflow_digests.append(digest(workflow))
    assert workflow_digests[0] != workflow_digests[1]
    for role, report in worker.reader_documents.items():
        copied = ReadingReport.model_validate(
            read_json(worker.calls[4]["directory"] / f"{role}.json"))
        assert digest(copied) == digest(report)
    assert len({attempt["thread_id"] for attempt in state["attempts"]}) == 6


async def test_stale_repair_coverage_fails_before_second_reviewer(source_case, tmp_path):
    state, worker, run = await execute_completion(
        source_case, tmp_path, max_revisions=1,
        coverage_verdicts=["unsupported"], coverage_fault="stale_repair_coverage")
    assert state["status"] == "failed" and "workflow_sha256" in state["error"]
    assert worker.integrations == 2 and worker.reviews == 1
    assert_no_supported_exports(state, run)


async def test_negative_coverage_is_bounded_to_one_repair(source_case, tmp_path):
    state, worker, run = await execute_completion(
        source_case, tmp_path, max_revisions=1,
        coverage_verdicts=["unsupported", "unsupported"])
    assert state["status"] == "needs_revision" and state["revision"] == 1
    assert worker.integrations == worker.reviews == 2
    assert len(worker.calls) == 6
    assert_no_supported_exports(state, run)


@pytest.mark.parametrize("pin_kind", ["coverage_module", "skill_original", "skill_snapshot"])
async def test_completion_implementation_and_skill_pins_reject_mutation(
    source_case, tmp_path, monkeypatch, pin_kind,
):
    # Simulate a changed file digest rather than editing another owner's files.
    frozen_sha = runtime.sha
    changed = {"path": None}

    def altered_sha(path):
        return "0" * 64 if Path(path).resolve() == changed["path"] else frozen_sha(path)

    monkeypatch.setattr(runtime, "sha", altered_sha)

    def change_pinned_digest(worker, directory):
        config = read_json(directory.parents[1] / "config.json")
        files = config["implementation"]
        if pin_kind == "coverage_module":
            record = next(item for item in files
                          if Path(item["path"]).name == "workflow_coverage.py")
        else:
            name = "assay_extraction_skill" + ("_snapshot" if pin_kind.endswith("snapshot") else "")
            record = next(item for item in files if item.get("name") == name)
        assert len(record["sha256"]) == 64
        changed["path"] = Path(record["path"]).resolve()

    state, worker, run = await execute_completion(
        source_case, tmp_path, on_integrator=change_pinned_digest)
    assert state["status"] == "failed" and "changed" in state["error"].lower()
    assert str(changed["path"]) in state["error"]
    assert worker.integrations == 1 and worker.reviews == 0
    assert_no_supported_exports(state, run)


async def test_finding_view_keeps_lineage_without_old_gap_opinions(source_case, tmp_path):
    state, worker, _ = await execute_completion(source_case, tmp_path, reader_gap="resolved")
    assert state["status"] == "supported"
    reviewer = worker.calls[3]
    view = read_json(reviewer["directory"] / "finding_view.json")
    assert view["format_version"] == "coverage_finding_pages/1.0"
    entries = [entry for page in view["pages"] for entry in page["entries"]]
    expected = {(role, finding.id) for role, report in worker.reader_documents.items()
                for finding in report.findings}
    assert {(entry["reader_role"], entry["finding_id"]) for entry in entries} == expected
    assert all(page["claim_status"] == "unaccepted_reader_findings_and_integration_proposals"
               for page in view["pages"])
    serialized = json.dumps(view)
    assert "OLD_GAP_OPINION_SENTINEL" not in serialized
    assert "OLD_GAP_RETRIEVAL_OPINION_SENTINEL" not in serialized
    for forbidden_key in ("unresolved", "next_evidence", "blocking", "verdict"):
        assert f'"{forbidden_key}"' not in serialized
    assert "OLD_GAP_OPINION_SENTINEL" not in reviewer["prompt"]
    for page in view["pages"]:
        assert {item["role"]: item["report_sha256"] for item in page["readers"]} == {
            role: digest(report) for role, report in worker.reader_documents.items()
        }


async def test_single_large_reader_finding_does_not_prevent_fresh_review(source_case, tmp_path):
    class LargeFindingWorker(CompletionWorker):
        async def run(self, prompt, attempt_dir):
            result = await super().run(prompt, attempt_dir)
            if re.search(r"ROLE:\s*(\w+)", prompt).group(1) == "chemistry":
                path = Path(attempt_dir) / "artifact.json"
                document = read_json(path)
                document["findings"][0]["description"] += " Exact contextual finding." * 2000
                write_json(path, document)
                self.reader_documents["chemistry"] = ReadingReport.model_validate(document)
            return result

    worker = LargeFindingWorker(source_case)
    state = await runtime.execute(source_case[1], tmp_path / "large_finding", worker=worker,
                                  require_coverage=True, max_revisions=0)
    assert state["status"] == "supported"  # Injected worker judgments, not scientific acceptance.
    assert worker.reviews == 1
    reviewer = worker.calls[3]
    view = read_json(reviewer["directory"] / "finding_view.json")
    references = [entry for page in view["pages"] for entry in page["entries"]
                  if entry.get("content_mode") == "bound_reference"]
    assert [(entry["reader_role"], entry["finding_id"]) for entry in references] == [
        ("chemistry", "prepare")]
    assert ".workflow_coverage read " in reviewer["prompt"]
    assert "OLD_GAP_OPINION_SENTINEL" not in json.dumps(view)


async def test_coverage_sidecars_cannot_bypass_native_turn_completion(source_case, tmp_path):
    state, worker, run = await execute_completion(source_case, tmp_path, fault="unfinished_turn")
    assert state["status"] == "failed" and worker.reviews == 0
    assert_no_supported_exports(state, run)


async def test_coverage_opt_out_preserves_existing_formats_and_proofs(source_case, tmp_path):
    worker = FakeWorker(source_case)
    run = tmp_path / "legacy"
    state = await runtime.execute(source_case[1], run, worker=worker, require_coverage=False)
    assert state["status"] == "supported" and state["format_version"] == "workflow_run/1.0"
    config = read_json(run / "config.json")
    assert config["format_version"] == "workflow_run_config/1.0"
    assert [call["role"] for call in worker.calls] == [
        "chemistry", "information", "integrator", "reviewer",
    ]
    assert set(state["attempts"][3]["inputs"]) == {"candidate", "field_review_scaffold"}
    assert all(attempt["transport_complete"] for attempt in state["attempts"])
    for call in worker.calls:
        assert not (call["directory"] / "coverage.json").exists()
        assert not (call["directory"] / "coverage_review.json").exists()
    primary = read_json(worker.calls[3]["directory"] / "artifact.json")
    assert set(primary) == {
        "workflow_sha256", "source_packet_sha256", "inspected_passages",
        "checks", "omissions", "notes",
    }
    assert (run / "graph_candidate.json").is_file() and (run / "protocol.txt").is_file()


@pytest.mark.parametrize("mode", ["baseline", "review_only", "repair"])
async def test_coverage_requires_fresh_specialist_lineage_before_start(
    source_case, tmp_path, mode,
):
    worker = FakeWorker(source_case)
    options = {}
    if mode == "review_only":
        seed = tmp_path / "seed.json"
        write_json(seed, worker.workflow)
        options["initial_workflow"] = seed
    elif mode == "repair":
        seed, feedback = repair_inputs(worker, tmp_path)
        options.update(initial_workflow=seed, repair_review=feedback)
    run = tmp_path / "no_lineage"
    with pytest.raises(ValueError, match="specialists.*reader lineage"):
        await runtime.execute(source_case[1], run, worker=worker, mode=mode,
                              require_coverage=True, **options)
    assert worker.calls == [] and not run.exists()
