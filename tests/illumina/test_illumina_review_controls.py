"""Independent negative controls for scientific source classes and review scope.

These tests exercise deterministic acceptance boundaries, not model entailment.
"""

import pytest

from genomics_discovery.illumina.models import (
    DiagramExtraction,
    DiagramReview,
    digest,
    review_outcome,
    review_reasons,
    review_targets,
    validate_extraction,
    validate_review,
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


def test_card_level_background_citation_need_not_invent_a_member_assignment(control):
    data, sources, image, _, metadata = control
    data['paper_matches'].append({'id': 'unassigned', 'source_id': 'metadata:p',
        'methods': [], 'assessment': 'identity_verified', 'relationship': 'background',
        'evidence': [metadata]})
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources, require_paper_matches=True)
    assert review_outcome(positive_review(candidate, image), candidate, sources) == 'supported'


@pytest.mark.parametrize('relationship', ['supports_architecture', 'supports_variant'])
def test_positive_scientific_support_cannot_omit_the_supported_method(control, relationship):
    data, _, _, _, _ = control
    data['paper_matches'][0].update(methods=[], relationship=relationship)
    with pytest.raises(ValueError, match='must identify a named method'):
        DiagramExtraction.model_validate(data)


def test_unassigned_background_does_not_satisfy_method_paper_coverage(control):
    data, sources, _, _, _ = control
    data['paper_matches'][0].update(methods=[], relationship='background')
    candidate = DiagramExtraction.model_validate(data)
    assert any('Named method lacks a paper-match assessment' in error
               for error in validate_extraction(candidate, sources, require_paper_matches=True))


def test_host_publication_hold_is_explained_even_when_reviewer_accepts(control):
    data, sources, image, _, _ = control
    data['paper_matches'][0].update(methods=['A'], relationship='supports_architecture')
    data['paper_matches'].append({**data['paper_matches'][0], 'id': 'background_b',
                                  'methods': ['B'], 'relationship': 'background'})
    candidate = DiagramExtraction.model_validate(data)
    review = positive_review(candidate, image)
    assert not validate_extraction(candidate, sources, require_paper_matches=True)
    assert review_outcome(review, candidate, sources) == 'research_needed'
    assert review_reasons(review, candidate) == [{
        'target': 'scope:B', 'basis': 'publication_association_not_established',
        'question': 'The recorded papers do not establish a non-background association '
                    'for this named method. Resolve the association at the declared scope '
                    'or retain it as unresolved; background content alone is insufficient.'}]


def catalogue_mention(data, sources):
    sources['catalogue:c'] = {'kind': 'text', 'content_level': 'catalogue',
                              'text': 'Smith et al. A separate paper. Journal 2015.'}
    data['inspected_sources'].append('catalogue:c')
    paper = {'id': 'secondary_mention', 'source_id': 'catalogue:c', 'methods': [],
             'assessment': 'listed_not_read', 'relationship': 'catalogue_citation',
             'evidence': [{'source_id': 'catalogue:c', 'basis': 'text', 'support': 'reported',
                           'locator': 'References', 'quote': sources['catalogue:c']['text']}]}
    data['paper_matches'].append(paper)
    return paper


def test_literal_secondary_mention_is_retained_without_claiming_primary_support(control):
    data, sources, image, _, _ = control
    catalogue_mention(data, sources)
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources)
    assert review_outcome(positive_review(candidate, image), candidate, sources) == 'supported'


@pytest.mark.parametrize('change', [
    {'assessment': 'identity_verified'}, {'assessment': 'content_checked'},
    {'methods': ['B']}, {'relationship': 'supports_architecture', 'methods': ['B']},
    {'relationship': 'supports_variant', 'methods': ['B']}, {'evidence': []},
])
def test_secondary_mention_cannot_be_upgraded_or_assign_a_method(control, change):
    data, sources, _, _, _ = control
    paper = catalogue_mention(data, sources)
    paper.update(change)
    candidate = DiagramExtraction.model_validate(data)
    assert validate_extraction(candidate, sources)


def test_secondary_mention_does_not_close_missing_method_association(control):
    data, sources, image, _, _ = control
    data['paper_matches'][0]['methods'] = ['A']
    catalogue_mention(data, sources)
    data['unknowns'] = [{'id': 'missing_b', 'question': 'Which publication describes B?',
                         'blocking': True, 'affected_ids': ['scope:B']}]
    candidate = DiagramExtraction.model_validate(data)
    assert not validate_extraction(candidate, sources, require_paper_matches=True)
    assert review_outcome(positive_review(candidate, image), candidate, sources) == 'research_needed'


def test_primary_correction_cannot_use_a_title_to_support_an_external_claim(control):
    data, sources, image, _, _ = control
    sources['paper:p'].update(title='A capture method', text='A capture method.\nCapture precedes library construction.')
    title = {'source_id': 'paper:p', 'basis': 'text', 'support': 'reported',
             'locator': 'Title; external methods were found elsewhere', 'quote': 'A capture method.',
             'rationale': 'The title identifies the paper; unregistered external content gives the chemistry.'}
    data['corrections'] = [correction(image, title, 'use_primary')]
    data['flows'][0]['evidence'] = [image, title]
    candidate = DiagramExtraction.model_validate(data)
    errors = validate_extraction(candidate, sources)
    assert any('requires primary content beyond a title' in e for e in errors)
    assert any('Primary correction must be explicitly evidenced' in e for e in errors)
    review = positive_review(candidate, image)
    assert validate_review(review, candidate, sources)


def test_article_title_can_verify_identity_but_cannot_claim_content_inspection(control):
    data, sources, _, _, _ = control
    sources['paper:p'].update(title='A capture method', text='A capture method.\nCapture precedes library construction.')
    title = {'source_id': 'paper:p', 'basis': 'text', 'support': 'reported',
             'locator': 'Title', 'quote': 'A capture method.'}
    data['paper_matches'].append({'id': 'identity', 'source_id': 'paper:p', 'methods': [],
        'assessment': 'identity_verified', 'relationship': 'background', 'evidence': [title]})
    assert not validate_extraction(DiagramExtraction.model_validate(data), sources)
    data['paper_matches'][-1]['assessment'] = 'content_checked'
    assert any('Content-checked publication needs its own inspected content evidence' in e
               for e in validate_extraction(DiagramExtraction.model_validate(data), sources))


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
