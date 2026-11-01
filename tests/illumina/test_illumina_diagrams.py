"""Structural and custody controls for diagram records, not model-accuracy tests."""

import copy

import pytest
from pydantic import ValidationError

from genomics_discovery.illumina.models import (
    DiagramExtraction,
    DiagramReview,
    Evidence,
    digest,
    export_graph,
    review_outcome,
    review_targets,
    validate_extraction,
    validate_review,
)
from genomics_discovery.illumina.graph import protocol_text


@pytest.fixture
def example():
    image = {"source_id": "diagram:a", "locator": "split arrows", "basis": "image",
             "support": "depicted", "bbox": [0.1, 0.1, 0.8, 0.8]}
    text = {"source_id": "paper:1", "locator": "abstract", "basis": "text",
            "support": "reported", "quote": "RNA and DNA are separated"}
    sources = {"diagram:a": {"kind": "image", "path": "diagram.png", "sha256": "a" * 64},
               "paper:1": {"kind": "text", "content_level": "abstract",
                           "text": "RNA and DNA are separated before processing."},
               "metadata:2": {"kind": "text", "content_level": "metadata",
                              "text": "A paper title"}}
    states = [{"id": ident, "label": label, "kind": kind, "evidence": [image]}
              for ident, label, kind in (("cell", "cell", "material"),
                                        ("rna", "separated RNA", "material"),
                                        ("dna", "separated DNA", "material"),
                                        ("reads", "sequencing reads", "data"))]
    operations = [{"id": "split", "label": "Separate RNA and DNA", "description": "Separate material",
                   "inputs": ["cell"], "outputs": ["rna", "dna"], "evidence": [image]},
                  {"id": "read", "label": "Read RNA branch", "description": "Read branch",
                   "inputs": ["rna"], "outputs": ["reads"], "evidence": [image]}]
    flows = [{"id": ident, "source": src, "target": dst, "kind": kind, "evidence": [image]}
             for ident, src, dst, kind in (("f1", "cell", "split", "material_input"),
                                          ("f2", "split", "rna", "material_output"),
                                          ("f3", "split", "dna", "material_output"),
                                          ("f4", "rna", "read", "material_input"),
                                          ("f5", "read", "reads", "data_output"))]
    data = {"record_id": "a/b", "title": "Grouped example", "method_names": ["A", "B"],
            "scope": "Diagram, not execution recipe", "inspected_sources": ["diagram:a", "paper:1"],
            "states": states, "operations": operations, "flows": flows,
            "method_scopes": [{"method": name, "operation_ids": ops, "status": "resolved",
                               "rationale": "Shown scope", "evidence": [image]}
                              for name, ops in (("A", ["split", "read"]), ("B", ["read"]))],
            "paper_matches": [{"id": "p1", "source_id": "paper:1", "methods": ["A", "B"],
                               "assessment": "content_checked", "relationship": "supports_architecture",
                               "evidence": [text]}], "corrections": [], "unknowns": []}
    return data, sources, image, text


def reviewed(candidate, image):
    checks = []
    for target in sorted(review_targets(candidate)):
        evidence, verdict, action = [image], "supported", "accept"
        if target.startswith("paper:"):
            evidence = next(p.evidence for p in candidate.paper_matches if target == "paper:" + p.id)
        elif target.startswith("correction:"):
            evidence = next(c.evidence for c in candidate.corrections if target == "correction:" + c.id)
        elif target.startswith("scope:"):
            scope = next(s for s in candidate.method_scopes if target == "scope:" + s.method)
            if scope.status == "unresolved":
                verdict, action = "unresolved", "research"
        for correction in candidate.corrections:
            if correction.target_id == target and correction.disposition == "use_primary":
                evidence = correction.evidence
        checks.append({"target": target, "verdict": verdict, "action": action,
                       "rationale": "Checked", "evidence": evidence})
    return DiagramReview(record_id=candidate.record_id, candidate_sha256=digest(candidate),
                         inspected_sources=candidate.inspected_sources,
                         checks=checks)


def test_branch_survives_projection_and_ids_are_namespaced(example):
    data, sources, image, _ = example
    candidate = DiagramExtraction.model_validate(data)
    review = reviewed(candidate, image)
    assert not validate_extraction(candidate, sources, expected_record_id="a/b")
    assert not validate_review(review, candidate, sources)
    graph = export_graph(candidate, review, sources)
    assert graph["status"] == "supported"
    assert graph["nodes"][0]["id"] == "illumina:a%2Fb"
    routes = [e for e in graph["edges"] if e["kind"] == "material_output"]
    assert len(routes) == 2 and len({e["source"] for e in routes}) == 1
    assert {e["kind"] for e in graph["edges"]}.isdisjoint({"method_development", "shared_mechanism"})


def test_readable_view_preserves_branch_ids_typed_flows_and_evidence(example):
    data, sources, image, _ = example
    candidate = DiagramExtraction.model_validate(data)
    review = reviewed(candidate, image)
    text = protocol_text(candidate, review, sources)
    assert text.index("OPERATION split") < text.index("OPERATION read")
    assert "Output States: rna (separated RNA); dna (separated DNA)" in text
    assert "FLOW f3: split (Separate RNA and DNA) -> dna (separated DNA) [material_output]" in text
    assert "diagram:a @ split arrows [depicted]" in text
    assert "RNA and DNA are separated" in text
    assert "Module" not in text
    assert text == protocol_text(export_graph(candidate, review, sources))


def test_grouped_method_can_remain_unresolved_without_inheriting_all_steps(example):
    data, sources, image, _ = example
    data["method_scopes"][1].update(status="unresolved", operation_ids=[], evidence=[],
                                    rationale="Source does not disambiguate B")
    data["unknowns"] = [{"id": "u", "question": "Which steps apply to B?", "blocking": True,
                         "affected_ids": ["scope:B"]}]
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources)
    assert review_outcome(reviewed(candidate, image), candidate, sources) == "research_needed"


def test_correctable_scope_error_is_not_masked_by_candidate_unresolved_flag(example):
    data, sources, image, _ = example
    data["method_scopes"][1].update(status="unresolved", operation_ids=[], evidence=[],
                                    rationale="A declared unresolved scope")
    candidate = DiagramExtraction.model_validate(data)
    raw = reviewed(candidate, image).model_dump(mode="json")
    check = next(c for c in raw["checks"] if c["target"] == "scope:B")
    check.update(verdict="unsupported", action="revise",
        rationale="The source identifies B only as background; correct this extraction scope.")
    assert review_outcome(DiagramReview.model_validate(raw), candidate, sources) == "needs_revision"
    check.update(verdict="unresolved", action="research")
    assert review_outcome(DiagramReview.model_validate(raw), candidate, sources) == "research_needed"


@pytest.mark.parametrize("mutation,pattern", [
    (lambda d: d["flows"][0].update(source="absent"), "endpoint"),
    (lambda d: d["flows"][4].update(kind="material_output"), "endpoint types"),
    (lambda d: d["operations"][0].update(outputs=["rna"]), "I/O and Flows disagree"),
    (lambda d: d["states"][0].update(id="split"), "unique across"),
    (lambda d: d["method_scopes"][1].update(method="invented C"), "each named method"),
    (lambda d: d["method_scopes"][1].update(operation_ids=["invented step"]), "unknown Operation"),
    (lambda d: d["paper_matches"][0].update(methods=["future variant"]), "named methods"),
])
def test_invalid_structure_rejected(example, mutation, pattern):
    data, _, _, _ = example
    mutation(data)
    with pytest.raises(ValidationError, match=pattern):
        DiagramExtraction.model_validate(data)


def test_cycle_rejected_even_when_io_is_otherwise_complete(example):
    data, _, image, _ = example
    data["flows"].append({"id": "loop", "source": "read", "target": "split",
                          "kind": "precedes", "evidence": [image]})
    with pytest.raises(ValidationError, match="acyclic"):
        DiagramExtraction.model_validate(data)


def test_unknown_source_fabricated_quote_and_uninspected_source_rejected(example):
    data, sources, _, _ = example
    data["paper_matches"][0]["evidence"][0]["quote"] = "Invented chemical mechanism"
    candidate = DiagramExtraction.model_validate(data)
    assert any("quote not present" in x for x in validate_extraction(candidate, sources))
    data["paper_matches"][0]["evidence"][0]["source_id"] = "invented:source"
    assert any("Unknown evidence source" in x for x in validate_extraction(
        DiagramExtraction.model_validate(data), sources))
    data["paper_matches"] = []
    data["inspected_sources"] = ["paper:1"]
    errors = validate_extraction(DiagramExtraction.model_validate(data), sources)
    assert any("not declared inspected" in x for x in errors)
    assert any("requires an inspected registered image" in x for x in errors)


def test_metadata_does_not_establish_architecture_and_assignment_id_is_checked(example):
    data, sources, _, _ = example
    data["paper_matches"][0].update(source_id="metadata:2", assessment="identity_verified",
                                    evidence=[])
    candidate = DiagramExtraction.model_validate(data)
    errors = validate_extraction(candidate, sources, expected_record_id="another",
                                  expected_title="another")
    assert any("metadata alone" in x for x in errors)
    assert any("record ID" in x for x in errors)
    assert any("title" in x for x in errors)


def test_unverified_catalogue_reference_cannot_be_elevated_to_identity_verified(example):
    data, sources, _, _ = example
    sources["metadata:2"]["identity_status"] = "unverified_catalogue_reference"
    data["inspected_sources"].append("metadata:2")
    data["paper_matches"][0].update(
        source_id="metadata:2", assessment="identity_verified", relationship="catalogue_citation",
        evidence=[{"source_id": "metadata:2", "locator": "catalogue reference", "basis": "text",
                   "support": "reported", "quote": "A paper title"}])
    candidate = DiagramExtraction.model_validate(data)
    assert any("Unverified catalogue reference" in x for x in validate_extraction(candidate, sources))
    data["paper_matches"][0]["assessment"] = "listed_not_read"
    assert not validate_extraction(DiagramExtraction.model_validate(data), sources)
    sources["metadata:2"]["identity_status"] = "pubmed_record_retrieved"
    data["paper_matches"][0]["assessment"] = "identity_verified"
    assert not validate_extraction(DiagramExtraction.model_validate(data), sources)


def test_each_named_method_needs_assessed_paper_membership_when_references_exist(example):
    data, sources, image, _ = example
    data["paper_matches"][0]["methods"] = ["A"]
    assert any("Named method lacks" in x for x in validate_extraction(
        DiagramExtraction.model_validate(data), sources))
    data["paper_matches"][0]["methods"] = ["A", "B"]
    data["paper_matches"][0]["relationship"] = "unresolved"
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources)
    assert review_outcome(reviewed(candidate, image), candidate, sources) == "research_needed"
    data["paper_matches"] = []
    candidate = DiagramExtraction.model_validate(data)
    assert any("Named method lacks" in x for x in validate_extraction(
        candidate, sources, require_paper_matches=True))
    assert not validate_extraction(candidate, sources)


def test_expected_member_check_requires_explicit_source_attested_names(example):
    data, sources, _, _ = example
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources, expected_method_names=("B", "A"))
    assert any("expected members" in x for x in validate_extraction(
        candidate, sources, expected_method_names=("A", "B", "C")))


def test_missing_publication_can_be_preserved_only_as_explicit_blocked_scope(example):
    data, sources, image, _ = example
    data["paper_matches"] = []
    candidate = DiagramExtraction.model_validate(data)
    assert any("Named method lacks" in x for x in validate_extraction(
        candidate, sources, require_paper_matches=True))
    data["unknowns"] = [{"id": "paper_a", "question": "The supplied sources do not identify A's paper association.",
                         "blocking": True, "affected_ids": ["scope:A"]},
                        {"id": "paper_b", "question": "No publication is linked to B in the supplied row.",
                         "blocking": True, "affected_ids": ["scope:B"]}]
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources, require_paper_matches=True)
    assert review_outcome(reviewed(candidate, image), candidate, sources) == "research_needed"
    data["unknowns"][0]["blocking"] = False
    assert any("assessment: A" in x for x in validate_extraction(
        DiagramExtraction.model_validate(data), sources, require_paper_matches=True))
    data["unknowns"][0].update(blocking=True, affected_ids=["scope:B"])
    assert any("assessment: A" in x for x in validate_extraction(
        DiagramExtraction.model_validate(data), sources, require_paper_matches=True))
    data["unknowns"][0].update(affected_ids=["scope:A"],
                              question="Could not establish a DOI or PMID for this entry.")
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources, require_paper_matches=True)
    assert review_outcome(reviewed(candidate, image), candidate, sources) == "research_needed"


def test_review_must_cover_every_target_and_bind_exact_candidate(example):
    data, sources, image, _ = example
    candidate = DiagramExtraction.model_validate(data)
    review = reviewed(candidate, image)
    raw = review.model_dump(mode="json")
    raw["checks"].pop()
    assert any("every record target" in x for x in validate_review(
        DiagramReview.model_validate(raw), candidate, sources))
    raw = review.model_dump(mode="json")
    raw["checks"].append(copy.deepcopy(raw["checks"][0]))
    assert any("every record target" in x for x in validate_review(
        DiagramReview.model_validate(raw), candidate, sources))
    raw = review.model_dump(mode="json")
    raw["candidate_sha256"] = "0" * 64
    assert any("exact candidate" in x for x in validate_review(
        DiagramReview.model_validate(raw), candidate, sources))


def test_reviewer_must_inspect_the_image_and_negative_actions_control_status(example):
    data, sources, image, _ = example
    candidate = DiagramExtraction.model_validate(data)
    review = reviewed(candidate, image)
    raw = review.model_dump(mode="json")
    raw["inspected_sources"] = ["paper:1"]
    assert any("every candidate diagram" in x for x in validate_review(
        DiagramReview.model_validate(raw), candidate, sources))
    raw = review.model_dump(mode="json")
    raw["checks"][0].update(verdict="unsupported", action="revise")
    assert review_outcome(DiagramReview.model_validate(raw), candidate, sources) == "needs_revision"
    raw["checks"][0].update(verdict="unresolved", action="research")
    assert review_outcome(DiagramReview.model_validate(raw), candidate, sources) == "research_needed"


def test_correction_requires_primary_content_and_keeps_observation_separate(example):
    data, sources, image, text = example
    data["corrections"] = [{"id": "c", "target_id": "flow:f1",
                            "diagram_observation": "Diagram order", "primary_statement": "Primary order",
                            "disposition": "use_primary", "evidence": [image, text]}]
    data["flows"][0]["evidence"].append(text)
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources)
    graph = export_graph(candidate, reviewed(candidate, image), sources)
    assert any(n["node_type"] == "correction" for n in graph["nodes"])
    assert graph["workflow"]["corrections"][0]["diagram_observation"] == "Diagram order"
    assert graph["workflow"]["flows"] == candidate.model_dump(mode="json")["flows"]
    data["corrections"][0]["evidence"] = [image]
    assert any("both diagram and primary" in x for x in validate_extraction(
        DiagramExtraction.model_validate(data), sources))


@pytest.mark.parametrize("updates", [
    {"bbox": [0, 0, 0, 1]}, {"bbox": [0, 0, 2, 1]},
    {"support": "inferred"}, {"support": "reported"},
])
def test_image_regions_and_inference_labels_are_not_implicit(example, updates):
    image = {**example[2], **updates}
    with pytest.raises(ValidationError):
        Evidence.model_validate(image)
