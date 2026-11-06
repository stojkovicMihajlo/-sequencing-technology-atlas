"""Incomplete knowledge cannot silently become either ancestry or a global veto."""
import copy
import json

import pytest

from change_models import ChangeAnalysis, ChangeReview, CoverageChangeReview
from models import graph_overlay, validate_review
from test_change_records import proposal, review, SOURCES, BEFORE, AFTER, EXTRACTION
from genomics_discovery.illumina.extraction import sha, write
from genomics_discovery.illumina.history import build_history_index, technology_history


def current_review(findings=()):
    value = review(); value.pop('omitted_candidates')
    value.update(format_version='citation_change_review/2', coverage_findings=list(findings))
    return CoverageChangeReview.model_validate(value)


def coverage(kind='research_lead', *, blocks=False):
    return {'id': 'coverage1', 'kind': kind, 'reference_id': 'R1',
        'description': 'A synthetic source-coverage question, not a new experimental result.',
        'affected_claim_ids': ['claim1'] if blocks else [], 'blocks_affected_claims': blocks,
        'evidence': [{'source_id': 'seed', 'quote': AFTER}, {'source_id': 'R1', 'quote': BEFORE}],
        'requested_evidence': 'A separate reported implementation detail.' if kind == 'research_lead' else None}


def export(judged, sources=None, bindings=None):
    return graph_overlay(ChangeAnalysis.model_validate(proposal()), judged, sources or SOURCES,
                         'test-pin', bindings or {})


def test_research_request_is_preserved_without_suppressing_an_independent_accepted_claim():
    judged = current_review([coverage()]); graph = export(judged)
    assert len(graph['links']) == 1 and not graph['withheld']
    assert graph['coverage_review']['findings'][0]['kind'] == 'research_lead'
    assert not judged.omitted_candidates
    assert graph['coverage_review']['completeness'] == 'no_supported_omission_identified'


def test_supported_independent_omission_marks_incomplete_and_requests_revision_without_rewriting_other_claims():
    judged = current_review([coverage('omitted_supported_contribution')]); graph = export(judged)
    assert len(graph['links']) == 1 and judged.omitted_candidates
    assert graph['coverage_review']['completeness'] == 'incomplete'
    assert graph['links'][0]['claim']['change_records'][0]['after'] == proposal()['claims'][0]['change_records'][0]['after']


@pytest.mark.parametrize('defect', ['unknown_claim', 'empty_blocked_ids', 'abstract', 'missing_reference', 'one_sided', 'no_request'])
def test_coverage_status_and_evidence_are_enforced(defect):
    issue = coverage('omitted_supported_contribution'); sources = copy.deepcopy(SOURCES)
    if defect == 'unknown_claim': issue['affected_claim_ids'] = ['invented']
    elif defect == 'empty_blocked_ids': issue['blocks_affected_claims'] = True
    elif defect == 'abstract': sources['R1']['content_level'] = 'abstract'
    elif defect == 'missing_reference': issue['reference_id'] = 'unregistered'
    elif defect == 'one_sided': issue['evidence'] = issue['evidence'][:1]
    else: issue = coverage(); issue['requested_evidence'] = None
    with pytest.raises(ValueError): export(current_review([issue]), sources)


def test_specific_blocker_cannot_coexist_with_acceptance():
    judged = current_review([coverage(blocks=True)])
    with pytest.raises(ValueError, match='blocking coverage'):
        export(judged)
    judged.findings[0].decision = 'needs_evidence'
    graph = export(judged)
    assert not graph['links'] and graph['withheld'][0]['reason'] == 'needs_evidence'


def test_legacy_omission_publication_does_not_change():
    value = review(); value['omitted_candidates'] = ['Legacy unresolved review finding']
    graph = export(ChangeReview.model_validate(value))
    assert not graph['links'] and graph['withheld'][0]['reason'] == 'review_found_omissions'


def test_history_query_retains_research_lead_and_rejects_relabelled_coverage(tmp_path):
    extraction = {**EXTRACTION, 'record_id': 'later'}; write(tmp_path / 'extraction.json', extraction)
    bindings = {'record_id': 'later', 'parent_artifact_pins': {'extraction.json': sha(tmp_path / 'extraction.json')}}
    graph = export(current_review([coverage()]), bindings=bindings)
    write(tmp_path / 'sources.json', SOURCES)

    def manifest():
        write(tmp_path / 'graph.json', graph)
        path = tmp_path / 'history.json'
        write(path, {'format_version': 'illumina_history_inputs/1', 'overlays': [{
            'path': 'graph.json', 'sha256': sha(tmp_path / 'graph.json'), 'seed_extraction': 'extraction.json',
            'source_registry': {'path': 'sources.json', 'sha256': sha(tmp_path / 'sources.json')}}]})
        return path

    result = technology_history(build_history_index(manifest()), 'later')
    assert len(result['historical_contributions']) == 1
    assert result['coverage_reports'][0]['coverage']['findings'][0]['requested_evidence']
    graph['coverage_review']['findings'][0].update(affected_claim_ids=['claim1'], blocks_affected_claims=True)
    with pytest.raises(ValueError, match='blocking coverage'):
        build_history_index(manifest())
