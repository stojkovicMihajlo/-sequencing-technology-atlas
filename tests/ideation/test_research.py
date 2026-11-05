"""The research contract binds questions, findings, graph targets and review."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from genomics_discovery.ideation.models import (
    CandidateIdea, IdeaReview, ProposedAssay, RESEARCH_AREAS, parse_candidate,
    validate_review_binding,
)
from genomics_discovery.ideation.run import SCHEMAS, schemas_for
from genomics_discovery.illumina.models import digest
from tests.ideation.test_models import candidate_data, review_data


def research_questions():
    return {
        'capability': 'Which prior assay resolves the required RNA contrast and in which specimen?',
        'mechanism': 'Which demonstrated property could distinguish the two RNA populations?',
        'interfaces': 'Would library preparation retain the proposed discriminating mark?',
        'challenge': 'Could selective RNA recovery mimic the expected gain, and what control distinguishes it?',
    }


def research_candidate_data():
    data = candidate_data()
    data.update(format_version='proposed_assay/1.1', research={
        area: {'question': question, 'conclusion': 'No scientific finding in this software fixture.',
               'status': 'unresolved', 'targets': ['operation:prepare', 'change:change1'],
               'remaining_uncertainty': 'Retrieve appropriate evidence; no research was performed by this fixture.'}
        for area, question in research_questions().items()})
    return data


def test_current_contract_keeps_graph_and_research_separate_and_reviewable():
    idea = parse_candidate(research_candidate_data())
    assert isinstance(idea, ProposedAssay)
    review = IdeaReview.model_validate(review_data(idea))
    validate_review_binding(review, idea)
    assert {'research:'+area for area in RESEARCH_AREAS} <= set(review.reviewed_targets)
    assert not any(a.target.startswith('research:') for a in idea.assessments)
    assert SCHEMAS['propose'] is ProposedAssay


@pytest.mark.parametrize('change', [
    lambda d: d['research'].pop('interfaces'),
    lambda d: d['research']['mechanism'].update(status='evidence_found'),
    lambda d: d['research']['interfaces'].update(targets=['operation:invented']),
    lambda d: d['research']['interfaces'].update(targets=['operation:prepare', 'operation:prepare']),
    lambda d: d['research']['challenge'].update(remaining_uncertainty=None),
    lambda d: d.update(format_version='proposed_assay/1.0'),
])
def test_research_cannot_be_missing_unlocated_or_silently_downgraded(change):
    data = research_candidate_data(); change(data)
    with pytest.raises(ValidationError): parse_candidate(data)


def test_reviewer_cannot_skip_research_or_keep_stale_findings():
    idea = parse_candidate(research_candidate_data())
    review = review_data(idea)
    review['reviewed_targets'].remove('research:interfaces')
    with pytest.raises(ValueError, match='every graph'):
        validate_review_binding(IdeaReview.model_validate(review), idea)
    revised = idea.model_dump(mode='json')
    revised['research']['interfaces']['conclusion'] = 'A new unverified conclusion.'
    with pytest.raises(ValueError, match='exact candidate'):
        validate_review_binding(IdeaReview.model_validate(review_data(idea)), parse_candidate(revised))


def test_legacy_digest_and_schema_do_not_gain_new_fields():
    legacy = CandidateIdea.model_validate(candidate_data())
    saved = deepcopy(legacy.model_dump(mode='json'))
    parsed = parse_candidate(saved)
    assert type(parsed) is CandidateIdea
    assert parsed.model_dump(mode='json') == saved
    assert digest(parsed) == digest(legacy)
    assert 'research' not in saved
    assert schemas_for({'format_version': 'ideation_run/1'})['propose'] is CandidateIdea
    assert schemas_for({'format_version': 'ideation_run/2'})['propose'] is ProposedAssay
    with pytest.raises(ValueError, match='Unknown candidate'):
        parse_candidate({**saved, 'format_version': 'imaginary'})
