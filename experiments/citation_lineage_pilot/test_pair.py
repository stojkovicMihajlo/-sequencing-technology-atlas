"""Negative controls for matching two immutable diagram graphs."""

import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from models import Evidence, Finding, Review, UncertaintyAssessment
from pair_models import PairAnalysis, PairClaim, OperationCoverage, DiagramAdequacy, export_pair, validate_pair, validate_pair_review
import pair_run


JATS_PRIMARY = b'''<article><body><p>We adapted the earlier method <xref ref-type="bibr" rid="r1">1</xref>.</p></body>
<back><ref-list><ref id="r1"><element-citation><article-title>Earlier method</article-title>
<pub-id pub-id-type="pmid">1234</pub-id></element-citation></ref></ref-list></back></article>'''
PDF_PRIMARY = b"%PDF-1.7\nRegistered original PDF bytes for the preparation test."


def primary_source(tmp_path, name, raw):
    path = tmp_path / name
    path.write_bytes(raw)
    return {"text": "Registered full-text rendition.", "content_level": "fulltext",
            "original": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}}


def test_pdf_original_is_preserved_without_claiming_an_empty_bibliography(tmp_path):
    source = primary_source(tmp_path, "original.PDF", PDF_PRIMARY)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    entry = pair_run.prepare_primary(source, "right:paper", "right", inputs)
    assert (inputs / "right_primary.PDF").read_bytes() == PDF_PRIMARY
    assert not (inputs / "right_primary.xml").exists()
    assert entry["source_format"] == "PDF"
    assert entry["status"] == "not_parsed"
    assert entry["references"] is None
    assert "does not mean the paper has no references" in entry["limitation"]
    assert entry["original_sha256"] == source["original"]["sha256"]


def test_jats_keeps_original_extension_and_parses_references_and_context(tmp_path):
    source = primary_source(tmp_path, "original.nxml", JATS_PRIMARY)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    entry = pair_run.prepare_primary(source, "left:paper", "left", inputs)
    assert (inputs / "left_primary.nxml").read_bytes() == JATS_PRIMARY
    assert entry["source_format"] == "JATS"
    assert entry["status"] == "parsed" and entry["limitation"] is None
    assert "Earlier method" in str(entry["references"]["r1"])
    assert "We adapted the earlier method" in str(entry["references"]["r1"])


@pytest.mark.parametrize("suffix,raw", [("pdf", PDF_PRIMARY), ("xml", JATS_PRIMARY)])
@pytest.mark.parametrize("recorded_hash", [None, "wrong-hash"])
def test_primary_original_hash_is_required_and_checked(tmp_path, suffix, raw, recorded_hash):
    source = primary_source(tmp_path, "original." + suffix, raw)
    source["original"]["sha256"] = recorded_hash
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    with pytest.raises(ValueError, match="registered hash"):
        pair_run.prepare_primary(source, "left:paper", "left", inputs)
    assert not list(inputs.iterdir())


def test_nonarticle_xml_is_not_silently_a_paper_with_no_references(tmp_path):
    source = primary_source(tmp_path, "original.xml", b"<html><body>Access denied</body></html>")
    with pytest.raises(ValueError, match="PDF or JATS article"):
        pair_run.prepare_primary(source, "left:paper", "left", tmp_path)


def pair_preparation_case(tmp_path, monkeypatch):
    """Isolate source copying from the separately tested native review reopening gate."""
    plan, registries, reopened = {}, {}, []
    for side, suffix, raw in (("left", "xml", JATS_PRIMARY), ("right", "pdf", PDF_PRIMARY)):
        batch = tmp_path / side
        attempt = batch / "attempt"
        attempt.mkdir(parents=True)
        for name in ("extraction.json", "graph.json", "review.json"):
            (attempt / name).write_text("{}")
        (attempt / "card.json").write_text(json.dumps({"record_id": side}))
        source = primary_source(attempt, "paper." + suffix, raw)
        registries[side] = {"paper": source}
        pins = {"source_registry.json": "source-registry-pin"}
        row = {"status": "supported", "attempt_directory": str(attempt), "artifact_pins": pins}
        (batch / "state.json").write_text(json.dumps({"cards": {side: row}}))
        plan[side] = {"batch": str(batch), "record_id": side, "primary_paper": "paper"}

    def reopen(row, card):
        reopened.append(card["record_id"])
        return {"workflow": {"title": card["record_id"], "scope": "fixture", "unknowns": []},
                "candidate_sha256": "candidate-pin", "review_sha256": "review-pin",
                "source_registry": {}, "nodes": [], "edges": []}, registries[card["record_id"]]

    monkeypatch.setattr(pair_run, "_load_card", reopen)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    return plan_path, registries, reopened


def test_prepare_accepts_reviewed_mixed_jats_pdf_and_records_unparsed_status(tmp_path, monkeypatch):
    plan, _, reopened = pair_preparation_case(tmp_path, monkeypatch)
    inputs = pair_run.prepare(plan, tmp_path / "run")
    assert reopened == ["left", "right"]
    references = json.loads((inputs / "bibliography.json").read_text())
    assert references["left"]["status"] == "parsed"
    assert references["right"]["status"] == "not_parsed"
    assert references["right"]["references"] is None
    assert (inputs / "right_primary.pdf").read_bytes() == PDF_PRIMARY
    sources = json.loads((inputs / "sources.json").read_text())
    assert sources["right:paper"]["text"] == "Registered full-text rendition."
    assert sources["right:paper"]["original"]["sha256"] == references["right"]["original_sha256"]


def test_pdf_support_does_not_relax_fulltext_requirement(tmp_path, monkeypatch):
    plan, registries, _ = pair_preparation_case(tmp_path, monkeypatch)
    registries["right"]["paper"]["content_level"] = "abstract"
    with pytest.raises(ValueError, match="both original full texts"):
        pair_run.prepare(plan, tmp_path / "run")


def test_format_handling_does_not_bypass_extraction_review_gate(tmp_path, monkeypatch):
    plan, _, _ = pair_preparation_case(tmp_path, monkeypatch)

    def reject(row, card):
        raise ValueError("Completed extraction review integrity failed")

    monkeypatch.setattr(pair_run, "_load_card", reject)
    with pytest.raises(ValueError, match="review integrity failed"):
        pair_run.prepare(plan, tmp_path / "run")


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


@pytest.mark.parametrize("side", ["left", "right"])
def test_accepted_pair_review_requires_both_bound_papers(side):
    a, r, inputs = case()
    r.findings[0].evidence = getattr(a.claims[0], side + "_evidence")
    missing = "right" if side == "left" else "left"
    with pytest.raises(ValueError, match=f"registered {missing}-method paper"):
        validate_pair_review(r, a, inputs, "pin")
    with pytest.raises(ValueError, match=f"registered {missing}-method paper"):
        export_pair(a, r, inputs, "pin")


@pytest.mark.parametrize("decision", ["reject", "revise", "needs_evidence"])
def test_nonaccepted_pair_review_may_report_one_sided_failure(decision):
    a, r, inputs = case()
    r.findings[0].decision = decision
    r.findings[0].evidence = a.claims[0].left_evidence
    validate_pair_review(r, a, inputs, "pin")
    assert not export_pair(a, r, inputs, "pin")["correspondences"]


def test_no_relation_disposition_does_not_require_positive_two_sided_support():
    a, r, inputs = case()
    a.claims[0].relation = "no_established_relation"
    r.findings[0].evidence = a.claims[0].left_evidence
    validate_pair_review(r, a, inputs, "pin")
    assert not export_pair(a, r, inputs, "pin")["correspondences"]


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
