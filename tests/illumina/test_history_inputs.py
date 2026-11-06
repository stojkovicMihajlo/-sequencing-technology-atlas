"""Custody controls for old and curated source registries used by history work."""
import copy
import json

import pytest

from genomics_discovery.illumina.extraction import sha
from genomics_discovery.illumina.history_inputs import resolve_article


@pytest.fixture
def curated(tmp_path):
    original = tmp_path / 'opaque.xml'
    original.write_text('<article><front><article-meta><article-id pub-id-type="pmid">123</article-id>'
                        '</article-meta></front><body><p>Reported method.</p></body></article>')
    text = tmp_path / 'reading.txt'; text.write_text('Registered reading text')
    package = tmp_path / 'source_package.json'
    package.write_text(json.dumps({'method_papers': [{'paper_id': 'pmid-123', 'files': [
        {'kind': 'article', 'path': 'shared/opaque.xml', 'sha256': sha(original)},
        {'kind': 'reading-text', 'path': 'shared/reading.txt', 'sha256': sha(text)}]}]}))
    source = {'content_level': 'fulltext', 'pmid': '123', 'text': text.read_text(),
              'path': str(text), 'sha256': sha(text), 'custody_files': [
                  {'path': str(p), 'sha256': sha(p)} for p in (original, text, package)]}
    return source, original, package


def test_curated_package_resolves_exact_article_without_mutating_registration(curated):
    source, original, _ = curated
    before = copy.deepcopy(source)
    resolved = resolve_article(source)
    assert resolved['path'] == str(original) and resolved['sha256'] == sha(original)
    assert source == before and 'original' not in source


def test_legacy_original_remains_exactly_bound(curated):
    source, original, _ = curated
    source['original'] = {'path': str(original), 'sha256': sha(original)}
    assert resolve_article(source) == source['original']
    original.write_text('Changed bytes')
    with pytest.raises(ValueError, match='changed|registered hash'):
        resolve_article(source)


@pytest.mark.parametrize('defect', ['wrong_publication', 'wrong_role', 'ambiguous_articles', 'missing_pin'])
def test_unrelated_or_ambiguous_custody_does_not_establish_original(curated, defect):
    source, original, package = curated
    data = json.loads(package.read_text()); paper = data['method_papers'][0]
    if defect == 'wrong_publication':
        paper['paper_id'] = 'pmid-999'
    elif defect == 'wrong_role':
        paper['files'][0]['kind'] = 'metadata'
    elif defect == 'ambiguous_articles':
        paper['files'].append({'kind': 'article', 'path': 'other.xml', 'sha256': '0' * 64})
    else:
        source['custody_files'] = [p for p in source['custody_files'] if p['path'] != str(original)]
    package.write_text(json.dumps(data))
    next(p for p in source['custody_files'] if p['path'] == str(package))['sha256'] = sha(package)
    with pytest.raises(ValueError, match='unambiguous|no pinned'):
        resolve_article(source)


def test_original_front_matter_identity_is_checked(curated):
    source, original, _ = curated
    source['pmid'] = '999'
    source['original'] = {'path': str(original), 'sha256': sha(original)}
    with pytest.raises(ValueError, match='article PMID'):
        resolve_article(source)


def test_supplement_cannot_become_primary_by_sharing_publication_id(curated):
    source, _, _ = curated
    source['document_role'] = 'supplement'
    with pytest.raises(ValueError, match='not metadata or a supplement'):
        resolve_article(source)
