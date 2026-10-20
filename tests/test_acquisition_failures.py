"""Failure diagnostics never promote failed native work or echo raw messages."""

from dataclasses import asdict
import errno
import json
import os
from pathlib import Path

import pytest

from genomics_discovery.curation.codex_worker import WorkerResult, assess_archived_attempt
from genomics_discovery.curation.extraction_batch.storage import read, sha, write
from genomics_discovery.curation.source_acquisition import acquire
from genomics_discovery.curation.source_acquisition.failures import native_failure
from test_source_acquisition import Finder, document, fetcher, findings, jats, worklist

POLICY = "Invalid prompt: we've limited access to this content for safety reasons."
SECRET = 'SECRET_SYSTEM_PROMPT sk-sensitive-placeholder Bearer secret-value'


class FailedFinder(Finder):
    def __init__(self,kind):
        super().__init__({'assay':findings('assay',[])})
        self.kind=kind

    async def run(self,prompt,attempt):
        if self.kind=='raised':raise RuntimeError(SECRET)
        result=await super().run(prompt,attempt)
        events=[{'type':'thread.started','thread_id':result.thread_id},{'type':'turn.started'}]
        if self.kind in {'policy','execution','transport'}:
            message=POLICY+' '+SECRET if self.kind=='policy' else SECRET
            events.extend([{'type':'error','message':message},
                           {'type':'turn.failed','error':{'message':message}}])
            error=('Codex transport failed (ConnectionError); inspect attempt archives'
                   if self.kind=='transport' else 'Codex exited with code 1; see stderr.log and events.jsonl')
            result=WorkerResult(1,result.thread_id,{},.1,error)
        elif self.kind=='timeout':
            result=WorkerResult(124,result.thread_id,{},.1,'Codex exceeded the 60-second attempt timeout')
        elif self.kind=='incomplete':
            result=WorkerResult(0,result.thread_id,{},.1,None)
        elif self.kind=='malformed_completion':
            events.extend([{'type':'error','message':POLICY+' '+SECRET},
                           {'type':'turn.completed','usage':'invalid non-dictionary usage'}])
        else:events.append({'type':'turn.completed','usage':result.usage})
        (attempt/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))
        write(attempt/'result.json',asdict(result))
        if self.kind=='invalid_jsonl':
            with (attempt/'events.jsonl').open('a') as handle:handle.write(SECRET+'\n')
        elif self.kind=='empty_final':(attempt/'final.txt').write_text('')
        elif self.kind=='missing_events':(attempt/'events.jsonl').unlink()
        elif self.kind=='prompt':(attempt/'prompt.txt').write_text(SECRET)
        elif self.kind=='invalid_artifact':(attempt/'artifact.json').write_text(SECRET)
        elif self.kind=='invalid_schema':write(attempt/'artifact.json',{'secret-field':SECRET})
        elif self.kind=='missing_artifact':(attempt/'artifact.json').unlink()
        elif self.kind=='method':
            artifact=read(attempt/'artifact.json');artifact['method_id']='wrong-method';write(attempt/'artifact.json',artifact)
        elif self.kind=='returned_thread':
            return WorkerResult(0,'different-thread',result.usage,.1,None)
        elif self.kind=='returned_usage':
            return WorkerResult(0,result.thread_id,{'input_tokens':999},.1,None)
        return result


@pytest.mark.parametrize('kind,expected',[
    ('policy','provider_policy_rejection'),('timeout','native_timeout'),
    ('transport','native_transport_failure'),('execution','native_execution_failure'),
    ('raised','native_execution_failure'),('incomplete','native_archive_invalid'),
    ('invalid_jsonl','native_archive_invalid'),('empty_final','native_archive_invalid'),('missing_events','native_archive_invalid'),
    ('malformed_completion','native_archive_invalid'),
    ('prompt','native_archive_invalid'),('returned_thread','native_archive_invalid'),
    ('returned_usage','native_archive_invalid'),('invalid_artifact','finder_output_invalid'),
    ('invalid_schema','finder_output_invalid'),('missing_artifact','finder_output_invalid'),
    ('method','finder_output_invalid')])
async def test_failure_classes_remain_failed_and_have_bounded_safe_archive_evidence(tmp_path,kind,expected):
    tasks=worklist(tmp_path)
    def forbidden_download(url):pytest.fail('A rejected finder cannot trigger host retrieval')
    result=await acquire(tasks,tmp_path/'run',worker=FailedFinder(kind),downloader=forbidden_download)
    method=result['methods']['assay']
    assert method['status']=='failed' and method['packet'] is None
    assert result['registration'] is None and result['scientific_resolution'] is False
    failure=method['failure']
    assert failure['class']==expected and failure['stage']=='finder'
    assert failure['source_availability']=='not_assessed'
    assert len(failure['message'])<160
    assert SECRET not in json.dumps(method) and 'sk-sensitive-placeholder' not in json.dumps(method)
    assert 'secret-field' not in json.dumps(method)
    pointer=failure['archive_pointer'];path=Path(pointer['path'])
    if pointer['exists']:assert pointer['sha256']==sha(path)
    else:assert pointer['sha256'] is None
    if kind=='policy':
        assert pointer['line']==3 and pointer['json_pointer']=='/message'
        event=json.loads(path.read_text().splitlines()[pointer['line']-1])
        assert event['type']=='error' and event['message'].startswith(POLICY)
    if kind=='timeout':assert path.name=='result.json' and pointer['json_pointer']=='/error'
    if kind=='invalid_jsonl':assert path.name=='events.jsonl' and pointer['line']==4
    if kind=='malformed_completion':
        assert path.name=='events.jsonl' and pointer['line']==4 and pointer['json_pointer']=='/usage'
    if kind=='empty_final':assert path.name=='final.txt'
    if kind=='missing_events':assert path.name=='events.jsonl' and pointer['exists'] is False
    if kind=='prompt':assert path.name=='prompt.txt'
    if kind=='method':assert pointer['json_pointer']=='/method_id'
    if kind=='missing_artifact':assert path.name=='artifact.json' and pointer['exists'] is False
    assert read(tmp_path/'run/state.json')['methods']['assay']==method
    assert read(tmp_path/'run/replacements.json')['replacements']=={}


async def test_source_unavailability_is_not_a_native_failure_or_a_claim_that_paper_is_missing(tmp_path):
    tasks=worklist(tmp_path)
    report=findings('assay',[document()])
    def unavailable(url):raise OSError('Public endpoint unavailable in this attempt')
    result=await acquire(tasks,tmp_path/'run',worker=Finder({'assay':report}),downloader=unavailable)
    method=result['methods']['assay']
    assert method['status']=='sources_retained_unregistered' and 'failure' not in method
    assert method['documents'][0]['status']=='unavailable'
    assert 'Public endpoint unavailable' in method['documents'][0]['error']
    assert result['registration'] is None and not result['scientific_resolution']


async def test_valid_unavailable_searches_keep_existing_nonfailure_path(tmp_path):
    tasks=worklist(tmp_path);report=findings('assay',[])
    report['searches'][0].update(status='access_limited',note='Access was limited in this search; availability is not established.')
    result=await acquire(tasks,tmp_path/'run',worker=Finder({'assay':report}))
    method=result['methods']['assay']
    assert method['status']=='sources_retained_unregistered' and 'failure' not in method
    assert read(tmp_path/'run/methods/assay/acquisition.json')['searches'][0]['status']=='access_limited'


@pytest.mark.parametrize('number', [errno.ENOSPC, errno.EDQUOT])
@pytest.mark.parametrize('phase', ['original', 'rendition'])
async def test_host_storage_exhaustion_is_not_source_unavailability_or_partial_registration(
    tmp_path, monkeypatch, number, phase,
):
    from genomics_discovery.curation import cached_documents
    from genomics_discovery.curation.source_acquisition import runtime

    tasks = worklist(tmp_path)
    docs = [document(), document('supplement', url='https://example.org/supp.txt',
        role='supplement', fmt='text', linked_from='https://example.org/article.xml')]
    finder = Finder({'assay': findings('assay', docs)})
    fetch = fetcher({'https://example.org/article.xml': jats(),
                     'https://example.org/supp.txt': b'Rotate nuclei at 4 C.'})
    if phase == 'original':
        original_write = runtime.atomic_bytes

        def limited_write(path, content):
            if path.name == 'supplement.txt':
                raise OSError(number, 'Injected host capacity failure')
            return original_write(path, content)

        monkeypatch.setattr(runtime, 'atomic_bytes', limited_write)
    else:
        def limited_rendition(*args, **kwargs):
            raise OSError(number, 'Injected host capacity failure')

        monkeypatch.setattr(cached_documents, '_publish_rendition', limited_rendition)
    result = await acquire(tasks, tmp_path/'run', worker=finder, downloader=fetch)
    method = result['methods']['assay']
    assert method['status'] == 'failed' and method['packet'] is None
    assert method['failure'] == {
        'stage': 'host_storage', 'class': 'host_storage_failure',
        'message': 'Host storage capacity prevented retaining or inspecting source bytes.',
        'errno': number, 'source_availability': 'not_assessed'}
    assert finder.calls == ['assay'] and len(fetch.calls) == 2
    assert result['registration'] is None and result['scientific_resolution'] is False
    assert read(tmp_path/'run/replacements.json')['replacements'] == {}
    # The earlier valid primary and finder archive survive, without a packet
    # claiming that the capacity-limited acquisition has finished.
    assert (tmp_path/'run/methods/assay/originals/primary/primary.xml').read_bytes() == jats()
    assert (tmp_path/'run/methods/assay/finder/events.jsonl').is_file()


def save_events(attempt,events):
    attempt.mkdir()
    (attempt/'events.jsonl').write_text(''.join(json.dumps(e)+'\n' for e in events))


def test_policy_words_in_source_tool_output_or_resolved_error_cannot_masquerade_as_terminal_policy_block(tmp_path):
    attempt=tmp_path/'attempt'
    save_events(attempt,[{'type':'thread.started','thread_id':'thread'},
        {'type':'item.completed','item':{'type':'command_execution','aggregated_output':POLICY}},
        {'type':'error','message':POLICY},{'type':'turn.completed','usage':{}}])
    failure=native_failure(attempt,WorkerResult(0,'thread',{},.1,None),prompt_changed=True)
    assert failure['class']=='native_archive_invalid'
    assert failure['archive_pointer']['path']==str(attempt/'prompt.txt')


@pytest.mark.parametrize('event,pointer',[
    ({'type':'turn.failed','error':{'message':POLICY}},'/error/message'),
    ({'type':'turn.failed','error':POLICY},'/error'),
    ({'type':'error','message':{'message':POLICY}},'/message/message')])
def test_event_pointers_match_actual_control_event_shape(tmp_path,event,pointer):
    attempt=tmp_path/'attempt';save_events(attempt,[event])
    failure=native_failure(attempt,WorkerResult(1,'thread',{},.1,'Codex exited with code 1'))
    assert failure['class']=='provider_policy_rejection'
    assert failure['archive_pointer']['json_pointer']==pointer
    assert failure['archive_pointer']['line']==1


def test_actual_cip_tap_archive_is_properly_rejected_and_diagnosed_without_changing_it():
    configured=os.environ.get('ATLAS_ACQUISITION_FAILURE_ARCHIVE')
    if not configured:pytest.skip('Set ATLAS_ACQUISITION_FAILURE_ARCHIVE to the frozen CIP-TAP finder archive')
    attempt=Path(configured).resolve()
    before={path:sha(path) for path in attempt.iterdir() if path.is_file()}
    raw=read(attempt/'result.json');result=WorkerResult(**raw)
    archive=assess_archived_attempt(attempt)
    assert not archive.complete and raw['exit_code']==1
    failure=native_failure(attempt,result,archive=archive)
    assert failure['class']=='provider_policy_rejection'
    pointer=failure['archive_pointer']
    assert pointer['path']==str(attempt/'events.jsonl')
    assert pointer['line']==39 and pointer['json_pointer']=='/message'
    assert pointer['sha256']=='4b09f29df75c1cb75d00162a9b9457331b363e3df96b5ba46fb0c78983aecca4'
    assert before=={path:sha(path) for path in attempt.iterdir() if path.is_file()}
