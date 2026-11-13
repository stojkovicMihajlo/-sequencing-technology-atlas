import asyncio
import errno
from http.client import IncompleteRead
import json
from pathlib import Path
from types import SimpleNamespace
from ssl import SSLError
from urllib.error import HTTPError, URLError
import pytest

from experiments.card_methods_full_20261112 import recover as runner
from experiments.card_methods_full_20261112.preflight import audit


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.mark.parametrize('extra,held', [(False, True), (True, False)])
def test_evidence_request_is_not_retried_without_new_evidence(tmp_path, monkeypatch, extra, held):
    old = tmp_path / 'old'
    put(old / 'review.json', {'checks': [{'action': 'research', 'target': 'operation:x', 'rationale': 'Read supplement.'}]})
    state = tmp_path / 'state.json'
    put(state, {'cards': {'a': {'status': 'research_needed', 'attempt_directory': str(old)}}})
    audit_path = tmp_path / 'audit'; audit(state, audit_path)
    existing = tmp_path / 'existing.json'; put(existing, {})
    monkeypatch.setattr(runner, 'build_selection', lambda *_: {'methods': [
        {'method_id': 'a', 'primary_paper_id': 'pmid-1', 'canonical_directory': 'methods/a'}]})
    monkeypatch.setattr(runner, 'prepare_method', lambda *_args, **_kwargs: ({}, {}, []))
    # New evidence gets an actual pinned file. Recovery does not claim that it answers the question.
    paper = tmp_path / 'supplement.txt'; paper.write_text('Example supplemental procedure.')
    source = {'kind': 'text', 'content_level': 'fulltext', 'path': str(paper),
              'sha256': runner.sha(paper), 'text': paper.read_text()}
    monkeypatch.setattr(runner, 'recover_evidence', lambda *_args, **_kwargs: {'supplement:1': source} if extra else {})
    output = tmp_path / 'out'
    runner.recover(state, audit_path, tmp_path, tmp_path, Path('unused'), Path('unused'), existing, output, 'unused@example.org')
    assert ('a' in runner.read(output / 'holds.json')) == held
    assert runner.read(output / 'results.json')['a']['scientific_gap_resolved'] is False
    assert ('a' in runner.read(output / 'registered_additions.json')) == extra


def test_only_actual_provider_error_creates_policy_hold(tmp_path):
    state = tmp_path / 'state.json'; rows = {}
    for name, event in [('a', {'type': 'error', 'message': 'This content was flagged for possible biological risk.'}),
                        ('b', {'type': 'item.completed', 'text': 'flagged for possible biological risk'})]:
        attempt = tmp_path / name; stage = attempt / 'native'
        put(attempt / 'stages.json', [{'path': str(stage)}])
        put(stage / 'events.jsonl', event)
        rows[name] = {'status': 'failed', 'attempt_directory': str(attempt)}
    put(state, {'cards': rows})
    audit(state, tmp_path / 'audit')
    holds = runner.read(tmp_path / 'audit/intervention_holds.json')
    assert list(holds) == ['a']
    assert holds['a']['evidence']['sha256'] == runner.sha(tmp_path / 'a/native/events.jsonl')


def investigation_case(method='m',pmid='123'):
    return {'selection':{'method_id':method,'canonical_directory':'methods/'+method},
            'paper':{'paper_id':'pmid-'+pmid,'title':'Assigned original paper '+pmid,'doi':['10.9999/example-'+pmid]},
            'audit':{'research_requests':[{'target':'record','question':'Read the original evidence.'}]},
            'hold':{'failure_category':'evidence_request_unresolved','reason':'Original evidence needed.'}}


@pytest.fixture
def offline_investigator(monkeypatch):
    """Keep the actual finder/Findings/registration adapter; replace transport only."""
    from experiments.card_methods_full_20261112 import investigate_holds as investigator
    class Worker:
        def __init__(self,**kwargs):pass
        async def run(self,prompt,attempt,**kwargs):
            case=investigator.read(attempt/'request.json')['cases'][0]
            paper=case['assigned_paper'];pmid=paper['paper_id'].removeprefix('pmid-')
            investigator.write(attempt/'final.txt',{'method_id':case['method']['method_id'],
                'documents':[{'id':'original','url':'https://repository.example/'+pmid+'.pdf',
                    'role':'primary_paper','format':'pdf','identity':{'pmid':pmid,'doi':paper['doi'][0],'title':paper['title']},
                    'rationale':'Offline location fixture, not scientific evidence.'}],
                'searches':[{'query':'Exact assigned paper','status':'found','note':'Offline transport fixture.'}]})
            return SimpleNamespace(exit_code=0,error=None,usage={},elapsed_seconds=0)
    monkeypatch.setenv('SLURM_JOB_ID','offline-no-model')
    monkeypatch.setattr(investigator,'CodexWorker',Worker)
    monkeypatch.setattr(investigator,'prepare_method',lambda *_:({}, {}, []))
    monkeypatch.setattr(investigator,'assess_archived_attempt',lambda *_:SimpleNamespace(complete=True))
    return investigator


def invoke_investigation_adapter(tmp_path):
    from genomics_discovery.illumina.review_recovery import investigate_request
    request={'method_id':'m','fingerprint':'offline-request',
             'questions':[{'target':'record','question':'Read the original evidence.'}]}
    policy={'new_route':{'description':'Declared repository lead'},'case':investigation_case(),
            'library':str(tmp_path/'library'),'vault':str(tmp_path/'vault')}
    return asyncio.run(investigate_request(request,{},policy,tmp_path/'adapter'))


@pytest.mark.parametrize('exception,category',[
    (OSError(errno.ENOSPC,'No space left on device'),'host_storage_failure'),
    (OSError(errno.EDQUOT,'Disk quota exceeded'),'host_storage_failure'),
    (TypeError('Broken registration implementation'),'software_error'),
    (KeyError('missing_adapter_field'),'software_error'),
    (PermissionError(errno.EACCES,'Local output permission denied'),'software_error'),
])
def test_registration_execution_errors_reach_real_review_adapter(tmp_path,monkeypatch,offline_investigator,exception,category):
    def broken(*args,**kwargs):raise exception
    monkeypatch.setattr(offline_investigator,'register',broken)
    result=invoke_investigation_adapter(tmp_path)
    assert result['sources']=={}
    assert result['failure']['category']=='source_execution_failure'
    assert result['failure']['failure_category']==category
    assert result['failure']['exception_type']==type(exception).__name__
    assert result['failure']['stage']=='document_registration'
    state=offline_investigator.read(tmp_path/'adapter/investigation/state.json')['publications']['pmid-123']
    assert state['status']=='investigation_failed';assert state['failed_document_id']=='original'
    if category=='host_storage_failure':assert state['host_failure']['source_availability']=='not_assessed'


@pytest.mark.parametrize('exception',[
    ValueError('Original publication identity differs'),
    URLError('No public original at this route'),
    HTTPError('https://repository.example/123.pdf',403,'Forbidden',None,None),
    TimeoutError('Public source timed out'),
    ConnectionResetError('Public source closed the download'),
    IncompleteRead(b'partial original bytes'),
    SSLError('Public source TLS failure; retain hold'),
])
def test_expected_source_errors_stay_holds_through_real_review_adapter(tmp_path,monkeypatch,offline_investigator,exception):
    def unavailable(*args,**kwargs):raise exception
    monkeypatch.setattr(offline_investigator,'register',unavailable)
    result=invoke_investigation_adapter(tmp_path)
    assert 'failure' not in result;assert result['sources']=={}
    assert result['holds'][0]['status']=='retrieval_or_registration_held'
    assert result['holds'][0]['exception_type']==type(exception).__name__
    assert result['holds'][0]['failure_category']=='source_access_or_identity_hold'


def test_registration_software_failure_is_isolated_to_its_publication(tmp_path,monkeypatch,offline_investigator):
    parent=tmp_path/'inputs';put(parent/'recovery_02/registered_additions.json',{})
    snapshot=tmp_path/'snapshot.json';put(snapshot,{'source_release':str(parent),
        'cases':[investigation_case('broken','123'),investigation_case('other','456')]})
    def register(document,paper,output,**kwargs):
        if paper['paper_id']=='pmid-123':raise TypeError('One broken registration')
        output.mkdir(parents=True);path=output/'reading.txt';path.write_text('Synthetic registered reading fixture.')
        return {'kind':'text','content_level':'fulltext','document_role':'primary_article',
                'path':str(path),'sha256':offline_investigator.sha(path),'text':path.read_text()}
    monkeypatch.setattr(offline_investigator,'register',register)
    output=tmp_path/'run';asyncio.run(offline_investigator.run(snapshot,tmp_path/'library',tmp_path/'vault',output,workers=2))
    state=offline_investigator.read(output/'state.json')['publications']
    assert state['pmid-123']['status']=='investigation_failed'
    assert state['pmid-456']['status']=='sources_recovered_need_review'
    additions=offline_investigator.read(output/'registered_additions.json')
    assert set(additions)=={'other'}


def test_actual_terminal_provider_classifier_is_preserved_by_adapter(tmp_path,monkeypatch,offline_investigator):
    message=('This content was flagged for possible biological risk. If this seems wrong, '
        'try rephrasing your request. We are continuously refining our work in detecting '
        'biological risk, and you can read more about our approach in our blog post: '
        'https://openai.com/index/preparing-for-future-ai-capabilities-in-biology')
    async def refused(self,prompt,attempt,**kwargs):
        offline_investigator.write(attempt/'result.json',{'exit_code':1,'thread_id':'offline-provider-fixture'})
        events=[{'type':'thread.started','thread_id':'offline-provider-fixture'},
                {'type':'turn.failed','error':{'message':message}}]
        (attempt/'events.jsonl').write_text(''.join(json.dumps(event)+'\n' for event in events))
        return SimpleNamespace(exit_code=1,error=message,usage={},elapsed_seconds=0)
    monkeypatch.setattr(offline_investigator.CodexWorker,'run',refused)
    monkeypatch.setattr(offline_investigator,'register',lambda *_args,**_kwargs:pytest.fail('Provider refusal must stop registration'))
    result=invoke_investigation_adapter(tmp_path)
    assert result['failure']['category']=='provider_policy_block'
    assert result['failure']['diagnostic']['category']=='provider_biological_risk_block'
    assert result['failure']['stage']=='source_finder'
