from genomics_discovery.curation.operation_registry.selections import find_selections, FIELDS


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
