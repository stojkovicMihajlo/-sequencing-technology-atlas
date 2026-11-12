"""Retrieval misses and context bounds must be explicit to the model."""
import json

import pytest

from genomics_discovery.illumina.extraction import main, source_excerpt, registered_text_groups


def test_query_miss_never_returns_unrelated_intro():
    sources = {'p': {'kind': 'text', 'text': 'An unrelated introductory paragraph.'}}
    result = source_excerpt(sources, 'p', query='missing reaction')
    assert result['found'] is False and result['text'] == '' and result['start'] is None
    assert result['match_count'] == 0


def test_literal_retrieval_retains_original_offsets_and_bounded_context():
    text = 'a' * 900 + 'Reverse transcription' + 'b' * 900 + 'reverse transcription'
    result = source_excerpt({'p': {'text': text}}, 'p', query='reverse transcription', limit=1000)
    assert result['match_starts'] == [900, 1821]
    assert result['text'] == text[result['start']:result['start'] + 1000]
    assert result['match_count'] == 2
    with pytest.raises(ValueError, match='nonnegative'):
        source_excerpt({'p': {'text': text}}, 'p', start=-1)


def test_search_all_registered_sources_is_bounded_and_keeps_source_ids(tmp_path, capsys):
    path = tmp_path / 'registry.json'
    path.write_text(json.dumps({f'paper:{i}': {'kind': 'text', 'text': 'RNA capture ' * 1000}
                               for i in range(12)}))
    assert main(['source', '--registry', str(path), '--query', 'capture', '--limit', '800']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['found'] is True and result['source_match_count'] == 12
    assert result['truncated'] is True and len(result['results']) == 8
    assert sum(len(r['text']) for r in result['results']) <= 800
    assert all(r['source_id'].startswith('paper:') for r in result['results'])


def test_identical_paper_text_is_returned_once_without_erasing_associations(tmp_path, capsys):
    base = {'kind': 'text', 'pmid': '123', 'title': 'An assay',
        'content_level': 'fulltext', 'text': 'RNA capture is described.'}
    sources = {'poster:paper': {**base, 'association_status': 'candidate_for_poster'},
        'primary:paper': {**base, 'association_status': 'primary_candidate'}}
    path = tmp_path/'registry.json'
    path.write_text(json.dumps(sources))
    main(['source', '--registry', str(path), '--query', 'capture'])
    result = json.loads(capsys.readouterr().out)
    assert result['source_match_count'] == 2
    assert result['distinct_text_rendition_match_count'] == 1
    assert result['results'][0]['registrations'] == [
        {'source_id': 'poster:paper', 'association_status': 'candidate_for_poster'},
        {'source_id': 'primary:paper', 'association_status': 'primary_candidate'}]
    assert not result['truncated']
    assert registered_text_groups(sources) == [['poster:paper', 'primary:paper']]


@pytest.mark.parametrize('changed', [
    {'pmid': '456'}, {'title': 'Different publication'}, {'content_level': 'abstract'},
    {'text': 'RNA capture is described differently.'}])
def test_distinct_evidence_is_never_collapsed_due_to_a_matching_title_or_text(changed):
    base = {'kind': 'text', 'pmid': '123', 'title': 'An assay',
        'content_level': 'fulltext', 'text': 'RNA capture is described.'}
    assert registered_text_groups({'a': base, 'b': {**base, **changed}}) == [['a'], ['b']]


def test_list_doi_registration_is_hashable_without_rewriting_its_metadata():
    # The EpiRADSeq source bundle used this list-valued DOI and failed before
    # writing the author prompt. This retrieval grouping must accept JSON values.
    base = {'kind': 'text', 'pmid': None, 'doi': ['10.1111/2041-210X.12435'],
        'title': 'EpiRADseq: scalable analysis of genomewide patterns of methylation using next-generation sequencing',
        'url': 'http://onlinelibrary.wiley.com/doi/10.1111/2041-210X.12435/abstract',
        'content_level': 'fulltext', 'text': 'Registered protocol text.'}
    registry = {'paper': base, 'duplicate': {**base, 'association_status': 'another depiction'}}
    original = json.dumps(registry, sort_keys=True)
    assert registered_text_groups(registry) == [['paper', 'duplicate']]
    assert json.dumps(registry, sort_keys=True) == original


@pytest.mark.parametrize('changed', [
    {'doi': '10.1111/2041-210X.12435'},
    {'doi': ['10.1111/2041-210X.99999']},
    {'doi': ['10.1111/2041-210X.12435', '10.1111/2041-210X.99999']},
    {'title': ['An assay']},
    {'content_level': 'abstract'},
    {'text': 'Different registered protocol text.'},
])
def test_structured_metadata_does_not_conflate_different_registration_values(changed):
    base = {'kind': 'text', 'doi': ['10.1111/2041-210X.12435'],
        'title': 'An assay', 'content_level': 'fulltext', 'text': 'Registered protocol text.'}
    assert registered_text_groups({'a': base, 'b': {**base, **changed}}) == [['a'], ['b']]
