"""Tiny offline checks of recovery registration, never native extraction."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo = Path(os.environ.get('ATLAS_REPOSITORY', Path(__file__).resolve().parents[3]))
    monkeypatch.setenv('ATLAS_REPOSITORY', str(repo))
    monkeypatch.setenv('SLURM_JOB_ID', 'offline-registration-test')
    spec = importlib.util.spec_from_file_location('register_recovery', Path(__file__).with_name('register_research_recovery.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    parent = tmp_path / 'parent'
    parent.mkdir()
    image = parent / 'diagram.png'
    image.write_bytes(b'fixture-bytes-for-source-custody-only')
    literal = {'reference_id': 'm:references:0', 'role': 'references', 'pmid': None,
               'source_reference': {'text': 'Literal source citation'}}
    card = {'record_id': 'm', 'source_id': 'card:m', 'source_title': 'M',
            'diagram_asset_ids': ['a'], 'references': [literal]}
    mod.write(parent / 'inventory.json', {'cards': [card]})
    mod.write(parent / 'sources.json', {'inventory_sha256': mod.sha(parent / 'inventory.json'),
        'assets': [{'asset_id': 'a', 'source_id': 'diagram:a', 'path': str(image), 'sha256': mod.sha(image),
                    'url': 'https://example.org/diagram.png', 'status': 'downloaded'}],
        'publications': {}, 'fulltexts': {}, 'statistics': {}})
    xml = parent / 'pubmed.xml'
    xml.write_text('<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12</PMID>'
                   '<Article><ArticleTitle>Primary source</ArticleTitle></Article></MedlineCitation>'
                   '</PubmedArticle></PubmedArticleSet>')
    overlay = {'additional_publications': {'12': {'pmid': '12', 'title': 'Primary source',
        'identity_status': 'pubmed_record_retrieved', 'metadata_source': str(xml),
        'metadata_source_sha256': mod.sha(xml), 'abstract': []}}, 'additional_fulltexts': {},
        'additional_external_sources': {}, 'card_scope_overlays': [{'card_id': 'm',
            'primary_method_sources': [{'paper': {'pmid': '12'}}]}]}
    recovery = parent / 'recovery.json'
    mod.write(recovery, overlay)
    return mod, parent, recovery, overlay, literal


def test_primary_lead_is_registered_without_rewriting_literal_reference(setup, tmp_path):
    mod, parent, recovery, _, literal = setup
    mod.register(parent / 'sources.json', recovery, tmp_path / 'registered', [])
    result = mod.read(tmp_path / 'registered/inventory.json')['cards'][0]
    assert result['references'] == [literal]
    assert result['candidate_publications'][0]['pmid'] == '12'
    assert result['candidate_publications'][0]['association_status'] == 'unverified_candidate'
    index = mod.read(tmp_path / 'registered/inventory.json')['publications']
    assert any(p['pmid'] == '12' and 'm' in p['card_ids'] for p in index)
    assert mod.read(parent / 'inventory.json')['cards'][0]['references'] == [literal]


def test_declared_pmid_must_occur_in_its_actual_metadata_source(setup, tmp_path):
    mod, parent, recovery, overlay, _ = setup
    xml = parent / 'pubmed.xml'
    xml.write_text(xml.read_text().replace('<PMID>12</PMID>', '<PMID>34</PMID>'))
    overlay['additional_publications']['12']['metadata_source_sha256'] = mod.sha(xml)
    mod.write(recovery, overlay)
    with pytest.raises(ValueError, match='declared record'):
        mod.register(parent / 'sources.json', recovery, tmp_path / 'wrong-identity', [])


def test_changed_document_bytes_cannot_be_imported(setup, tmp_path):
    mod, parent, recovery, overlay, _ = setup
    overlay['additional_publications']['12']['metadata_source_sha256'] = '0' * 64
    mod.write(recovery, overlay)
    with pytest.raises(ValueError, match='custody mismatch'):
        mod.register(parent / 'sources.json', recovery, tmp_path / 'changed-bytes', [])


def test_external_normalized_lead_does_not_rewrite_literal_reference(setup, tmp_path):
    mod, parent, recovery, overlay, literal = setup
    text = parent / 'abstract.txt'
    text.write_text('Preprint title\n\nOriginal abstract')
    overlay['additional_external_sources']['preprint'] = {
        'doi': '10.1101/012345', 'title': 'Preprint title',
        'identity_status': 'primary_preprint_api_record_retrieved',
        'content_level': 'abstract', 'text_path': str(text), 'text_sha256': mod.sha(text)}
    inventory = mod.read(parent / 'inventory.json')
    inventory['cards'][0]['source_row_id'] = 'row:1'
    mod.write(parent / 'inventory.json', inventory)
    sources = mod.read(parent / 'sources.json')
    sources['inventory_sha256'] = mod.sha(parent / 'inventory.json')
    mod.write(parent / 'sources.json', sources)
    overlay['citation_resolutions'] = [{'citation_id': 'c1', 'occurrences': [{'row_id': 'row:1'}],
        'candidate_papers': [{'doi': '10.1101/012345'}]}]
    mod.write(recovery, overlay)
    output = tmp_path / 'external'
    mod.register(parent / 'sources.json', recovery, output, [])
    card = mod.read(output / 'inventory.json')['cards'][0]
    assert card['references'][0] == literal
    assert card['references'][1]['role'] == 'candidate_publications'
    external = mod.read(output / 'sources.json')['external_publications']
    assert len(external) == 1
    assert next(iter(external.values()))['content_level'] == 'abstract'


def test_external_provider_payload_requires_upstream_normalization(setup, tmp_path):
    mod, parent, recovery, overlay, _ = setup
    overlay['additional_external_sources']['preprint'] = {
        'primary_api_record': {'doi': '10.1101/012345', 'title': 'Preprint title'}}
    mod.write(recovery, overlay)
    with pytest.raises(ValueError, match='normalized identity'):
        mod.register(parent / 'sources.json', recovery, tmp_path / 'external-unbound', [])


@pytest.mark.parametrize('version,key', [('jats_reading_blocks/1.0','article_sha256'),
                                        ('cached_reading_blocks/1.0','original_sha256')])
def test_declared_reading_block_formats_bind_the_same_own_article(setup, tmp_path, version, key):
    mod, _, _, _, _ = setup
    article = tmp_path / 'article.xml'
    article.write_text('<article><front><article-meta><article-id pub-id-type="pmid">12</article-id>'
                       '</article-meta></front><body><p>' + 'Source body. ' * 30 + '</p></body></article>')
    blocks = tmp_path / 'blocks.json'
    mod.write(blocks, {'format_version':version,'source_id':'paper:12',key:mod.sha(article),'blocks':[]})
    full = {'format':'JATS','path':str(article),'sha256':mod.sha(article),'blocks_path':str(blocks)}
    mod.verify_fulltext_identity('12',full,{'pmid':'12'})
    mod.write(blocks, {'format_version':version,'source_id':'paper:12',key:'0'*64,'blocks':[]})
    with pytest.raises(ValueError, match='do not bind'):
        mod.verify_fulltext_identity('12',full,{'pmid':'12'})
