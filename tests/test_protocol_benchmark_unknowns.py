"""Versioned uncertainty accounting with synthetic evidence, never native calls."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from test_protocol_benchmark import records as records
from test_comparison_profiles import example as example

from genomics_discovery.curation.protocol_benchmark.models import (
    BenchmarkAssessment, BenchmarkAssessmentV2, GraphReviewAssessment, GraphReviewAssessmentV2,
    UnknownPreservationCheck, parse_assessment, parse_graph_review,
)
from genomics_discovery.curation.protocol_benchmark.scoring import score_assessment
from genomics_discovery.curation.protocol_benchmark.validation import validate_assessment
from genomics_discovery.curation.workflow_extraction.models import Workflow, digest


def assessed_unknown(records):
    workflow, packet, reference, _, assessment, _ = records
    graph = workflow.model_dump(mode="json")
    graph["unresolved"] = [{"id": "rt-enzyme", "description": reference.unknowns[0].question,
                           "blocking": False, "next_evidence": reference.unknowns[0].next_evidence}]
    workflow = Workflow.model_validate(graph)
    data = assessment.model_dump(mode="json")
    data.update(format_version="protocol_benchmark_assessment/1.1", workflow_sha256=digest(workflow),
        unknown_checks=[{"unknown_id": "enzyme", "outcome": "retained", "targets": ["gap:rt-enzyme"],
            "rationale": "This gap explicitly retains the scoped missing-enzyme question.", "evidence": []}])
    return workflow, packet, reference, data


def _validation_schema(value):
    """Ignore documentation, preserving properties actually named 'description'."""
    if isinstance(value, list):
        return [_validation_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: ({name: _validation_schema(schema) for name, schema in item.items()}
              if key in {"properties", "$defs", "definitions", "patternProperties"}
              else _validation_schema(item))
        for key, item in value.items() if key != "description"
    }


def test_legacy_validation_schema_and_roundtrip_digests_are_unchanged():
    # Baseline validation hashes were recovered from the pre-field-guidance models.
    # Documentation is versioned in new run schemas, not a change to stored data.
    assert digest(_validation_schema(BenchmarkAssessment.model_json_schema())) == "e147877d266c5c47801f43ccb0268dd8b1bf850771f42405e6a8176e57526d86"
    assert digest(_validation_schema(GraphReviewAssessment.model_json_schema())) == "5c58f4c5384a024dec399b0b68440aadde9aae9690581f3ac37dcb6a994a49ec"
    old = BenchmarkAssessment.model_validate({"reference_sha256": "1" * 64,
        "workflow_sha256": "2" * 64, "source_packet_sha256": "3" * 64,
        "inspected_passages": [], "obligations": [], "factual_audits": []})
    assert digest(old) == "d465466738999448876e7848b2efafa600169accfdae59bde4f4b7cd400922ac"
    assert type(parse_assessment(old.model_dump_json())) is BenchmarkAssessment
    assert digest(parse_assessment(old.model_dump_json())) == digest(old)
    assert "unknown_checks" not in old.model_dump()
    schema = BenchmarkAssessmentV2.model_json_schema()
    assert {"format_version", "unknown_checks"} <= set(schema["required"])


def test_v2_schema_cli_export_is_explicit_and_toolless(capsys):
    import json
    from genomics_discovery.curation.protocol_benchmark.__main__ import main

    assert main(["schema", "graph-review-v2"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assessment = schema["$defs"]["BenchmarkAssessmentV2"]
    assert assessment["properties"]["format_version"]["const"] == "protocol_benchmark_assessment/1.1"
    assert {"format_version", "unknown_checks"} <= set(assessment["required"])


def test_retained_gap_is_a_valid_qualified_witness_without_fabricated_absence_quote(records):
    workflow, packet, reference, data = assessed_unknown(records)
    assessment = parse_assessment(data)
    assert type(assessment) is BenchmarkAssessmentV2
    assert validate_assessment(assessment, reference, workflow, packet) == []
    score = score_assessment(assessment, reference, workflow, packet)
    assert score["unknown_preservation"]["status"] == "retained"
    assert score["unknown_preservation"]["strict_retained_fraction"] == 1
    assert score["obligation_coverage"]["strict_covered_fraction"] == 1
    data["unknown_checks"][0]["targets"] = ["operation:rt", "gap:rt-enzyme"]
    assert validate_assessment(parse_assessment(data), reference, workflow, packet) == []


@pytest.mark.parametrize("defect", ["missing", "duplicate", "foreign_id", "foreign_target", "unqualified_target",
    "quote", "uninspected", "workflow_hash", "reference_hash", "packet_hash"])
def test_unknown_accounting_and_evidence_corruption_fail_closed(records, defect):
    workflow, packet, reference, data = assessed_unknown(records)
    row = data["unknown_checks"][0]
    if defect == "missing":
        data["unknown_checks"] = []
    elif defect == "duplicate":
        data["unknown_checks"].append(deepcopy(row))
    elif defect == "foreign_id":
        row["unknown_id"] = "not-a-reference-question"
    elif defect == "foreign_target":
        row["targets"] = ["gap:foreign-workflow-gap"]
    elif defect == "unqualified_target":
        row["targets"] = ["rt-enzyme"]
    elif defect == "quote":
        row["evidence"] = [reference.obligations[0].evidence[0].model_dump(mode="json")]
        row["evidence"][0]["quote"] = "A source statement that never occurred."
    elif defect == "uninspected":
        data["inspected_passages"] = data["inspected_passages"][:1]
    else:
        data[{"workflow_hash": "workflow_sha256", "reference_hash": "reference_sha256",
              "packet_hash": "source_packet_sha256"}[defect]] = "0" * 64
    assessment = parse_assessment(data)
    assert validate_assessment(assessment, reference, workflow, packet)
    with pytest.raises(ValueError):
        score_assessment(assessment, reference, workflow, packet)


def test_unknown_criterion_is_separate_and_unassessed_is_not_retained(records):
    workflow, packet, reference, data = assessed_unknown(records)
    for outcome in ("partial", "missing", "unassessed", "reference_revision_needed"):
        data["unknown_checks"][0].update(outcome=outcome,
            evidence=[reference.obligations[0].evidence[0].model_dump(mode="json")] if
                     outcome == "reference_revision_needed" else [])
        score = score_assessment(parse_assessment(data), reference, workflow, packet)
        assert score["obligation_coverage"]["strict_covered_fraction"] == 1
        assert score["unknown_preservation"]["strict_retained_fraction"] == 0
        assert score["unknown_preservation"]["status"] == "incomplete"
        assert score["unknown_preservation"]["counts"][outcome] == 1


def test_zero_unknowns_are_not_applicable_and_legacy_is_not_assessed(records):
    workflow, packet, reference, _, assessment, _ = records
    legacy = score_assessment(assessment, reference, workflow, packet)
    assert legacy["unknown_preservation"]["status"] == "not_assessed"
    assert legacy["unknown_preservation"]["strict_retained_fraction"] is None
    no_questions = reference.model_copy(update={"unknowns": ()})
    data = assessment.model_dump(mode="json")
    data.update(format_version="protocol_benchmark_assessment/1.1", unknown_checks=[],
                reference_sha256=digest(no_questions))
    score = score_assessment(parse_assessment(data), no_questions, workflow, packet)
    assert score["unknown_preservation"]["denominator"] == 0
    assert score["unknown_preservation"]["status"] == "not_applicable"
    assert score["unknown_preservation"]["strict_retained_fraction"] is None


def test_no_resolved_outcome_or_unsupported_reference_revision_can_be_authored():
    row = {"unknown_id": "u", "outcome": "resolved", "targets": ["operation:rt"], "rationale": "Claimed answer."}
    with pytest.raises(ValidationError):
        UnknownPreservationCheck.model_validate(row)
    row["outcome"] = "reference_revision_needed"
    with pytest.raises(ValidationError, match="source evidence"):
        UnknownPreservationCheck.model_validate(row)
    row.update(outcome="retained", targets=[])
    with pytest.raises(ValidationError, match="qualified graph witnesses"):
        UnknownPreservationCheck.model_validate(row)


def test_graph_review_dispatch_preserves_both_assessment_versions(records):
    from test_workflow_extraction_runtime import make_review

    workflow, packet, reference, data = assessed_unknown(records)
    combined = {"workflow_review": make_review(workflow.model_dump(mode="json"), packet), "assessment": data}
    parsed = parse_graph_review(combined)
    assert type(parsed) is GraphReviewAssessmentV2
    assert digest(parse_graph_review(parsed.model_dump_json())) == digest(parsed)
    combined["assessment"] = records[4].model_dump(mode="json")
    assert type(parse_graph_review(combined)) is GraphReviewAssessment
    with pytest.raises(ValueError, match="Unsupported"):
        parse_assessment({**data, "format_version": "protocol_benchmark_assessment/99"})
