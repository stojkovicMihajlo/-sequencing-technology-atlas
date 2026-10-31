"""Independent negative controls for scientific source classes and review scope.

These tests exercise deterministic acceptance boundaries, not model entailment.
"""

import pytest

from genomics_discovery.curation.illumina_diagrams import (
    DiagramExtraction, DiagramReview, digest, review_outcome, review_targets,
    validate_extraction, validate_review,
)


@pytest.fixture
def control():
    image = {"source_id": "diagram:d", "basis": "image", "support": "depicted",
             "locator": "capture stage", "bbox": [0.1, 0.1, 0.9, 0.9]}
    paper = {"source_id": "paper:p", "basis": "text", "support": "reported",
             "locator": "Methods/capture", "quote": "Capture precedes library construction."}
    metadata = {"source_id": "metadata:p", "basis": "text", "support": "reported",
                "locator": "title", "quote": "A capture method"}
    sources = {
        "diagram:d": {"kind": "image", "path": "diagram.png", "sha256": "a" * 64},
        "paper:p": {"kind": "text", "content_level": "fulltext",
                    "text": "Capture precedes library construction.", "pmid": "1"},
        "metadata:p": {"kind": "text", "content_level": "metadata",
                       "text": "A capture method", "pmid": "1"},
    }
    data = {
        "record_id": "control", "title": "Scoped source diagram",
        "method_names": ["A", "B"], "scope": "Two explicitly named methods",
        "inspected_sources": list(sources),
        "states": [
            {"id": "before", "label": "RNA", "kind": "material", "evidence": [image]},
            {"id": "after", "label": "Captured RNA", "kind": "material", "evidence": [image]},
        ],
        "operations": [{"id": "capture", "label": "Capture", "description": "Capture RNA",
                        "inputs": ["before"], "outputs": ["after"], "evidence": [image]}],
        "flows": [
            {"id": "in", "source": "before", "target": "capture", "kind": "material_input",
             "evidence": [image]},
            {"id": "out", "source": "capture", "target": "after", "kind": "material_output",
             "evidence": [image]},
        ],
        "method_scopes": [{"method": name, "operation_ids": ["capture"], "status": "resolved",
                           "rationale": "Both variants explicitly depict capture", "evidence": [image]}
                          for name in ["A", "B"]],
        "paper_matches": [{"id": "paper_match", "source_id": "paper:p", "methods": ["A", "B"],
                           "assessment": "content_checked", "relationship": "supports_architecture",
                           "evidence": [paper]}],
        "corrections": [], "unknowns": [],
    }
    return data, sources, image, paper, metadata


def positive_review(candidate, image):
    checks = []
    for target in sorted(review_targets(candidate)):
        evidence = [image]
        if target.startswith(("state:", "operation:", "flow:")):
            kind, ident = target.split(":", 1)
            records = {"state": candidate.states, "operation": candidate.operations,
                       "flow": candidate.flows}[kind]
            evidence = next(record.evidence for record in records if record.id == ident)
        elif target.startswith("paper:"):
            evidence = next(p.evidence for p in candidate.paper_matches
                            if "paper:" + p.id == target)
        elif target.startswith("correction:"):
            evidence = next(p.evidence for p in candidate.corrections
                            if "correction:" + p.id == target)
        checks.append({"target": target, "verdict": "supported", "action": "accept",
                       "rationale": "Target checked against its source", "evidence": evidence})
    return DiagramReview(record_id=candidate.record_id, candidate_sha256=digest(candidate),
                         inspected_sources=candidate.inspected_sources, checks=checks)


def correction(image, paper, disposition):
    return {"id": "correction", "target_id": "flow:in", "diagram_observation": "Drawing order",
            "primary_statement": "Primary protocol reports a different order",
            "disposition": disposition, "evidence": [image, paper]}


def test_positive_control_is_supported(control):
    data, sources, image, _, _ = control
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources)
    assert review_outcome(positive_review(candidate, image), candidate, sources) == "supported"


@pytest.mark.parametrize("collection", ["states", "operations", "flows"])
def test_metadata_cannot_be_sole_evidence_for_a_scientific_record(control, collection):
    data, sources, _, _, metadata = control
    data[collection][0]["evidence"] = [metadata]
    candidate = DiagramExtraction.model_validate(data)
    assert validate_extraction(candidate, sources), "Publication metadata was accepted as scientific evidence"


def test_unresolved_applicability_cannot_be_supported_by_omitting_unknown(control):
    data, sources, image, _, _ = control
    data["method_scopes"][1].update(status="unresolved", operation_ids=[], evidence=[],
                                    rationale="Source does not identify B's operations")
    candidate = DiagramExtraction.model_validate(data)
    assert review_outcome(positive_review(candidate, image), candidate, sources) == "research_needed"


def test_unresolved_correction_cannot_be_supported_by_omitting_unknown(control):
    data, sources, image, paper, _ = control
    data["corrections"] = [correction(image, paper, "keep_unresolved")]
    candidate = DiagramExtraction.model_validate(data)
    assert review_outcome(positive_review(candidate, image), candidate, sources) == "research_needed"


def test_primary_correction_requires_primary_support_on_affected_target(control):
    data, sources, image, paper, _ = control
    data["corrections"] = [correction(image, paper, "use_primary")]
    candidate = DiagramExtraction.model_validate(data)
    assert validate_extraction(candidate, sources), "Primary correction left its target supported by image only"


def test_matched_publication_cannot_be_an_image_source(control):
    data, sources, _, _, _ = control
    data["paper_matches"][0].update(source_id="diagram:d", assessment="listed_not_read",
                                    relationship="catalogue_citation", evidence=[])
    candidate = DiagramExtraction.model_validate(data)
    assert validate_extraction(candidate, sources), "An image was accepted as a matched publication"


def test_content_checked_paper_needs_reviewer_content_evidence(control):
    data, sources, image, _, _ = control
    candidate = DiagramExtraction.model_validate(data)
    raw = positive_review(candidate, image).model_dump(mode="json")
    next(c for c in raw["checks"] if c["target"] == "paper:paper_match")["evidence"] = [image]
    assert validate_review(DiagramReview.model_validate(raw), candidate, sources), (
        "A generic diagram judgment accepted publication-content verification")


def test_primary_correction_review_needs_both_sources(control):
    data, sources, image, paper, _ = control
    data["corrections"] = [correction(image, paper, "use_primary")]
    data["flows"][0]["evidence"] = [paper]
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources)
    baseline = positive_review(candidate, image)
    assert not validate_review(baseline, candidate, sources)
    raw = baseline.model_dump(mode="json")
    next(c for c in raw["checks"] if c["target"] == "correction:correction")["evidence"] = [image]
    assert validate_review(DiagramReview.model_validate(raw), candidate, sources), (
        "A correction was accepted without reviewing its primary support")
