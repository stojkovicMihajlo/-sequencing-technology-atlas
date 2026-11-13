"""Prepared dispatch integrity and isolation, not scientific acceptance tests."""
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
import json
import pytest
from genomics_discovery.illumina import prepared


def pin(path,value):
    prepared.write(path,value)
    return {'path':str(path),'sha256':prepared.sha(path)}


def test_workers_are_explicit_and_bounded():
    for n in (1,25,50):prepared.check_workers(n)
    for n in (0,51,True,'25'):
        with pytest.raises(ValueError):prepared.check_workers(n)


def test_registered_text_renaming_or_whitespace_is_not_new_evidence():
    assert prepared.text_identity({'text':'A  supported\npassage'})==prepared.text_identity({'text':'A supported passage'})
    assert prepared.text_identity({'text':'A different fact'})!=prepared.text_identity({'text':'A supported passage'})


def test_reconcile_explicit_later_state_keeps_method_scope_and_history(tmp_path):
    runtime=pin(tmp_path/'runtime.json',{'files':{}})
    a=pin(tmp_path/'old.json',{'cards':{'m':{'status':'research_needed'}}})
    b=pin(tmp_path/'new.json',{'cards':{'m':{'status':'supported'}}})
    config={'states':[{**x,'runtime_manifest':runtime} for x in (a,b)],'expected_methods':1,
            'provider_attempts':{},'expected_counts':{'supported':1}}
    with patch.object(prepared,'revalidate_completed') as verify:result=prepared.reconcile(config)
    assert result['cards']['m']['status']=='supported'
    assert len(result['cards']['m']['provenance'])==2
    verify.assert_called_once()
    (tmp_path/'new.json').write_text('{}')
    with pytest.raises(ValueError,match='Pinned input'):prepared.reconcile(config)


def test_provider_hold_requires_actual_policy_diagnostic(tmp_path):
    runtime=pin(tmp_path/'runtime.json',{'files':{}})
    state=pin(tmp_path/'state.json',{'cards':{'m':{'status':'failed'}}})
    attempt=tmp_path/'attempt';attempt.mkdir();(attempt/'events.jsonl').write_text('record')
    config={'states':[{**state,'runtime_manifest':runtime}],'expected_methods':1,
        'provider_attempts':{'m':{'path':str(attempt),'events_sha256':prepared.sha(attempt/'events.jsonl')}},
        'expected_counts':{'provider_policy_block':1}}
    with patch.object(prepared,'classify_archived_terminal_failure',return_value={'category':'provider_quota_error'}):
        with pytest.raises(ValueError,match='Not the recorded'):prepared.reconcile(config)
    with patch.object(prepared,'classify_archived_terminal_failure',return_value={'category':'provider_biological_risk_block'}):
        assert prepared.reconcile(config)['cards']['m']['status']=='provider_policy_block'


@pytest.mark.asyncio
async def test_dispatch_excludes_accepted_and_isolates_failures(tmp_path,monkeypatch):
    monkeypatch.setenv('SLURM_JOB_ID','offline-no-model');bundles=[]
    for mid,status in [('accepted','supported'),('bad','research_needed'),('ready','research_needed')]:
        bundles.append({'method_id':mid,'prior_status':status,'reason':'New registered evidence',
            'card':pin(tmp_path/(mid+'_card.json'),{'record_id':mid}),
            'registry':pin(tmp_path/(mid+'_sources.json'),{})})
    manifest=tmp_path/'manifest.json';prepared.write(manifest,{'bundles':bundles});calls=[]
    async def extract(card,*args,**kwargs):
        calls.append(card['record_id'])
        if card['record_id']=='bad':raise ValueError('One bad method')
        assert kwargs['max_revisions']==2 and kwargs['representation']=='illumina_diagram/1.1'
        return {'status':'supported'}
    with patch.object(prepared,'extract_registered_workflow',side_effect=extract):
        result=await prepared.run_prepared(manifest,tmp_path/'run',workers=25,worker=object())
    assert set(calls)=={'bad','ready'} and result['counts']=={'failed':2,'supported':1}
    assert 'Accepted methods' in result['cards']['accepted']['reason']


@pytest.mark.asyncio
async def test_non_slurm_fails_before_output(tmp_path,monkeypatch):
    monkeypatch.delenv('SLURM_JOB_ID',raising=False)
    with pytest.raises(ValueError,match='Slurm'):await prepared.run_prepared(tmp_path/'unused',tmp_path/'never')
    assert not (tmp_path/'never').exists()


@pytest.mark.asyncio
async def test_prepared_mixed_review_seed_goes_directly_to_one_correction(tmp_path,monkeypatch):
    monkeypatch.setenv('SLURM_JOB_ID','offline-no-model')
    seed={'directory':str(tmp_path/'preserved'),'pins':{'review.json':'a'*64}}
    bundle={'method_id':'mixed','prior_status':'needs_revision','reason':'Known revise plus research findings.',
        'card':pin(tmp_path/'card.json',{'record_id':'mixed'}),'registry':pin(tmp_path/'registry.json',{}),
        'revision_seed':seed}
    manifest=tmp_path/'manifest.json';prepared.write(manifest,{'bundles':[bundle]})
    from unittest.mock import AsyncMock
    dispatch=AsyncMock(return_value={'status':'research_needed'})
    with patch.object(prepared,'extract_registered_workflow',dispatch):
        result=await prepared.run_prepared(manifest,tmp_path/'run',workers=1,max_revisions=2,worker=object())
    assert result['cards']['mixed']['status']=='research_needed'
    assert dispatch.await_count==1 and dispatch.call_args.kwargs['revision_seed']==seed
    assert dispatch.call_args.kwargs['max_revisions']==1


@pytest.mark.parametrize('fault',[None,'quote','field','question','scope'])
def test_adjudication_binds_questions_fields_and_literal_source_premises(fault):
    value={'unknowns':[{'question':'Which reported route applies?','blocking':True}]}
    candidate=SimpleNamespace(model_dump=lambda **kw:value)
    request={'question':'Does the passage resolve this route?','scope_rationale':'Keep the focal method/variant.',
        'field_bindings':[{'pointer':'/unknowns/0','value_sha256':prepared.digest(value['unknowns'][0])}],
        'source_premises':[{'source_id':'paper:1','quote':'Reported focal route.'}]}
    if fault=='quote':request['source_premises'][0]['quote']='Invented route.'
    elif fault=='field':request['field_bindings'][0]['value_sha256']='0'*64
    elif fault in {'question','scope'}:request['question' if fault=='question' else 'scope_rationale']=''
    if fault:
        with pytest.raises(ValueError):prepared.validate_adjudication_request(request,candidate,{'paper:1':{'text':'Reported focal route.'}})
    else:prepared.validate_adjudication_request(request,candidate,{'paper:1':{'text':'Reported focal route.'}})


@pytest.mark.asyncio
@pytest.mark.parametrize('fresh_outcome',['research_needed','needs_revision','supported'])
@pytest.mark.parametrize('auto_recovery',[False,True])
async def test_real_adjudication_review_routes_only_actual_repair_verdict(tmp_path,monkeypatch,fresh_outcome,auto_recovery):
    from dataclasses import asdict
    import copy
    from genomics_discovery.execution.codex_worker import WorkerResult
    from tests.illumina.test_illumina_diagrams import example
    from tests.illumina.test_illumina_batch_controls import png
    from genomics_discovery.illumina.representation import StructuredDiagramExtraction,StructuredDiagramReview
    # Reuse the existing small source/graph fixture, then add typed identities.
    raw,sources,image,_=example.__wrapped__()
    raw=copy.deepcopy(raw);sources=copy.deepcopy(sources);raw['format_version']='illumina_diagram/1.1'
    for st in raw['states']:
        st['properties']=[dict(id='identity',subject='self',property='identity',value=st['label'],evidence=[image])]
        if st['kind']=='data':st['properties'].append(dict(id='type',subject='self',property='data_type',value='sequence_reads',evidence=[image]))
    for op in raw['operations']:
        op.update(resolution='single_event',input_roles=[dict(state_id=s,roles=['sample'],explanation='Shown input',evidence=[image]) for s in op['inputs']],
            effects=[dict(aspect='selection' if op['id']=='split' else 'readout',input_state_ids=op['inputs'],output_state_ids=op['outputs'],statement=op['description'],evidence=[image])],
            output_grouping='one_output' if len(op['outputs'])==1 else 'distinct_outputs')
    candidate=StructuredDiagramExtraction.model_validate(raw)
    source_image=tmp_path/'source.png';source_image.write_bytes(png(b'\x00\x00\x00\xff'))
    sources[image['source_id']].update(path=str(source_image),sha256=prepared.sha(source_image))
    def review(action):
        checks=[]
        for target in sorted(prepared.review_targets(candidate,format_version='illumina_diagram_review/1.2')):
            ev=candidate.paper_matches[0].evidence if target.startswith('paper:') else [image]
            decision=action if target=='record' else 'accept'
            checks.append(dict(target=target,verdict='supported' if decision=='accept' else 'unresolved',action=decision,
                rationale='Synthetic source judgment, not biological truth.',evidence=ev))
        return StructuredDiagramReview(record_id=candidate.record_id,candidate_sha256=prepared.digest(candidate),
            inspected_sources=candidate.inspected_sources,checks=checks)
    old=tmp_path/'old';old.mkdir();last=old/'reviewer';last.mkdir()
    (last/'stage_prompt.txt').write_text('CALLER CONTEXT\n{}')
    card={'record_id':candidate.record_id,'source_mode':'diagram'}
    for n,v in [('card.json',card),('source_registry.json',sources),('extraction.json',candidate.model_dump(mode='json')),
        ('review.json',review('research').model_dump(mode='json')),('stages.json',[{'path':str(last)}])]:prepared.write(old/n,v)
    prior={'status':'research_needed','attempt_directory':str(old),'artifact_pins':{n:prepared.sha(old/n) for n in
        ['card.json','source_registry.json','extraction.json','review.json','stages.json']}}
    quote=candidate.paper_matches[0].evidence[0]
    request={'question':'Reassess this exact source claim.','scope_rationale':'Same method and scope.',
        'field_bindings':[{'pointer':'/scope','value_sha256':prepared.digest(candidate.scope)}],
        'source_premises':[{'source_id':quote.source_id,'quote':quote.quote}]}
    config={'cases':[{'method_id':candidate.record_id,'prior':prior,'request':request}]}
    if auto_recovery:config['recovery']={'max_rounds':1,'state_directory':str(tmp_path/'cohort_recovery')}
    manifest=tmp_path/'cases.json';prepared.write(manifest,config)
    class Worker:
        async def run(self,prompt,attempt,*,image_paths=()):
            doc=review({'research_needed':'research','needs_revision':'revise','supported':'accept'}[fresh_outcome])
            prepared.write(attempt/'artifact.json',doc.model_dump(mode='json'));(attempt/'prompt.txt').write_text(prompt)
            usage={'input_tokens':1,'output_tokens':1};result=WorkerResult(0,'fresh-adjudication',usage,0.1,None)
            events=[{'type':'thread.started','thread_id':result.thread_id},{'type':'turn.started'},{'type':'turn.completed','usage':usage}]
            (attempt/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events));(attempt/'final.txt').write_text('Synthetic transport only.')
            prepared.write(attempt/'result.json',asdict(result))
            prepared.write(attempt/'command.json',{'argv':['fake','exec',*[s for p in image_paths for s in ['--image',str(p)]],'-'],
                'image_inputs':[{'path':str(p),'sha256':prepared.sha(p)} for p in image_paths]})
            return result
    async def corrected(*args,**kw):
        assert kw['max_revisions']==1 and 'revision_seed' in kw
        assert prepared.read(kw['revision_seed']['directory']+'/review.json')['checks']
        return {'status':'supported'}
    from genomics_discovery.illumina import review_recovery
    from unittest.mock import AsyncMock
    from functools import partial
    cache=AsyncMock(return_value={'sources':{}})
    monkeypatch.setattr(prepared,'recover_review',partial(review_recovery.recover_review,cache_lookup=cache))
    monkeypatch.setenv('SLURM_JOB_ID','offline-no-model')
    with patch.object(prepared,'extract_registered_workflow',side_effect=corrected) as repair:
        result=await prepared.adjudicate(manifest,tmp_path/'adjudication',workers=1,worker=Worker())
    row=result['cards'][candidate.record_id]
    assert row['status']==('supported' if fresh_outcome=='needs_revision' else fresh_outcome),row
    assert repair.call_count==(1 if fresh_outcome=='needs_revision' else 0)
    assert prepared.read(old/'review.json')['checks']==review('research').model_dump(mode='json')['checks']
    if fresh_outcome=='research_needed':
        assert prepared.read(row['attempt_directory']+'/review.json')==review('research').model_dump(mode='json')
        assert row['attempt_directory']!=str(old)
    if auto_recovery and fresh_outcome=='research_needed':
        assert cache.await_count==1 and row['recovery']['status']=='held_no_new_evidence'
        assert cache.call_args.args[0]['original_directory']==row['attempt_directory']
        assert cache.call_args.args[0]['review_raw_sha256']==prepared.sha(Path(row['attempt_directory'])/'review.json')
    else:cache.assert_not_awaited()
