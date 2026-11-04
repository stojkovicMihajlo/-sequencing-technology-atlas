"""Negative controls for matching two immutable diagram graphs."""

import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from models import Evidence, Finding, Review, UncertaintyAssessment
from pair_models import PairAnalysis, PairClaim, OperationCoverage, DiagramAdequacy, export_pair, validate_pair


def case():
    graph = {"cards": {}, "nodes": [], "edges": []}
    sides, sources = {}, {}
    for side in ("left", "right"):
        graph["cards"][side] = {"record_id": side, "status": "supported", "admission": "supported",
            "scope": "fixture", "publication_ids": [], "source_registry": {}, "artifact_pins": {}, "unknowns": []}
        for name, kind in (("in", "state"), ("op", "operation"), ("out", "state")):
            graph["nodes"].append({"id": side + ":" + name, "node_type": kind,
                "record_id": side, "admission": "supported", "original": {"id": name, "label": name}})
        for a, b, kind in (("in", "op", "material_input"), ("op", "out", "material_output")):
            graph["edges"].append({"id": side + ":" + kind, "source": side + ":" + a,
                "target": side + ":" + b, "kind": kind, "record_id": side,
                "admission": "supported", "original": {"id": kind}})
        sources[side] = {"text": "The demonstrated transformation produces a tagged library.", "content_level": "fulltext"}
        sides[side] = {"record_id": side, "paper_source_ids": [side]}
    c = PairClaim(id="c1", left_operation_ids=["left:op"], right_operation_ids=["right:op"],
        relation="shared_mechanism", direction="undirected", shared_description="Source-supported tagging",
        difference="Distinct substrate contexts", consequence="Equal performance is not established",
        rationale="Both papers demonstrate the specified transformation",
        left_evidence=[Evidence(source_id="left", quote=sources["left"]["text"])],
        right_evidence=[Evidence(source_id="right", quote=sources["right"]["text"])], unresolved=[])
    a = PairAnalysis(claims=[c], operation_coverage=[OperationCoverage(operation_id=x+":op", claim_ids=["c1"],
        explanation="Transformation compared") for x in sides], diagram_adequacy=[DiagramAdequacy(record_id=x,
        verdict="sufficient_with_paper_annotations", explanation="Current boundary sufficient for this scoped claim") for x in sides], summary="Fixture")
    r = Review(analysis_sha256="pin", findings=[Finding(claim_id="c1", decision="accept", rationale="Scoped support",
        evidence=c.left_evidence+c.right_evidence, unresolved_assessments=[])], omitted_candidates=[], summary="Fixture review")
    return a, r, {"graph": graph, "sources": sources, "sides": sides}


def test_export_preserves_original_graph_and_separates_correspondences_from_flows():
    a, r, inputs = case(); before = copy.deepcopy(inputs)
    g = export_pair(a, r, inputs, "pin")
    assert inputs == before
    assert g["source_graph"] == before["graph"]
    assert len(g["correspondences"]) == 1 and not g["historical_links"]
    assert len(g["source_graph"]["edges"]) == 4
    assert g["correspondences"][0]["groups"]["left"]["input_state_ids"] == ["left:in"]


@pytest.mark.parametrize("ids", [["made-up"], ["right:op"], ["left:op", "left:op"]])
def test_invented_wrong_method_or_duplicate_operation_rejected(ids):
    a, _, inputs = case(); a.claims[0].left_operation_ids = ids
    with pytest.raises(ValueError, match="exact Operation"):
        validate_pair(a, inputs)


def test_wrong_source_cannot_support_other_side():
    a, _, inputs = case(); a.claims[0].left_evidence = a.claims[0].right_evidence
    with pytest.raises(ValueError, match="Unregistered evidence"):
        validate_pair(a, inputs)


def test_fabricated_quote_rejected():
    a, _, inputs = case(); a.claims[0].right_evidence[0].quote = "This statement is not present in the paper."
    with pytest.raises(ValueError, match="Quote absent"):
        validate_pair(a, inputs)


def test_operation_coverage_cannot_hide_unmatched_operations():
    a, _, inputs = case(); a.operation_coverage.pop()
    with pytest.raises(ValueError, match="each existing operation"):
        validate_pair(a, inputs)


def test_coverage_cannot_invent_a_membership():
    a, _, inputs = case(); a.operation_coverage[0].claim_ids = []
    with pytest.raises(ValueError, match="membership"):
        validate_pair(a, inputs)


def test_shared_mechanism_cannot_be_exported_as_history_by_direction():
    a, _, _ = case(); raw = a.claims[0].model_dump(); raw["direction"] = "left_to_right"
    with pytest.raises(ValueError, match="historical"):
        PairClaim.model_validate(raw)


@pytest.mark.parametrize("decision", ["reject", "revise", "needs_evidence"])
def test_nonaccepted_claims_withheld(decision):
    a, r, inputs = case(); r.findings[0].decision = decision
    assert not export_pair(a, r, inputs, "pin")["correspondences"]


def test_review_omission_or_unassessed_uncertainty_blocks_export():
    a, r, inputs = case(); r.omitted_candidates = ["Important missing comparison"]
    assert not export_pair(a, r, inputs, "pin")["correspondences"]
    r.omitted_candidates = []; a.claims[0].unresolved = ["Unverified substrate compatibility"]
    with pytest.raises(ValueError, match="every unresolved issue"):
        export_pair(a, r, inputs, "pin")


def test_qualified_claim_keeps_limit_and_rejects_blocking_acceptance():
    a, r, inputs = case(); a.claims[0].unresolved = ["Exact recipe equivalence is not claimed"]
    r.findings[0].unresolved_assessments = [UncertaintyAssessment(issue_index=0, blocks_relation=False,
        rationale="Outside the explicitly scoped mechanism comparison")]
    assert export_pair(a, r, inputs, "pin")["correspondences"][0]["claim"]["unresolved"] == a.claims[0].unresolved
    r.findings[0].unresolved_assessments[0].blocks_relation = True
    with pytest.raises(ValueError, match="blocking"):
        export_pair(a, r, inputs, "pin")


def test_zero_links_is_valid_and_no_relation_claim_never_published():
    a, r, inputs = case(); a.claims[0].relation = "no_established_relation"
    assert not export_pair(a, r, inputs, "pin")["correspondences"]
    a.claims = []; r.findings = []
    for row in a.operation_coverage: row.claim_ids = []
    assert not export_pair(a, r, inputs, "pin")["correspondences"]


def test_changed_analysis_or_missing_review_rejected():
    a, r, inputs = case()
    with pytest.raises(ValueError, match="different analysis"):
        export_pair(a, r, inputs, "changed")
    r.findings = []
    with pytest.raises(ValueError, match="every claim"):
        export_pair(a, r, inputs, "pin")
