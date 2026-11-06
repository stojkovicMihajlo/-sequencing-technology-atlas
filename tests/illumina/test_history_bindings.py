"""A real citation is not an exact workflow mapping; software fixtures only."""
import copy
import json

import pytest

from genomics_discovery.illumina.change_records import resolve_pointer
from genomics_discovery.illumina.history_bindings import (
    WorkflowBinding, WorkflowBindingReview, mapped_claim, validate_binding, validate_binding_review,
)


EARLIER = 'The earlier workflow copies the selected DNA with custom primers.'
LATER = 'We used those custom primers for the bead-bound tagged fragments.'


@pytest.fixture
def binding_case():
    evidence = lambda sid, quote: {'source_id': sid, 'quote': quote}
    earlier = {'record_id': 'earlier', 'states': [{'id': 'in', 'properties': [{'id': 'p', 'value': 'primer'}]}],
        'operations': [{'id': 'amplify'}], 'flows': [{'id': 'f', 'source': 'in', 'target': 'amplify'}]}
    later = {'record_id': 'later', 'states': [{'id': 'out', 'label': 'tagged DNA'}],
        'operations': [{'id': 'amplify'}], 'flows': []}
    claim = {'id': 'claim1', 'relation': 'documented_reuse', 'claim_scope': 'seed_workflow', 'unresolved': [],
        'change_records': [{'id': 'change1', 'before': EARLIER, 'after': LATER,
            'workflow_locations': {'earlier_scope': 'paper_only', 'earlier': [],
                'later': [{'kind': 'state', 'record_id': 'out', 'field': '/label'}]}}]}
    context = {'citation': {'overlay_sha256': 'a' * 64, 'item': {'claim': claim, 'review': {'decision': 'accept'}}},
        'sides': {'left': {'paper_source_ids': ['left:paper']}, 'right': {'paper_source_ids': ['right:paper']}},
        'left_extraction': earlier, 'right_extraction': later,
        'sources': {'left:paper': {'content_level': 'fulltext', 'text': EARLIER},
                    'right:paper': {'content_level': 'fulltext', 'text': LATER}}}
    locations = [{'kind': 'state', 'record_id': 'in', 'field': '/properties/0/value'},
                 {'kind': 'flow', 'record_id': 'f', 'field': '/source'}]
    refs = [evidence('left:paper', EARLIER), evidence('right:paper', LATER)]
    document = {'citation_claim_id': 'claim1', 'citation_overlay_sha256': 'a' * 64,
        'mappings': [{'change_id': 'change1', 'status': 'located', 'earlier_locations': locations,
            'rationale': 'The supplied graph contains the scoped primer feature.', 'evidence': refs,
            'unresolved': [], 'refinement_request': None}], 'summary': 'No new scientific claim.'}
    review = {'analysis_sha256': 'b' * 64, 'findings': [{'change_id': 'change1', 'decision': 'accept',
        'rationale': 'Both exact paper sides and the supplied graph locations checked.',
        'evidence': refs, 'unresolved_assessments': []}], 'omitted_candidates': [], 'summary': 'Fixture only.'}
    return context, document, review


def test_state_property_and_flow_can_locate_an_unchanged_contribution(binding_case):
    context, raw, judged = binding_case
    document = WorkflowBinding.model_validate(raw); original = copy.deepcopy(context['citation']['item']['claim'])
    validate_binding_review(WorkflowBindingReview.model_validate(judged), document, context, 'b' * 64)
    mapped = mapped_claim(document, context)
    assert mapped['change_records'][0]['workflow_locations']['earlier_scope'] == 'bound_workflow'
    assert {a['kind'] for a in mapped['change_records'][0]['workflow_locations']['earlier']} == {'state', 'flow'}
    assert context['citation']['item']['claim'] == original
    mapped['change_records'][0]['workflow_locations'] = original['change_records'][0]['workflow_locations']
    assert mapped == original


@pytest.mark.parametrize('defect', ['wrong_claim', 'wrong_version', 'invented_state', 'wrong_kind',
    'invented_property', 'missing_change', 'duplicate_change', 'outside_assay'])
def test_binding_cannot_substitute_identity_or_invent_workflow_records(binding_case, defect):
    context, raw, _ = binding_case
    if defect == 'wrong_claim': raw['citation_claim_id'] = 'other'
    elif defect == 'wrong_version': raw['citation_overlay_sha256'] = 'c' * 64
    elif defect == 'invented_state': raw['mappings'][0]['earlier_locations'][0]['record_id'] = 'invented'
    elif defect == 'wrong_kind': raw['mappings'][0]['earlier_locations'][0]['kind'] = 'operation'
    elif defect == 'invented_property': raw['mappings'][0]['earlier_locations'][0]['field'] = '/properties/8/value'
    elif defect == 'missing_change': context['citation']['item']['claim']['change_records'].append(
        {**copy.deepcopy(context['citation']['item']['claim']['change_records'][0]), 'id': 'change2'})
    elif defect == 'duplicate_change': raw['mappings'].append(copy.deepcopy(raw['mappings'][0]))
    else: context['citation']['item']['claim']['claim_scope'] = 'supporting_experiment'
    with pytest.raises(ValueError): validate_binding(WorkflowBinding.model_validate(raw), context)


@pytest.mark.parametrize('defect', ['one_paper', 'stale_review', 'missing_check', 'blocked_issue', 'refinement_pending'])
def test_fresh_review_cannot_approve_an_incomplete_mapping(binding_case, defect):
    context, raw, judged = binding_case
    if defect == 'one_paper': judged['findings'][0]['evidence'].pop()
    elif defect == 'stale_review': judged['analysis_sha256'] = 'stale'
    elif defect == 'missing_check': judged['findings'] = []
    elif defect == 'blocked_issue':
        raw['mappings'][0]['unresolved'] = ['Wrong implementation is possible.']
        judged['findings'][0]['unresolved_assessments'] = [{'issue_index': 0, 'blocks_binding': True, 'rationale': 'Source does not identify this implementation.'}]
    else:
        raw['mappings'][0].update(status='needs_workflow_refinement', refinement_request='Locate the missing primer feature in the earlier source.')
    with pytest.raises(ValueError):
        validate_binding_review(WorkflowBindingReview.model_validate(judged), WorkflowBinding.model_validate(raw), context, 'b' * 64)


@pytest.mark.parametrize('pointer', ['/properties/-1', '/properties/01', '/bad~2escape'])
def test_workflow_locations_use_json_pointer_semantics(pointer):
    with pytest.raises(ValueError): resolve_pointer({'properties': ['one', 'two'], 'bad~2escape': 1}, pointer)
