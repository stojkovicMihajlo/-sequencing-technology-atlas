"""Prepared dispatch integrity and isolation, not scientific acceptance tests."""
from unittest.mock import patch
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
