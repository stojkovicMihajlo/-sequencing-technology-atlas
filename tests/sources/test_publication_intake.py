import json
import pytest
from pathlib import Path
from genomics_discovery.sources.publication_intake import acquire_publication,cache_index,identity_check,sha,unpaywall_leads,recover_assigned_primaries


def article(pmid='123',title='A physical library preparation method'):
    return f'<article><front><article-meta><article-id pub-id-type="pmid">{pmid}</article-id><article-id pub-id-type="doi">10.9999/example</article-id><title-group><article-title>{title}</article-title></title-group></article-meta></front><body><p>Experimental workflow.</p></body></article>'.encode()


def metadata():return {'pmid':'123','title':'A physical library preparation method','identifiers':{'doi':['10.9999/example']}}


def test_entire_collection_index_and_cached_original_reused_without_network(tmp_path):
    source=tmp_path/'source';source.mkdir();p=source/'article.xml';p.write_bytes(article())
    papers={str(i):{'pmid':str(i),'files':[]} for i in range(307)}
    papers['123']={'pmid':'123','files':[{'path':'article.xml','kind':'article','sha256':sha(p)}]}
    collection=source/'collection.json';collection.write_text(json.dumps({'papers':papers}))
    index,total=cache_index(collection,sha(collection));assert total==307
    def forbidden(*a,**k):raise AssertionError('Cached match must not request network')
    result=acquire_publication(metadata(),tmp_path/'attempt',index['123'],pmc_acquirer=forbidden,selected_acquirer=forbidden)
    assert result['status']=='cached_original_verified';assert result['document_role']=='primary_article'


def test_pmc_original_needs_title_and_own_identifier_body(tmp_path):
    def pmc(meta,out):
        (out/'article.xml').write_bytes(article());(out/'article.txt').write_text('Rendition');(out/'blocks.json').write_text('{}')
        return {'status':'fulltext_acquired','path':'article.xml','text_path':'article.txt','blocks_path':'blocks.json'}
    result=acquire_publication(metadata(),tmp_path/'attempt',pmc_acquirer=pmc)
    assert result['status']=='acquired_original_verified';assert len(result['files'])==3


def test_wrong_cached_body_is_held_not_silently_reused(tmp_path):
    p=tmp_path/'wrong.xml';p.write_bytes(article(title='Wrong cached paper'))
    result=acquire_publication(metadata(),tmp_path/'attempt',[{'absolute_path':str(p),'sha256':sha(p)}],
        pmc_acquirer=lambda *a:{'status':'unavailable'},selected_acquirer=lambda *a:('pmid-123',[],[]))
    assert result['status']=='public_source_or_identity_hold';assert result['files']==[]
    assert result['attempts'][0]['stage']=='cached_original'


def test_approved_oa_metadata_reuse_filters_versions_and_prohibited_web_routes(tmp_path):
    raw=tmp_path/'prior.json';raw.write_text(json.dumps({'doi':'10.9999/example','title':metadata()['title'],
        'oa_locations':[{'version':'acceptedVersion','url_for_pdf':'http://author.example/paper.pdf','host_type':'repository'},
                        {'version':'submittedVersion','url_for_pdf':'https://preprint.example/draft.pdf'},
                        {'version':'publishedVersion','url_for_pdf':'https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/paper.pdf'}]}))
    result=unpaywall_leads({'doi':['10.9999/example'],'title':metadata()['title']},tmp_path/'leads','approved@example.org',
        existing_response={'path':str(raw),'sha256':sha(raw)},fetcher=lambda *a,**k:(_ for _ in ()).throw(AssertionError('must reuse metadata')))
    assert result['status']=='metadata_identity_matches';assert len(result['candidates'])==1
    assert result['candidates'][0]['url']=='https://author.example/paper.pdf'
    assert result['receipt']['retrieval_mode']=='reuse_prior_approved_response'


def test_oa_title_variant_is_only_an_unaccepted_lead_not_changed_primary(tmp_path):
    raw=tmp_path/'prior.json';raw.write_text(json.dumps({'doi':'10.9999/example','title':'Unverified different title','oa_locations':[]}))
    result=unpaywall_leads({'doi':['10.9999/example'],'title':metadata()['title']},tmp_path/'leads','approved@example.org',existing_response={'path':str(raw),'sha256':sha(raw)})
    assert result['status']=='doi_matches_title_variant_unaccepted';assert result['candidates']==[]


def test_oa_wrong_own_doi_is_not_accepted(tmp_path):
    raw=tmp_path/'prior.json';raw.write_text(json.dumps({'doi':'10.9999/wrong','title':metadata()['title'],'oa_locations':[]}))
    with pytest.raises(ValueError,match='own DOI differs'):
        unpaywall_leads({'doi':['10.9999/example'],'title':metadata()['title']},tmp_path/'leads',None,existing_response={'path':str(raw),'sha256':sha(raw)})


def recovery_config(tmp_path,locations):
    snapshot=tmp_path/'snapshot.json';snapshot.write_text(json.dumps({'cases':[{'selection':{'method_id':'assigned'},'paper':{'paper_id':'pmid-123','title':metadata()['title'],'doi':['10.9999/example']}}]}))
    collection=tmp_path/'collection.json';collection.write_text(json.dumps({'papers':{}}))
    bulk=tmp_path/'bulk';bulk.mkdir();(bulk/'ACQUISITION.json').write_text(json.dumps({'papers':{}}))
    prior=tmp_path/'approved.json';prior.write_text(json.dumps({'doi':'10.9999/example','title':metadata()['title'],'oa_locations':locations}))
    config={'snapshot':{'path':str(snapshot),'sha256':sha(snapshot)},'cache_collection':{'path':str(collection),'sha256':sha(collection)},
            'bulk_output':str(bulk),'approved_contact_sha256':'not-used-for-cached-response','targets':[{'method_id':'assigned','pmid':'123'}],
            'oa_response_cache':{'123':{'path':str(prior),'sha256':sha(prior)}}}
    path=tmp_path/'config.json';path.write_text(json.dumps(config));return path,config


def test_assigned_recovery_skips_old_failures_and_challenge_host_then_hands_off_new_registered_source(tmp_path,monkeypatch):
    monkeypatch.delenv('ATLAS_PUBLIC_ACCESS_CONTACT',raising=False)
    path,config=recovery_config(tmp_path,[{'version':'acceptedVersion','url_for_pdf':u} for u in ['https://old.example/a.pdf','https://challenge.example/b.pdf','https://new.example/c.pdf']])
    config['targets'][0]['previously_failed_urls']=['http://old.example/a.pdf'];config['blocked_hosts']=['challenge.example'];path.write_text(json.dumps(config))
    live_index=Path(config['bulk_output'])/'ACQUISITION.json';original_index=live_index.read_bytes()
    called=[]
    def register(document,paper,output):
        live_index.write_text(json.dumps({'papers':{'new-later-entry':{'status':'acquired'}}}))
        called.append(document.url);assert paper['paper_id']=='pmid-123';return {'paper_id':'pmid-123','original':{'path':'verified.pdf','sha256':'verified-byte-pin'}}
    result=recover_assigned_primaries(path,tmp_path/'result',registrar=register)
    assert called==['https://new.example/c.pdf'];assert result['ready_methods']==['assigned'];assert result['model_calls']==0
    handoff=json.loads((tmp_path/'result/READY_HANDOFF.json').read_text());assert handoff['fresh_review_required']
    assert json.loads((tmp_path/'result/OUTCOMES.json').read_text())['papers']['assigned']['attempts'][0]['status']=='unchanged_failed_route_skipped'
    assert (tmp_path/'result/BULK_CACHE_CHECKPOINT.json').read_bytes()==original_index
    assert json.loads((tmp_path/'result/BULK_CACHE_LOOKUP_RECEIPT.json').read_text())['raw_sha256']==sha(tmp_path/'result/BULK_CACHE_CHECKPOINT.json')


def test_assigned_primary_does_not_silently_substitute_another_publication(tmp_path):
    path,config=recovery_config(tmp_path,[]);config['targets'][0]['pmid']='wrong';path.write_text(json.dumps(config))
    with pytest.raises(ValueError,match='assigned-primary identity changed'):
        recover_assigned_primaries(path,tmp_path/'result',registrar=lambda *a:pytest.fail('must not retrieve wrong primary'))


def test_prior_failed_attempt_bytes_must_still_match_before_recovery(tmp_path):
    path,config=recovery_config(tmp_path,[])
    old=tmp_path/'old-attempt.json';old.write_text('{}');config['prior_source_attempts']=[{'path':str(old),'sha256':sha(old)}]
    path.write_text(json.dumps(config));old.write_text('{"changed":true}')
    with pytest.raises(ValueError,match='Prior recovery receipt changed'):
        recover_assigned_primaries(path,tmp_path/'result',registrar=lambda *a:pytest.fail('must not retrieve'))


def test_host_access_denial_stops_other_routes_on_that_host(tmp_path,monkeypatch):
    monkeypatch.delenv('ATLAS_PUBLIC_ACCESS_CONTACT',raising=False)
    path,config=recovery_config(tmp_path,[{'version':'publishedVersion','url_for_pdf':u} for u in ['https://denied.example/a.pdf','https://denied.example/b.pdf']])
    called=[]
    def denied(document,*args):called.append(document.url);raise ValueError('HTTP Error 403: Forbidden')
    result=recover_assigned_primaries(path,tmp_path/'result',registrar=denied)
    assert called==['https://denied.example/a.pdf'];assert result['ready_methods']==[]
    attempts=json.loads((tmp_path/'result/OUTCOMES.json').read_text())['papers']['assigned']['attempts']
    assert attempts[-1]['status']=='provider_access_challenge_not_repeated'


def test_verified_cached_original_is_pending_registration_and_never_ready(tmp_path,monkeypatch):
    monkeypatch.delenv('ATLAS_PUBLIC_ACCESS_CONTACT',raising=False)
    path,config=recovery_config(tmp_path,[])
    original=tmp_path/'cached.xml';original.write_bytes(article())
    collection=Path(config['cache_collection']['path']);collection.write_text(json.dumps({'papers':{'p':{'pmid':'123','files':[{'kind':'article','path':original.name,'sha256':sha(original)}]}}}))
    config['cache_collection']['sha256']=sha(collection);path.write_text(json.dumps(config))
    result=recover_assigned_primaries(path,tmp_path/'result',registrar=lambda *a:pytest.fail('cached branch must not request network'))
    assert result['ready_methods']==[]
    handoff=json.loads((tmp_path/'result/READY_HANDOFF.json').read_text())
    assert handoff['ready_methods']==[];assert handoff['cached_pending_registration']['assigned']['next_action']=='register_cached_original'
    assert json.loads((tmp_path/'result/PRIMARY_OVERRIDES.json').read_text())=={}


def test_programming_error_is_failed_stage_not_public_access_hold(tmp_path,monkeypatch):
    monkeypatch.delenv('ATLAS_PUBLIC_ACCESS_CONTACT',raising=False)
    path,config=recovery_config(tmp_path,[{'version':'publishedVersion','url_for_pdf':'https://public.example/a.pdf'}])
    def broken(*args):raise TypeError('programming defect')
    with pytest.raises(TypeError,match='programming defect'):
        recover_assigned_primaries(path,tmp_path/'result',registrar=broken)
    record=json.loads((tmp_path/'result/OUTCOMES.json').read_text())['papers']['assigned']
    assert record['status']=='recovery_execution_failed';assert record['failed_stage']=='oa_metadata_or_registration'
