"""Explicit retries select reviewed units; they neither replan nor rerun neighbors."""
import copy

import pytest

from experiments.transcriptomics_revision_20261104 import extract_plan as runner
from experiments.transcriptomics_revision_20261104 import native_planning as native
from experiments.transcriptomics_revision_20261104.test_extract_plan import planned, typed_transport


def test_selection_uses_declared_plan_order_without_mutating_inputs():
    worklist = {'units': [{'unit_id': key} for key in ('a', 'b', 'c')]}
    bundles = [{'unit_id': key, 'original': key} for key in ('c', 'a', 'b')]
    before = copy.deepcopy((worklist, bundles))
    selected = runner.select_bundles(worklist, bundles, ['c', 'a'])
    assert [b['unit_id'] for b in selected] == ['a', 'c']
    assert selected[0] is bundles[1]
    assert (worklist, bundles) == before
    assert [b['unit_id'] for b in runner.select_bundles(worklist, bundles)] == ['a', 'b', 'c']


@pytest.mark.parametrize('requested, message', [
    ([], 'at least one'), (['a', 'a'], 'Duplicate'),
    (['missing'], 'Unknown'), (['A'], 'Unknown'),
])
def test_selection_rejects_empty_duplicate_unknown_and_case_guesses(requested, message):
    with pytest.raises(ValueError, match=message):
        runner.select_bundles({'units': [{'unit_id': 'a'}]}, [{'unit_id': 'a'}], requested)


@pytest.mark.asyncio
async def test_selected_retry_does_not_read_or_launch_unselected_neighbor(tmp_path, monkeypatch):
    directory = await planned(tmp_path, monkeypatch)
    worklist, bundles = runner.load_plan(directory)
    worklist = copy.deepcopy(worklist)
    worklist['units'].append({'unit_id': 'drop_seq'})
    # This test isolates dispatch after the separately tested full-plan custody gate.
    # Reading this unused bundle would fail, proving the neighbor was not dispatched.
    bundles = [*bundles, {'unit_id': 'drop_seq', 'card_path': str(tmp_path / 'must_not_read.json')}]
    before = copy.deepcopy((worklist, bundles))
    monkeypatch.setattr(runner, 'load_plan', lambda _: (worklist, bundles))
    worker = typed_transport(monkeypatch)
    output = tmp_path / 'selected_retry'
    report = await runner.execute(directory, output, workers=1, worker=worker, unit_ids=['one_method'])
    assert report['passed']
    assert [call['role'] for call in worker.calls] == ['author', 'reviewer']
    assert not (output / 'cards/drop_seq').exists()
    state = native.read(output / 'state.json')
    assert set(state['cards']) == {'one_method'}
    assert state['policy']['worklist_sha256'] == native.digest(worklist)
    assert state['policy']['unit_selection'] == {
        'mode': 'explicit_subset', 'requested_unit_ids': ['one_method'],
        'selected_unit_ids': ['one_method'], 'unselected_unit_ids': ['drop_seq'],
    }
    assert (worklist, bundles) == before


@pytest.mark.asyncio
async def test_bad_selection_stops_before_creating_output_or_calling_worker(tmp_path, monkeypatch):
    directory = await planned(tmp_path, monkeypatch)
    worker = typed_transport(monkeypatch)
    output = tmp_path / 'not_created'
    with pytest.raises(ValueError, match='Unknown reviewed work unit'):
        await runner.execute(directory, output, worker=worker, unit_ids=['unknown'])
    assert not worker.calls
    assert not output.exists()


@pytest.mark.asyncio
async def test_subset_does_not_bypass_parent_source_pin_checks(tmp_path, monkeypatch):
    directory = await planned(tmp_path, monkeypatch)
    _, bundles = runner.load_plan(directory)
    path = bundles[0]['registry_path']
    registry = native.read(path)
    registry['web::paper:1']['text'] = 'Changed after independent review.'
    native.write(path, registry)
    worker = typed_transport(monkeypatch)
    with pytest.raises(ValueError, match='Workflow source bundle changed'):
        await runner.execute(directory, tmp_path / 'not_created', worker=worker, unit_ids=['one_method'])
    assert not worker.calls
    assert not (tmp_path / 'not_created').exists()


def test_cli_passes_explicit_subset_to_existing_reviewed_plan(tmp_path, monkeypatch):
    observed = {}

    async def execute(planning, output, **kwargs):
        observed.update(planning=planning, output=output, **kwargs)
        return {'passed': True}

    monkeypatch.setattr(runner, 'execute', execute)
    assert runner.main(['--planning-run', str(tmp_path / 'reviewed'), '--output', str(tmp_path / 'retry'),
                        '--unit-id', 'smart_seq2', '--workers', '1']) == 0
    assert observed == {'planning': (tmp_path / 'reviewed').resolve(),
                        'output': (tmp_path / 'retry/extractions').resolve(),
                        'workers': 1, 'unit_ids': ['smart_seq2']}
