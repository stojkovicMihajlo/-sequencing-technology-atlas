"""Synthetic contract checks; these fixtures are not scientific reference labels."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from genomics_discovery.curation.protocol_benchmark.comparisons import (
    CHECK_FIELDS, CLAIM_TYPES, ComparisonReference, ComparisonReview, ComparisonTask,
    comparison_review_outcome, comparison_schema, export_benchmark_relations,
    score_comparison, validate_comparison_reference, validate_comparison_review,
)
from genomics_discovery.curation.protocol_benchmark.models import ProtocolReference
from genomics_discovery.curation.workflow_extraction.models import Workflow, digest
from genomics_discovery.curation.workflow_extraction.sources import build_packet


@pytest.fixture
def comparison_records(tmp_path):
    packets, references, workflows, evidence = [], [], [], []
    for side, text in (
        ("left", "Cells are lysed and RNA is captured on primer-bearing beads. "
         "Each bead retains a cell barcode.\n\n"
         "Captured RNA is reverse transcribed to cDNA before pooled amplification."),
        ("right", "Tissue RNA is captured on a fixed array of primers. "
         "Each array spot retains a coordinate barcode.\n\n"
         "Captured RNA is reverse transcribed to cDNA before pooled amplification."),
    ):
        source = tmp_path / f"{side}.txt"
        source.write_text(text)
        packet = build_packet(method_id=side, title=f"Synthetic {side} assay",
            scope=f"Synthetic {side} capture and conversion", variant="fixture",
            sources=[{"source_id": "paper", "title": "Synthetic primary source",
                      "url": f"https://example.org/{side}", "role": "primary_paper",
                      "path": str(source)}])
        ev = [{"source_id": "paper", "passage_id": passage["passage_id"],
               "quote": passage["text"], "support": "reported", "rationale": "Fixture support."}
              for passage in packet["sources"][0]["passages"]]
        reference = ProtocolReference.model_validate({
            "case_id": side, **{key: packet[key] for key in ("method_id", "scope", "variant")},
            "source_packet_sha256": digest(packet), "measurement": "RNA capture and attributed cDNA",
            "obligations": [{"id": ident, "kind": "operation", "text": statement,
                "criticality": "required", "why_needed": "Preserve the declared transformation.",
                "evidence": [ev[index]]} for index, (ident, statement) in enumerate((
                    ("capture", "Capture RNA while retaining the reported barcode attribution."),
                    ("convert", "Reverse transcribe RNA to cDNA.")))],
            "unknowns": [], "query_cases": [],
            "admissible_decompositions": [{"id": "combined", "description": "A declared composite.",
                                           "obligation_ids": ["capture", "convert"]}],
            "source_coverage": [{"source_id": "paper", "inspected_passage_ids":
                [item["passage_id"] for item in ev], "role_in_assay": "Synthetic protocol",
                "notes": "Only the declared fixture statements are assessed."}],
        })
        workflow = Workflow.model_validate({
            **{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
            "states": [{"id": ident, "kind": "material", "description": description,
                        "evidence": [ev[index]]} for ident, description, index in (
                ("rna", "Input RNA", 0), ("captured", "Captured RNA", 0), ("cdna", "cDNA", 1))],
            "operations": [{"id": ident, "name": ident, "action": action,
                "inputs": [inp], "outputs": [out], "resources": [],
                "information_effect": effect, "evidence": [ev[index]]}
                for ident, action, inp, out, effect, index in (
                    ("capture", "Capture RNA", "rna", "captured", "Retain barcode attribution", 0),
                    ("rt", "Reverse transcribe RNA", "captured", "cdna", "Represent RNA in cDNA", 1))],
            "flows": [{"id": f"f{index}", "source": source_id, "target": target,
                       "kind": kind, "basis": "Declared fixture route", "evidence": [ev[index // 2]]}
                      for index, (source_id, target, kind) in enumerate((
                          ("rna", "capture", "material_input"),
                          ("capture", "captured", "material_output"),
                          ("captured", "rt", "material_input"), ("rt", "cdna", "material_output")))],
            "modules": [{"id": "capture-convert", "name": "Capture and conversion",
                         "operation_ids": ["capture", "rt"],
                         "boundary_rationale": "Declared connected fixture unit."}], "unresolved": [],
        })
        packets.append(packet)
        references.append(reference)
        workflows.append(workflow)
        evidence.append([{**item, "side": side} for item in ev])
    task = ComparisonTask.model_validate({
        "comparison_id": "capture-attribution", "left_case_id": "left", "right_case_id": "right",
        "question": "Compare connected RNA capture units, material interfaces and barcode meaning.",
        "why_included": "Tests explicit identity units and open relation outcomes.",
        "split": "development", "allowed_claim_types": CLAIM_TYPES,
        "annotation_policy": "No expected label or forced relation; unknown is not false.",
    })
    inspected = [{key: item[key] for key in ("side", "source_id", "passage_id")}
                 for group in evidence for item in group]
    def bound(index):
        reference = references[index]
        return {"case_id": reference.case_id, "method_id": reference.method_id,
            "reference_sha256": digest(reference), "source_packet_sha256": digest(packets[index]),
            "unit": {"description": "A declared connected capture/conversion unit.",
                     "inputs": "RNA at its source-defined interface", "transformation": "Capture RNA",
                     "outputs": "Captured RNA", "information": "Retain barcode attribution",
                     "context": "The exact synthetic fixture scope", "connectedness_rationale":
                     "The specified capture and conversion share the captured-RNA intermediate.",
                     "selected_obligation_ids": ["capture", "convert"], "evidence": [evidence[index][0]]}}
    claims = [{"id": kind, "claim_type": kind, "text": f"Precisely scoped {kind} proposition.",
        "scope": "Only the compared synthetic capture units and their stated inputs/outputs.",
        "judgment": judgment, "rationale": "An authored synthetic judgment, not scientific validation.",
        "evidence": [] if judgment == "not_established" else [evidence[0][0], evidence[1][0]],
        "preserved_differences": ["The declared identity units differ."],
        "counterexamples": ["The coordinate barcode does not identify an individual cell."]
                           if judgment == "contradicted" else [],
        "unknowns": ["Exact cross-method transfer conditions are unassessed."]}
        for kind, judgment in zip(CLAIM_TYPES,
                                  ("supported", "not_established", "not_established", "contradicted"))]
    comparison = ComparisonReference.model_validate({
        "comparison_id": task.comparison_id, "task_sha256": digest(task),
        "annotation_session_id": "fresh-annotator", "left": bound(0), "right": bound(1),
        "inspected_passages": inspected, "claims": claims,
    })
    review = ComparisonReview.model_validate({
        "comparison_sha256": digest(comparison), "task_sha256": digest(task),
        "left_reference_sha256": digest(references[0]), "right_reference_sha256": digest(references[1]),
        "left_source_packet_sha256": digest(packets[0]), "right_source_packet_sha256": digest(packets[1]),
        "review_session_id": "fresh-independent-reviewer", "inspected_passages": inspected,
        "unit_checks": [{"side": side, "verdict": "supported", "rationale": "Fixture source check.",
                         "evidence": [evidence[index][0]]} for index, side in enumerate(("left", "right"))],
        "claim_checks": [{"claim_id": claim.id, "verdict": "supported", "checked_fields": CHECK_FIELDS,
            "rationale": "All fields of this synthetic judgment were explicitly checked.",
            "evidence": [] if claim.judgment == "not_established" else [evidence[0][0], evidence[1][0]]}
            for claim in comparison.claims],
    })
    return comparison, review, (*references, *packets), task, workflows


def test_predeclared_questions_cover_all_cases_without_gold_labels():
    root = Path(__file__).resolve().parents[1] / "benchmarks/protocol_graph_v1"
    cases = {row["case_id"]: row for row in json.loads((root / "cases.json").read_text())["cases"]}
    document = json.loads((root / "comparisons.json").read_text())
    tasks = [ComparisonTask.model_validate(row) for row in document["comparisons"]]
    assert len(tasks) == 8
    assert len({task.comparison_id for task in tasks}) == 8
    assert {case_id for task in tasks for case_id in (task.left_case_id, task.right_case_id)} == set(cases)
    assert sum(cases[task.left_case_id]["family"] == cases[task.right_case_id]["family"] for task in tasks) == 6
    for task in tasks:
        assert set(task.allowed_claim_types) == set(CLAIM_TYPES)
        expected = "development" if all(cases[case_id]["split"] == "development"
            for case_id in (task.left_case_id, task.right_case_id)) else "future_tuning_holdout"
        assert task.split == expected
        assert "expected_label" not in task.model_dump()


def test_unknown_is_not_negative_and_supported_purpose_implies_no_other_relation(comparison_records):
    comparison, review, args, task, _ = comparison_records
    assert validate_comparison_reference(comparison, *args, task=task) == []
    assert validate_comparison_review(review, comparison, *args, task=task) == []
    score = score_comparison(review, comparison, *args, task=task)
    assert score["counts"] == {"supported": 1, "contradicted": 1, "not_established": 2}
    exported = export_benchmark_relations(review, comparison, *args, task=task)
    assert [row["claim_type"] for row in exported["relations"]] == ["shared_purpose"]
    assert len(exported["unexported_claims"]) == 3
    assert "precision" not in score and "registry" not in exported


def test_packet_local_id_collision_does_not_allow_evidence_from_the_other_side(comparison_records):
    comparison, _, args, task, _ = comparison_records
    data = comparison.model_dump(mode="json")
    left_ev, right_ev = data["claims"][0]["evidence"]
    assert (left_ev["source_id"], left_ev["passage_id"]) == (right_ev["source_id"], right_ev["passage_id"])
    assert left_ev["quote"] != right_ev["quote"]
    data["claims"][0]["evidence"][1]["quote"] = left_ev["quote"]
    errors = validate_comparison_reference(ComparisonReference.model_validate(data), *args, task=task)
    assert any("right" in error and "quote" in error for error in errors)


@pytest.mark.parametrize("field", ["case_id", "method_id", "reference_sha256", "source_packet_sha256"])
def test_exact_side_identity_and_hashes_are_bound(comparison_records, field):
    comparison, _, args, task, _ = comparison_records
    data = comparison.model_dump(mode="json")
    data["left"][field] = "0" * 64 if field.endswith("sha256") else "another-case"
    assert validate_comparison_reference(ComparisonReference.model_validate(data), *args, task=task)


@pytest.mark.parametrize("field", ["comparison_sha256", "task_sha256", "left_reference_sha256",
    "right_reference_sha256", "left_source_packet_sha256", "right_source_packet_sha256"])
def test_review_cannot_transfer_to_other_exact_artifacts(comparison_records, field):
    comparison, review, args, task, _ = comparison_records
    data = review.model_dump(mode="json")
    data[field] = "0" * 64
    changed = ComparisonReview.model_validate(data)
    assert validate_comparison_review(changed, comparison, *args, task=task)
    with pytest.raises(ValueError):
        export_benchmark_relations(changed, comparison, *args, task=task)


def test_task_question_and_allowed_claim_types_cannot_silently_change(comparison_records):
    comparison, _, args, task, _ = comparison_records
    changed = task.model_copy(update={"question": "A different question"})
    assert "Comparison task_sha256 does not match" in validate_comparison_reference(comparison, *args, task=changed)
    changed = task.model_copy(update={"allowed_claim_types": ("shared_purpose",)})
    assert any("not allowed" in error for error in validate_comparison_reference(comparison, *args, task=changed))


def test_no_support_cannot_be_used_to_declare_a_false_relation(comparison_records):
    comparison, *_ = comparison_records
    data = comparison.model_dump(mode="json")
    claim = data["claims"][3]
    claim["evidence"] = []
    with pytest.raises(ValidationError, match="both sides"):
        ComparisonReference.model_validate(data)
    claim["evidence"] = data["claims"][0]["evidence"]
    claim["counterexamples"] = []
    with pytest.raises(ValidationError, match="counterexample"):
        ComparisonReference.model_validate(data)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "unknown", "unchecked_field", "same_session",
                                    "uninspected", "one_side", "unit_evidence_side"])
def test_independent_review_requires_exhaustive_source_qualified_checks(comparison_records, defect):
    comparison, review, args, task, _ = comparison_records
    data = review.model_dump(mode="json")
    if defect == "missing":
        data["claim_checks"].pop()
    elif defect == "duplicate":
        data["claim_checks"].append(deepcopy(data["claim_checks"][0]))
    elif defect == "unknown":
        data["claim_checks"][0]["claim_id"] = "foreign-claim"
    elif defect == "unchecked_field":
        data["claim_checks"][0]["checked_fields"].remove("preserved_differences")
    elif defect == "same_session":
        data["review_session_id"] = comparison.annotation_session_id
    elif defect == "uninspected":
        data["inspected_passages"] = [row for row in data["inspected_passages"] if row["passage_id"] != "p00002"]
    elif defect == "one_side":
        data["claim_checks"][0]["evidence"].pop()
    else:
        data["unit_checks"][0]["evidence"] = data["unit_checks"][1]["evidence"]
    assert validate_comparison_review(ComparisonReview.model_validate(data), comparison, *args, task=task)


def test_uncertain_review_does_not_count_or_export_its_claim(comparison_records):
    comparison, review, args, task, _ = comparison_records
    data = review.model_dump(mode="json")
    data["claim_checks"][0].update(verdict="uncertain", evidence=[])
    changed = ComparisonReview.model_validate(data)
    assert validate_comparison_review(changed, comparison, *args, task=task) == []
    assert comparison_review_outcome(changed) == "research_needed"
    score = score_comparison(changed, comparison, *args, task=task)
    assert score["reviewed_claim_count"] == 3
    assert score["counts"]["supported"] == 0
    assert export_benchmark_relations(changed, comparison, *args, task=task)["relations"] == []


def test_unresolved_unit_blocks_scoring_claims_that_depend_on_it(comparison_records):
    comparison, review, args, task, _ = comparison_records
    data = review.model_dump(mode="json")
    data["unit_checks"][1].update(verdict="uncertain", evidence=[])
    changed = ComparisonReview.model_validate(data)
    assert validate_comparison_review(changed, comparison, *args, task=task) == []
    assert score_comparison(changed, comparison, *args, task=task)["reviewed_claim_count"] == 0


def test_valid_comparison_can_export_no_relations_without_creating_negative_gold(comparison_records):
    comparison, review, args, task, _ = comparison_records
    data = comparison.model_dump(mode="json")
    data.update(claims=[], no_claims_reason="No relation was established within the declared inspected scope.")
    empty = ComparisonReference.model_validate(data)
    data = review.model_dump(mode="json")
    data.update(comparison_sha256=digest(empty), claim_checks=[], no_claims_check="supported",
                no_claims_rationale="The declared scoped abstention was independently checked.")
    checked = ComparisonReview.model_validate(data)
    assert validate_comparison_review(checked, empty, *args, task=task) == []
    assert score_comparison(checked, empty, *args, task=task)["counts"] == {
        "supported": 0, "contradicted": 0, "not_established": 0}
    assert export_benchmark_relations(checked, empty, *args, task=task)["relations"] == []


def test_optional_targets_bind_exact_workflows_and_allow_unequal_units(comparison_records):
    comparison, _, args, task, workflows = comparison_records
    data = comparison.model_dump(mode="json")
    for index, side in enumerate(("left", "right")):
        data[side]["unit"].update(workflow_sha256=digest(workflows[index]),
            selected_protocol_targets=["operation:capture", "operation:rt"] if index == 0 else ["operation:capture"])
    selected = ComparisonReference.model_validate(data)
    assert validate_comparison_reference(selected, *args, task=task)
    assert validate_comparison_reference(selected, *args, task=task,
        left_workflow=workflows[0], right_workflow=workflows[1]) == []
    data["left"]["unit"]["selected_protocol_targets"] = ["operation:absent"]
    assert any("unknown selected" in error for error in validate_comparison_reference(
        ComparisonReference.model_validate(data), *args, task=task,
        left_workflow=workflows[0], right_workflow=workflows[1]))


def test_schema_rejects_implicit_acceptance_and_unbound_graph_targets(comparison_records):
    comparison, *_ = comparison_records
    assert comparison_schema()["additionalProperties"] is False
    assert comparison_schema("review")["additionalProperties"] is False
    data = comparison.model_dump(mode="json")
    data["accepted"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        ComparisonReference.model_validate(data)
    del data["accepted"]
    data["left"]["unit"]["selected_protocol_targets"] = ["operation:capture"]
    with pytest.raises(ValidationError, match="workflow_sha256"):
        ComparisonReference.model_validate(data)


def test_original_source_changes_block_reference_review_scoring_and_export(comparison_records):
    comparison, review, args, task, _ = comparison_records
    Path(args[2]["sources"][0]["original_path"]).write_text("Changed original bytes.")
    assert any("Invalid source packet/original" in error for error in
               validate_comparison_reference(comparison, *args, task=task))
    with pytest.raises(ValueError):
        score_comparison(review, comparison, *args, task=task)
