"""Source-admission checks for the bounded Task2 prior-cache recovery."""
import importlib.util
import json
from pathlib import Path

import pytest

from scripts.illumina.technology_sources.inventory import sha


FILE = (Path(__file__).resolve().parents[2] / 'provenance/experiments/'
        'technology_sources_20261105/release_01/task2_04/recover_sources.py')
SPEC = importlib.util.spec_from_file_location('task2_source_recovery', FILE)
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


@pytest.mark.parametrize('case', ['correct_article', 'different_title', 'modified_bytes'])
def test_prior_cache_requires_original_bytes_and_own_article_identity(tmp_path, case):
    cache, root = tmp_path / 'cache', tmp_path / 'new_sources'
    cache.mkdir(); root.mkdir()
    article = cache / 'article.xml'
    title = 'Wrong article' if case == 'different_title' else 'Assay introduction'
    article.write_text('<article><front><article-meta><article-id pub-id-type="pmid">42</article-id>'
        '<article-id pub-id-type="doi">10.1/assay</article-id><title-group><article-title>'
        + title + '</article-title></title-group></article-meta></front><body><sec><title>Methods</title><p>'
        + 'Original procedure body. ' * 110 + '</p></sec></body></article>')
    manifest = cache / 'sources.json'
    manifest.write_text(json.dumps({'fulltexts': {'42': {'status': 'fulltext_acquired',
        'path': 'article.xml', 'sha256': sha(article)}}}))
    if case == 'modified_bytes':
        article.write_text(article.read_text() + '\n')
    state = {'papers': {'pmid-42': {'id': 'pmid-42', 'pmid': '42', 'status': 'metadata_only',
        'files': [], 'metadata': {'title': 'Assay introduction', 'identifiers': {'doi': ['10.1/assay']}}}},
        'files': {}}
    RECOVERY.cached_articles(root, state, [manifest], {'pmid-42'})
    p = state['papers']['pmid-42']
    receipts = json.loads((root / 'CACHE_REUSE_RECEIPTS.json').read_text())
    if case == 'correct_article':
        assert p['status'] == 'fulltext_acquired'
        assert {f['kind'] for f in p['files']} == {'article', 'reading-text', 'blocks'}
        for f in p['files']:
            assert sha(root / f['path']) == f['sha256']
        assert receipts[0]['identity']['scientific_content_reviewed'] is False
    else:
        assert p['status'] == 'metadata_only'
        assert not p['files']
        assert receipts[0]['error']
        assert receipts[0]['reused'] is False
