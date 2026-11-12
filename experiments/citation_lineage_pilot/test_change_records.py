"""Versioned change fields, source-side checks and independent change review."""
import copy
import json

import pytest

from change_models import ChangeAnalysis, ChangeReview
from models import parse_analysis, validate_analysis, validate_review, graph_overlay
from genomics_discovery.illumina.change_records import ExperimentalChange, validate_changes, workflow_records
from genomics_discovery.illumina.paper_reads import record_read, check_full_reads


BEFORE = 'The earlier method attached adapters to purified DNA.'
AFTER = 'We adapted the earlier procedure and attached adapters to bead-bound DNA.'
SOURCES = {'seed': {'content_level': 'fulltext', 'text': AFTER},
           'R1': {'content_level': 'fulltext', 'text': BEFORE},
           'bibliography': {'content_level': 'seed_reference_contexts', 'text': json.dumps({
               'R1': {'contexts': [AFTER]}})}}
EXTRACTION = {'states': [{'id': 's1', 'label': 'Bead-bound DNA', 'properties': [{'id': 'attachment', 'value': 'bead-bound'}]}],
              'operations': [{'id': 'o1', 'label': 'Attach adapters'}], 'flows': []}


def evidence(sid, quote):
    return {'source_id': sid, 'quote': quote}


def change():
    return {'id': 'change1', 'compared_feature': 'DNA attachment state during adapter introduction',
        'before': 'Purified DNA receives adapters.', 'after': 'Bead-bound DNA receives adapters.',
        'retained': [], 'workflow_locations': {'earlier_scope': 'paper_only', 'earlier': [],
            'later': [{'kind': 'state', 'record_id': 's1', 'field': '/properties/0/value'}]},
        'evidence': {'before': [evidence('R1', BEFORE)], 'after': [evidence('seed', AFTER)],
                     'retained': [], 'inheritance': [evidence('seed', AFTER)]},
        'consequence_and_limits': {'effect_status': 'not_established', 'reported_effects': [],
                                  'limits': [], 'limit_evidence': [], 'unresolved': []}}


def proposal():
    return {'format_version': 'citation_analysis/4', 'seed_method': 'Later assay',
        'claims': [{'id': 'claim1', 'reference_id': 'R1', 'contributor': 'Earlier procedure',
            'contributor_kind': 'experimental_component', 'relation': 'documented_reuse',
            'citation_context': AFTER, 'claim_scope': 'seed_workflow', 'seed_operation_ids': ['o1'],
            'change_records': [change()], 'catalogue_candidates': [], 'unresolved': []}],
        'reference_dispositions': {'R1': 'Investigated procedural attribution'},
        'summary': 'One attributed procedure with a different attachment state; no gain established.'}


def review():
    check = {'change_id': 'change1', 'decision': 'accept',
        'factual_assessment': 'Both attachment states are reported.',
        'workflow_assessment': 'The State property is an existing boundary.',
        'evidence_assessment': 'Later attribution and earlier demonstration are distinct.',
        'consequence_assessment': 'No measured improvement is asserted.',
        'evidence': [evidence('R1', BEFORE), evidence('seed', AFTER)]}
    return {'format_version': 'citation_change_review/1', 'analysis_sha256': 'test-pin',
        'findings': [{'claim_id': 'claim1', 'decision': 'accept', 'rationale': 'Synthetic two-sided source check.',
            'evidence': check['evidence'], 'unresolved_assessments': [], 'change_checks': [check]}],
        'omitted_candidates': [], 'summary': 'Synthetic software fixture, not assay evidence.'}


def validate(candidate, sources=SOURCES):
    validate_analysis(candidate, sources, ['R1'], ['o1'], [], seed_extraction=EXTRACTION)


def test_explicit_fields_flow_from_native_format_to_reviewed_overlay():
    candidate = parse_analysis(proposal()); assert isinstance(candidate, ChangeAnalysis)
    validate(candidate)
    judged = ChangeReview.model_validate(review()); validate_review(judged, candidate, 'test-pin', SOURCES)
    result = graph_overlay(candidate, judged, SOURCES, 'test-pin', {})
    assert result['format_version'] == 'citation_lineage_pilot/4'
    item = result['links'][0]
    assert item['kind'] == 'documented_reuse' and item['relationship_layer'] == 'history'
    assert item['historical_development_edge'] is True
    assert result['classification_policy'] == 'assay_contribution_scope/1'
    assert item['claim']['change_records'][0]['workflow_locations']['later'][0]['kind'] == 'state'
    assert 'seed_design_change' not in candidate.model_dump()['claims'][0]
    assert item['claim']['seed_design_change'] == candidate.claims[0].seed_design_change


@pytest.mark.parametrize('contributor_kind,layer,historical', [
    ('assay','history',True),('experimental_component','history',True),
    ('general_method','supporting_context',False),('other','supporting_context',False)])
def test_documented_use_is_scoped_separately_from_experimental_ancestry(contributor_kind,layer,historical):
    raw=proposal();raw['claims'][0]['contributor_kind']=contributor_kind
    candidate=parse_analysis(raw);validate(candidate)
    judged=ChangeReview.model_validate(review())
    result=graph_overlay(candidate,judged,SOURCES,'test-pin',{})
    edge=result['links'][0]
    assert edge['kind']=='documented_reuse'  # Keep a valid source-supported use.
    assert edge['relationship_layer']==layer
    assert edge['historical_development_edge'] is historical
    assert edge['claim']['change_records']==raw['claims'][0]['change_records']
    assert edge['review']==judged.findings[0].model_dump()


@pytest.mark.parametrize('defect', ['wrong_before_side', 'invented_state', 'invented_property', 'invented_earlier_ids'])
def test_change_locations_and_evidence_sides_are_checked(defect):
    raw = proposal(); c = raw['claims'][0]['change_records'][0]
    if defect == 'wrong_before_side': c['evidence']['before'] = [evidence('seed', AFTER)]
    elif defect == 'invented_state': c['workflow_locations']['later'][0]['record_id'] = 'invented'
    elif defect == 'invented_property': c['workflow_locations']['later'][0]['field'] = '/properties/9/value'
    else:
        c['workflow_locations'].update(earlier_scope='bound_workflow', earlier=[{
            'kind': 'operation', 'record_id': 'not_supplied', 'field': None}])
    with pytest.raises(ValueError): validate(ChangeAnalysis.model_validate(raw))


@pytest.mark.parametrize('defect', ['missing_change_review', 'nonaccepted_change', 'abstract_only', 'no_attribution'])
def test_claim_acceptance_cannot_override_failed_or_unreviewed_changes(defect):
    raw, judged, sources = proposal(), review(), copy.deepcopy(SOURCES)
    if defect == 'missing_change_review': judged['findings'][0]['change_checks'] = []
    elif defect == 'nonaccepted_change': judged['findings'][0]['change_checks'][0]['decision'] = 'needs_evidence'
    elif defect == 'abstract_only': sources['R1']['content_level'] = 'abstract'
    else: raw['claims'][0]['change_records'][0]['evidence']['inheritance'] = []
    with pytest.raises(ValueError):
        validate_review(ChangeReview.model_validate(judged), ChangeAnalysis.model_validate(raw), 'test-pin', sources)


def test_improvement_requires_actual_metric_comparator_and_context():
    raw = change(); effects = raw['consequence_and_limits']; effects['effect_status'] = 'reported'
    effects['reported_effects'] = [{'status': 'measured', 'description': 'Reported recovery differs.',
        'metric': 'recovery', 'reported_value': '2-fold', 'comparison_basis': 'comparative',
        'comparator': None, 'tested_context': 'Specified sample',
        'attribution_scope': 'this_change', 'joint_change_ids': [], 'evidence': [evidence('seed', AFTER)]}]
    with pytest.raises(ValueError, match='comparator'):
        ExperimentalChange.model_validate(raw)
    effects['reported_effects'][0]['comparator'] = 'Specified earlier procedure'
    assert ExperimentalChange.model_validate(raw).consequence_and_limits.reported_effects[0].reported_value == '2-fold'
    # Quote entailment is a separate scientific-review obligation: this test
    # verifies fields, not that the invented numerical fixture is biologically true.


def test_absolute_measurement_does_not_require_an_invented_comparator():
    raw = change(); consequences = raw['consequence_and_limits']
    consequences.update(effect_status='reported', reported_effects=[{
        'status': 'measured', 'description': 'Reported absolute yield, not evidence of improvement.',
        'metric': 'library yield', 'reported_value': '5 ng', 'comparison_basis': 'absolute',
        'comparator': None, 'tested_context': 'Specified sample', 'attribution_scope': 'whole_assay',
        'joint_change_ids': [], 'evidence': [evidence('seed', AFTER)]}])
    assert ExperimentalChange.model_validate(raw).consequence_and_limits.reported_effects[0].comparator is None


def test_operation_grouping_does_not_define_historical_relation():
    raw = proposal(); candidate = ChangeAnalysis.model_validate(raw); validate(candidate)
    split = copy.deepcopy(EXTRACTION)
    split['operations'] = [{'id': 'oA', 'label': 'Prepare input'}, {'id': 'oB', 'label': 'Attach adapters'}]
    raw['claims'][0]['seed_operation_ids'] = ['oA', 'oB']
    validate_analysis(ChangeAnalysis.model_validate(raw), SOURCES, ['R1'], ['oA', 'oB'], [], seed_extraction=split)
    assert raw['claims'][0]['relation'] == candidate.claims[0].relation == 'documented_reuse'


def test_full_paper_read_coverage_rejects_query_only_gaps_and_stale_sources(tmp_path):
    sources = {'seed': {'text': '0123456789'}}
    registry = tmp_path / 'sources.json'; registry.write_text(json.dumps(sources))
    ledger = tmp_path / 'reads.jsonl'
    record_read(ledger, registry, sources, {'source_id': 'seed', 'start': 5, 'text': '56789'})
    with pytest.raises(ValueError, match='Full paper'):
        check_full_reads(ledger, registry, sources, ['seed'])
    record_read(ledger, registry, sources, {'source_id': 'seed', 'start': 0, 'text': '01234'})
    assert check_full_reads(ledger, registry, sources, ['seed'])['papers']['seed']['complete_text_delivered']
    registry.write_text(json.dumps({'seed': {'text': 'Different paper text'}}))
    with pytest.raises(ValueError):
        check_full_reads(ledger, registry, {'seed': {'text': 'Different paper text'}}, ['seed'])


def test_explicit_reviewed_change_reopens_through_the_history_query(tmp_path):
    from genomics_discovery.illumina.extraction import sha
    from genomics_discovery.illumina.history import build_history_index, technology_history
    extraction = tmp_path / 'extraction.json'
    extraction.write_text(json.dumps({**EXTRACTION, 'record_id': 'later'}))
    registry = tmp_path / 'sources.json'; registry.write_text(json.dumps(SOURCES))
    candidate = ChangeAnalysis.model_validate(proposal()); judged = ChangeReview.model_validate(review())
    overlay = graph_overlay(candidate, judged, SOURCES, 'test-pin', {
        'record_id': 'later', 'parent_artifact_pins': {'extraction.json': sha(extraction)}})
    path = tmp_path / 'graph.json'; path.write_text(json.dumps(overlay))
    manifest = tmp_path / 'history_inputs.json'; manifest.write_text(json.dumps({
        'format_version': 'illumina_history_inputs/1', 'overlays': [{
            'path': str(path), 'sha256': sha(path), 'seed_extraction': str(extraction),
            'source_registry': {'path': str(registry), 'sha256': sha(registry)}}]}))
    result = technology_history(build_history_index(manifest), 'later')
    claim = result['historical_contributions'][0]['claim']
    assert claim['change_records'][0]['workflow_locations']['later'][0]['kind'] == 'state'
    assert result['frontier'][0]['continuation_status'] == 'unbound_external_contribution'
    registry.write_text(json.dumps({'seed': {'text': 'Changed registered evidence'}}))
    with pytest.raises(ValueError, match='History input changed'):
        build_history_index(manifest)
