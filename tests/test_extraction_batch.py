"""Durable controller integration using archived fake native-worker turns."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from genomics_discovery.curation.extraction_batch import BatchConfig, plan, run, status
from genomics_discovery.curation.extraction_batch.__main__ import main
from genomics_discovery.curation.extraction_batch.storage import (
    ControllerBusy, PinDrift, controller_lock, read, sha, write,
)
from test_workflow_extraction_runtime import FakeWorker
from test_workflow_extraction_runtime import source_case as source_case


def inventory(tmp_path, source_case, ids=("good",), *, missing=False):
    jobs=[{"id": ident, "status":"ready", "packet":str(source_case[1])} for ident in ids]
    if missing:
        jobs.append({"id":"not_ready", "status":"missing_sources", "reason":"Original protocol absent",
                     "next_evidence":"Acquire original Methods and supplement"})
    path=tmp_path/'inventory.json'
    write(path,{"format_version":"extraction_inventory/1.0", "jobs":jobs})
    return path


async def test_mixed_inventory_isolation_and_accepted_parent_export(source_case,tmp_path):
    manifest=inventory(tmp_path,source_case,('good','bad'),missing=True)
    directory=tmp_path/'batch'
    plan(manifest,directory,config=BatchConfig(transport_retries=0))

    class SelectivelyFail(FakeWorker):
        async def run(self,prompt,attempt_dir):
            if 'bad' in Path(attempt_dir).parts:
                self.fault='worker_exit'
            return await super().run(prompt,attempt_dir)

    state=await run(directory,worker_factory=lambda:SelectivelyFail(source_case))
    assert state['status']=='partial'
    assert {k:r['status'] for k,r in state['jobs'].items()}=={
        'good':'supported','bad':'failed','not_ready':'missing_sources'}
    assert state['jobs']['bad']['failure_kind']=='transport'
    parents=read(directory/'parents.json')
    assert set(parents)=={'good'}
    assert set(parents['good'])=={'packet','workflow','review'}
    assert Path(parents['good']['workflow']).is_file()
    assert status(directory)['input_health']['good']=='unchanged'


async def test_completed_work_survives_restart_without_native_calls(source_case,tmp_path):
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory)
    worker=FakeWorker(source_case)
    first=await run(directory,worker_factory=lambda:worker)
    assert first['jobs']['good']['status']=='supported'
    # Simulate controller death after runtime completion but before its own save.
    stale=read(directory/'state.json')
    stale['jobs']['good']['status']='running'
    stale['jobs']['good'].pop('result',None)
    write(directory/'state.json',stale)

    def forbidden():
        pytest.fail('Completed work must not launch another worker')

    resumed=await run(directory,worker_factory=forbidden)
    assert resumed['jobs']['good']['status']=='supported'
    assert len(resumed['jobs']['good']['attempts'])==1
    assert set(read(directory/'parents.json'))=={'good'}


async def test_failed_review_retries_only_review_and_keeps_integration(source_case,tmp_path):
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory,config=BatchConfig(transport_retries=1))

    class FailFirstReview(FakeWorker):
        failed=False
        async def run(self,prompt,attempt_dir):
            if 'ROLE: reviewer' in prompt and not self.failed:
                self.failed=True
                raise RuntimeError('temporary transport outage')
            return await super().run(prompt,attempt_dir)

    worker=FailFirstReview(source_case)
    state=await run(directory,worker_factory=lambda:worker)
    row=state['jobs']['good']
    assert row['status']=='supported'
    assert [a['mode'] for a in row['attempts']]==['baseline','review_only']
    assert worker.integrations==1 and worker.reviews==1
    assert not row.get('error')
    assert Path(row['attempts'][0]['run'],'attempts/001_integrator/artifact.json').exists()


async def test_cancelled_review_resumes_from_successful_integrator(source_case,tmp_path):
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory,config=BatchConfig(transport_retries=0))
    reached=asyncio.Event()

    class Interrupted(FakeWorker):
        async def run(self,prompt,attempt_dir):
            if 'ROLE: reviewer' in prompt:
                reached.set()
                await asyncio.Event().wait()
            return await super().run(prompt,attempt_dir)

    task=asyncio.create_task(run(directory,worker_factory=lambda:Interrupted(source_case)))
    await asyncio.wait_for(reached.wait(),2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert read(directory/'state.json')['status']=='interrupted'
    worker=FakeWorker(source_case)
    state=await run(directory,worker_factory=lambda:worker)
    assert state['jobs']['good']['status']=='supported'
    assert [a['mode'] for a in state['jobs']['good']['attempts']]==['baseline','review_only']
    assert worker.integrations==0 and worker.reviews==1


async def test_failed_repair_preserves_candidate_and_negative_review(source_case,tmp_path):
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory,config=BatchConfig(transport_retries=0))

    class RepairInterrupted(FakeWorker):
        async def run(self,prompt,attempt_dir):
            if 'ROLE: integrator' in prompt and self.integrations:
                raise RuntimeError('interrupted repair')
            return await super().run(prompt,attempt_dir)

    initial=await run(directory,worker_factory=lambda:RepairInterrupted(source_case,review_verdicts=('unsupported',)))
    assert initial['jobs']['good']['status']=='failed'
    old_review=Path(initial['jobs']['good']['attempts'][0]['run'],'attempts/002_reviewer/artifact.json')
    old_hash=sha(old_review)
    worker=FakeWorker(source_case)
    resumed=await run(directory,worker_factory=lambda:worker)
    assert resumed['jobs']['good']['status']=='supported'
    assert resumed['jobs']['good']['attempts'][-1]['mode']=='repair'
    assert sha(old_review)==old_hash
    assert worker.integrations==1 and worker.reviews==1


async def test_needs_revision_is_science_not_a_transport_retry(source_case,tmp_path):
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory,config=BatchConfig(max_revisions=0,transport_retries=3))
    worker=FakeWorker(source_case,review_verdicts=('unsupported',))
    state=await run(directory,worker_factory=lambda:worker)
    assert state['status']=='completed'
    assert state['jobs']['good']['status']=='needs_revision'
    assert len(state['jobs']['good']['attempts'])==1
    assert not read(directory/'parents.json')
    state=await run(directory,worker_factory=lambda:pytest.fail('Scientific outcomes are terminal'))
    assert state['jobs']['good']['status']=='needs_revision'


async def test_native_semaphore_bounds_sessions_not_just_methods(source_case,tmp_path):
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case,('one','two')),directory,
         config=BatchConfig(mode='specialists',concurrency=2,transport_retries=0))
    active=maximum=0

    class Counted(FakeWorker):
        async def run(self,prompt,attempt_dir):
            nonlocal active,maximum
            self.reader_ready.set()  # No fake two-reader barrier while pool bounds sessions.
            active+=1
            maximum=max(maximum,active)
            try:
                await asyncio.sleep(.01)
                return await super().run(prompt,attempt_dir)
            finally:
                active-=1

    state=await run(directory,worker_factory=lambda:Counted(source_case))
    assert maximum==2 and active==0
    assert all(r['status']=='supported' for r in state['jobs'].values())


async def test_source_hash_drift_prevents_retry_and_excludes_parent(source_case,tmp_path):
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory)
    original=Path(source_case[0]['sources'][0]['original_path'])
    original.write_text('Changed source')
    state=await run(directory,worker_factory=lambda:pytest.fail('Drift must fail before native sessions'))
    assert state['jobs']['good']['status']=='pin_drift'
    assert not read(directory/'parents.json')


async def test_completed_artifact_drift_cannot_be_admitted_from_status(source_case,tmp_path):
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory)
    state=await run(directory,worker_factory=lambda:FakeWorker(source_case))
    review=Path(state['jobs']['good']['result']['review'])
    review.write_text('{}')
    state=await run(directory,worker_factory=lambda:pytest.fail('Do not silently rerun tampered work'))
    assert state['jobs']['good']['status']=='pin_drift'
    assert read(directory/'parents.json')=={}


async def test_config_and_implementation_drift_rejected(source_case,tmp_path,monkeypatch):
    from genomics_discovery.curation.extraction_batch import controller
    implementation=tmp_path/'implementation.py'
    implementation.write_text('version = 1')
    monkeypatch.setattr(controller,'_implementation_pins',lambda:{str(implementation):sha(implementation)})
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory)
    original_plan=(directory/'plan.json').read_bytes()
    document=read(directory/'plan.json'); document['config']['concurrency']=99
    write(directory/'plan.json',document)
    with pytest.raises(PinDrift,match='plan/config'):
        await run(directory)
    (directory/'plan.json').write_bytes(original_plan)
    implementation.write_text('version = 2')
    with pytest.raises(PinDrift,match='Pinned file changed'):
        await run(directory)


async def test_single_controller_lock_and_unknown_orphan_fail_closed(source_case,tmp_path):
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory)
    with controller_lock(directory):
        with pytest.raises(ControllerBusy):
            await run(directory)
    started=directory/'methods/good/attempts/0001/attempts/001_integrator/started.json'
    write(started,{'spawned':False,'pid':None})
    state=await run(directory,worker_factory=lambda:pytest.fail('Unknown spawn must reserve native capacity'))
    assert state['status']=='blocked_native_sessions'
    assert state['native_session_uncertainty']==[str(started)]


def test_plan_cli_defaults_and_invalid_missing_jobs(source_case,tmp_path,capsys):
    manifest=inventory(tmp_path,source_case,missing=True)
    directory=tmp_path/'batch'
    assert main(['plan',str(manifest),'--run',str(directory)])==0
    assert json.loads(capsys.readouterr().out)['status']=='planned'
    config=read(directory/'plan.json')['config']
    assert (config['mode'],config['model'],config['effort'],config['timeout_seconds'])==(
        'baseline','gpt-6.1-sol','xhigh',None)
    assert config['require_source_coverage'] is True
    assert main(['status',str(directory)])==0
    assert json.loads(capsys.readouterr().out)['status']=='planned'
    broken=tmp_path/'bad_inventory.json'
    write(broken,{'jobs':[{'id':'missing','status':'missing_sources','reason':'absent'}]})
    with pytest.raises(ValueError):
        plan(broken,tmp_path/'bad_batch')


def test_source_receipt_original_bytes_are_pinned(source_case,tmp_path,capsys):
    binary=tmp_path/'publisher.pdf'; binary.write_bytes(b'%PDF-original')
    receipt=tmp_path/'receipts.json'; write(receipt,{'files':{'publisher.pdf':sha(binary)}})
    manifest=inventory(tmp_path,source_case)
    raw=read(manifest); raw['source_receipts']='receipts.json'; write(manifest,raw)
    directory=tmp_path/'batch'; plan(manifest,directory)
    assert read(directory/'plan.json')['pins'][str(binary)]==sha(binary)
    binary.write_bytes(b'%PDF-changed')
    health = status(directory)['global_input_health']
    assert 'Pinned file changed' in health[str(binary)]
    assert main(['status',str(directory)]) == 2
    assert json.loads(capsys.readouterr().out)['input_status'] == 'pin_drift'
    with pytest.raises(PinDrift,match='publisher.pdf'):
        from genomics_discovery.curation.extraction_batch.controller import _load
        _load(directory)


async def test_source_first_batch_validates_coverage_and_reuses_blind_prep(source_case,tmp_path):
    from test_workflow_source_obligations import SourceWorker
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory,
         config=BatchConfig(require_source_coverage=True,transport_retries=1))

    class InterruptedReview(SourceWorker):
        failed=False
        async def run(self,prompt,attempt_dir):
            if 'ROLE: reviewer' in prompt and not self.failed:
                self.failed=True
                raise RuntimeError('transient reviewer transport failure')
            return await super().run(prompt,attempt_dir)

    worker=InterruptedReview(source_case)
    state=await run(directory,worker_factory=lambda:worker)
    row=state['jobs']['good']
    assert row['status']=='supported',row.get('error')
    assert [a['mode'] for a in row['attempts']]==['baseline','review_only']
    assert [c['role'] for c in worker.calls].count('source_obligations')==1
    assert worker.integrations==1 and worker.reviews==1
    assert read(Path(row['attempts'][1]['run'])/'config.json')['source_obligations_input']
    assert set(read(directory/'parents.json'))=={'good'}
    assert row['result']['runtime_run']==row['attempts'][-1]['run']
    assert set(row['result']['accepted_source_coverage'])=={'source_obligations','source_coverage_review'}


async def test_source_sidecar_rejection_does_not_export_positive_primary(source_case,tmp_path):
    from test_workflow_source_obligations import SourceWorker
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory,
         config=BatchConfig(require_source_coverage=True,max_revisions=0))
    worker=SourceWorker(source_case,omit_wash=True)
    state=await run(directory,worker_factory=lambda:worker)
    row=state['jobs']['good']
    assert row['status']=='needs_revision',row.get('error')
    assert row['result']['primary_outcome']=='supported'
    assert read(directory/'parents.json')=={}


async def test_source_sidecar_drift_excludes_previously_supported_parent(source_case,tmp_path):
    from test_workflow_source_obligations import SourceWorker
    directory=tmp_path/'batch'
    plan(inventory(tmp_path,source_case),directory,
         config=BatchConfig(require_source_coverage=True))
    state=await run(directory,worker_factory=lambda:SourceWorker(source_case))
    assert state['jobs']['good']['status']=='supported'
    attempt=Path(state['jobs']['good']['attempts'][0]['run'])
    sidecar=Path(read(attempt/'state.json')['artifacts']['source_coverage_review']['path'])
    sidecar.write_text('{}')
    state=await run(directory,worker_factory=lambda:pytest.fail('Do not rerun tampered accepted work'))
    assert state['jobs']['good']['status']=='pin_drift'
    assert read(directory/'parents.json')=={}
