"""Thin deployment orchestration, with real runtime archives and no native calls."""
import asyncio
import json
from pathlib import Path

import pytest

from genomics_discovery.curation import deployment
from genomics_discovery.curation.extraction_batch import BatchConfig
from genomics_discovery.curation.extraction_batch.storage import read, sha, write
from test_batch_graph import distinct_case
from test_workflow_extraction_runtime import FakeWorker, source_case as source_case


def inputs(tmp_path, source_case, ids=('a', 'b'), *, missing=()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    cases = {mid: distinct_case(source_case, tmp_path / mid, mid) for mid in ids}
    jobs = [{'id': mid, 'title': mid, 'status': 'ready', 'packet': str(case[1])}
            for mid, case in cases.items()]
    jobs += [{'id': mid, 'status': 'missing_sources', 'reason': 'Exact original unavailable',
              'next_evidence': f'Acquire {mid} original Methods'} for mid in missing]
    manifest, atlas = tmp_path / 'inventory.json', tmp_path / 'atlas.json'
    write(manifest, {'jobs': jobs})
    write(atlas, {'methods': [{'id': job['id'], 'name': job['id']} for job in jobs],
                  'claims': [], 'revision_id': 'deployment-test'})
    return manifest, atlas, cases


def worker_factory(cases, *, negative=(), calls=None, before=None):
    class RoutedWorker:
        def __init__(self):
            self.workers = {mid: FakeWorker(case, review_verdicts=('unsupported',) if mid in negative else ())
                            for mid, case in cases.items()}

        async def run(self, prompt, attempt_dir):
            parts = Path(attempt_dir).parts; mid = parts[parts.index('methods') + 1]
            if before:
                await before(mid, prompt, attempt_dir)
            if calls is not None:
                calls.append((mid, 'integrator' if 'ROLE: integrator' in prompt else 'reviewer'))
            return await self.workers[mid].run(prompt, attempt_dir)
    return RoutedWorker


def forbidden():
    pytest.fail('Completed work must not launch another worker')


def config(**kwargs):
    return BatchConfig(concurrency=2, max_revisions=0, transport_retries=0, **kwargs)


def test_plan_accounts_201_ids_and_default_source_first_concurrency(tmp_path, source_case):
    manifest, atlas, _ = inputs(tmp_path, source_case, ('a',), missing=tuple(f'missing-{i}' for i in range(200)))
    output = tmp_path / 'deployment'
    state = deployment.plan_deployment(manifest, atlas, output)
    frozen = read(output / 'plan.json')
    ids = [mid for c in frozen['chunks'] for mid in c['method_ids']]
    assert len(ids) == len(set(ids)) == len(state['methods']) == 201
    assert [len(c['method_ids']) for c in frozen['chunks']] == [10] * 20 + [1]
    assert frozen['config']['concurrency'] == 10
    assert frozen['config']['require_source_coverage'] is True
    for chunk in frozen['chunks']:
        child = read(Path(chunk['batch']) / 'plan.json')
        assert child['config'] == frozen['config']
        assert child['pins'][str(atlas)] == sha(atlas)
        assert child['pins'][str(Path(deployment.__file__).resolve())] == sha(Path(deployment.__file__))
    assert deployment.status_deployment(output)['status_counts'] == {'ready': 1, 'missing_sources': 200}


async def test_partial_chunks_publish_before_next_and_resume_without_calls(tmp_path, source_case):
    manifest, atlas, cases = inputs(tmp_path, source_case, ('a', 'b', 'c'), missing=('missing',))
    output = tmp_path / 'deployment'
    deployment.plan_deployment(manifest, atlas, output, batch_size=2, config=config())
    calls = []

    async def before(mid, prompt, attempt_dir):
        if mid == 'c':
            assert (output / 'chunks/0001/publications/0001/publication.json').is_file()

    state = await deployment.run_deployment(output, worker_factory=worker_factory(cases, negative=('b',), calls=calls, before=before))
    assert state['status'] == 'partial'
    assert {mid: m['status'] for mid, m in state['methods'].items()} == {
        'a': 'supported', 'b': 'needs_revision', 'c': 'supported', 'missing': 'missing_sources'}
    assert state['methods']['missing']['next_evidence'] == 'Acquire missing original Methods'
    assert all(c['status'] == 'complete' for c in state['chunks'])
    latest = Path(state['latest_publication']['path'])
    assert set(read(latest / 'extractions.json')) == {'a', 'c'}
    assert read(latest / 'publication.json')['previous_publication']['directory'].endswith('chunks/0001/publications/0001')
    prior = {str(p): sha(p) for p in output.rglob('*') if p.is_file() and p.name != '.controller.lock'}
    resumed = await deployment.run_deployment(output, worker_factory=forbidden)
    assert resumed['status'] == 'partial'
    assert {str(p): sha(p) for p in output.rglob('*') if p.is_file() and p.name != '.controller.lock'} == prior
    assert len(calls) == 6


async def test_previous_accepted_ids_are_validated_and_never_rerun(tmp_path, source_case):
    manifest, atlas, cases = inputs(tmp_path / 'inputs', source_case)
    first_manifest = tmp_path / 'first.json'; write(first_manifest, {'jobs': [read(manifest)['jobs'][0]]})
    first = tmp_path / 'first'; deployment.plan_deployment(first_manifest, atlas, first, config=config())
    accepted = await deployment.run_deployment(first, worker_factory=worker_factory(cases))
    prior = Path(accepted['latest_publication']['path'])
    second = tmp_path / 'second'
    planned = deployment.plan_deployment(manifest, atlas, second, previous=prior, batch_size=1, config=config())
    assert planned['methods']['a']['status'] == 'already_accepted'
    assert planned['inherited_methods'] == ['a']
    assert read(second / 'plan.json')['chunks'][0]['batch'] is None
    calls = []
    state = await deployment.run_deployment(second, worker_factory=worker_factory(cases, calls=calls))
    assert state['status'] == 'completed'
    assert {mid for mid, _ in calls} == {'b'}
    assert set(read(Path(state['latest_publication']['path']) / 'extractions.json')) == {'a', 'b'}
    (prior / 'graph.json').write_text('{}')
    with pytest.raises(ValueError):
        await deployment.run_deployment(second, worker_factory=forbidden)


@pytest.mark.parametrize('committed', [False, True])
async def test_resume_after_publication_interruption_never_reexecutes_batch(tmp_path, source_case, monkeypatch, committed):
    manifest, atlas, cases = inputs(tmp_path, source_case, ('a',))
    output = tmp_path / 'deployment'; deployment.plan_deployment(manifest, atlas, output, config=config())
    original = deployment.batch_graph.publish

    def interrupted(*args, **kwargs):
        if committed:
            original(*args, **kwargs)
        else:
            args[2].mkdir(parents=True)
            write(args[2] / 'extractions.json', {'incomplete': 'uncommitted'})
        raise RuntimeError('Stopped before deployment journal commit')

    monkeypatch.setattr(deployment.batch_graph, 'publish', interrupted)
    with pytest.raises(RuntimeError):
        await deployment.run_deployment(output, worker_factory=worker_factory(cases))
    monkeypatch.setattr(deployment.batch_graph, 'publish', original)
    state = await deployment.run_deployment(output, worker_factory=forbidden)
    expected = '0001' if committed else '0002'
    assert Path(state['latest_publication']['path']).name == expected
    assert state['status'] == 'completed'
    assert set(read(Path(state['latest_publication']['path']) / 'extractions.json')) == {'a'}


async def test_cancelled_native_review_resumes_existing_candidate_before_next_chunk(tmp_path, source_case):
    manifest, atlas, cases = inputs(tmp_path, source_case)
    output = tmp_path / 'deployment'; deployment.plan_deployment(manifest, atlas, output, batch_size=1, config=config())
    reached = asyncio.Event()

    async def pause(mid, prompt, attempt_dir):
        if mid == 'a' and 'ROLE: reviewer' in prompt:
            reached.set(); await asyncio.Event().wait()

    task = asyncio.create_task(deployment.run_deployment(output, worker_factory=worker_factory(cases, before=pause)))
    await asyncio.wait_for(reached.wait(), 5); task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert read(output / 'state.json')['status'] == 'interrupted'
    assert read(output / 'chunks/0002/batch/state.json')['status'] == 'planned'
    calls = []
    state = await deployment.run_deployment(output, worker_factory=worker_factory(cases, calls=calls))
    assert state['status'] == 'completed'
    assert ('a', 'integrator') not in calls
    assert ('a', 'reviewer') in calls and ('b', 'integrator') in calls


@pytest.mark.parametrize('changed', ['atlas', 'code', 'receipt', 'plan'])
async def test_global_drift_stops_before_workers(tmp_path, source_case, monkeypatch, changed):
    manifest, atlas, cases = inputs(tmp_path, source_case)
    implementation = tmp_path / 'driver.py'; implementation.write_text('version=1')
    monkeypatch.setattr(deployment, '__file__', str(implementation))
    binary = tmp_path / 'original.pdf'; binary.write_bytes(b'%PDF-original')
    receipt = tmp_path / 'receipts.json'; write(receipt, {'files': {'original.pdf': sha(binary)}})
    raw = read(manifest); raw['source_receipts'] = 'receipts.json'; write(manifest, raw)
    output = tmp_path / 'deployment'; deployment.plan_deployment(manifest, atlas, output, config=config())
    path = {'atlas': atlas, 'code': implementation, 'receipt': binary, 'plan': output / 'chunks/0001/batch/plan.json'}[changed]
    path.write_bytes(path.read_bytes() + b' ')
    with pytest.raises(ValueError):
        await deployment.run_deployment(output, worker_factory=forbidden)
    assert read(output / 'state.json')['status'] == 'planned'


async def test_no_supported_chunk_continues_without_empty_publication(tmp_path, source_case):
    manifest, atlas, cases = inputs(tmp_path, source_case)
    output = tmp_path / 'deployment'; deployment.plan_deployment(manifest, atlas, output, batch_size=1, config=config())
    state = await deployment.run_deployment(output, worker_factory=worker_factory(cases, negative=('a',)))
    assert state['status'] == 'partial'
    assert 'publication' not in state['chunks'][0]
    assert state['chunks'][1]['publication']['accepted_methods'] == ['b']
    assert not (output / 'chunks/0001/publications').exists()


def test_cli_plan_and_status_do_not_run_workers(tmp_path, source_case, capsys):
    manifest, atlas, _ = inputs(tmp_path, source_case)
    output = tmp_path / 'deployment'
    assert deployment.main(['plan', str(manifest), '--atlas', str(atlas), '--output', str(output)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['method_count'] == 2 and summary['completed_chunks'] == 0
    assert 'methods' not in summary and 'latest_publication' in summary
    assert deployment.main(['status', str(output)]) == 0
    assert '"status": "planned"' in capsys.readouterr().out
    assert deployment.main(['status', str(output), '--json']) == 0
    assert set(json.loads(capsys.readouterr().out)['methods']) == {'a', 'b'}
    assert deployment.main(['plan', str(manifest), '--atlas', str(atlas), '--output', str(output)]) == 2


def test_conflicting_source_receipt_aliases_reject_before_planning(tmp_path, source_case):
    manifest, atlas, _ = inputs(tmp_path, source_case)
    original = tmp_path / 'original.pdf'; original.write_bytes(b'%PDF-original')
    receipt = tmp_path / 'receipts.json'
    write(receipt, {'files': {str(original): '0' * 64, './original.pdf': sha(original)}})
    raw = read(manifest); raw['source_receipts'] = str(receipt); write(manifest, raw)
    output = tmp_path / 'deployment'
    with pytest.raises(ValueError, match='Conflicting deployment pin'):
        deployment.plan_deployment(manifest, atlas, output)
    assert not output.exists()


async def test_running_status_overlays_active_batch_without_changing_journal(tmp_path, source_case, capsys):
    manifest, atlas, cases = inputs(tmp_path, source_case)
    output = tmp_path / 'deployment'; deployment.plan_deployment(manifest, atlas, output, batch_size=1, config=config())
    reached, release = asyncio.Event(), asyncio.Event()

    async def pause(mid, prompt, attempt_dir):
        if mid == 'a' and 'ROLE: integrator' in prompt:
            reached.set(); await release.wait()

    task = asyncio.create_task(deployment.run_deployment(output, worker_factory=worker_factory(cases, before=pause)))
    try:
        await asyncio.wait_for(reached.wait(), 5)
        before = sha(output / 'state.json')
        snapshot = deployment.status_deployment(output)
        assert snapshot['active_chunk'] == 1
        assert snapshot['active_batch']['jobs']['a']['status'] == 'running'
        assert snapshot['methods']['a']['status'] == 'running'
        assert snapshot['methods']['a']['recorded_status'] == 'ready'
        assert snapshot['methods']['b']['status'] == 'ready'
        assert snapshot['status_counts'] == {'running': 1, 'ready': 1}
        assert snapshot['recorded_status_counts'] == {'ready': 2}
        assert sha(output / 'state.json') == before
        assert deployment.main(['status', str(output)]) == 1
        display = json.loads(capsys.readouterr().out)
        assert display['status_counts'] == {'running': 1, 'ready': 1}
        assert display['active_chunk'] == 1 and 'methods' not in display
        assert sha(output / 'state.json') == before
    finally:
        release.set()
        await task
    assert deployment.status_deployment(output)['active_batch'] is None
