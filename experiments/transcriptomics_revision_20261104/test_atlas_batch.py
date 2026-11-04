"""Batch dispatch preserves identity review, source custody and partial outcomes."""
import copy
import json

import pytest

from experiments.transcriptomics_revision_20261104 import atlas_batch as batch
from experiments.transcriptomics_revision_20261104 import native_planning as native
from experiments.transcriptomics_revision_20261104 import extract_plan
from experiments.transcriptomics_revision_20261104.planning import WorkPlan, digest, validate_plan
from experiments.transcriptomics_revision_20261104.test_extract_plan import archive, planned, typed_transport
from tests.illumina import test_illumina_batch_controls as transport


def test_scope_reused_and_held_sources_cannot_be_dispatched():
    manifest = {'selected_record_ids': ['new'], 'parent_census_file_sha256': 'parent-pin'}
    parent = {'rows': [{'record_id': rid} for rid in ('new', 'blocked', 'done', 'later')]}
    receipt = {'manifest_sha256': 'manifest-pin', 'source_census_sha256': 'parent-pin',
               'pending_records': ['later'], 'selected_record_ids': ['new'], 'held_records': {'blocked': 'Existing provider hold'},
               'reused_records': {'done': {'batch': 'original', 'sha256': 'pin'}}}
    assert batch.validate_scope(manifest, receipt, parent, 'manifest-pin') == receipt
    for key in ('held_records', 'reused_records'):
        bad = copy.deepcopy(receipt); bad[key]['new'] = 'must not rerun'
        with pytest.raises(ValueError, match='must not enter'):
            batch.validate_scope(manifest, bad, parent, 'manifest-pin')
    bad = copy.deepcopy(receipt); bad['selected_record_ids'].append('new')
    with pytest.raises(ValueError, match='differs'):
        batch.validate_scope(manifest, bad, parent, 'manifest-pin')


@pytest.mark.asyncio
async def test_global_bundle_runs_existing_typed_extraction_review_and_publication(tmp_path, monkeypatch):
    directory = await planned(tmp_path, monkeypatch)
    worklist, bundles = extract_plan.load_plan(directory)
    worklist = {**worklist, 'global_index_sha256': 'fixture-global-review'}
    worker = typed_transport(monkeypatch)
    result = await batch.extract_dispatch(worklist, bundles, tmp_path / 'extractions', worker=worker)
    assert result['passed'] and result['extraction_counts'] == {'supported': 1}
    assert [c['role'] for c in worker.calls] == ['author', 'reviewer']
    state = native.read(tmp_path / 'extractions/state.json')
    assert state['policy']['global_index_sha256'] == 'fixture-global-review'
    assert native.read(tmp_path / 'extractions/atlas.json')['cards']['one_method']['admission'] == 'supported'
    assert (tmp_path / 'extractions/cards/one_method/attempt_001/workflow.txt').exists()


@pytest.mark.asyncio
async def test_dispatch_rejects_stale_bundle_before_worker_call(tmp_path, monkeypatch):
    directory = await planned(tmp_path, monkeypatch)
    worklist, bundles = extract_plan.load_plan(directory)
    worklist['global_index_sha256'] = 'fixture'
    source = bundles[0]['registry_path']; value = native.read(source)
    value['web::paper:1']['text'] = 'Replaced after review.'; native.write(source, value)
    worker = typed_transport(monkeypatch)
    with pytest.raises(ValueError, match='source bundle changed'):
        await batch.extract_dispatch(worklist, bundles, tmp_path / 'extractions', worker=worker)
    assert not worker.calls


@pytest.mark.asyncio
async def test_native_driver_refuses_non_slurm_execution(tmp_path, monkeypatch):
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    with pytest.raises(ValueError, match='Slurm'):
        await batch.execute(tmp_path / 'manifest', tmp_path / 'receipt', tmp_path / 'run')
    assert not (tmp_path / 'run').exists()


@pytest.mark.parametrize('identity_status', ['clean', 'unselected_pending', 'selected_pending', 'additional_alias'])
@pytest.mark.asyncio
async def test_complete_batch_uses_one_reviewed_unit_for_two_depictions(tmp_path, monkeypatch, identity_status):
    initial = await planned(tmp_path, monkeypatch)
    census = native.read(initial / 'inputs/census.json')
    for row in census['rows']:
        row['source_method_labels'] = ['Example']
    source = tmp_path / 'census.json'; native.write(source, census)
    manifest_dir = tmp_path / 'batches'
    batch.batching.write_batches(source, manifest_dir, ['web', 'poster'])
    receipt = tmp_path / 'scope.json'
    native.write(receipt, {'selected_record_ids': ['web', 'poster'],
                          'held_records': {}, 'reused_records': {}, 'pending_records': [],
                          'manifest_sha256': native.sha(manifest_dir / 'manifest.json'),
                          'source_census_sha256': native.sha(manifest_dir / 'parent_census.json')})
    base_plan = native.read(initial / 'plan.json')
    original_execute = native.execute
    class PlanningWorker:
        async def run(self, prompt, attempt, image_paths=()):
            context = json.loads(prompt.split('\nCONTEXT\n', 1)[1])
            local_census = native.read(attempt.parent.parent / 'inputs/census.json')
            raw = copy.deepcopy(base_plan); raw['census_sha256'] = digest(local_census)
            plan = WorkPlan.model_validate(raw)
            if context['role'] == 'author': payload = plan.model_dump(mode='json')
            else:
                payload = {'plan_sha256': digest(plan.model_dump(mode='json')), 'assessments': [
                    {'target': target, 'decision': 'accept', 'reason': 'Synthetic independent review',
                     'evidence': [e.model_dump(mode='json') for e in plan.identity_decisions[0].evidence]}
                    for target in validate_plan(local_census, plan)]}
            return archive(attempt, prompt, image_paths, payload, 'larger-' + attempt.name)
    async def planning(census_path, output):
        return await original_execute(census_path, output, worker=PlanningWorker())
    monkeypatch.setattr(native, 'execute', planning)
    worker = typed_transport(monkeypatch)
    original_extract = batch.extract_dispatch
    calls = []
    async def extraction(worklist, bundles, output, **kwargs):
        calls.extend(b['unit_id'] for b in bundles)
        existing = transport.candidate
        def renamed(sources, *, revised=False):
            candidate = existing(sources, revised=revised)
            return candidate.model_copy(update={'record_id': worklist['units'][0]['unit_id']})
        monkeypatch.setattr(transport, 'candidate', renamed)
        return await original_extract(worklist, bundles, output, worker=worker, **kwargs)
    monkeypatch.setattr(batch, 'extract_dispatch', extraction)
    original_admit = batch.global_plan.admit_global
    def admission(*args, **kwargs):
        decisions = original_admit(*args, **kwargs)
        if identity_status.endswith('_pending'):
            decisions['pending_source_record_ids'].append('web' if identity_status == 'selected_pending' else 'outside_scope')
        if identity_status == 'additional_alias':
            decisions['additional_alias_requests'].append({'reason': 'Synthetic unresolved alias request'})
        return decisions
    monkeypatch.setattr(batch.global_plan, 'admit_global', admission)
    result = await batch.execute(manifest_dir / 'manifest.json', receipt, tmp_path / 'run', workers=2)
    expected_status = 'completed' if identity_status in {'clean', 'unselected_pending'} else 'completed_with_unresolved_records'
    assert result['status'] == expected_status
    assert len(calls) == 1 and calls[0].startswith('dispatch_')
    assert result['admitted_methods'] == 1
    graph = native.read(tmp_path / 'run/extractions/atlas.json')
    assert graph['cards'][calls[0]]['admission'] == 'supported'
    sources = native.read(tmp_path / 'run/dispatch_sources/workflow_bundles/unit_0001/source_registry.json')
    assert 'web::paper:1' in sources and 'poster::diagram' in sources
    assert native.read(tmp_path / 'run/source_scope.json')['selected_record_ids'] == ['web', 'poster']
    assert len(native.read(tmp_path / 'run/source_dispositions.json')) == 2
    assert not any(edge['kind'] in {'method_development', 'component_adoption'} for edge in graph['edges'])


@pytest.mark.asyncio
async def test_planning_failure_is_recorded_and_does_not_reach_extraction(tmp_path, monkeypatch):
    monkeypatch.setenv('SLURM_JOB_ID', 'synthetic-test')
    manifest = tmp_path / 'manifest.json'; receipt = tmp_path / 'receipt.json'
    native.write(manifest, {'selected_record_ids': ['source'], 'parent_census_file_sha256': 'parent-pin',
                            'batches': [{'batch_id': 'b1', 'oversized': False}]})
    native.write(receipt, {'selected_record_ids': ['source'], 'held_records': {}, 'reused_records': {},
                           'pending_records': [], 'manifest_sha256': native.sha(manifest), 'source_census_sha256': 'parent-pin'})
    monkeypatch.setattr(batch.batching, 'load_batches', lambda _: (native.read(manifest), {'rows': [{'record_id': 'source'}]}))
    async def failed(*args, **kwargs): raise ValueError('Fixture corrupted identity inputs')
    monkeypatch.setattr(batch.batching, 'execute_batches', failed)
    with pytest.raises(ValueError, match='corrupted identity'):
        await batch.execute(manifest, receipt, tmp_path / 'run')
    state = native.read(tmp_path / 'run/state.json')
    assert state['status'] == 'failed' and state['finished_at']
    assert not (tmp_path / 'run/extractions').exists()


def test_scope_receipt_rejects_stale_hash_or_missing_parent_coverage():
    manifest = {'selected_record_ids': ['new'], 'parent_census_file_sha256': 'parent'}
    parent = {'rows': [{'record_id': rid} for rid in ('new', 'later')]}
    receipt = {'manifest_sha256': 'manifest', 'source_census_sha256': 'parent',
               'selected_record_ids': ['new'], 'held_records': {}, 'reused_records': {}, 'pending_records': ['later']}
    with pytest.raises(ValueError, match='bind this manifest'):
        batch.validate_scope(manifest, receipt, parent, 'changed')
    receipt['pending_records'] = []
    with pytest.raises(ValueError, match='every parent source'):
        batch.validate_scope(manifest, receipt, parent, 'manifest')
