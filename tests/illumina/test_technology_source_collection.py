import json
from pathlib import Path

import pytest

from scripts.illumina.technology_sources.inventory import catalogue_group, key, slug
from scripts.illumina.technology_sources.materialize import Store, link, materialize, publish
from scripts.illumina.technology_sources.render import render


def test_names_do_not_erase_scientifically_relevant_punctuation():
    assert key('3′-Seq') == key("3'-seq")
    assert key('CapSeq') != key('CAP-seq')
    assert slug('CapSeq') != slug('CAP-seq')
    assert slug('3′-Seq') != slug('3Seq')
    assert slug('scM&T-seq') != slug('scMT-seq')


def test_catalogue_group_preserves_multiple_placements_without_inventing_domains():
    def method(*paths):
        return {'classifications': [{'path': p} for p in paths]}
    assert catalogue_group(method(['Transcriptome', 'RNA Low-Level Detection'],
                                  ['Genome', 'DNA Low-Level Detection'])) == [
                                      'Combined catalogue categories', 'Genome + Transcriptome']
    assert catalogue_group(method(['Transcriptome', 'RNA Low-Level Detection'],
                                  ['rna_poster', 'RNA Low-Level Detection'],
                                  ['single_cell_poster', 'RNA Low-Level Detection'])) == ['Transcriptome']
    assert catalogue_group(method(['dna_poster', 'Epigenetics'])) == ['Category review pending']
    assert catalogue_group(method(['single_cell_poster', 'Integrated Techniques'])) == ['Category review pending']


def test_store_rejects_corrupt_source_and_deduplicates_bytes(tmp_path):
    import hashlib
    s = Store(tmp_path / 'out')
    source = tmp_path / 'article.txt'; source.write_text('source text')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='mismatch'):
        s.file(source, 'a' * 64, {})
    a = s.file(source, digest, {'a': 1})
    b = s.blob(source.read_bytes(), '.txt', {'b': 2})
    assert a == b and len(s.files) == 1
    assert len(next(iter(s.files.values()))['origins']) == 2


def test_relative_links_remain_portable_and_conflicts_fail(tmp_path):
    target = tmp_path / 'shared/a.txt'; target.parent.mkdir(); target.write_text('a')
    dest = tmp_path / 'methods/a.txt'
    link(target, dest); link(target, dest)
    assert not dest.readlink().is_absolute()
    assert dest.read_text() == 'a'
    other = tmp_path / 'shared/b.txt'; other.write_text('b')
    with pytest.raises(ValueError, match='Conflicting'):
        link(other, dest)


def fixture(tmp_path):
    import hashlib
    image = tmp_path / 'image.png'; image.write_bytes(b'original image bytes')
    registry = {'card:combined': {'kind': 'text', 'content_level': 'catalogue', 'text': 'Shared card <literal>', 'title': 'A / B'},
        'diagram:one': {'kind': 'image', 'path': str(image), 'sha256': hashlib.sha256(image.read_bytes()).hexdigest(), 'url': 'https://example.org/image.png'},
        'paper:1': {'kind': 'text', 'content_level': 'abstract', 'pmid': '1', 'title': 'Listed paper'},
        'paper:2': {'kind': 'text', 'content_level': 'abstract', 'pmid': '2', 'title': 'Candidate paper'}}
    rp = tmp_path / 'registry.json'; rp.write_text(json.dumps(registry))
    methods = {n: {'id': n, 'name': n.upper(), 'kind': 'technology', 'source_records': ['combined'],
        'classifications': [{'path': ['Transcriptome', 'RNA Transcription']}]} for n in ('a', 'b')}
    inventory = {'methods': methods, 'occurrences': {'combined': {'record_id': 'combined', 'title': 'A / B', 'members': ['a', 'b'], 'batch': 'catalogue',
        'registry': str(rp), 'shared_source': True, 'card': {'references': [{'pmid': '1', 'role': 'references'}]}}}, 'counts': {'source_occurrences': 1}}
    return inventory


def test_shared_depiction_has_separate_method_folders_not_duplicate_files(tmp_path):
    state = materialize(fixture(tmp_path), tmp_path / 'out', [])
    publish(tmp_path / 'out', state)
    render(tmp_path / 'out', state)
    a, b = [tmp_path / 'out/technologies' / n for n in ('a', 'b')]
    assert (a / 'diagrams/diagram-one.png').resolve() == (b / 'diagrams/diagram-one.png').resolve()
    assert (a / 'papers/pmid-1/paper.json').is_file()
    assert (a / 'paper_candidates/pmid-2/paper.json').is_file()
    assert not (a / 'papers/pmid-2').exists()
    assert 'Shared card &lt;literal&gt;' in (a / 'index.html').read_text()
    assert (tmp_path / 'out/classification/transcriptome/rna-transcription/a').resolve() == a
    assert (tmp_path / 'out/groups/Transcriptome/a').resolve() == a
    assert not state['errors']


def test_missing_registry_is_recorded_without_losing_technology(tmp_path):
    inv = fixture(tmp_path)
    Path(inv['occurrences']['combined']['registry']).unlink()
    state = materialize(inv, tmp_path / 'out', [])
    publish(tmp_path / 'out', state)
    result = render(tmp_path / 'out', state)
    assert len(state['errors']) == 1
    assert result['technology_directories_without_diagrams'] == 2
    assert (tmp_path / 'out/technologies/a/method.json').is_file()


def test_materialize_does_not_overwrite_existing_collection(tmp_path):
    inv = fixture(tmp_path)
    materialize(inv, tmp_path / 'out', [])
    with pytest.raises(FileExistsError):
        materialize(inv, tmp_path / 'out', [])
