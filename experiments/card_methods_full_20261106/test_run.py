"""Software controls for full-card dispatch, source isolation and completed holds."""
import copy
import csv
import json

import pytest

from experiments.card_methods_full_20261106 import run
from experiments.diagram_paper_pilot_20261106.test_sources import source


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_scope_deduplicates_methods_not_shared_cards_or_papers(tmp_path):
    scope = tmp_path / 'scope.csv'
    fields = ['card_id', 'method_id', 'method', 'entry_type', 'directory']
    rows = [dict(card_id=card, method_id=mid, method=mid.upper(), entry_type=kind,
                 directory='methods/RNA/category/' + mid) for card, mid, kind in
            [('shared', 'a', 'named_method'), ('shared', 'b', 'named_method'),
             ('duplicate', 'a', 'named_method'), ('resource', 'kit', 'commercial_kit')]]
    with scope.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    library = tmp_path / 'library'
    for mid in ('a', 'b'):
        put(library / ('methods/RNA/category/' + mid) / 'source_package.json', {
            'method_id': mid, 'method_papers': [
                {'paper_id': 'pmid-1', 'availability': 'metadata_only'},
                {'paper_id': 'pmid-2', 'availability': 'fulltext_acquired'}]})
    prior = tmp_path / 'pilot.json'; put(prior, {'methods': []})
    selection = run.build_selection(library, scope, prior)
    assert [r['method_id'] for r in selection['methods']] == ['a', 'b']
    assert all(r['primary_paper_id'] == 'pmid-2' for r in selection['methods'])
    assert selection['methods'][0]['card_ids'] == ['duplicate', 'shared']
    assert selection['source_card_count'] == 3
    put(prior, {'methods': [{'method_id': 'a', 'primary_paper_id': 'pmid-1'}]})
    assert run.build_selection(library, scope, prior)['methods'][0]['primary_paper_id'] == 'pmid-1'


def test_missing_primary_is_local_and_changed_source_is_not_called_missing(source):
    path, vault, package, selected = source
    bad = copy.deepcopy(package); bad['method_id'] = 'b'
    bad['method_papers'][0]['availability'] = 'metadata_only'
    bad_path = path.parent.parent / 'b/source_package.json'
    put(bad_path, bad)
    put(bad_path.parent / 'method.json', {'id': 'b', 'entry_type': 'named_method',
                                        'identity_decisions': [{'action': 'retain'}]})
    selection = {'methods': [selected, {'method_id': 'b', 'primary_paper_id': 'pmid-123'}],
                 'reason_for_fresh_extraction': 'Synthetic source-isolation check'}
    library = path.parents[4]
    bundles, held, cards = run.engine.prepare_independently(library, vault, selection)
    assert [c['record_id'] for c, _, _ in bundles] == ['a']
    assert held['b']['status'] == 'source_recovery_needed' and len(cards) == 2
    (vault / 'reading.txt').write_text('Changed source bytes')
    bundles, held, _ = run.engine.prepare_independently(library, vault, selection)
    assert not bundles and held['a']['status'] == 'failed'
    assert 'source bytes differ' in held['a']['reason'].lower()


def test_classification_links_do_not_create_duplicate_dispatch(source):
    path, vault, _, selected = source
    library = path.parents[4]
    alias = library / 'methods/Other/category/a'
    alias.parent.mkdir(parents=True)
    alias.symlink_to(path.parent, target_is_directory=True)
    selection = {'methods': [selected], 'reason_for_fresh_extraction': 'Classification-link check'}
    bundles, held, _ = run.engine.prepare_independently(library, vault, selection)
    assert len(bundles) == 1 and not held
    selected['canonical_directory'] = str(path.parent.relative_to(library))
    bundles, held, _ = run.engine.prepare_independently(library, vault, selection)
    assert len(bundles) == 1 and not held


def test_prior_index_preserves_original_attempt_paths_and_latest_outcomes(tmp_path):
    one, two = tmp_path / 'one', tmp_path / 'two'
    put(one / 'state.json', {'cards': {'a': {'status': 'supported', 'attempt_directory': '/old/a'},
                                     'b': {'status': 'interrupted'}}})
    put(two / 'state.json', {'cards': {'b': {'status': 'research_needed', 'attempt_directory': '/new/b'}}})
    old = (one / 'state.json').read_bytes()
    run.combine_prior_runs([one, two], tmp_path / 'index')
    state = run.engine.read(tmp_path / 'index/state.json')
    assert state['cards']['a']['attempt_directory'] == '/old/a'
    assert state['cards']['b']['status'] == 'research_needed'
    assert (one / 'state.json').read_bytes() == old
    assert len(state['source_states']) == 2


@pytest.mark.parametrize('complete,errors,expected', [(True, False, 0), (True, True, 2), (False, False, 2)])
def test_exit_status_distinguishes_execution_from_scientific_acceptance(complete, errors, expected):
    assert run.engine.completion_exit_code({'execution_complete': complete,
        'has_execution_errors': errors, 'all_supported': False}) == expected
