import pytest
from genomics_discovery.curation.operation_registry.selections import (
    find_selections, FIELDS, list_selections, resolve_selection,
)


def view(text):
    record = {f: [] for f in FIELDS}
    record.update(title='Synthetic lookup example', operations=[{'id':'o','name':text,
                  'evidence':[{'quote':'A different paper mentions polymerase'}]}])
    return {'record':record}


def test_explicit_prefix_finds_inflections_without_citation_contamination():
    views = {'a':view('Wash beads'), 'b':view('Transfer bead-bound material')}
    assert find_selections(views, 'bead', field='operations')['total'] == 1
    assert find_selections(views, 'bead*', field='operations')['total'] == 2
    assert find_selections(views, 'polymerase', field='operations')['total'] == 0
    limited = find_selections(views, 'bead*', field='operations', limit=1)
    assert limited['truncated'] and len(limited['results']) == 1


def test_short_handles_preserve_exact_identity_across_order_changes():
    views = {'a:long:identifier': view('Wash beads'), 'b:other:identifier': view('Transfer beads')}
    rows = list_selections(views)
    for row in rows:
        assert resolve_selection(views, row['selection_ref']) == row['occurrence_id']
        assert resolve_selection(dict(reversed(list(views.items()))), row['selection_ref']) == row['occurrence_id']
        assert resolve_selection(views, row['occurrence_id']) == row['occurrence_id']
        assert len(row['selection_ref']) == 16
    with pytest.raises(ValueError, match='Unknown or ambiguous'):
        resolve_selection(views, rows[0]['selection_ref'][:-1])
    with pytest.raises(ValueError, match='Unknown or ambiguous'):
        resolve_selection(views, 'a:long:identifer')


def test_handle_collisions_never_silently_choose_a_record(monkeypatch):
    import genomics_discovery.curation.operation_registry.selections as module
    monkeypatch.setattr(module, 'selection_ref', lambda _: 'sel:collision')
    views = {'a': view('Wash'), 'b': view('Transfer')}
    with pytest.raises(ValueError, match='collision'): list_selections(views)
    with pytest.raises(ValueError, match='ambiguous'): resolve_selection(views, 'sel:collision')
    assert resolve_selection(views, 'a') == 'a'
