"""Real Poppler/registration with fake archived native inspection, no model calls."""

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import shutil

import pytest

from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.extraction_batch.storage import read, sha, write
from genomics_discovery.curation.source_acquisition import pdf_inspection as inspection
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from test_pdf_registration import DOI, TITLE, metadata, scope

pytestmark = pytest.mark.skipif(not shutil.which('pdftotext') or not shutil.which('pdftoppm'),
                                reason='Real Poppler required')


def make_pdf(path, pages):
    # Real PDF, including a two-column fixture when a page is a tuple.
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    page_ids = []
    for page in pages:
        columns = page if isinstance(page, tuple) else (page,)
        streams = []
        for column, text in enumerate(columns):
            escaped = [line.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
                       for line in text.splitlines()]
            streams.append(f'BT /F1 10 Tf {40+300*column} 740 Td ' +
                           ' 0 -15 Td '.join('('+line+') Tj' for line in escaped) + ' ET')
        stream = '\n'.join(streams).encode()
        page_id = len(objects)+1; page_ids.append(page_id)
        objects.extend([f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_id+1} 0 R >>'.encode(),
                        b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream'])
    objects[1] = ('<< /Type /Pages /Kids ['+' '.join(f'{i} 0 R' for i in page_ids)+f'] /Count {len(pages)} >>').encode()
    data=b'%PDF-1.4\n';offsets=[0]
    for index,obj in enumerate(objects,1):
        offsets.append(len(data));data+=str(index).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    start=len(data);data+=f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode()
    data+=b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
    data+=f'trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()
    path.write_bytes(data)


@pytest.fixture
def case(tmp_path):
    original=tmp_path/'untrusted_filename.pdf'
    make_pdf(original,[TITLE+'\nDOI: '+DOI+'\nMethods\nActual original procedure.\nEND_TARGET_ARTICLE'])
    acquisition=tmp_path/'acquisition.json'
    write(acquisition,{'method_id':'example','documents':[{'id':'downloaded','role':'primary_paper',
        'format':'pdf','status':'retrieved_unregistered','path':str(original),'original_sha256':sha(original),
        'final_url':'https://author.example.org/not_an_identity.pdf','identity':{'doi':DOI,'title':TITLE}}]})
    raw=tmp_path/'metadata.json';raw.write_bytes(metadata())
    receipt=tmp_path/'bibliography_receipt.json'
    write(receipt,{'status':'downloaded','requested_url':'https://api.crossref.org/works/'+DOI,
        'final_url':'https://api.crossref.org/works/'+DOI,'content_type':'application/json',
        'path':str(raw),'sha256':sha(raw)})
    return acquisition,original,receipt


def rebind_original(case):
    acquisition,original,_=case;document=read(acquisition)
    document['documents'][0]['original_sha256']=sha(original);write(acquisition,document)


class Inspector:
    def __init__(self,*,selection=None,relationships=None,decision='select',mutate=None,archive='complete'):
        self.selection=selection;self.relationships=relationships or {};self.decision=decision
        self.mutate=mutate;self.archive=archive;self.calls=[]

    async def run(self,prompt,attempt):
        self.calls.append(prompt);request=read(attempt/'request.json')
        document={'method_id':request['scope']['method_id'],'original_sha256':request['original_sha256'],
            'request_sha256':sha(attempt/'request.json'),'decision':self.decision,
            'reason':'Model-authored fixture inspection of the supplied rendered article pages.',
            'selection':self.selection or {'kind':'whole_document','basis':'Inspected every rendered fixture page; only the target publication appears.'},
            'pages':[{'page':page['page'],'image_sha256':page['image_sha256'],
                'relationship':self.relationships.get(page['page'],'target_article'),
                'reading_order':'coherent','observations':'The rendered fixture shows this article and matching text.',
                'text_quote':page['text'][:100] or None} for page in request['pages']]}
        if self.decision!='select':document['selection']=None
        write(attempt/'artifact.json',document)
        (attempt/'prompt.txt').write_text(prompt)
        events=[{'type':'thread.started','thread_id':'pdf-inspector'},{'type':'turn.started'}]
        if self.archive!='incomplete':events.append({'type':'turn.completed','usage':{'input_tokens':10}})
        (attempt/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        (attempt/'final.txt').write_text('Saved the bounded inspection proposal.')
        result=WorkerResult(0,'pdf-inspector',{'input_tokens':10},.01,None)
        write(attempt/'result.json',asdict(result))
        if self.mutate:self.mutate(document,request,attempt)
        return result if self.archive!='wrong_result' else WorkerResult(0,'wrong-thread',{},.01,None)


async def inspect(case,tmp_path,worker=None,**kwargs):
    return await inspection.inspect_pdf(case[0],'downloaded',scope(),tmp_path/'inspection',
        worker=worker or Inspector(),bibliographic_receipt=case[2],**kwargs)


async def test_clean_download_automatically_registers_actual_bytes_with_native_and_page_provenance(case,tmp_path):
    original_bytes=case[1].read_bytes();worker=Inspector()
    def forbidden_fetch(url):pytest.fail('Cached metadata must not search or download again')
    state=await inspect(case,tmp_path,worker,fetch=forbidden_fetch)
    assert state['status']=='registered_for_fresh_source_review' and state['scientific_resolution'] is False
    assert len(worker.calls)==1 and 'Open EVERY supplied page PNG with view_image' in worker.calls[0]
    packet=load_packet(Path(state['packet']))
    assert packet['method_id']=='example'
    assert 'Actual original procedure.' in '\n'.join(p['text'] for p in packet['sources'][0]['passages'])
    report=read(tmp_path/'inspection/inspection_report.json')
    assert report['original_sha256']==sha(case[1]) and report['selection']['kind']=='whole_document'
    assert 'not verified image opening' in report['assessment_role']
    pins=read(Path(state['source_receipts']))['files']
    assert pins[str(case[1])]==sha(case[1]) and pins[str(case[0])]==sha(case[0])
    assert any(name.endswith('inspector/events.jsonl') for name in pins)
    assert any('/pages/page-' in name and name.endswith('.png') for name in pins)
    assert all(sha(Path(name))==expected for name,expected in pins.items())
    assert case[1].read_bytes()==original_bytes


async def test_clean_page_boundaries_exclude_adjacent_article_but_keep_its_pinned_original(case,tmp_path):
    make_pdf(case[1],[TITLE+'\nDOI: '+DOI+'\nTarget procedure.\nEND_TARGET_ARTICLE',
                       'A wholly separate publication\nDOI: 10.9999/unrelated\nOther procedure.'])
    rebind_original(case)
    selected={'kind':'exact_text_bounds','basis':'Inspected both rendered pages: the target occupies only page one.',
              'start':TITLE,'end':'END_TARGET_ARTICLE'}
    state=await inspect(case,tmp_path,Inspector(selection=selected,relationships={2:'other_article'}))
    assert state['status']=='registered_for_fresh_source_review'
    text=Path(tmp_path/'inspection/registration/primary.txt').read_text()
    assert 'Target procedure.' in text and 'Other procedure' not in text
    assert 'Other procedure' in Path(tmp_path/'inspection/registration/excluded_suffix.txt').read_text()


@pytest.mark.parametrize('bounded',[False,True])
async def test_mixed_column_bundle_cannot_register_even_when_title_identifier_and_selectors_match(case,tmp_path,bounded):
    make_pdf(case[1],[(TITLE+'\nDOI: '+DOI+'\nTarget procedure.\nEND_TARGET_ARTICLE',
                        'Another paper starts here\nInterleaved neighboring content.')])
    rebind_original(case)
    selection={'kind':'exact_text_bounds','basis':'A proposed contiguous text span cannot establish exclusive mixed-column content.',
               'start':TITLE,'end':'END_TARGET_ARTICLE'} if bounded else None
    with pytest.raises(ValueError,match='Selected mixed'):
        await inspect(case,tmp_path,Inspector(selection=selection,relationships={1:'mixed_articles'}))
    assert not (tmp_path/'inspection/registration/packet.json').exists()
    assert read(tmp_path/'inspection/state.json')['status']=='failed'


@pytest.mark.parametrize('content',[
    TITLE+'\nDOI: 10.9999/wrong\nMethods from wrong publication.',
    'An unrelated citing publication\nReferences\n'+TITLE+' DOI: '+DOI,
    'Correction: '+TITLE+'\nOriginal publication DOI: '+DOI])
async def test_wrong_doi_citing_paper_and_correction_fail_before_native_inspection(case,tmp_path,content):
    make_pdf(case[1],[content]);rebind_original(case);worker=Inspector()
    with pytest.raises(ValueError):await inspect(case,tmp_path,worker)
    assert not worker.calls and not (tmp_path/'inspection/registration/packet.json').exists()


@pytest.mark.parametrize('mutation',['method','original','request','image','quote','missing_page','duplicate_page',
                                     'interleaved','other_article','empty_anchor','invented_receipt'])
async def test_host_rejects_unbound_or_unusable_native_selection(case,tmp_path,mutation):
    def mutate(document,request,attempt):
        if mutation=='method':document['method_id']='another-method'
        elif mutation=='original':document['original_sha256']='f'*64
        elif mutation=='request':document['request_sha256']='f'*64
        elif mutation=='image':document['pages'][0]['image_sha256']='f'*64
        elif mutation=='quote':document['pages'][0]['text_quote']='Fabricated visual/transcribed source content.'
        elif mutation=='missing_page':document['pages']=[]
        elif mutation=='duplicate_page':document['pages'].append(document['pages'][0].copy())
        elif mutation=='interleaved':document['pages'][0]['reading_order']='interleaved'
        elif mutation=='other_article':document['pages'][0]['relationship']='other_article'
        elif mutation=='empty_anchor':document['pages'][0]['text_quote']=None
        else:document['selection']['inspection_report']={'path':'fabricated.json','sha256':'0'*64}
        write(attempt/'artifact.json',document)
    with pytest.raises(ValueError):await inspect(case,tmp_path,Inspector(mutate=mutate))
    assert not (tmp_path/'inspection/inspection_report.json').exists()
    assert not (tmp_path/'inspection/registration/packet.json').exists()


@pytest.mark.parametrize('archive',['incomplete','wrong_result'])
async def test_selection_without_complete_matching_native_provenance_is_not_a_host_report(case,tmp_path,archive):
    with pytest.raises(ValueError,match='complete unchanged native archive'):
        await inspect(case,tmp_path,Inspector(archive=archive))
    assert not (tmp_path/'inspection/inspection_report.json').exists()


@pytest.mark.parametrize('which',['image','request','original'])
async def test_inspector_cannot_rewrite_the_original_or_host_preparation(case,tmp_path,which):
    def mutate(document,request,attempt):
        path=Path(request['pages'][0]['image']) if which=='image' else case[1] if which=='original' else attempt/'request.json'
        path.write_bytes(path.read_bytes()+b'changed after preparation')
    with pytest.raises(ValueError):await inspect(case,tmp_path,Inspector(mutate=mutate))
    assert not (tmp_path/'inspection/inspection_report.json').exists()


@pytest.mark.parametrize('decision',['reject','needs_inspection'])
async def test_valid_negative_remains_an_inspection_outcome_without_registration(case,tmp_path,decision):
    state=await inspect(case,tmp_path,Inspector(decision=decision))
    assert state['status']=='inspection_'+decision and state['scientific_resolution'] is False
    report=read(tmp_path/'inspection/inspection_report.json')
    assert report['decision']==decision and report['selection'] is None
    assert all(sha(Path(name))==expected for name,expected in report['files'].items())
    assert not (tmp_path/'inspection/registration').exists()


async def test_default_worker_keeps_required_model_effort_no_timeout_and_no_network(case,tmp_path,monkeypatch):
    calls=[]
    class DefaultWorker(Inspector):
        def __init__(self,**kwargs):
            super().__init__();calls.append(kwargs)
            for key,value in kwargs.items():setattr(self,key,value)
    monkeypatch.setattr(inspection,'CodexWorker',DefaultWorker)
    await inspection.inspect_pdf(case[0],'downloaded',scope(),tmp_path/'inspection',bibliographic_receipt=case[2])
    assert calls==[{'model':'gpt-6.1-sol','effort':'xhigh','timeout_seconds':None,
                   'web_search':False,'network_access':False}]


async def test_missing_cached_metadata_fetches_only_exact_authoritative_endpoint(case,tmp_path):
    from genomics_discovery.curation.source_acquisition.downloads import Download
    calls=[]
    def fetch(url):
        calls.append(url);return Download(metadata(),url,'application/json')
    state=await inspection.inspect_pdf(case[0],'downloaded',scope(),tmp_path/'inspection',worker=Inspector(),fetch=fetch)
    assert state['status']=='registered_for_fresh_source_review'
    assert calls==['https://api.crossref.org/works/'+DOI]


async def test_cancellation_has_no_packet_and_native_retry_requires_a_fresh_directory(case,tmp_path):
    class Cancelled(Inspector):
        async def run(self,prompt,attempt):raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):await inspect(case,tmp_path,Cancelled())
    assert read(tmp_path/'inspection/state.json')['status']=='cancelled'
    assert not (tmp_path/'inspection/registration').exists()
    worker=Inspector()
    with pytest.raises(FileExistsError):await inspect(case,tmp_path,worker)
    assert not worker.calls


async def test_changed_acquired_original_fails_before_creating_inspection(case,tmp_path):
    case[1].write_bytes(case[1].read_bytes()+b'changed')
    with pytest.raises(ValueError,match='Original PDF'):
        await inspect(case,tmp_path)
    assert not (tmp_path/'inspection').exists()


async def test_existing_registration_remains_final_admission_gate(case,tmp_path,monkeypatch):
    def refuse(*args,**kwargs):raise ValueError('Existing registration refused the proposed input')
    monkeypatch.setattr(inspection.registration,'register_pdf',refuse)
    with pytest.raises(ValueError,match='Existing registration refused'):
        await inspect(case,tmp_path)
    assert read(tmp_path/'inspection/state.json')['status']=='failed'
    assert not (tmp_path/'inspection/registration/packet.json').exists()
