"""Retrieval misses and context bounds must be explicit to the model."""
import json

import pytest

from genomics_discovery.illumina.extraction import main, source_excerpt


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
