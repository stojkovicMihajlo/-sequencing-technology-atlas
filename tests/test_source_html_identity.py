"""Exact scoped identity exceptions never turn previews/challenges into papers."""
import json
from pathlib import Path

import pytest

from genomics_discovery.curation.source_acquisition.runtime import _identity,_match,_normal,_doi,_primary,acquire
from genomics_discovery.curation.source_acquisition.html_identity import assess_primary_html
from genomics_discovery.curation.source_acquisition.models import Identity,Document
from genomics_discovery.curation.workflow_extraction.sources import build_packet,load_packet
from genomics_discovery.curation.run_state import sha
from test_source_acquisition import Finder,document,fetcher,findings,worklist

DOI='10.1234/protocol'
FINAL='Published protocol'
MANUSCRIPT='A documented manuscript title for the protocol'
PMC='https://pmc.ncbi.nlm.nih.gov/articles/PMC123/'


def html(*,pmid='123',doi=DOI,title=FINAL,body='Reported original article content.',manuscript=False,page_title=None):
    metadata=f'<meta name="citation_title" content="{title}">'
    if pmid is not None:metadata+=f'<meta name="citation_pmid" content="{pmid}">'
    if doi is not None:metadata+=f'<meta name="citation_doi" content="{doi}">'
    version=(f'<section class="pmc-layout__citation"><div>Cell. Author manuscript; available in PMC.</div>'
             f'<div>Published in final edited form as: Cell. doi: <a href="https://doi.org/{doi}">{doi}</a></div></section>') if manuscript else ''
    return (f'<html><head><title>{page_title or title}</title>{metadata}</head><body><article>{version}'
            f'<h1>{title}</h1><p>{body}</p></article></body></html>').encode()


def assess(raw,expected=None,declared=None,url=PMC):
    return assess_primary_html(raw,_identity(raw,'html'),expected or Identity(pmid='123',doi=DOI,title=FINAL),
                               declared or Identity(pmid='123',doi=DOI,title=FINAL),url,normal=_normal,doi=_doi)


def test_absent_pmid_has_exact_independently_scoped_doi_title_route():
    raw=html(pmid=None)
    receipt=assess(raw)
    assert receipt['identifier_route']=='exact_scoped_doi_title_missing_pmid'
    assert receipt['absent_identifiers']==['pmid']
    assert _identity(raw,'html')['identifiers']['pmid']==[]
    # Shared JATS/association matcher remains strict; the exception is HTML-primary only.
    with pytest.raises(ValueError,match='pmid mismatch'):_match(_identity(raw,'html'),Identity(pmid='123',doi=DOI,title=FINAL))


@pytest.mark.parametrize('expected',[Identity(pmid='123'),Identity(pmid='123',doi=DOI),Identity(pmid='123',title=FINAL)])
def test_finder_assertion_cannot_fill_missing_independent_scope_identifiers(expected):
    with pytest.raises(ValueError,match='absent.*independent exact scoped'):
        assess(html(pmid=None),expected)


@pytest.mark.parametrize('raw,declared',[
    (html(pmid='999'),None),(html(doi='10.9999/wrong'),None),
    (html(),Identity(pmid='999',doi=DOI,title=FINAL)),
    (html(pmid=None),Identity(pmid='999',doi=DOI,title=FINAL)),
    (html()+b'<meta name="citation_doi" content="10.9999/wrong">',None)])
def test_no_identifier_conflict_or_ambiguity_is_waived(raw,declared):
    with pytest.raises(ValueError):assess(raw,declared=declared)


def test_documented_manuscript_variant_preserves_actual_and_expected_titles():
    receipt=assess(html(title=MANUSCRIPT,manuscript=True))
    assert receipt['title_variant_accepted'] and receipt['source_version']=='author_manuscript'
    assert receipt['actual_title']==MANUSCRIPT and receipt['expected_title']==FINAL
    assert receipt['finder_declared_title']==FINAL
    assert receipt['frozen_expected_identity']=={'pmid':'123','doi':DOI,'title':FINAL}
    assert receipt['documented_version_evidence'][0]['links']==['https://doi.org/'+DOI]


@pytest.mark.parametrize('expected,url,marker,declared',[
    (Identity(pmid='123'),PMC,True,None),
    (Identity(pmid='123',doi=DOI),PMC,True,None),
    (None,'https://example.org/articles/PMC123/',True,None),
    (None,PMC,False,None),
    (None,PMC,True,Identity(pmid='123',doi=DOI,title='Unrelated third title'))])
def test_title_exception_requires_scoped_identifiers_and_actual_supported_host_version_block(expected,url,marker,declared):
    with pytest.raises(ValueError,match='title mismatch'):
        assess(html(title=MANUSCRIPT,manuscript=marker),expected,declared,url)


def test_body_prose_or_reference_to_an_author_manuscript_cannot_authorize_title_change():
    body=f'Author manuscript; Published in final edited form as: <a href="https://doi.org/{DOI}">Reference</a>'
    with pytest.raises(ValueError,match='title mismatch'):assess(html(title=MANUSCRIPT,body=body))


@pytest.mark.parametrize('body,page_title',[
    ('This is a preview of subscription content. Access via your institution.',None),
    ('This is a preview of\nsubscription content. <h2>Methods</h2>','Paper landing'),
    ('Abstract only.','This is a preview of subscription content.'),
    ('Checking your browser before accessing ...','Checking your browser - reCAPTCHA')])
def test_explicit_preview_challenge_stays_unregistered_even_with_exact_citation_metadata(body,page_title):
    with pytest.raises(ValueError,match='preview|challenge'):
        assess(html(body=body,page_title=page_title))


@pytest.mark.parametrize('tag',['header','nav','footer','p'])
@pytest.mark.parametrize('pmid',[None,'123'])
def test_preview_ui_in_article_chrome_is_not_lost_by_scientific_text_rendition(tag,pmid):
    body=f'<{tag}>This is a pre<span>view</span> of subscription content.</{tag}><p>Abstract only.</p>'
    with pytest.raises(ValueError,match='subscription preview'):
        assess(html(pmid=pmid,body=body))


@pytest.mark.parametrize('tag',['header','div','p','footer'])
def test_normal_document_title_does_not_hide_a_body_browser_challenge(tag):
    body=f'<{tag}>Checking <span>your</span> browser before accessing the requested article.</{tag}>'
    with pytest.raises(ValueError,match='browser/access challenge'):
        assess(html(pmid=None,body=body,page_title=FINAL))


@pytest.mark.parametrize('hidden',[
    '<script>const template = "<header>This is a preview of subscription content.</header> Checking your browser";</script>',
    '<style>header::after { content: "This is a preview of subscription content. Checking your browser"; }</style>',
    '<template><header>This is a preview of subscription content.</header><p>Checking your browser.</p></template>',
    '<template><head><title>Checking your browser</title></head><header>This is a preview of subscription content.</header></template>',
    '<div hidden><div><p>This is a preview of subscription content. Checking your browser.</p></div></div>',
    '<meta name="description" content="This is a preview of subscription content. Checking your browser.">'])
def test_nonrendered_templates_style_script_hidden_nodes_and_metadata_do_not_supply_access_negatives(hidden):
    receipt=assess(html(pmid=None,body=hidden+'<p>Reported original article content.</p>'))
    assert receipt['identifier_route']=='exact_scoped_doi_title_missing_pmid'


def test_no_methods_heading_or_arbitrary_length_is_an_identity_requirement():
    receipt=assess(html(body='A short original protocol description.'))
    assert 'not proof of full article' in receipt['content_boundary']
    assert 'full_text' not in receipt


async def test_real_acquisition_resolve_uses_host_assessment_and_retains_absence_without_invented_metadata(tmp_path):
    tasks=worklist(tmp_path)
    expected={'assay':{'method_id':'assay','title':'Assay assay','scope':'General original protocol',
        'variant':'Original','primary_identity':{'pmid':'123','doi':DOI,'title':FINAL}}}
    doc=document(fmt='html',url='https://example.org/article.html')
    raw=html(pmid=None)
    result=await acquire(tasks,tmp_path/'out',scopes=expected,
        worker=Finder({'assay':findings('assay',[doc])}),downloader=fetcher({doc['url']:raw}))
    assert result['registration'] and result['scientific_resolution'] is False
    row=result['methods']['assay']['documents'][0]
    assert row['status']=='registered_for_fresh_source_review' and row['host_identity']['identifiers']['pmid']==[]
    assert row['identity_assessment']['absent_identifiers']==['pmid']
    ledger=json.loads((tmp_path/'out/methods/assay/originals_ledger.json').read_bytes())
    assert ledger[0]['identity_assessment']==row['identity_assessment']
    pins=json.loads((tmp_path/'out/source_receipts.json').read_bytes())['files']
    assert pins[row['path']]==sha(Path(row['path']))
    packet=load_packet(Path(result['methods']['assay']['packet']))
    assert packet['scope']==expected['assay']['scope'] and packet['variant']=='Original'


async def test_manuscript_registration_preserves_actual_title_and_version_evidence(tmp_path):
    tasks=worklist(tmp_path)
    scopes={'assay':{'method_id':'assay','title':'Assay assay','scope':'General original protocol',
        'variant':'Original','primary_identity':{'pmid':'123','doi':DOI,'title':FINAL}}}
    doc=document(fmt='html',url=PMC)
    result=await acquire(tasks,tmp_path/'out',scopes=scopes,
        worker=Finder({'assay':findings('assay',[doc])}),
        downloader=fetcher({PMC:html(title=MANUSCRIPT,manuscript=True)}))
    row=result['methods']['assay']['documents'][0]
    assert row['status']=='registered_for_fresh_source_review'
    packet=load_packet(Path(result['methods']['assay']['packet']))
    assert packet['sources'][0]['title']==MANUSCRIPT
    assessment=row['identity_assessment']
    assert assessment['expected_title']==FINAL and assessment['source_version']=='author_manuscript'
    assert assessment['title_variant_accepted'] and assessment['documented_version_evidence']
    ledger=json.loads((tmp_path/'out/methods/assay/originals_ledger.json').read_bytes())
    assert ledger[0]['identity_assessment']==assessment
    assert result['scientific_resolution'] is False


@pytest.mark.parametrize('raw',[
    html(pmid=None,body='This is a preview of subscription content.'),
    html(pmid=None,body='<header>This is a preview of subscription content.</header><p>Abstract only.</p>'),
    html(pmid=None,body='<p>Checking your browser before accessing the requested article.</p>',page_title=FINAL),
    b'<html><head><title>Checking your browser - reCAPTCHA</title></head><body>Checking your browser.</body></html>'])
async def test_full_registration_never_promotes_preview_or_captcha(tmp_path,raw):
    tasks=worklist(tmp_path);doc=document(fmt='html',url='https://example.org/article.html')
    scope={'assay':{'method_id':'assay','title':'Assay assay','scope':'General original protocol','variant':'Original',
             'primary_identity':{'pmid':'123','doi':DOI,'title':FINAL}}}
    result=await acquire(tasks,tmp_path/'out',scopes=scope,worker=Finder({'assay':findings('assay',[doc])}),
        downloader=fetcher({doc['url']:raw}))
    assert result['registration'] is None and result['methods']['assay']['packet'] is None
    row=result['methods']['assay']['documents'][0]
    assert row['status']=='retrieved_unregistered' and Path(row['path']).read_bytes()==raw
    assert result['scientific_resolution'] is False
