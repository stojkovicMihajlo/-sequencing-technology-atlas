import json
import pytest
from pathlib import Path

from genomics_discovery.sources.acquisition.downloads import Download
from genomics_discovery.sources.acquisition.models import Document, Findings
from experiments.card_methods_full_20261112.investigate_holds import groups, strict_schema
from experiments.card_methods_full_20261112.located_documents import register

PAPER = {'paper_id': 'pmid-123', 'title': 'A test measurement method.', 'doi': ['10.1234/test']}

def candidate(**kwargs):
    return Document.model_validate({'id':'source', 'url':'https://example.org/paper.xml',
        'role':'primary_paper', 'format':'jats', 'identity': {'pmid':'123', 'doi':'10.1234/test', 'title':PAPER['title']},
        'rationale':'Exact assigned paper', **kwargs})

def xml(pmid='123', doi='10.1234/test'):
    return f'''<article><front><article-meta><article-id pub-id-type="pmid">{pmid}</article-id>
    <article-id pub-id-type="doi">{doi}</article-id><title-group><article-title>{PAPER['title']}</article-title></title-group>
    </article-meta></front><body><sec><title>Methods</title><p>{'Actual article procedure. ' * 10}</p></sec></body></article>'''.encode()

def test_original_xml_registered_with_identity_and_custody(tmp_path):
    out = tmp_path/'doc'
    result = register(candidate(), PAPER, out, downloader=lambda u: Download(xml(),u,'application/xml'))
    assert result['assigned_paper_id'] == PAPER['paper_id']
    assert result['document_role'] == 'primary_article'
    assert len(result['custody_files']) == 5
    assert 'Actual article procedure' in result['text']
    assert (out/'original.xml').read_bytes() == xml()

@pytest.mark.parametrize('raw', [xml('124'), xml(doi='10.1234/wrong'), b'<PubmedArticleSet/>'])
def test_wrong_or_metadata_only_xml_is_not_registered(tmp_path, raw):
    with pytest.raises(ValueError):
        register(candidate(), PAPER, tmp_path/'doc', downloader=lambda u: Download(raw,u,'application/xml'))
    assert not (tmp_path/'doc/registered_source.json').exists()

def test_pdf_url_returning_html_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='non-PDF'):
        register(candidate(format='pdf'), PAPER, tmp_path/'doc',
                 downloader=lambda u: Download(b'<html>login</html>',u,'text/html'))

def test_supplement_needs_actual_parent_link(tmp_path):
    raw = b'<html><head><meta name="citation_doi" content="10.1234/test"><meta name="citation_title" content="A test measurement method."></head><body>No attachment link.</body></html>'
    def fetch(u): return Download(raw if u.endswith('parent') else b'%PDF-1.4 invalid',u,'text/html')
    with pytest.raises(ValueError, match='exact retrieved attachment'):
        register(candidate(role='supplement', format='pdf', linked_from='https://example.org/parent'), PAPER, tmp_path/'doc', downloader=fetch)

def test_duplicate_methods_share_one_publication_and_provider_blocks_excluded():
    def row(mid,pid,category):return {'selection':{'method_id':mid}, 'paper':{'paper_id':pid}, 'hold':{'failure_category':category}}
    data={'cases':[row('a','pmid-1','evidence_recovery_failed'),row('b','pmid-1','evidence_recovery_failed'),row('c','pmid-2','provider_policy_block')]}
    result=groups(data)
    assert list(result)==['pmid-1'] and len(result['pmid-1'])==2
    assert groups(data, {'c'}) == {}

def test_native_schema_has_required_nullable_fields():
    result=strict_schema(Findings.model_json_schema())
    def check(value):
        if isinstance(value,dict):
            if value.get('type')=='object' and 'properties' in value:
                assert set(value['required'])==set(value['properties'])
                assert value['additionalProperties'] is False
            for child in value.values():check(child)
        elif isinstance(value,list):
            for child in value:check(child)
    check(result)
