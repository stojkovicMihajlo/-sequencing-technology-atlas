"""Compact navigation must not silently rewrite or lose scientific fields."""
from copy import deepcopy
import json
import sys

import pytest
from test_workflow_transformations import cases as cases
from playground.workflow_unit_review_inputs import review_view
from genomics_discovery.curation.operation_registry.selection_views import (
    inspect_pointer, selection_overview,
)


def test_evidence_locators_restore_exact_record_including_interfaces(cases):
    for occurrence in cases[0].values():
        view = review_view(occurrence)
        before = deepcopy(view)
        overview = selection_overview(view)
        def restore(value):
            if isinstance(value, dict):
                if set(value) == {'pointer', 'count', 'support_types', 'passages'}:
                    evidence = inspect_pointer(view, value['pointer'])
                    assert len(evidence) == value['count']
                    return evidence
                return {key: restore(child) for key, child in value.items()}
            if isinstance(value, list):
                return [restore(child) for child in value]
            return value
        assert restore(overview['record']) == view['record']
        assert overview['occurrence_sha256'] == view['occurrence_sha256']
        assert overview['record']['internal_states']
        assert 'selected_checks' not in overview['record']
        assert view == before


def test_pointer_escaping_and_independent_result():
    data = {'a/b': {'m~n': [1, {'x': 2}]}, '~1': 3, '': 4}
    assert inspect_pointer(data, '/a~1b/m~0n/1/x') == 2
    assert inspect_pointer(data, '/~01') == 3
    assert inspect_pointer(data, '/') == 4
    copy = inspect_pointer(data, '')
    copy['a/b']['m~n'].append(5)
    assert len(data['a/b']['m~n']) == 2


@pytest.mark.parametrize('pointer', ['#/', 'a', '/~2', '/~', '/items/01', '/items/-1',
    '/items/-', '/items/3', '/items/0/x', '/absent', '/items/١'])
def test_invalid_or_nonexistent_pointer_fails(pointer):
    with pytest.raises(ValueError):
        inspect_pointer({'items': [1]}, pointer)


def test_cli_overview_and_pointer_are_wired_to_original_record(cases, tmp_path, monkeypatch, capsys):
    from genomics_discovery.curation.operation_registry.__main__ import main
    occurrence = next(iter(cases[0].values()))
    from genomics_discovery.curation.operation_registry.selections import selection_ref
    view = review_view(occurrence)
    path = tmp_path / 'views.json'
    path.write_text(json.dumps({occurrence.id: view}))
    monkeypatch.setattr(sys, 'argv', ['atlas-concepts', 'selections', str(path), 'overview', selection_ref(occurrence.id)])
    main()
    overview = json.loads(capsys.readouterr().out)
    pointer = overview['record']['operations'][0]['evidence']['pointer']
    monkeypatch.setattr(sys, 'argv', ['atlas-concepts', 'selections', str(path), 'inspect', occurrence.id,
                                     '--pointer', pointer])
    main()
    exact = json.loads(capsys.readouterr().out)
    assert exact['value'] == view['record']['operations'][0]['evidence']
    assert exact['occurrence_sha256'] == view['occurrence_sha256']
