"""Synthetic benchmark accounting; these fixtures do not validate scientific labels."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from test_comparison_profiles import example as example

from genomics_discovery.curation.protocol_benchmark.__main__ import main
from genomics_discovery.curation.protocol_benchmark.models import (
    BenchmarkAssessment, ProtocolReference, ReferenceReview,
)
from genomics_discovery.curation.protocol_benchmark.scoring import score_assessment
from genomics_discovery.curation.protocol_benchmark.validation import (
    reference_review_outcome, validate_assessment, validate_reference, validate_reference_review,
)
from genomics_discovery.curation.workflow_extraction.models import digest


@pytest.fixture
def records(example):
    workflow, packet, _, evidence, source = example
    refs = [{"source_id": ev["source_id"], "passage_id": ev["passage_id"]} for ev in evidence]
    reference = ProtocolReference.model_validate({
        "case_id": "fixture", **{field: packet[field] for field in ("method_id", "scope", "variant")},
        "source_packet_sha256": digest(packet), "measurement": "Scoped fractionation interfaces",
        "obligations": [{"id": ident, "kind": kind, "text": text, "criticality": criticality,
                         "why_needed": "Preserve the stated source distinction.", "evidence": [evidence[0]]}
            for ident, kind, text, criticality in (
                ("fractions", "operation", "Separate DNA and RNA fractions.", "critical"),
                ("attribution", "information", "Retain indexed-well attribution.", "required"))],
        "unknowns": [{"id": "enzyme", "question": "Which exact reverse-transcription enzyme is used?",
                     "consequence": "Enzyme-specific mechanism is not established.",
                     "inspected_passages": refs, "next_evidence": "Retrieve the exact enzyme record."}],
        "admissible_decompositions": [
            {"id": "combined", "description": "One operation may retain fractionation and attribution.",
             "obligation_ids": ["fractions", "attribution"]},
            {"id": "separate", "description": "Separate actions may preserve the same interfaces.",
             "obligation_ids": ["fractions", "attribution"]}],
        "query_cases": [{"id": "fraction_query", "question": "Which fractions are retained?",
                         "answer": "DNA and RNA fractions.", "obligation_ids": ["fractions"],
                         "evidence": [evidence[0]]}],
        "source_coverage": [{"source_id": "paper",
            "inspected_passage_ids": [ev["passage_id"] for ev in evidence],
            "role_in_assay": "Primary synthetic protocol",
            "notes": "Reference obligations intentionally cover only part of the workflow."}],
    })
    checks = lambda items: [{"target_id": item.id, "verdict": "supported",
        "rationale": "Synthetic evaluator judgment, not an expert label.", "evidence": [evidence[0]]}
        for item in items]
    review = ReferenceReview.model_validate({
        "reference_sha256": digest(reference), "source_packet_sha256": digest(packet),
        "inspected_passages": refs, "obligation_checks": checks(reference.obligations),
        "unknown_checks": checks(reference.unknowns),
        "decomposition_checks": checks(reference.admissible_decompositions),
        "query_checks": checks(reference.query_cases), "missing_obligations": [],
    })
    assessment = BenchmarkAssessment.model_validate({
        "reference_sha256": digest(reference), "workflow_sha256": digest(workflow),
        "source_packet_sha256": digest(packet), "inspected_passages": refs,
        "obligations": [{"obligation_id": item.id, "outcome": "covered",
            "targets": ["operation:separate"], "rationale": "The same operation carries both facts.",
            "evidence": [evidence[0]]} for item in reference.obligations],
        "factual_audits": [{"id": "fraction_fact", "claim": "Separate DNA and RNA fractions.",
            "targets": ["operation:separate"], "verdict": "supported",
            "rationale": "The cited source describes this action.", "evidence": [evidence[0]]}],
    })
    return workflow, packet, reference, review, assessment, source


def test_many_obligations_can_share_a_composite_witness_and_alternatives(records):
    workflow, packet, reference, review, assessment, _ = records
    assert validate_reference(reference, packet) == []
    assert validate_reference_review(review, reference, packet) == []
    assert reference_review_outcome(review) == "supported_reference_candidate"
    assert validate_assessment(assessment, reference, workflow, packet) == []
    score = score_assessment(assessment, reference, workflow, packet)
    assert score["obligation_coverage"]["strict_covered_fraction"] == 1
    assert len(reference.admissible_decompositions) == 2
    assert score["reference_status"] == "model_authored_reference_candidate"


def test_partial_gold_unmatched_outputs_are_unjudged_and_not_false(records):
    workflow, packet, reference, _, assessment, _ = records
    score = score_assessment(assessment, reference, workflow, packet)
    assert "operation:rt" in score["unmatched_targets"]["target_ids"]
    assert score["unmatched_targets"]["status"] == "unjudged"
    assert score["factual_audit"]["counts"]["unsupported"] == 0
    assert score["factual_audit"]["supported_claim_precision"] is None
    assert score["obligation_coverage"]["counts"]["missing"] == 0


def test_abstentions_and_unassessed_coverage_cannot_create_perfect_precision(records):
    workflow, packet, reference, _, assessment, _ = records
    data = assessment.model_dump(mode="json")
    for row in data["obligations"]:
        row.update(outcome="unassessed", targets=[], evidence=[])
    data["factual_audits"][0].update(verdict="abstained", evidence=[])
    data["factual_audit_scope"] = "complete_declared"
    score = score_assessment(BenchmarkAssessment.model_validate(data), reference, workflow, packet)
    assert score["obligation_coverage"]["strict_covered_fraction"] == 0
    assert score["obligation_coverage"]["critical_not_covered"] == ["fractions"]
    assert score["factual_audit"]["verified_supported_fraction"] == 0
    assert score["factual_audit"]["supported_claim_precision"] is None


def test_zero_audit_denominator_is_not_perfect_precision(records):
    workflow, packet, reference, _, assessment, _ = records
    empty = assessment.model_copy(update={"factual_audits": (), "factual_audit_scope": "complete_declared"})
    score = score_assessment(empty, reference, workflow, packet)["factual_audit"]
    assert score["denominator"] == 0
    assert score["verified_supported_fraction"] is None
    assert score["supported_claim_precision"] is None


def test_precision_requires_complete_decided_claim_audit(records):
    workflow, packet, reference, _, assessment, _ = records
    complete = assessment.model_copy(update={"factual_audit_scope": "complete_declared"})
    assert score_assessment(complete, reference, workflow, packet)["factual_audit"]["supported_claim_precision"] == 1
    data = complete.model_dump(mode="json")
    data["factual_audits"][0].update(verdict="uncertain", evidence=[])
    score = score_assessment(BenchmarkAssessment.model_validate(data), reference, workflow, packet)
    assert score["factual_audit"]["counts"]["unsupported"] == 0
    assert score["factual_audit"]["possible_supported_fraction"] == 1
    assert score["factual_audit"]["supported_claim_precision"] is None


def test_reachability_and_target_presence_never_automatically_cover_order(records):
    workflow, packet, reference, _, assessment, _ = records
    data = reference.model_dump(mode="json")
    data["obligations"][0]["kind"] = "ordering"
    reference = ProtocolReference.model_validate(data)
    evaluated = assessment.model_dump(mode="json")
    evaluated["reference_sha256"] = digest(reference)
    evaluated["obligations"][0].update(outcome="unassessed", targets=["operation:separate", "operation:rt"],
                                      evidence=[])
    score = score_assessment(BenchmarkAssessment.model_validate(evaluated), reference, workflow, packet)
    assert score["obligation_coverage"]["by_kind"]["ordering"] == {"unassessed": 1}
    assert score["obligation_coverage"]["strict_covered_fraction"] == .5
    assert score["obligation_coverage"]["critical_not_covered"] == ["fractions"]


@pytest.mark.parametrize("defect", ["workflow_hash", "reference_hash", "target", "quote", "uninspected",
                                    "missing_obligation", "duplicate_obligation"])
def test_bound_assessment_corruption_fails_closed(records, defect):
    workflow, packet, reference, _, assessment, _ = records
    data = assessment.model_dump(mode="json")
    if defect == "workflow_hash":
        data["workflow_sha256"] = "0" * 64
    elif defect == "reference_hash":
        data["reference_sha256"] = "0" * 64
    elif defect == "target":
        data["obligations"][0]["targets"] = ["operation:invented"]
    elif defect == "quote":
        data["obligations"][0]["evidence"][0]["quote"] = "An invented quotation."
    elif defect == "uninspected":
        data["inspected_passages"] = []
    elif defect == "missing_obligation":
        data["obligations"].pop()
    else:
        data["obligations"].append(deepcopy(data["obligations"][0]))
    candidate = BenchmarkAssessment.model_validate(data)
    assert validate_assessment(candidate, reference, workflow, packet)
    with pytest.raises(ValueError):
        score_assessment(candidate, reference, workflow, packet)


@pytest.mark.parametrize("defect", ["unknown_obligation", "duplicate_source", "source_scope", "variant"])
def test_reference_scope_and_internal_ids_are_bound(records, defect):
    _, packet, reference, *_ = records
    data = reference.model_dump(mode="json")
    if defect == "unknown_obligation":
        data["admissible_decompositions"][0]["obligation_ids"] = ["missing"]
    elif defect == "duplicate_source":
        data["source_coverage"].append(deepcopy(data["source_coverage"][0]))
    elif defect == "source_scope":
        data["source_coverage"][0]["inspected_passage_ids"] = []
    else:
        data["variant"] = "foreign route"
    assert validate_reference(ProtocolReference.model_validate(data), packet)


@pytest.mark.parametrize("group", ["obligation_checks", "unknown_checks", "decomposition_checks", "query_checks"])
def test_review_requires_exhaustive_checks_for_each_reference_component(records, group):
    _, packet, reference, review, *_ = records
    data = review.model_dump(mode="json")
    data[group].pop()
    assert validate_reference_review(ReferenceReview.model_validate(data), reference, packet)


def test_supported_scoped_unknown_does_not_require_fabricated_absence_quote(records):
    _, packet, reference, review, *_ = records
    data = review.model_dump(mode="json")
    data["unknown_checks"][0]["evidence"] = []
    assert validate_reference_review(ReferenceReview.model_validate(data), reference, packet) == []
    data["inspected_passages"] = data["inspected_passages"][:1]
    assert validate_reference_review(ReferenceReview.model_validate(data), reference, packet)


def test_new_source_supported_missing_obligation_blocks_reference_gate(records):
    _, packet, reference, review, *_ = records
    data = review.model_dump(mode="json")
    missing = reference.obligations[0].model_dump(mode="json")
    missing.update(id="additional_fact", text="Additional scoped source requirement.")
    data["missing_obligations"] = [missing]
    review = ReferenceReview.model_validate(data)
    assert validate_reference_review(review, reference, packet) == []
    assert reference_review_outcome(review) == "needs_revision"


def test_uncertain_review_stays_unresolved_without_cancelling_integrity(records):
    _, packet, reference, review, *_ = records
    data = review.model_dump(mode="json")
    data["decomposition_checks"][0].update(verdict="uncertain", evidence=[])
    review = ReferenceReview.model_validate(data)
    assert validate_reference_review(review, reference, packet) == []
    assert reference_review_outcome(review) == "research_needed"


def test_factual_audit_is_independent_of_reference_matches(records):
    workflow, packet, reference, _, assessment, _ = records
    data = assessment.model_dump(mode="json")
    false = deepcopy(data["factual_audits"][0])
    false.update(id="false_amplification", claim="The reported DNA fraction is amplified.",
                 targets=["operation:rt"], verdict="unsupported",
                 rationale="The source explicitly says DNA is not amplified.")
    ev = deepcopy(reference.obligations[0].evidence[0].model_dump(mode="json"))
    p = packet["sources"][0]["passages"][1]
    ev.update(passage_id=p["passage_id"], quote=p["text"])
    false["evidence"] = [ev]
    data["factual_audits"].append(false)
    score = score_assessment(BenchmarkAssessment.model_validate(data), reference, workflow, packet)
    assert score["obligation_coverage"]["strict_covered_fraction"] == 1
    assert score["factual_audit"]["counts"]["unsupported"] == 1


def test_changed_original_and_authored_gold_status_are_rejected(records):
    _, packet, reference, _, _, source = records
    source.write_text("Changed original source.")
    assert validate_reference(reference, packet)[0].startswith("Invalid source packet/original")
    with pytest.raises(ValidationError):
        ProtocolReference.model_validate({**reference.model_dump(), "annotation_status": "expert_gold"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        ProtocolReference.model_validate({**reference.model_dump(), "accepted": True})


def test_module_schema_cli_is_toolless_and_check_preserves_negative_outcomes(records, tmp_path, capsys):
    workflow, packet, reference, review, assessment, _ = records
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "genomics_discovery.curation.protocol_benchmark",
        "schema", "reference"], cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(root / "src")},
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["additionalProperties"] is False
    documents = {"packet": packet, "workflow": workflow.model_dump(mode="json"),
        "reference": reference.model_dump(mode="json"), "review": review.model_dump(mode="json"),
        "assessment": assessment.model_dump(mode="json")}
    paths = {key: tmp_path / f"{key}.json" for key in documents}
    for key, value in documents.items():
        paths[key].write_text(json.dumps(value))
    assert main(["check", "review", str(paths["review"]), "--packet", str(paths["packet"]),
                 "--reference", str(paths["reference"])]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["integrity_valid"] and checked["review_outcome"] == "supported_reference_candidate"
    assert "accepted" not in checked
    assert main(["score", str(paths["assessment"]), "--packet", str(paths["packet"]),
        "--reference", str(paths["reference"]), "--workflow", str(paths["workflow"])]) == 0
    score = json.loads(capsys.readouterr().out)
    assert score["unmatched_targets"]["status"] == "unjudged"
    assert score["factual_audit"]["supported_claim_precision"] is None
