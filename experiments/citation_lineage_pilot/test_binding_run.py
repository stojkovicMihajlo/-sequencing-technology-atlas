"""Reopen a mapped citation through the actual history consumer; fixtures only."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from change_models import ChangeAnalysis, ChangeReview
from models import graph_overlay
import pair_run
from test_change_records import proposal, review, SOURCES, EXTRACTION, BEFORE, AFTER
from genomics_discovery.illumina.extraction import sha, write
from genomics_discovery.illumina.history import build_history_index, technology_history
from genomics_discovery.illumina.history_bindings import WorkflowBinding, WorkflowBindingReview


def bundle(tmp_path):
    root = tmp_path / 'binding'; inputs = root / 'inputs'; inputs.mkdir(parents=True)
    earlier = copy.deepcopy(EXTRACTION); earlier['record_id'] = 'earlier'
    later = copy.deepcopy(EXTRACTION); later['record_id'] = 'later'
    for side, extraction, text in (('left', earlier, BEFORE), ('right', later, AFTER)):
        write(inputs / (side + '_extraction.json'), extraction)
        (inputs / (side + '_primary.xml')).write_text('<article><body><p>' + text + '</p></body></article>')
    sources = copy.deepcopy(SOURCES)
    sources['R1']['original'] = {'path': str(inputs / 'left_primary.xml'), 'sha256': sha(inputs / 'left_primary.xml')}
    sources['seed']['provenance'] = {'original': {'path': str(inputs / 'right_primary.xml'), 'sha256': sha(inputs / 'right_primary.xml')}}
    sides = {side: {'record_id': ident, 'title': ident, 'paper_source_ids': [side + ':paper'],
        'extraction_sha256': sha(inputs / (side + '_extraction.json'))}
        for side, ident in (('left', 'earlier'), ('right', 'later'))}
    candidate = ChangeAnalysis.model_validate(proposal()); judged = ChangeReview.model_validate(review())
    original = graph_overlay(candidate, judged, sources, 'test-pin', {
        'record_id': 'later', 'parent_artifact_pins': {'extraction.json': sides['right']['extraction_sha256']}})
    write(inputs / 'citation_graph.json', original); write(inputs / 'citation_sources.json', sources)
    write(inputs / 'citation_manifest.json', {'format_version': 'illumina_history_inputs/1', 'overlays': [{
        'path': 'citation_graph.json', 'sha256': sha(inputs / 'citation_graph.json'),
        'seed_extraction': 'right_extraction.json', 'source_registry': {
            'path': 'citation_sources.json', 'sha256': sha(inputs / 'citation_sources.json')}}]})
    write(inputs / 'citation.json', {'overlay_sha256': sha(inputs / 'citation_graph.json'), 'item': original['links'][0]})
    write(inputs / 'sources.json', {**sources, 'left:paper': copy.deepcopy(sources['R1']), 'right:paper': copy.deepcopy(sources['seed'])})
    write(inputs / 'sides.json', sides)
    write(inputs / 'graph.json', {'cards': {}, 'nodes': [], 'edges': []})
    write(inputs / 'bibliography.json', {side: {'original_sha256': sha(inputs / (side + '_primary.xml')),
        'local_path': str(inputs / (side + '_primary.xml'))} for side in sides})
    author = root / 'attempts/001_author'; author.mkdir(parents=True)
    reviewer = root / 'attempts/002_review'; reviewer.mkdir()
    document = WorkflowBinding.model_validate({'citation_claim_id': 'claim1',
        'citation_overlay_sha256': sha(inputs / 'citation_graph.json'), 'mappings': [{
            'change_id': 'change1', 'status': 'located', 'earlier_locations': [{
                'kind': 'state', 'record_id': 's1', 'field': '/properties/0/value'}],
            'rationale': 'Fixture State property is explicitly represented.', 'evidence': [
                {'source_id': 'left:paper', 'quote': BEFORE}, {'source_id': 'right:paper', 'quote': AFTER}],
            'unresolved': [], 'refinement_request': None}], 'summary': 'Fixture location only.'})
    write(author / 'artifact.json', document.model_dump(mode='json'))
    fresh = WorkflowBindingReview.model_validate({'analysis_sha256': sha(author / 'artifact.json'), 'findings': [{
        'change_id': 'change1', 'decision': 'accept', 'rationale': 'Fixture two-sided location review.',
        'evidence': document.mappings[0].model_dump()['evidence'], 'unresolved_assessments': []}],
        'omitted_candidates': [], 'summary': 'Fixture, not a native scientific review.'})
    write(reviewer / 'artifact.json', fresh.model_dump(mode='json'))
    write(root / 'state.json', {'stages': [{'role': 'review', 'path': str(reviewer)}]})
    graph = pair_run.export_binding(root, document, fresh, author / 'artifact.json')
    write(root / 'graph.json', graph)
    manifest = tmp_path / 'history.json'
    entries = json.loads((inputs / 'citation_manifest.json').read_text())['overlays']
    for entry in entries:
        for key in ('path', 'seed_extraction'): entry[key] = str(inputs / entry[key])
        entry['source_registry']['path'] = str(inputs / entry['source_registry']['path'])
    entries.append({'path': str(root / 'graph.json'), 'sha256': sha(root / 'graph.json'),
        'source_registry': {'path': str(inputs / 'sources.json'), 'sha256': sha(inputs / 'sources.json')}})
    write(manifest, {'format_version': 'illumina_history_inputs/1', 'overlays': entries})
    return root, manifest


def test_reviewed_binding_connects_workflows_without_counting_its_paper_edge_twice(tmp_path):
    root, manifest = bundle(tmp_path)
    index = build_history_index(manifest)
    assert len(index['edges']) == 1
    result = technology_history(index, 'later')
    assert {node.get('record_id') for node in result['nodes']} == {'earlier', 'later'}
    claim = result['historical_contributions'][0]['claim']
    assert claim['change_records'][0]['workflow_locations']['earlier_scope'] == 'bound_workflow'
    assert claim['change_records'][0]['after'] == proposal()['claims'][0]['change_records'][0]['after']
    assert not [node for node in index['nodes'].values() if node['kind'] != 'bound_workflow']


def test_mapping_cannot_rewrite_accepted_scientific_assertions_even_with_a_new_export_hash(tmp_path):
    root, manifest = bundle(tmp_path)
    graph = json.loads((root / 'graph.json').read_text())
    graph['links'][0]['claim']['change_records'][0]['after'] = 'An unsupported replacement claim.'
    write(root / 'graph.json', graph)
    data = json.loads(manifest.read_text()); data['overlays'][-1]['sha256'] = sha(root / 'graph.json'); write(manifest, data)
    with pytest.raises(ValueError, match='changes a scientific assertion'):
        build_history_index(manifest)


def test_changed_workflow_bytes_cannot_inherit_an_earlier_mapping_review(tmp_path):
    root, manifest = bundle(tmp_path)
    path = root / 'inputs/left_extraction.json'; path.write_text(path.read_text() + ' ')
    with pytest.raises(ValueError, match='artifact changed'):
        build_history_index(manifest)
