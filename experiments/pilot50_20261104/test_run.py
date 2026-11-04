"""Pilot scheduling checks; native model calls are mocked, never launched here."""
import asyncio
import copy
from pathlib import Path

import pytest

from experiments.pilot50_20261104 import run as pilot
from genomics_discovery.illumina.extraction import StageFailure


def bundles_at(root, ids=('one', 'two')):
    bundles = []
    for rid in ids:
        folder = root / rid
        card = {'record_id': rid, 'source_title': rid, 'references': [],
                'implementation_scope': 'Original scoped implementation'}
        for name, value in {'card.json': card, 'source_registry.json': {}, 'images.json': []}.items():
            pilot.write(folder / name, value)
        bundles.append({'record_id': rid, 'card': str(folder / 'card.json'),
            'registry': str(folder / 'source_registry.json'), 'images': [],
            'pins': {name: pilot.sha(folder / name) for name in
                     ('card.json', 'source_registry.json', 'images.json')}})
    return {'bundles': bundles, 'selection_sha256': 'a' * 64}


def selection_at(path):
    methods = [{'method_id': f'm{i:02d}', 'name': f'Method {i}',
                'scope': f'Original scoped implementation {i}', 'primary_pmid': str(i + 1),
                'primary_record_id': f's{i}', 'source_record_ids': [f's{i}'],
                'source_records': [{'record_id': f's{i}', 'source_registry_path': f'/fixture/s{i}/source_registry.json',
                                    'card_path': f'/fixture/s{i}/card.json'}]} for i in range(50)]
    selection = {'methods': methods}
    pilot.write(path, selection)
    return selection


@pytest.fixture
def slurm(monkeypatch):
    monkeypatch.setenv('SLURM_JOB_ID', 'offline-fixture-not-a-real-job')


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['duplicate', 'identity_mismatch'])
async def test_invalid_dispatch_is_rejected_before_any_native_call(tmp_path, monkeypatch, slurm, mutation):
    manifest = bundles_at(tmp_path / 'sources')
    if mutation == 'duplicate':
        manifest['bundles'].append(copy.deepcopy(manifest['bundles'][0]))
    else:
        manifest['bundles'][0]['record_id'] = 'different-target'
    calls = []
    monkeypatch.setattr(pilot, 'extract_registered_workflow', lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match='One dispatch'):
        await pilot.extract(manifest, tmp_path / 'batch', worker=object())
    assert calls == []
    assert not (tmp_path / 'batch').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('filename', ['card.json', 'source_registry.json', 'images.json'])
async def test_changed_prepared_inputs_stop_batch_before_dispatch(tmp_path, monkeypatch, slurm, filename):
    manifest = bundles_at(tmp_path / 'sources')
    path = Path(manifest['bundles'][0]['card']).parent / filename
    # Whitespace is semantically innocuous, but changed bytes must not reuse a pin.
    path.write_text(path.read_text() + '\n')
    calls = []
    monkeypatch.setattr(pilot, 'extract_registered_workflow', lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match='bundle changed'):
        await pilot.extract(manifest, tmp_path / 'batch', worker=object())
    assert calls == [] and not (tmp_path / 'batch').exists()


@pytest.mark.asyncio
async def test_extraction_is_bounded_isolates_failures_and_reopens_publication(tmp_path, monkeypatch, slurm):
    manifest = bundles_at(tmp_path / 'sources', ('one', 'two', 'three'))
    worker = object()
    calls, active, peak = [], 0, 0

    async def fake_native(card, registry, images, directory, passed_worker, **kwargs):
        nonlocal active, peak
        assert passed_worker is worker
        assert kwargs == {'max_revisions': 1, 'representation': 'illumina_diagram/1.1'}
        calls.append(card['record_id'])
        active += 1; peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        if card['record_id'] == 'one':
            raise StageFailure('native_execution', 'Simulated transport failure')
        if card['record_id'] == 'two':
            return {'status': 'research_needed', 'reason': 'Insufficient source evidence'}
        # Merely returning supported without archived review artifacts is NOT admission.
        return {'status': 'supported'}

    monkeypatch.setattr(pilot, 'extract_registered_workflow', fake_native)
    result = await pilot.extract(manifest, tmp_path / 'batch', workers=2, worker=worker)
    state = pilot.read(tmp_path / 'batch/state.json')
    assert sorted(calls) == ['one', 'three', 'two'] and peak == 2
    assert result['counts'] == {'failed': 1, 'research_needed': 1, 'supported': 1}
    assert state['cards']['one']['failure_category'] == 'native_execution'
    assert all(row.get('finished_at') for row in state['cards'].values())
    assert result['publication_errors'] == ['three']
    graph = pilot.read(tmp_path / 'batch/atlas.json')
    assert graph['cards']['three']['status'] == 'integrity_error'
    assert not any(card.get('admission') == 'supported' for card in graph['cards'].values())
    assert (tmp_path / 'batch/CHECKLIST.csv').is_file()
    assert result['graph_sha256'] == pilot.sha(tmp_path / 'batch/atlas.json')


def test_comparison_preserves_predeclared_baseline_order_and_does_not_choose_by_new_outcome(tmp_path):
    selection = {'methods': [{'method_id': 'one', 'scope': 'Original assay'},
                             {'method_id': 'two', 'scope': 'Another assay'}]}
    current_baseline = {'attempt_directory': '/old/current19', 'artifact_pins': {'extraction.json': 'b' * 64},
                        'declared_status': 'needs_revision'}
    old_supported = {'attempt_directory': '/old/occurrence', 'artifact_pins': {'extraction.json': 'c' * 64},
                     'declared_status': 'supported'}
    options = [{'reference': current_baseline, 'kind': 'latest_reviewed_scoped_workflow'},
               {'reference': old_supported, 'kind': 'archived_source_occurrence'}]
    pilot.write(tmp_path / 'sources/baseline_references.json', {'one': options, 'two': []})
    pilot.write(tmp_path / 'batch/state.json', {'cards': {
        'one': {'attempt_directory': '/new/one', 'status': 'supported',
                'artifact_pins': {'extraction.json': 'd' * 64}},
        'two': {'attempt_directory': '/new/two', 'status': 'failed'}}})
    result = pilot.comparison_manifest(selection, tmp_path / 'sources', tmp_path / 'batch')
    first, second = result['methods']
    assert first['baseline'] == current_baseline
    assert first['all_predeclared_baselines'] == options
    assert first['candidate']['attempt_directory'] == '/new/one'
    assert first['identity_match']['status'] == 'pending'
    assert second['baseline'] is None and second['candidate'] is None


@pytest.mark.asyncio
async def test_comparison_holds_missing_artifacts_and_isolates_native_errors(tmp_path, monkeypatch, slurm):
    methods = [{'method_id': name} for name in ('unpaired', 'invalid', 'fails', 'works')]
    manifest = {'format_version': 'atlas_pilot_comparison/1', 'methods': methods}
    statuses = ['unpaired', 'artifact_or_schema_issue', 'pending_identity', 'pending_identity']
    monkeypatch.setattr(pilot.evaluation, 'evaluate_manifest', lambda *a: {
        'methods': [{'method_id': m['method_id'], 'comparison_status': s}
                    for m, s in zip(methods, statuses)]})
    called = []

    async def fake_native(row, output, seed):
        called.append(row['method_id'])
        if row['method_id'] == 'fails':
            raise ValueError('Simulated invalid evidence binding')
        return {'model_assessed_outcome': 'equivalent'}

    monkeypatch.setattr(pilot.evaluation, 'evaluate_pair_native', fake_native)
    result = await pilot.compare(manifest, tmp_path / 'evaluation', workers=2)
    assert called == ['fails', 'works']
    assert result['counts'] == {'held': 2, 'failed': 1, 'completed': 1}
    assert result['assessed_outcomes'] == {'equivalent': 1}
    assert 'not expert gold' in result['boundary']


@pytest.mark.asyncio
@pytest.mark.parametrize('workers', [0, 5, True, '2'])
async def test_execute_rejects_invalid_concurrency_before_creating_run(tmp_path, slurm, workers):
    with pytest.raises(ValueError, match='one to four'):
        await pilot.execute(tmp_path / 'not-read.json', tmp_path / 'out', tmp_path, workers=workers)
    assert not (tmp_path / 'out').exists()


@pytest.mark.asyncio
async def test_execute_requires_slurm_before_reading_sources(tmp_path, monkeypatch):
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    with pytest.raises(ValueError, match='Sherlock Slurm'):
        await pilot.execute(tmp_path / 'not-read.json', tmp_path / 'out', tmp_path)
    assert not (tmp_path / 'out').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('history_fails', [False, True])
async def test_execute_finishes_all_extractions_before_history_and_keeps_comparison_after_history_error(
        tmp_path, monkeypatch, slurm, history_fails):
    path = tmp_path / 'selection.json'; selection = selection_at(path)
    events = []

    def fake_prepare(selection_path, output):
        assert pilot.read(selection_path) == selection
        manifest = bundles_at(output, tuple(m['method_id'] for m in selection['methods']))
        manifest['selection_sha256'] = pilot.sha(selection_path)
        pilot.write(output / 'baseline_references.json', {})
        for name in ('primary_selection.json', 'reference_cache.json'):
            pilot.write(output / name, {})
        events.append('prepare')
        return manifest

    async def fake_native(card, registry, images, directory, worker, **kwargs):
        await asyncio.sleep(0)
        events.append(card['record_id'])
        raise StageFailure('offline_fixture', 'No model call is made in this test')

    async def fake_history(batch, output, runtime, **kwargs):
        state = pilot.read(batch / 'state.json')
        assert len(state['cards']) == 50
        assert all(row['status'] == 'failed' and row['finished_at'] for row in state['cards'].values())
        assert len([x for x in events if x.startswith('m')]) == 50
        assert kwargs['primary_selection'].name == 'primary_selection.json'
        assert kwargs['source_cache'].name == 'reference_cache.json'
        events.append('history')
        if history_fails:
            raise ValueError('Simulated history stage failure')
        return {'historical_edges': 0, 'held_methods': 50}

    async def unexpected_native_evaluation(*args, **kwargs):
        pytest.fail('Unpaired failed extractions must not dispatch a comparison model')

    monkeypatch.setattr(pilot.prepare, 'prepare', fake_prepare)
    monkeypatch.setattr(pilot, 'CodexWorker', lambda **k: object())
    monkeypatch.setattr(pilot, 'extract_registered_workflow', fake_native)
    monkeypatch.setattr(pilot.history_stage, 'execute', fake_history)
    monkeypatch.setattr(pilot.evaluation, 'evaluate_pair_native', unexpected_native_evaluation)
    output = tmp_path / 'run'
    result = await pilot.execute(path, output, tmp_path / 'runtime', workers=3)
    assert events[0] == 'prepare' and events[-1] == 'history'
    assert result['status'] == 'completed'
    assert result['selection_sha256'] == pilot.sha(path)
    assert result['stages']['extraction']['counts'] == {'failed': 50}
    assert result['stages']['evaluation']['counts'] == {'held': 50}
    if history_fails:
        assert result['stages']['history']['status'] == 'failed'
    assert 'not 50 scientific successes' in result['boundary']
    assert pilot.read(output / 'RESULT.json') == pilot.read(output / 'state.json')


@pytest.mark.asyncio
async def test_failed_preparation_leaves_explicit_state_and_no_native_stage(tmp_path, monkeypatch, slurm):
    path = tmp_path / 'selection.json'; selection_at(path)

    def fail_prepare(*args):
        raise ValueError('Registered primary bytes changed')

    monkeypatch.setattr(pilot.prepare, 'prepare', fail_prepare)
    with pytest.raises(ValueError, match='primary bytes changed'):
        await pilot.execute(path, tmp_path / 'out', tmp_path / 'runtime')
    state = pilot.read(tmp_path / 'out/state.json')
    assert state['status'] == 'failed' and state['finished_at']
    assert state['stages'] == {}
    assert not (tmp_path / 'out/RESULT.json').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', [pilot.extract, pilot.compare])
async def test_direct_native_stage_requires_slurm(tmp_path, monkeypatch, stage):
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    with pytest.raises(ValueError, match='Sherlock Slurm'):
        await stage({}, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', [pilot.extract, pilot.compare])
async def test_direct_native_stage_rejects_zero_workers_before_inputs(tmp_path, slurm, stage):
    with pytest.raises(ValueError, match='one to four'):
        await stage({}, tmp_path / 'out', workers=0)
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('has_eligible_baseline', [False, True])
def test_known_different_version_is_preserved_but_not_used_as_baseline(tmp_path, has_eligible_baseline):
    selection = {'methods': [{'method_id': 'protocol_version', 'scope': 'Later protocol implementation'}]}
    excluded = {'reference': {'attempt_directory': '/prior/original-version',
                             'artifact_pins': {'extraction.json': 'a' * 64}, 'declared_status': 'supported'},
                'comparison_eligible': False, 'scope_match': 'source_depiction_baseline_only'}
    eligible = {'reference': {'attempt_directory': '/prior/same-version',
                             'artifact_pins': {'extraction.json': 'b' * 64}, 'declared_status': 'supported'},
                'comparison_eligible': True, 'scope_match': 'candidate_same_scoped_implementation'}
    options = [excluded, eligible] if has_eligible_baseline else [excluded]
    pilot.write(tmp_path / 'sources/baseline_references.json', {'protocol_version': options})
    pilot.write(tmp_path / 'batch/state.json', {'cards': {'protocol_version': {
        'attempt_directory': '/new/protocol-version', 'artifact_pins': {'extraction.json': 'c' * 64},
        'status': 'supported'}}})
    row = pilot.comparison_manifest(selection, tmp_path / 'sources', tmp_path / 'batch')['methods'][0]
    assert row['baseline'] == (eligible['reference'] if has_eligible_baseline else None)
    assert row['all_predeclared_baselines'] == options
    assert row['candidate'] is not None
    assert row['identity_match']['status'] == 'pending'
