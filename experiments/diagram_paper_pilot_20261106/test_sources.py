import json
from pathlib import Path

import pytest

from experiments.diagram_paper_pilot_20261106 import run


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def source(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    for name, text in [('diagram.png', 'image-fixture'), ('article.xml', 'original article'),
                       ('reading.txt', 'Reported transformation in a full article.')]:
        (vault / name).write_text(text)
    def file(name, kind):
        return {'path': name, 'sha256': run.sha(vault / name), 'kind': kind}
    method_dir = tmp_path / 'library/methods/Transcriptome/RNA Transcription/a'
    catalogue = method_dir / 'catalogue/shared-a-b'
    prose = 'What A and B measure.\n\nSecond paragraph explains how the workflow works.'
    put(catalogue / 'source_registry.json', {'card:shared-a-b': {
        'kind': 'text', 'content_level': 'catalogue', 'text': prose},
        'paper:999': {'kind': 'text', 'content_level': 'fulltext', 'text': 'Unassigned background'}})
    put(catalogue / 'source.json', {'card': {'source_title': 'A/B'}})
    put(catalogue / 'original_cms.json', {'text': prose})
    put(method_dir / 'method.json', {'id': 'a', 'entry_type': 'named_method',
        'identity_decisions': [{'action': 'retain'}]})
    package = {'method_id': 'a', 'name': 'A', 'method_papers': [{
        'paper_id': 'pmid-123', 'availability': 'fulltext_acquired', 'title': 'Method A',
        'association_basis': 'Existing curated assignment', 'url': 'https://example.org/a',
        'files': [file('article.xml', 'article'), file('reading.txt', 'reading-text')],
        'held_files': []}], 'source_depictions': [{'kind': 'website_card',
            'source_id': 'shared-a-b', 'shared_with_method_ids': ['b'],
            'diagrams': [{**file('diagram.png', 'image'), 'source_id': 'image:shared'}]}]}
    path = method_dir / 'source_package.json'
    put(path, package)
    return path, vault, package, {'method_id': 'a', 'primary_paper_id': 'pmid-123'}


def test_shared_depiction_preserves_one_method_own_paper_and_complete_prose(source):
    path, vault, _, selected = source
    card, registry, images = run.prepare_method(path, vault, selected)
    assert card['record_id'] == 'a' and card['verified_method_names'] == ['A']
    assert card['source_bindings'][0]['shared_with_method_ids'] == ['b']
    assert 'Second paragraph' in registry['card:shared-a-b']['text']
    assert 'paper:123' in registry and 'paper:999' not in registry
    assert images == [('image:shared', vault / 'diagram.png')]
    assert registry['paper:123']['content_level'] == 'fulltext'
    run.verify_registry_files(registry)


@pytest.mark.parametrize('defect', ['article_missing', 'text_changed', 'metadata_only', 'held', 'rejected_image'])
def test_bad_primary_or_image_stops_before_native_execution(source, defect):
    path, vault, package, selected = source
    if defect == 'article_missing':
        package['method_papers'][0]['files'] = [package['method_papers'][0]['files'][1]]
    elif defect == 'text_changed':
        (vault / 'reading.txt').write_text('Changed bytes')
    elif defect == 'metadata_only':
        package['method_papers'][0]['availability'] = 'metadata_only'
    elif defect == 'held':
        package['method_papers'][0]['held_files'] = [{'reason': 'Identity unresolved'}]
    else:
        package['source_depictions'][0]['diagrams'][0]['quality'] = 'rejected_as_complete_workflow_view'
    put(path, package)
    with pytest.raises(ValueError):
        run.prepare_method(path, vault, selected)


@pytest.mark.parametrize('key', ['method_id', 'primary_paper_id'])
def test_duplicate_dispatch_rejected(key):
    rows = [{'method_id': 'a', 'primary_paper_id': 'pmid-1'},
            {'method_id': 'b', 'primary_paper_id': 'pmid-2'}]
    rows[1][key] = rows[0][key]
    with pytest.raises(ValueError, match='distinct'):
        run.validate_selection({'methods': rows, 'reason_for_fresh_extraction': 'New pilot'})


def test_curated_content_addressed_links_keep_exact_original_hash(source):
    path, vault, package, selected = source
    original = vault / 'article.xml'
    storage = vault.parent / 'older-vault'
    storage.mkdir()
    moved = storage / 'article.xml'
    original.rename(moved)
    original.symlink_to(moved)
    _, registry, _ = run.prepare_method(path, vault, selected)
    assert str(moved) in {p['path'] for p in registry['paper:123']['custody_files']}
    moved.write_text('Changed underlying original')
    with pytest.raises(ValueError, match='changed'):
        run.verify_registry_files(registry)


def test_selection_is_exactly_ten_distinct_curated_methods():
    selection = run.read(Path(__file__).with_name('selected.json'))
    assert len(run.validate_selection(selection)) == 10
