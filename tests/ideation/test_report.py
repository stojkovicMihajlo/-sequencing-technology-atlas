"""Real model/report integration. Test candidates are not scientific results."""
import copy
import hashlib
import json
import re

import pytest

from genomics_discovery.ideation.models import CandidateIdea, IdeaReview, candidate_targets, parse_candidate
from genomics_discovery.ideation.report import export_report, proposal_overlay
from genomics_discovery.illumina.models import digest, property_target
from tests.ideation.test_models import atlas_ref, candidate_data, review_data
from tests.ideation.test_research import research_candidate_data


def bundle(*, parent=True, injected=False):
    data = candidate_data()
    if parent:
        data['changes'][0]['earlier_refs'] = [atlas_ref()]
    if injected:
        data['title'] = '</script><script>window.PWNED=1</script>&<img src=x onerror=alert(1)>'
        data['assessments'][0]['rationale'] = 'Literal source <script>bad()</script> & text.'
    idea = CandidateIdea.model_validate(data)
    review = IdeaReview.model_validate(review_data(idea))
    panel = {'reviews': [review.model_dump(mode='json')],
             'comparison_summary': 'Software fixture; no biological or novelty claim.',
             'searches_performed': [], 'previous_issue_resolutions': []}
    return idea, panel


def test_overlay_preserves_all_proposal_content_and_property_assessments():
    idea, panel = bundle()
    graph = proposal_overlay([idea.model_dump(mode='json')], panel, 'f' * 64)
    reconstructed = CandidateIdea.model_validate(graph['candidates'][0])
    assert reconstructed == idea
    assert set(a['target'] for a in graph['candidates'][0]['assessments']) == candidate_targets(idea)
    assert property_target('reads', 'type') in candidate_targets(reconstructed)
    assert graph['reviews'] == panel
    assert graph['atlas_sha256'] == 'f' * 64
    assert all(n['admission'] == 'proposal_only' for n in graph['nodes'])
    assert all(e['admission'] == 'proposal_only' for e in graph['edges'])
    assert not {'method_development', 'component_adoption', 'shared_mechanism'} & {e['kind'] for e in graph['edges']}


def test_prior_connection_passes_through_scoped_change_event():
    idea, panel = bundle()
    graph = proposal_overlay([idea.model_dump(mode='json')], panel, 'f' * 64)
    basis, = [e for e in graph['edges'] if e['kind'] == 'earlier_basis']
    link, = [e for e in graph['edges'] if e['kind'] == 'proposed_effect']
    assert basis['source'] == atlas_ref()['node_id']
    assert basis['target'] == link['source'] == graph['change_records'][0]['id']
    assert link['target'] == 'proposal:idea1:prepare'
    assert link['proposed_target'] == 'operation:prepare'
    assert basis['atlas_binding'] == atlas_ref()
    endpoints = {n['id'] for n in graph['nodes'] + graph['external_atlas_nodes']}
    assert all(e['source'] in endpoints and e['target'] in endpoints for e in graph['edges'])
    assert len({e['id'] for e in graph['edges']}) == len(graph['edges'])


def test_new_architecture_without_atlas_parent_keeps_its_design_change():
    idea, panel = bundle(parent=False)
    graph = proposal_overlay([idea.model_dump(mode='json')], panel, 'f' * 64)
    assert len(graph['change_records']) == 1
    assert graph['change_records'][0]['original'] == idea.changes[0].model_dump(mode='json')
    assert not graph['external_atlas_nodes']
    assert not any(e['kind'] == 'earlier_basis' for e in graph['edges'])
    assert any(e['kind'] == 'proposed_effect' for e in graph['edges'])


def test_property_and_flow_changes_retain_exact_addresses_without_inventing_nodes():
    idea, _ = bundle()
    data = idea.model_dump(mode='json')
    data['changes'][0]['proposed_target_ids'] = [property_target('reads', 'type'), 'flow:f2']
    idea = CandidateIdea.model_validate(data)
    panel = {'reviews': [review_data(idea)], 'comparison_summary': 'Fixture'}
    graph = proposal_overlay([data], panel, 'f' * 64)
    record = graph['change_records'][0]
    assert record['target_anchors'] == [
        {'target': property_target('reads', 'type'), 'kind': 'state_property',
         'graph_element_id': 'proposal:idea1:reads', 'property_id': 'type'},
        {'target': 'flow:f2', 'kind': 'flow', 'graph_element_id': 'proposal:idea1:f2'},
    ]
    link, = [e for e in graph['edges'] if e['kind'] == 'proposed_effect' and e['proposed_target'].startswith('state_property:')]
    assert link['target'] == 'proposal:idea1:reads'
    assert link['proposed_target'] == property_target('reads', 'type')
    assert 'proposal:idea1:f2' in {e['id'] for e in graph['edges']}
    assert 'proposal:idea1:f2' not in {n['id'] for n in graph['nodes']}
    ref, = [n for n in graph['nodes'] if n['node_type'] == 'flow_reference']
    assert ref['flow_id'] == 'proposal:idea1:f2'
    flow_link, = [e for e in graph['edges'] if e['kind'] == 'proposed_effect' and e['proposed_target'] == 'flow:f2']
    assert flow_link['target'] == ref['id']


def test_grouped_change_does_not_invent_pairwise_component_correspondences():
    idea, _ = bundle()
    data = idea.model_dump(mode='json')
    second = {**atlas_ref(), 'node_id': 'illumina:example:state:library'}
    data['changes'][0]['earlier_refs'].append(second)
    data['changes'][0]['proposed_target_ids'].append('state:cdna')
    idea = CandidateIdea.model_validate(data)
    graph = proposal_overlay([data], {'reviews': [review_data(idea)]}, 'f'*64)
    assert graph['format_version'] == 'assay_ideation_overlay/2'
    earlier = {atlas_ref()['node_id'], second['node_id']}
    later = {'proposal:idea1:prepare', 'proposal:idea1:cdna'}
    assert not any(e['source'] in earlier and e['target'] in later for e in graph['edges'])
    basis = [e for e in graph['edges'] if e['kind'] == 'earlier_basis']
    effects = [e for e in graph['edges'] if e['kind'] == 'proposed_effect']
    assert len(basis) == len(effects) == 2
    assert {e['target'] for e in basis} == {e['source'] for e in effects}


@pytest.mark.parametrize('failure', ['stale_review', 'missing_review', 'duplicate_candidate', 'invalid_route'])
def test_report_cannot_publish_invalid_or_unreviewed_proposal(failure):
    idea, panel = bundle(); candidates = [idea.model_dump(mode='json')]
    if failure == 'stale_review': panel['reviews'][0]['candidate_sha256'] = '0' * 64
    if failure == 'missing_review': panel['reviews'] = []
    if failure == 'duplicate_candidate': candidates.append(copy.deepcopy(candidates[0]))
    if failure == 'invalid_route': candidates[0]['workflow']['flows'][0]['target'] = 'invented'
    with pytest.raises(ValueError): proposal_overlay(candidates, panel, 'f' * 64)


def test_report_is_standalone_safe_and_does_not_modify_atlas(tmp_path):
    idea, panel = bundle(injected=True)
    original = tmp_path/'atlas.json'; original.write_text('{"canonical":"unchanged"}')
    raw = original.read_bytes(); atlas_sha = hashlib.sha256(raw).hexdigest()
    candidate = tmp_path/'candidate.json'; candidate.write_text(idea.model_dump_json())
    request = tmp_path/'request.json'; request.write_text(json.dumps({'prompt': 'Request </script><script>bad()</script>'}))
    run = tmp_path/'run'; run.mkdir()
    config = {'request_file': str(request), 'atlas_file': str(original), 'atlas_sha256': atlas_sha}
    export_report(run, config, [str(candidate)], panel)
    assert original.read_bytes() == raw
    assert CandidateIdea.model_validate_json(candidate.read_text()) == idea
    assert set(p.name for p in run.iterdir()) == {'proposal_graph.json', 'index.html', 'report.txt'}
    graph = json.loads((run/'proposal_graph.json').read_text())
    assert CandidateIdea.model_validate(graph['candidates'][0]) == idea
    page = (run/'index.html').read_text()
    assert len(re.findall(r'<script\b', page)) == 1
    assert page.count('</script>') == 1
    assert '<img src=x' not in page
    assert '\\u003c/script\\u003e' in page
    payload = page.split('const D=', 1)[1].split(';const NS=', 1)[0]
    decoded = json.loads(payload)
    assert CandidateIdea.model_validate(decoded['candidates'][0]) == idea
    assert decoded['request']['prompt'] == json.loads(request.read_text())['prompt']
    assert 'literature' not in decoded and 'literature_coverage' not in decoded
    assert 'textContent' in page and '.innerHTML' not in page
    assert "textblock(box,'Input States'" in page
    assert "textblock(box,'Output States'" in page
    assert "textblock(box,'Flow type'" in page
    assert "textblock(box,'From',name(record.source))" in page
    assert "textblock(box,'To',name(record.target))" in page
    assert 'not experimental validation' in page
    assert 'PROPOSALS, NOT VALIDATED TECHNOLOGIES' in (run/'report.txt').read_text()


def test_refused_export_leaves_no_partial_report(tmp_path):
    idea, panel = bundle(); panel['reviews'][0]['candidate_sha256'] = '0' * 64
    candidate = tmp_path/'candidate.json'; candidate.write_text(idea.model_dump_json())
    request = tmp_path/'request.json'; request.write_text('{"prompt":"Test"}')
    output = tmp_path/'output'; output.mkdir()
    with pytest.raises(ValueError):
        export_report(output, {'request_file': str(request), 'atlas_sha256': 'f' * 64}, [str(candidate)], panel)
    assert not list(output.iterdir())


def test_repeated_prior_anchor_does_not_duplicate_a_graph_edge():
    idea, panel = bundle(); data = idea.model_dump(mode='json')
    data['changes'][0]['earlier_refs'].append(atlas_ref())
    idea = CandidateIdea.model_validate(data); panel['reviews'] = [review_data(idea)]
    with pytest.raises(ValueError, match='Duplicate proposal edge'):
        proposal_overlay([data], panel, 'f' * 64)


def test_existing_literature_findings_and_coverage_are_displayed_with_version_binding(tmp_path):
    idea, panel = bundle()
    candidate = tmp_path/'candidate.json'; candidate.write_text(idea.model_dump_json())
    request = tmp_path/'request.json'; request.write_text('{"prompt":"Test"}')
    finding = {'candidate_id': idea.id, 'candidate_sha256': '0'*64,
        'checks': [{'id': 'check1', 'target': 'operation:prepare', 'claim': 'Fixture claim.',
                    'verdict': 'transfer_not_established', 'reason': 'Transfer remains untested.'}],
        'closest_priors': [{'title': '</script>Prior', 'overlap': 'Existing assay overlap.',
                           'residual_difference': 'Proposed context.'}],
        'coverage_limitations': ['The revised graph has not been researched.']}
    literature = {'candidate_findings': [finding], 'citation_checks': [],
                  'search_status': 'not_performed', 'searches_performed': [],
                  'limitations': ['Offline test only.']}
    coverage = {'scope': 'Target coverage, not scientific correctness', 'candidates': [
        {'candidate_id': idea.id, 'candidate_sha256': '0'*64,
         'checked_targets': ['operation:prepare'], 'unchecked_graph_targets': ['state:rna']}]}
    (tmp_path/'literature_report.json').write_text(json.dumps(literature))
    (tmp_path/'literature_coverage.json').write_text(json.dumps(coverage))
    export_report(tmp_path, {'request_file': str(request), 'atlas_sha256': 'f'*64}, [str(candidate)], panel)
    page = (tmp_path/'index.html').read_text()
    decoded = json.loads(page.split('const D=', 1)[1].split(';const NS=', 1)[0])
    assert decoded['literature'] == literature
    assert decoded['literature_coverage'] == coverage
    assert decoded['candidate_sha256'][idea.id] == digest(idea)
    assert decoded['candidate_sha256'][idea.id] != finding['candidate_sha256']
    assert 'These findings address an earlier candidate' in page
    assert "a.href='literature_report.json'" in page
    assert "b.href='literature_coverage.json'" in page
    assert page.count('</script>') == 1


def test_current_research_findings_survive_export_with_exact_review_binding(tmp_path):
    idea = parse_candidate(research_candidate_data())
    candidate = tmp_path/'candidate.json'; candidate.write_text(idea.model_dump_json())
    request = tmp_path/'request.json'; request.write_text('{"prompt":"Software fixture"}')
    panel = {'reviews': [review_data(idea)], 'comparison_summary': 'No scientific result in this fixture.'}
    export_report(tmp_path, {'request_file': str(request), 'atlas_sha256': 'f'*64}, [str(candidate)], panel)
    graph = json.loads((tmp_path/'proposal_graph.json').read_text())
    exported = parse_candidate(graph['candidates'][0])
    assert digest(exported) == digest(idea)
    node, = [n for n in graph['nodes'] if n['node_type'] == 'workflow']
    assert node['research'] == idea.research.model_dump(mode='json')
    text = (tmp_path/'report.txt').read_text()
    assert idea.research.interfaces.question in text
    assert idea.research.interfaces.remaining_uncertainty in text
    assert 'Affected targets: operation:prepare, change:change1' in text
    page = (tmp_path/'index.html').read_text()
    decoded = json.loads(page.split('const D=', 1)[1].split(';const NS=', 1)[0])
    assert decoded['candidates'][0]['research'] == idea.research.model_dump(mode='json')
