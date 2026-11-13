"""Real dispatcher/archived fake-worker recovery controls; no scientific oracle."""
import copy
from pathlib import Path
from unittest.mock import AsyncMock,patch

import pytest

from genomics_discovery.illumina import extraction as core
from genomics_discovery.illumina import review_recovery as recovery
from genomics_discovery.illumina.models import DiagramExtraction
from tests.illumina import test_illumina_batch_controls as transport
from tests.illumina.test_illumina_batch_controls import acquired


def source(path,text):
    path.write_text(text);original=path.with_suffix('.original.txt');original.write_text(text)
    conversion=path.with_suffix('.conversion.json')
    core.write(conversion,{'original_sha256':core.sha(original),'text_sha256':core.sha(path),'format':'text'})
    return {'kind':'text','content_level':'fulltext','document_role':'supplement','pmid':'1',
            'path':str(path),'sha256':core.sha(path),'text':text,
            'custody_files':[{'path':str(p),'sha256':core.sha(p)} for p in (original,path,conversion)]}


@pytest.fixture
def held_case(acquired,tmp_path,monkeypatch):
    root,card,acquired_sources,_=acquired
    registry,images=core.card_sources(card,acquired_sources,root)
    original=transport.candidate
    def factory(sources,**kwargs):
        raw=original(sources,**kwargs).model_dump(mode='json')
        if 'supplement:second' not in sources:
            raw['unknowns']=[{'id':'interface','blocking':True,
                'question':'Second source needed.' if 'supplement:first' in sources else 'First source needed.',
                'affected_ids':['operation:capture']}]
        return DiagramExtraction.model_validate(raw)
    monkeypatch.setattr(transport,'candidate',factory)
    monkeypatch.setenv('SLURM_JOB_ID','offline-no-model')
    return card,registry,images,transport.FakeWorker()


async def initial(case,path):
    card,registry,images,worker=case
    row=await core.extract_registered_workflow(card,registry,images,path,worker,
        max_revisions=0,representation='illumina_diagram/1.0')
    assert row['status']=='research_needed'
    return row


@pytest.mark.asyncio
async def test_two_round_actual_dispatch_and_complete_chain_resume_without_calls(held_case,tmp_path):
    card,registry,images,worker=held_case;old=tmp_path/'original';row=await initial(held_case,old)
    first=source(tmp_path/'first.txt','New source one; synthetic support, not a scientific label.')
    second=source(tmp_path/'second.txt','New source two; synthetic support, not a scientific label.')
    async def lookup(request,current,policy,output):
        return {'sources':{'supplement:second':second} if 'supplement:first' in current else {'supplement:first':first}}
    cache=AsyncMock(side_effect=lookup);finder=AsyncMock()
    policy={'max_rounds':2}
    result=await recovery.recover_review(row,old,tmp_path/'recovery',worker,policy,cache_lookup=cache,investigator=finder)
    assert result['status']=='supported'
    assert len(worker.calls)==6  # Original author/review, then two fresh author/review pairs.
    assert cache.await_count==2;finder.assert_not_awaited()
    calls=len(worker.calls)
    replay=await recovery.recover_review(row,old,tmp_path/'recovery',worker,policy,cache_lookup=cache,investigator=finder)
    assert replay['status']=='supported' and replay['recovery_reused']
    assert len(worker.calls)==calls and cache.await_count==2


@pytest.mark.asyncio
async def test_unavailable_generic_request_strategy_is_checkpointed(held_case,tmp_path):
    worker=held_case[-1];old=tmp_path/'original';row=await initial(held_case,old)
    cache=AsyncMock(return_value={'sources':{}})
    finder=AsyncMock(return_value={'sources':{},'holds':[{'stage':'source','reason':'Unavailable exact supplement'}]})
    policy={'max_rounds':1,'strategy':'new_review_request'}
    result=await recovery.recover_review(row,old,tmp_path/'recovery',worker,policy,cache_lookup=cache,investigator=finder)
    assert result['status']=='research_needed' and result['recovery']['status']=='held_no_new_evidence'
    assert finder.call_args.args[2]['new_route']['strategy']=='specific_unseen_review_request'
    again=await recovery.recover_review(row,old,tmp_path/'recovery',worker,policy,cache_lookup=cache,investigator=finder)
    assert again['recovery_reused'] and len(worker.calls)==2
    assert finder.await_count==1 and cache.await_count==1


@pytest.mark.asyncio
async def test_duplicate_registered_content_never_dispatches(held_case,tmp_path):
    worker=held_case[-1];old=tmp_path/'original';row=await initial(held_case,old)
    registry=core.read(old/'source_registry.json')
    original=source(tmp_path/'registered.txt','Registered full-text content.')
    registry['supplement:registered']=original;core.write(old/'source_registry.json',registry)
    row['artifact_pins']['source_registry.json']=core.sha(old/'source_registry.json')
    duplicate=copy.deepcopy(original)
    duplicate['document_role']='another_role_label_is_not_new_content'
    cache=AsyncMock(return_value={'sources':{'new-alias':duplicate}});dispatch=AsyncMock()
    result=await recovery.recover_review(row,old,tmp_path/'recovery',worker,{'max_rounds':1},cache_lookup=cache,dispatcher=dispatch)
    assert result['status']=='research_needed';dispatch.assert_not_awaited();assert len(worker.calls)==2


@pytest.mark.asyncio
async def test_unexpected_typeerror_is_software_failure_not_source_retry(held_case,tmp_path):
    old=tmp_path/'original';row=await initial(held_case,old)
    finder=AsyncMock();dispatch=AsyncMock();cache=AsyncMock(side_effect=TypeError('Broken host adapter'))
    result=await recovery.recover_review(row,old,tmp_path/'recovery',held_case[-1],{'strategy':'new_review_request'},
        cache_lookup=cache,investigator=finder,dispatcher=dispatch)
    assert result['status']=='failed' and result['failure_category']=='software_error'
    assert result['recovery_failure']['exception_type']=='TypeError'
    finder.assert_not_awaited();dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failure_is_not_missing_source(held_case,tmp_path):
    old=tmp_path/'original';row=await initial(held_case,old)
    result=await recovery.recover_review(row,old,tmp_path/'recovery',held_case[-1],{'strategy':'new_review_request'},
        cache_lookup=AsyncMock(return_value={'sources':{}}),
        investigator=AsyncMock(return_value={'sources':{},'failure':{'category':'provider_policy_block','stage':'finder'}}))
    assert result['status']=='provider_policy_block' and result['prior_scientific_outcome']=='research_needed'


@pytest.mark.parametrize('parent_hash',[None,'','bad','0'*64])
def test_auxiliary_image_requires_exact_nonempty_parent_hash(tmp_path,parent_hash):
    parent=source(tmp_path/'parent.txt','Registered primary text.')
    image=tmp_path/'figure.png';image.write_bytes(b'image')
    record={'kind':'image','image_role':'primary_paper_figure','parent_source_id':'paper:1',
        'parent_source_sha256':parent_hash,'path':str(image),'sha256':core.sha(image)}
    with pytest.raises(ValueError,match='bound registered primary'):
        recovery.distinct_sources({'paper:1':parent},{'figure:1':record})


def test_bound_auxiliary_image_is_new_evidence_not_a_primary_text_override(tmp_path):
    parent=source(tmp_path/'parent.txt','Registered primary text.');image=tmp_path/'figure.png';image.write_bytes(b'image')
    record={'kind':'image','image_role':'primary_paper_figure','parent_source_id':'paper:1',
        'parent_source_sha256':parent['sha256'],'path':str(image),'sha256':core.sha(image)}
    receipt=tmp_path/'render.json';core.write(receipt,{'image_sha256':record['sha256'],'parent_source_sha256':parent['sha256']})
    record['custody_files']=[{'path':str(p),'sha256':core.sha(p)} for p in (image,receipt)]
    assert recovery.distinct_sources({'paper:1':parent},{'figure:1':record})=={'figure:1':record}


@pytest.mark.asyncio
async def test_successful_unaffected_workflow_does_not_recover(tmp_path):
    cache=AsyncMock();finder=AsyncMock()
    row={'status':'supported'}
    assert await recovery.recover_review(row,tmp_path/'absent',tmp_path/'unused',object(),{},cache_lookup=cache,investigator=finder)==row
    cache.assert_not_awaited();finder.assert_not_awaited();assert not (tmp_path/'unused').exists()


@pytest.mark.asyncio
async def test_latest_child_cannot_reset_consumed_episode_bound(held_case,tmp_path):
    old=tmp_path/'original';row=await initial(held_case,old);worker=held_case[-1]
    first=source(tmp_path/'first.txt','One distinct registered supplement.')
    cache=AsyncMock(return_value={'sources':{'supplement:first':first}})
    policy={'max_rounds':1,'state_directory':str(tmp_path/'shared_state')}
    result=await recovery.recover_review(row,old,tmp_path/'recovery',worker,policy,cache_lookup=cache)
    assert result['status']=='research_needed' and len(worker.calls)==4
    child=Path(result['attempt_directory'])
    again=await recovery.recover_review(result,child,tmp_path/'other_output',worker,policy,cache_lookup=cache)
    assert again['recovery_reused'] and len(worker.calls)==4 and cache.await_count==1
    # Metadata-only callers also inherit the episode through the preserved child.
    bare={k:v for k,v in result.items() if k!='recovery_episode'}
    again=await recovery.recover_review(bare,child,tmp_path/'third_output',worker,policy,cache_lookup=cache)
    assert again['recovery_reused'] and cache.await_count==1


@pytest.mark.asyncio
async def test_changed_root_route_opens_one_new_recorded_episode(held_case,tmp_path):
    old=tmp_path/'original';row=await initial(held_case,old);worker=held_case[-1]
    cache=AsyncMock(return_value={'sources':{}});finder=AsyncMock(return_value={'sources':{}})
    policy={'max_rounds':1,'new_route':{'url':'https://example.org/first'}}
    result=await recovery.recover_review(row,old,tmp_path/'recovery',worker,policy,cache_lookup=cache,investigator=finder)
    changed={**policy,'new_route':{'url':'https://example.org/new-observed-route'}}
    reopened=await recovery.recover_review(result,old,tmp_path/'recovery',worker,changed,cache_lookup=cache,investigator=finder)
    assert finder.await_count==2 and reopened['recovery_episode']['id']!=result['recovery_episode']['id']
    replay=await recovery.recover_review(reopened,old,tmp_path/'recovery',worker,changed,cache_lookup=cache,investigator=finder)
    assert replay['recovery_reused'] and finder.await_count==2 and cache.await_count==2
    journal=core.read(tmp_path/'recovery/recovery.json')
    assert len(journal['episodes'])==2 and {e['used'] for e in journal['episodes'].values()}=={1}


def test_unregistered_text_is_not_new_source_evidence():
    with pytest.raises(ValueError,match='custody'):
        recovery.distinct_sources({}, {'paper:1':{'kind':'text','content_level':'fulltext',
            'pmid':'1','doi':'10.1/test','text':'Unregistered text.'}})


def test_identifier_order_and_rendition_path_are_not_new_evidence(tmp_path):
    first=source(tmp_path/'first.txt','Same scientific body.')
    first.update(pmid=['2','1'],doi=['10.1/B','10.1/a'])
    alias=copy.deepcopy(first)
    alias.update(pmid=['1','2'],doi=['https://doi.org/10.1/A','10.1/b'],
        text='[Rendition provenance, not publication content]\npath=/copied/runtime\n\nSame scientific body.')
    assert recovery.distinct_sources({'paper:1':first},{'alias':alias})=={}


def test_pinned_json_hash_and_parse_use_one_read(tmp_path,monkeypatch):
    path=tmp_path/'inventory.json';core.write(path,{'value':'pinned'})
    pin={'path':str(path),'sha256':core.sha(path)}
    original=Path.read_bytes;calls=[]
    def changing(self):
        value=original(self);calls.append(str(self))
        self.write_text('{"value":"changed after read"}')
        return value
    monkeypatch.setattr(Path,'read_bytes',changing)
    assert recovery._pinned(pin)=={'value':'pinned'} and calls==[str(path)]


@pytest.mark.asyncio
async def test_copied_tool_paths_do_not_repeat_a_finished_route(held_case,tmp_path,monkeypatch):
    old=tmp_path/'original';row=await initial(held_case,old)
    cache=AsyncMock(return_value={'sources':{}});finder=AsyncMock(return_value={'sources':{}})
    monkeypatch.setattr(recovery,'tool_inventory',lambda:{'tool':{'path':'/first/runtime/tool','sha256':'a'*64}})
    policy={'max_rounds':1,'new_route':{'url':'https://example.org/source'}}
    await recovery.recover_review(row,old,tmp_path/'recovery',held_case[-1],policy,cache_lookup=cache,investigator=finder)
    monkeypatch.setattr(recovery,'tool_inventory',lambda:{'tool':{'path':'/copied/runtime/tool','sha256':'a'*64}})
    result=await recovery.recover_review(row,old,tmp_path/'recovery',held_case[-1],policy,cache_lookup=cache,investigator=finder)
    assert result['recovery_reused'] and cache.await_count==1 and finder.await_count==1


@pytest.mark.asyncio
async def test_cache_only_rejects_a_document_url_outside_pinned_responses(tmp_path):
    original=tmp_path/'original.txt';original.write_text('Cached original bytes.')
    document=tmp_path/'document.json';receipt=tmp_path/'receipt.json'
    core.write(document,{'id':'paper','url':'https://example.org/unpinned','format':'text',
        'role':'primary_paper','identity':{'pmid':'1','title':'Paper'},'rationale':'Exact source.'})
    core.write(receipt,{'requested_url':'https://example.org/pinned','final_url':'https://example.org/pinned',
        'sha256':core.sha(original),'content_type':'text/plain'})
    policy={'cached_documents':[{'original':{'path':str(original),'sha256':core.sha(original)},
        'document':{'path':str(document),'sha256':core.sha(document)},
        'receipt':{'path':str(receipt),'sha256':core.sha(receipt)},'paper':{},'source_id':'paper:1'}]}
    with patch('genomics_discovery.sources.acquisition.downloads.download') as network:
        with pytest.raises(ValueError,match='pinned cached receipt URL'):
            await recovery.cached_sources({'method_id':'method'}, {},policy,tmp_path/'output')
    network.assert_not_called()


@pytest.mark.asyncio
async def test_one_global_store_is_namespaced_for_each_method(held_case,tmp_path):
    import shutil
    old=tmp_path/'original';row=await initial(held_case,old)
    second=tmp_path/'second';shutil.copytree(old,second)
    card=core.read(second/'card.json');card['record_id']='another-method';core.write(second/'card.json',card)
    raw=core.read(second/'extraction.json');raw['record_id']='another-method'
    candidate=DiagramExtraction.model_validate(raw);core.write(second/'extraction.json',raw)
    review=core.read(second/'review.json');review.update(record_id='another-method',candidate_sha256=recovery.digest(candidate))
    core.write(second/'review.json',review)
    policy={'max_rounds':1,'state_directory':str(tmp_path/'cohort_state')}
    cache=AsyncMock(return_value={'sources':{}})
    first=await recovery.recover_review(row,old,tmp_path/'first_recovery',held_case[-1],policy,cache_lookup=cache)
    other=await recovery.recover_review({'status':'research_needed'},second,tmp_path/'second_recovery',held_case[-1],policy,cache_lookup=cache)
    assert first['status']==other['status']=='research_needed' and cache.await_count==2
    assert first['recovery_episode']['store']!=other['recovery_episode']['store']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,category',[
    (FileNotFoundError('The source converter executable is absent'),'software_error'),
    (OSError(28,'No space left on device'),'host_storage_failure')])
async def test_actual_cache_registration_host_failure_never_reaches_finder(held_case,tmp_path,failure,category):
    old=tmp_path/'original';row=await initial(held_case,old)
    original=tmp_path/'cached.txt';original.write_text('Pinned original bytes.')
    candidate=tmp_path/'candidate.json';receipt=tmp_path/'receipt.json';url='https://example.org/exact'
    core.write(candidate,{'id':'paper','url':url,'format':'text','role':'primary_paper',
        'identity':{'pmid':'1','title':'Paper'},'rationale':'Exact public source.'})
    core.write(receipt,{'requested_url':url,'final_url':url,'sha256':core.sha(original),'content_type':'text/plain'})
    policy={'max_rounds':1,'strategy':'new_review_request','cached_documents':[{
        'original':{'path':str(original),'sha256':core.sha(original)},
        'document':{'path':str(candidate),'sha256':core.sha(candidate)},
        'receipt':{'path':str(receipt),'sha256':core.sha(receipt)},'paper':{},'source_id':'paper:recovered'}]}
    finder=AsyncMock();dispatch=AsyncMock()
    with patch('experiments.card_methods_full_20261112.located_documents.register',side_effect=failure) as register:
        result=await recovery.recover_review(row,old,tmp_path/'recovery',held_case[-1],policy,
            investigator=finder,dispatcher=dispatch)
    assert register.call_count==1 and result['status']=='failed' and result['failure_category']==category
    assert result['recovery_failure']['exception_type']==type(failure).__name__
    finder.assert_not_awaited();dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_checkpoint_entry_has_no_preliminary_model_calls_and_replays(held_case,tmp_path,monkeypatch):
    from functools import partial
    old=tmp_path/'original';row=await initial(held_case,old);row['attempt_directory']=str(old)
    checkpoint=tmp_path/'checkpoint.json';core.write(checkpoint,{'cards':{'example':row}})
    manuscript=source(tmp_path/'new.txt','New registered source supplied for the exact held question.')
    async def lookup(*args):
        assert len(held_case[-1].calls)==2
        return {'sources':{'supplement:first':manuscript}}
    cache=AsyncMock(side_effect=lookup)
    monkeypatch.setattr(recovery,'recover_review',partial(recovery.recover_review,cache_lookup=cache))
    policy=tmp_path/'policy.json';core.write(policy,{'max_rounds':1,'state_directory':str(tmp_path/'episodes')})
    manifest=tmp_path/'manifest.json';core.write(manifest,{'purpose':'recovery_validation','checkpoints':[{
        'method_id':'example','checkpoint':{'path':str(checkpoint),'sha256':core.sha(checkpoint),'pointer':'/cards/example'}}]})
    first=await recovery.run_completed(manifest,policy,tmp_path/'run',workers=1,worker=held_case[-1])
    assert first['counts']=={'research_needed':1} and len(held_case[-1].calls)==4 and cache.await_count==1
    again=await recovery.run_completed(manifest,policy,tmp_path/'resume',workers=1,worker=held_case[-1])
    assert again['cards']['example']['recovery_reused'] and len(held_case[-1].calls)==4 and cache.await_count==1


@pytest.mark.asyncio
async def test_completed_entry_rejects_a_synthetic_outcome_before_cache_or_worker(held_case,tmp_path,monkeypatch):
    old=tmp_path/'original';row=await initial(held_case,old)
    row.update(status='supported',attempt_directory=str(old))  # Contradicts the actual preserved research review.
    checkpoint=tmp_path/'checkpoint.json';core.write(checkpoint,row)
    manifest=tmp_path/'manifest.json';core.write(manifest,{'purpose':'recovery_validation','checkpoints':[{
        'method_id':'example','checkpoint':{'path':str(checkpoint),'sha256':core.sha(checkpoint)}}]})
    policy=tmp_path/'policy.json';core.write(policy,{'max_rounds':1})
    handoff=AsyncMock();monkeypatch.setattr(recovery,'recover_review',handoff)
    result=await recovery.run_completed(manifest,policy,tmp_path/'run',worker=held_case[-1])
    assert result['counts']=={'failed':1} and len(held_case[-1].calls)==2
    handoff.assert_not_awaited()
