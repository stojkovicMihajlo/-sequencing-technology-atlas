from argparse import Namespace
import copy
import json
from pathlib import Path

import pytest

from genomics_discovery.illumina.extraction import write, sha
from genomics_discovery.sources.acquisition.downloads import Download
from experiments.evidence_recovery_20261112 import prepare_remaining as prep


def row(mid='a', pid='pmid-123', category='evidence_recovery_failed'):
    return {'method': mid, 'paper': {'paper_id': pid, 'title': 'An assay.', 'doi': ['10.1234/assay']},
            'case': {'selection': {'method_id': mid, 'primary_paper_id': pid},
                     'hold': {'failure_category': category}}, 'findings': None}


def manifest(rows):
    return {'rows': rows, 'methods': [r['method'] for r in rows], 'case_count': len(rows)}


def test_cached_bytes_replay_avoids_refetch_but_preserves_other_observed_routes(tmp_path, monkeypatch):
    p = tmp_path/'original.pdf';p.write_bytes(b'Original bytes')
    receipt = {'sha256': sha(p), 'requested_url': 'https://author.example/paper',
               'final_url': 'https://author.example/paper.pdf', 'content_type': 'application/pdf'}
    requested=[]
    monkeypatch.setattr(prep,'download',lambda url:requested.append(url) or Download(b'Parent bytes',url,'text/html'))
    fetch = prep.cached_fetch(p,receipt)
    assert fetch(receipt['requested_url']).content == b'Original bytes'
    assert fetch(receipt['final_url']).final_url == receipt['final_url']
    assert requested == []
    fetch('https://publisher.example/observed-parent')
    assert requested == ['https://publisher.example/observed-parent']
    p.write_bytes(b'Changed bytes')
    with pytest.raises(ValueError,match='differs'):
        prep.cached_fetch(p,receipt)


@pytest.mark.parametrize('mutation', ['duplicate','provider','identity','publication'])
def test_dispatch_scope_rejects_duplicate_provider_and_identity_changes(mutation):
    rows=[row()]
    if mutation=='duplicate':rows.append(copy.deepcopy(rows[0]))
    if mutation=='provider':rows[0]['case']['hold']['failure_category']='provider_policy_block'
    if mutation=='identity':rows[0]['case']['selection']['method_id']='other'
    if mutation=='publication':rows[0]['case']['selection']['primary_paper_id']='pmid-999'
    with pytest.raises(ValueError):prep.validate_remaining_scope(manifest(rows),set())


def test_dispatch_scope_rejects_previously_started_method():
    with pytest.raises(ValueError,match='overlaps'):
        prep.validate_remaining_scope(manifest([row()]),{'a'})


def test_unchanged_primary_text_does_not_unlock_unresolved_request():
    old={'content_level':'fulltext','pmid':'123','text':'Same procedure.'}
    assert not prep.new_primary_rendition({'text':'Same procedure.'},{'old':old},'pmid-123')
    assert prep.new_primary_rendition({'text':'Procedure plus missing appendix.'},{'old':old},'pmid-123')
    assert not prep.new_primary_rendition({'text':'First procedure.'},{},'pmid-123')


def test_role_correction_reopens_report_and_original_pins(tmp_path):
    report=tmp_path/'experiments/report.json';report.parent.mkdir();report.write_text('Evidence-backed inspection')
    original=tmp_path/'cached/original.pdf';original.parent.mkdir();original.write_bytes(b'%PDF original')
    correction={'inspection_report':{'path':'sequencing-technology-atlas/experiments/report.json','sha256':sha(report)},
        'original':{'path':str(original),'sha256':sha(original)},'proposed_document':{
            'id':'combined_primary','url':'https://author.example/paper.pdf','role':'primary_paper','format':'pdf',
            'identity':{'pmid':'123','doi':'10.1234/assay','title':'An assay.'},
            'rationale':'Inspected primary article followed by its own supplement.'}}
    doc,directory,bound_report=prep.role_correction_candidate(correction,'pmid-123',tmp_path)
    assert doc.role=='primary_paper' and directory==original.parent and bound_report==report
    assert prep.role_correction_candidate(correction,'pmid-999',tmp_path) is None
    report.write_text('Replaced inspection')
    with pytest.raises(ValueError,match='bytes changed'):
        prep.role_correction_candidate(correction,'pmid-123',tmp_path)


@pytest.mark.asyncio
async def test_preparation_isolates_bad_publication_and_dispatches_new_primary(tmp_path, monkeypatch):
    monkeypatch.setenv('SLURM_JOB_ID','test')
    monkeypatch.setattr(prep,'verify_registry_files',lambda registry:None)
    source_release=tmp_path/'base';write(source_release/'recovery_02/registered_additions.json',{})
    prior=tmp_path/'prior';prior.mkdir()
    rows=[row('broken','pmid-124'),row('good','pmid-123')]
    for r in rows:r['publication_directory']=str(tmp_path/r['method'])
    # Broken's source registry is deliberately absent. Good can recover through OAI.
    write(tmp_path/'good/finder/source_registry.json',{})
    m={**manifest(rows),'source_release':str(source_release),'prior_run':str(prior)}
    path=tmp_path/'manifest.json';write(path,m)
    metadata=b'<PubmedArticleSet/>'
    def fake_fetch(url,path):path.write_bytes(metadata);return {'request_url':url,'sha256':sha(path)}
    monkeypatch.setattr(prep,'fetch',fake_fetch)
    monkeypatch.setattr(prep,'parse_pubmed',lambda raw:{'123':{'pmid':'123','identifiers':{'pmc':['PMC1']}}})
    monkeypatch.setattr(prep,'api_source',lambda *args:{'kind':'text','content_level':'fulltext',
        'document_role':'primary_article','pmid':'123','text':'Exact original.','sha256':'a'*64})
    calls=[]
    async def fake_extract(library,vault,selected,output,workers,**kwargs):
        selection=json.loads(selected.read_text());calls.append(selection['methods'][0]['method_id'])
        assert workers==1 and kwargs['max_revisions']==2
        assert kwargs['isolate_source_failures'] and not kwargs['stop_on_first_issue']
        assert kwargs['primary_overrides']['good']['pmid-123']['pmid']=='123'
        return {'counts':{'supported':1}}
    monkeypatch.setattr(prep.extractor,'execute',fake_extract)
    await prep.execute(Namespace(manifest=path,output=tmp_path/'output',workers=50,
        role_corrections=None,library=tmp_path/'library',vault=tmp_path/'vault'))
    result=json.loads((tmp_path/'output/state.json').read_text())
    assert result['methods']['broken']['status']=='preparation_failed'
    assert result['methods']['good']['status']=='completed' and calls==['good']
    assert path.read_bytes()==json.dumps(m,indent=2,ensure_ascii=False).encode()+b'\n'


@pytest.mark.asyncio
async def test_existing_retry_prevents_any_metadata_or_model_call(tmp_path,monkeypatch):
    monkeypatch.setenv('SLURM_JOB_ID','test')
    prior=tmp_path/'prior';write(prior/'retries/pmid-123/extraction/state.json',{'cards':{'a':{}}})
    path=tmp_path/'manifest.json';write(path,{**manifest([row()]),'prior_run':str(prior)})
    monkeypatch.setattr(prep,'fetch',lambda *args:pytest.fail('No metadata request allowed for duplicate dispatch'))
    with pytest.raises(ValueError,match='overlaps'):
        await prep.execute(Namespace(manifest=path,output=tmp_path/'output',workers=50,
            role_corrections=None,library=tmp_path/'library',vault=tmp_path/'vault'))
