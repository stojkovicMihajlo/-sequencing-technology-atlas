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


def bound_parent(tmp_path, raw):
    from genomics_discovery.illumina.extraction import sha
    path = tmp_path / 'parent.xml'; path.write_bytes(raw)
    return {'parent': {'kind':'text','content_level':'fulltext','pmid':'123',
        'custody_files':[{'path':str(path),'sha256':sha(path)}]}}


def word_bytes():
    from io import BytesIO
    from zipfile import ZipFile
    output=BytesIO()
    with ZipFile(output,'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+('Detailed library procedure. '*10)+'</w:t></w:r></w:p></w:body></w:document>')
    return output.getvalue()


def test_word_supplement_uses_exact_registered_parent_link(tmp_path):
    url='https://example.org/supp.docx'
    parent=xml().replace(b'</body>', f'<supplementary-material xmlns:xlink="http://www.w3.org/1999/xlink"><media xlink:href="{url}"/></supplementary-material></body>'.encode())
    source=register(candidate(role='supplement',format='docx',url=url),PAPER,tmp_path/'doc',
        downloader=lambda u:Download(word_bytes(),u,'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
        parent_sources=bound_parent(tmp_path,parent))
    assert source['document_role']=='supplement'
    assert 'Detailed library procedure.' in source['text']
    assert (tmp_path/'doc/word_conversion.json').exists()


def test_a_parent_citation_does_not_permit_guessing_a_supplement_url(tmp_path):
    parents=bound_parent(tmp_path,xml())
    with pytest.raises(ValueError,match='publication-bound'):
        register(candidate(role='supplement',format='docx',url='https://example.org/guessed.docx'),PAPER,tmp_path/'doc',
            downloader=lambda u:Download(word_bytes(),u,'application/zip'),parent_sources=parents)


def test_cited_protocol_uses_own_identity_and_actual_methods_reference(tmp_path):
    parent=xml().replace(b'</p></sec>',b'<xref ref-type="bibr" rid="R1">1</xref></p></sec>').replace(b'</article>',b'<back><ref-list><ref id="R1"><element-citation><pub-id pub-id-type="pmid">999</pub-id><pub-id pub-id-type="doi">10.1234/protocol</pub-id></element-citation></ref></ref-list></back></article>')
    child=xml('999','10.1234/protocol').replace(PAPER['title'].encode(),b'A cited procedure.')
    doc=candidate(role='cited_protocol',identity={'pmid':'999','doi':'10.1234/protocol','title':'A cited procedure.'})
    source=register(doc,PAPER,tmp_path/'doc',downloader=lambda u:Download(child,u,'application/xml'),
                    parent_sources=bound_parent(tmp_path,parent))
    assert source['pmid']=='999' and source['assigned_paper_id']=='pmid-123'
    assert source['document_role']=='cited_protocol'
    assert (tmp_path/'doc/dependency.json').exists()


def test_cited_protocol_without_parent_reference_stays_held(tmp_path):
    doc=candidate(role='cited_protocol',identity={'pmid':'999','doi':'10.1234/protocol','title':'A cited procedure.'})
    with pytest.raises(ValueError,match='identified Methods citation'):
        register(doc,PAPER,tmp_path/'doc',parent_sources=bound_parent(tmp_path,xml()))


def test_explicit_supplement_cover_binds_mirror_without_claiming_backlink(tmp_path):
    from experiments.card_methods_full_20261112.located_documents import _supplement_cover, _parent_records
    from genomics_discovery.sources.acquisition.models import Identity
    expected=Identity(pmid='123',doi='10.1234/test',title=PAPER['title'])
    sources=bound_parent(tmp_path,xml());parents=_parent_records(sources,expected)
    text='Supplementary Materials for\n'+PAPER['title']+'\nDOI: 10.1234/test\nActual original methods.\f'
    record=_supplement_cover(text,expected,parents,sources)
    assert record['adapter']=='source_recovery.supplement_cover/1'
    assert 'no verified publisher backlink' in record['limits']
    with pytest.raises(ValueError,match='supplement-for'):
        _supplement_cover(text.replace('Supplementary Materials for','References'),expected,parents,sources)
    with pytest.raises(ValueError):
        _supplement_cover(text.replace('10.1234/test','10.1234/other'),expected,parents,sources)
    with pytest.raises(ValueError,match='registered'):
        _supplement_cover(text,expected,[],{})
