"""Run provenance and replay controls; no scientific/model/network calls."""
import json
from pathlib import Path
from types import SimpleNamespace
import tarfile

import pytest

from genomics_discovery.execution import pipeline as p


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'repo'; (root / 'src').mkdir(parents=True)
    (root / 'src/worker.py').write_text('original source\n')
    (root / 'input.json').write_text('{"original": true}\n')
    action = {'stage': 'sources', 'native_models': False,
        'entrypoint': ['{python}', '{repo}/src/worker.py'], 'code_patterns': [],
        'inputs': {'source': 'input.json'}, 'parameters': {'workers': '2'},
        'arguments': ['--source', '{source}', '--workers', '{workers}', '--output', '{output}']}
    mapping = {'format_version': 'atlas_pipeline/1',
        'stages': [{'id': key} for key in ('sources', 'extraction', 'evolution')],
        'code_patterns': ['src/*.py'], 'actions': {'sources.test': action},
        'model': 'gpt-6.1-sol', 'effort': 'xhigh'}
    (root / 'pipeline.json').write_text(json.dumps(mapping))
    return root, tmp_path / 'run'


def prepare(project, **kw):
    root, run = project
    return p.plan(root / 'pipeline.json', 'sources.test', run, **kw)


def test_planning_archives_actual_code_inputs_and_command_without_running_stage(project):
    root, run = project; result = prepare(project, parameters={'workers': '50'})
    assert result['resolved_values']['workers'] == '50'
    assert result['command'][-1] == str(run / 'output')
    assert result['input_pins'][str(root / 'input.json')] == p.sha(root / 'input.json')
    assert not (run / 'started.json').exists()
    with tarfile.open(run / 'code.tar') as archive:
        assert archive.extractfile('src/worker.py').read() == b'original source\n'
    assert p.verify(run, current_code=True)['stage'] == 'sources'


def test_later_working_code_edits_do_not_rewrite_old_run_archive(project):
    root, run = project; prepare(project)
    (root / 'src/worker.py').write_text('new version\n')
    p.verify(run)
    with pytest.raises(ValueError, match='Pinned file changed'):
        p.verify(run, current_code=True)
    with tarfile.open(run / 'code.tar') as archive:
        assert archive.extractfile('src/worker.py').read() == b'original source\n'


@pytest.mark.parametrize('changed', ['input', 'manifest', 'archive'])
def test_changed_input_manifest_or_archive_is_detected(project, changed):
    root, run = project; prepare(project)
    target = {'input': root / 'input.json', 'manifest': run / 'manifest.json', 'archive': run / 'code.tar'}[changed]
    target.write_bytes(target.read_bytes() + b'changed')
    with pytest.raises(ValueError): p.verify(run)


def test_unset_missing_and_unknown_inputs_stop_before_creating_run(project, monkeypatch):
    root, run = project
    with pytest.raises(ValueError, match='Unknown input'): prepare(project, inputs={'invented': 'input.json'})
    with pytest.raises(ValueError, match='missing'): prepare(project, inputs={'source': 'absent.json'})
    monkeypatch.delenv('ATLAS_TEST_UNSET', raising=False)
    with pytest.raises(ValueError, match='unset environment'):
        prepare(project, inputs={'source': '${ATLAS_TEST_UNSET}/input.json'})
    assert not run.exists()


def test_existing_run_never_overwritten(project):
    _, run = project; prepare(project)
    before = (run / 'manifest.json').read_bytes()
    with pytest.raises(FileExistsError): prepare(project)
    assert (run / 'manifest.json').read_bytes() == before


def test_command_parameters_are_argv_not_shell_code(project):
    injected = '1; echo should-never-execute'
    result = prepare(project, parameters={'workers': injected})
    assert injected in result['command']


def test_execute_rejects_local_runs_and_records_process_outcome(project, monkeypatch):
    _, run = project; prepare(project)
    monkeypatch.delenv('SLURM_JOB_ID', raising=False)
    with pytest.raises(ValueError, match='Qiu Sherlock'): p.execute(run)
    monkeypatch.setenv('SLURM_JOB_ID', 'offline-fixture')
    monkeypatch.setenv('GROUP_SCRATCH', '/scratch/groups/xiaojie')
    def process(command, **kwargs):
        assert isinstance(command, list) and 'shell' not in kwargs
        (run / 'output').mkdir(); (run / 'output/state.json').write_text('{"science": "needs_review"}')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(p.subprocess, 'run', process)
    result = p.execute(run)
    assert result['status'] == 'completed' and 'output/state.json' in result['output_pins']
    p.verify(run)
    with pytest.raises(ValueError, match='already started'): p.execute(run)
    (run / 'output/state.json').write_text('changed')
    with pytest.raises(ValueError, match='Recorded output changed'): p.verify(run)


def test_execution_failure_and_code_drift_remain_failed_not_scientific_acceptance(project, monkeypatch):
    root, run = project; prepare(project)
    monkeypatch.setenv('SLURM_JOB_ID', 'offline-fixture'); monkeypatch.setenv('GROUP_SCRATCH', '/scratch/groups/xiaojie')
    def process(*args, **kw):
        (root / 'src/worker.py').write_text('changed during run')
        return SimpleNamespace(returncode=7)
    monkeypatch.setattr(p.subprocess, 'run', process)
    result = p.execute(run)
    assert result['status'] == 'failed' and result['process_exit_code'] == 7
    assert result['input_or_code_drift']
    assert 'scientific' not in result or result['scientific'] is not True


def test_native_account_and_result_checksum_are_enforced(project, monkeypatch):
    root, run = project
    value = json.loads((root / 'pipeline.json').read_text())
    value['actions']['sources.test']['native_models'] = True
    (root / 'pipeline.json').write_text(json.dumps(value)); prepare(project)
    monkeypatch.setenv('SLURM_JOB_ID', 'offline-fixture'); monkeypatch.setenv('GROUP_SCRATCH', '/scratch/groups/xiaojie')
    monkeypatch.delenv('ATLAS_CODEX_ACCOUNT_LABEL', raising=False)
    with pytest.raises(ValueError, match='account helper'): p.execute(run)
    monkeypatch.setenv('ATLAS_CODEX_ACCOUNT_LABEL', 'atlas-agent-runs')
    monkeypatch.setattr(p.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=0))
    p.execute(run); (run / 'result.json').write_text('{}')
    with pytest.raises(ValueError, match='result record changed'): p.verify(run)
