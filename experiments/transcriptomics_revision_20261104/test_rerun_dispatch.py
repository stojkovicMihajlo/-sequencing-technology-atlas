"""A targeted rerun must not duplicate active work or lose identity/source custody."""
import pytest

from experiments.transcriptomics_revision_20261104 import rerun_dispatch as rerun
from genomics_discovery.illumina.extraction import read, sha, write


@pytest.fixture
def parent(tmp_path, monkeypatch):
    root = tmp_path / 'parent'
    index = root / 'global_inputs/index.json'
    write(index, {'fixture': True})
    units = [{'unit_id': ident} for ident in ['a', 'b', 'c']]
    decisions = {'admitted_units': [{'unit': unit} for unit in units]}
    write(root / 'component_runs.json', {})
    write(root / 'dispatch_decisions.json', decisions)
    monkeypatch.setattr(rerun.global_plan, 'admit_global', lambda *args: decisions)
    worklist = {'format_version': 'atlas_admitted_worklist/1', 'global_index_sha256': sha(index),
                'dispatch_decisions_sha256': rerun.atlas_batch.digest(decisions), 'units': units}
    write(root / 'dispatch_sources/worklist.json', worklist)
    write(root / 'dispatch_sources/workflow_bundles/manifest.json',
          {'worklist_sha256': rerun.atlas_batch.digest(worklist),
           'bundles': [{'unit_id': u['unit_id']} for u in units]})
    cards = {}
    for unit in units:
        ident = unit['unit_id']
        attempt = root / 'extractions/cards' / ident
        pins = {}
        for name in ['extraction.json', 'review.json', 'stages.json']:
            write(attempt / name, {'fixture': ident})
            pins[name] = sha(attempt / name)
        cards[ident] = {'status': 'supported', 'attempt_directory': str(attempt), 'artifact_pins': pins}
    write(root / 'extractions/state.json', {'cards': cards})
    return root


def test_select_only_requested_completed_units(parent):
    worklist, bundles, receipt = rerun.selected_sources(parent, {'c': 'audited issue', 'a': 'audited issue'})
    assert [u['unit_id'] for u in worklist['units']] == ['a', 'c']
    assert [b['unit_id'] for b in bundles] == ['a', 'c']
    assert set(receipt['selected']) == {'a', 'c'}
    assert receipt['prior_extractions_used_as_evidence'] is False


def test_do_not_duplicate_an_active_extraction(parent):
    path = parent / 'extractions/state.json'
    state = read(path); state['cards']['a']['status'] = 'running'; write(path, state)
    with pytest.raises(ValueError, match='not active work'):
        rerun.selected_sources(parent, {'a': 'fix'})


def test_modified_original_attempt_is_rejected(parent):
    write(parent / 'extractions/cards/a/review.json', {'changed': True})
    with pytest.raises(ValueError, match='artifacts changed'):
        rerun.selected_sources(parent, {'a': 'fix'})


def test_changed_identity_decision_is_rejected(parent):
    write(parent / 'dispatch_decisions.json', {'admitted_units': []})
    with pytest.raises(ValueError, match='identity decisions differ'):
        rerun.selected_sources(parent, {'a': 'fix'})


@pytest.mark.parametrize('selection', [{}, {'a': ''}, {'a': None}, {'missing': 'fix'}])
def test_selection_requires_known_method_and_reason(parent, selection):
    with pytest.raises(ValueError):
        rerun.selected_sources(parent, selection)


@pytest.mark.asyncio
async def test_rerun_requires_slurm(parent, tmp_path, monkeypatch):
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    with pytest.raises(ValueError, match='Slurm'):
        await rerun.execute(parent, {'a': 'fix'}, tmp_path / 'new')
    assert not (tmp_path / 'new').exists()
