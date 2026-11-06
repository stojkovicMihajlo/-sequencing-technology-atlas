"""Version identity and source availability, without publisher/network calls."""
import json
from pathlib import Path

import pytest

from genomics_discovery.illumina.extraction import read, sha, write, verify_registry_files
from experiments.paper_fewshot_20261106 import sources


@pytest.fixture
def fake_publisher(tmp_path, monkeypatch):
    target = {'pmid': '123', 'paper_title': 'Assigned published assay', 'doi': '10.1234/final',
        'article_url': 'https://www.nature.com/articles/assigned',
        'pdf_url': 'https://www.nature.com/articles/assigned.pdf', 'version': 'final'}
    document = {'text': 'Assigned published assay 10.1234/final\n' + 'Methods and source facts. ' * 80,
        'html': '<a href="https://static-content.springer-cdn.com/MediaObjects/assigned_SI.pdf?download=1">Supplementary information</a>'}
    target_path = tmp_path / 'target.json'; write(target_path, target)
    calls = []

    def fetch(url, path):
        path.write_bytes(b'%PDF-1.7 synthetic source' if '.pdf' in url else document['html'].encode())
        return {'request_url': url, 'path': str(path), 'sha256': sha(path)}

    def convert(argv, **kwargs):
        calls.append(argv)
        if argv[0] == 'pdftotext':
            Path(argv[-1]).write_text(document['text'])

    monkeypatch.setattr(sources, 'fetch', fetch)
    monkeypatch.setattr(sources.subprocess, 'run', convert)
    return target_path, document, calls


def test_final_version_and_supplements_remain_separate_and_pinned(fake_publisher, tmp_path):
    target, _, calls = fake_publisher
    registry = sources.prepare(target, tmp_path / 'inputs')
    assert registry['paper:123']['document_role'] == 'primary_article'
    assert registry['supplement:1']['document_role'] == 'supplement'
    assert read(tmp_path / 'inputs/ACQUISITION.json')['model_image_inputs'] == []
    assert not any(s['kind'] == 'image' for s in registry.values())
    verify_registry_files(registry)
    Path(registry['paper:123']['original']['path']).write_bytes(b'changed PDF')
    with pytest.raises(ValueError, match='changed'):
        verify_registry_files(registry)


@pytest.mark.parametrize('wrong', ['Wrong title 10.1234/final', 'Assigned published assay 10.1234/preprint'])
def test_wrong_version_is_not_silently_substituted(fake_publisher, tmp_path, wrong):
    target, document, _ = fake_publisher
    document['text'] = wrong + '\n' + 'Substantial document. ' * 80
    with pytest.raises(ValueError, match='title and DOI'):
        sources.prepare(target, tmp_path / 'inputs')


def test_missing_supplement_inventory_is_visible(fake_publisher, tmp_path):
    target, document, _ = fake_publisher; document['html'] = '<html>No linked PDF</html>'
    registry = sources.prepare(target, tmp_path / 'inputs')
    receipt = read(tmp_path / 'inputs/ACQUISITION.json')
    assert set(registry) == {'paper:123'}
    assert 'No PDF supplement links' in receipt['unavailable'][0]['reason']
