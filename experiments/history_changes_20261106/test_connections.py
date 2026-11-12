"""Connection scheduling uses accepted citation claims, never all method pairs."""
import copy
import hashlib

from experiments.history_changes_20261106.connect import binding_candidates
from genomics_discovery.illumina.extraction import write


def inputs(tmp_path):
    text = 'Earlier full-paper experimental contribution.'
    paper = {'pmid': '123', 'pair_eligible': True, 'article_sha256': 'a' * 64,
             'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'source_id': 'paper'}
    earlier = {'record_id': 'earlier', 'batch': 'accepted', 'papers': [paper], 'extraction_sha256': 'b' * 64}
    later = {'record_id': 'later', 'batch': 'accepted', 'papers': [], 'extraction_sha256': 'c' * 64,
             'selected_primary': {'source_id': 'later-paper'}}
    run = tmp_path / 'citation'; run.mkdir()
    write(run / 'state.json', {'status': 'completed'})
    write(run / 'graph.json', {'links': [{'claim': {'id': 'accepted', 'relation': 'documented_reuse',
        'claim_scope': 'seed_workflow', 'reference_id': 'R1', 'catalogue_candidates': ['earlier']}}],
        'withheld': [{'claim': {'id': 'not-published', 'relation': 'documented_reuse'}}]})
    write(run / 'inputs/sources.json', {'R1': {'pmid': '123', 'text': text, 'original': {'sha256': 'a' * 64}}})
    return {'methods': {'earlier': earlier, 'later': later}}, {'later': str(run)}


def test_only_published_historical_contribution_schedules_one_exact_pair(tmp_path):
    index, runs = inputs(tmp_path)
    # Another registration for exactly the same method does not spend another call.
    index['methods']['earlier']['papers'] *= 2
    result = binding_candidates(index, runs)
    assert len(result['plans']) == 1
    plan = result['plans'][0]['plan']
    assert plan['left']['record_id'] == 'earlier' and plan['right']['record_id'] == 'later'
    assert plan['citation']['claim_id'] == 'accepted'
    assert not result['unbound_contributions']


def test_changed_article_or_text_is_not_an_earlier_implementation_match(tmp_path):
    index, runs = inputs(tmp_path)
    for key in ('article_sha256', 'text_sha256'):
        changed = copy.deepcopy(index)
        changed['methods']['earlier']['papers'][0][key] = '0' * 64
        result = binding_candidates(changed, runs)
        assert not result['plans']
        row = result['unbound_contributions'][0]
        assert row['reason'] == 'registered_source_version_mismatch'
        assert row['publication_candidates'][0]['record_id'] == 'earlier'


def test_shared_publication_does_not_choose_between_distinct_methods(tmp_path):
    index, runs = inputs(tmp_path)
    other = copy.deepcopy(index['methods']['earlier']); other['record_id'] = 'other'
    index['methods']['other'] = other
    from genomics_discovery.illumina.extraction import read
    path = tmp_path / 'citation/graph.json'; graph = read(path)
    graph['links'][0]['claim']['catalogue_candidates'] = []
    write(path, graph)
    result = binding_candidates(index, runs)
    assert not result['plans']
    assert result['unbound_contributions'][0]['candidate_methods'] == ['earlier', 'other']


def test_precedent_comparison_and_unfinished_citations_do_not_schedule_ancestry(tmp_path):
    from genomics_discovery.illumina.extraction import read
    index, runs = inputs(tmp_path)
    path = tmp_path / 'citation/graph.json'; graph = read(path)
    for relation in ('comparison', 'mechanistic_precedent'):
        graph['links'][0]['claim']['relation'] = relation; write(path, graph)
        assert not binding_candidates(index, runs)['plans']
    write(tmp_path / 'citation/state.json', {'status': 'failed'})
    assert not binding_candidates(index, runs)['plans']


def test_incorrect_name_hint_cannot_hide_exact_publication_candidate(tmp_path):
    from genomics_discovery.illumina.extraction import read
    index, runs = inputs(tmp_path)
    path = tmp_path / 'citation/graph.json'; graph = read(path)
    graph['links'][0]['claim']['catalogue_candidates'] = ['Earlier assay display name']
    write(path, graph)
    result = binding_candidates(index, runs)
    assert len(result['plans']) == 1
    assert result['plans'][0]['plan']['left']['record_id'] == 'earlier'
    assert result['plans'][0]['name_hints_used_as_identity_filter'] is False


def test_name_hint_cannot_resolve_shared_publication_ambiguity(tmp_path):
    index, runs = inputs(tmp_path)
    other = copy.deepcopy(index['methods']['earlier']); other['record_id'] = 'other'
    index['methods']['other'] = other
    # The author suggests only earlier, but both exact-source implementations
    # still require scientific disambiguation before a mapping is scheduled.
    result = binding_candidates(index, runs)
    assert not result['plans']
    assert result['unbound_contributions'][0]['candidate_methods'] == ['earlier', 'other']


def test_missing_workflow_differs_from_registered_but_ineligible_paper(tmp_path):
    index, runs = inputs(tmp_path)
    index['methods']['earlier']['papers'][0]['pair_eligible'] = False
    result = binding_candidates(index, runs)
    assert result['unbound_contributions'][0]['reason'] == 'cited_publication_not_pair_eligible'
    index['methods'].pop('earlier')
    result = binding_candidates(index, runs)
    assert result['unbound_contributions'][0]['reason'] == 'no_accepted_workflow_for_cited_publication'
