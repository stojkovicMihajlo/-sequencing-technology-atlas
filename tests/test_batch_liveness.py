"""Read-only process diagnostics never turn saved status into process evidence."""

import json
import socket

import pytest

from genomics_discovery.curation import deployment
from genomics_discovery.curation.extraction_batch import plan, status
from genomics_discovery.curation.extraction_batch.controller import _native_uncertainty
from genomics_discovery.curation.extraction_batch.storage import (
    controller_liveness, controller_lock, read, write,
)
from test_deployment import config, forbidden, inputs, worker_factory
from test_extraction_batch import inventory
from test_workflow_extraction_runtime import source_case as source_case


def test_liveness_uses_lock_and_never_creates_missing_record(tmp_path):
    assert controller_liveness(tmp_path)['status'] == 'inactive'
    assert not (tmp_path / '.controller.lock').exists()
    with controller_lock(tmp_path):
        owner = read(tmp_path / '.controller.lock')
        before = (tmp_path / '.controller.lock').read_bytes()
        assert owner['hostname'] == socket.gethostname()
        assert controller_liveness(tmp_path) == {'status': 'active', 'last_owner': owner}
        assert (tmp_path / '.controller.lock').read_bytes() == before
    # The PID still belongs to this live test process; the released lock proves
    # that no controller owns the directory.
    assert controller_liveness(tmp_path) == {'status': 'inactive', 'last_owner': owner}


def test_saved_running_is_interrupted_without_rewriting_archived_state(source_case, tmp_path, monkeypatch):
    directory = tmp_path / 'batch'
    plan(inventory(tmp_path, source_case), directory)
    saved = read(directory / 'state.json')
    saved['status'] = saved['jobs']['good']['status'] = 'running'
    write(directory / 'state.json', saved)
    started = directory / 'methods/good/attempts/0001/attempts/001_integrator/started.json'
    write(started, {'pid': 123, 'hostname': 'historical-mac'})
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}

    def forbidden(*args):
        pytest.fail('Status must not signal or query a saved PID')

    monkeypatch.setattr('os.kill', forbidden)
    observed = status(directory)
    assert observed['status'] == 'interrupted' and observed['recorded_status'] == 'running'
    assert observed['jobs']['good']['status'] == 'interrupted'
    assert observed['jobs']['good']['recorded_status'] == 'running'
    assert observed['native_session_uncertainty'] == [str(started)]
    assert {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()} == before
    with controller_lock(directory):
        assert status(directory)['status'] == 'running'


@pytest.mark.parametrize('host', [None, 'historical-remote'])
def test_legacy_or_remote_native_pid_is_uncertain_without_local_probe(tmp_path, monkeypatch, host):
    started = tmp_path / 'methods/good/attempts/0001/attempts/001_integrator/started.json'
    write(started, {'pid': 123, **({'hostname': host} if host else {})})
    monkeypatch.setattr('os.kill', lambda *args: pytest.fail('Unqualified PID must not be probed'))
    assert _native_uncertainty(tmp_path) == [str(started)]
    write(started.parent / 'result.json', {'completed': True})
    assert _native_uncertainty(tmp_path) == []


def test_current_host_absent_native_process_can_be_recovered(tmp_path, monkeypatch):
    started = tmp_path / 'methods/good/attempts/0001/attempts/001_integrator/started.json'
    write(started, {'pid': 123, 'hostname': socket.gethostname()})

    def absent(pid, signal):
        assert (pid, signal) == (123, 0)
        raise ProcessLookupError

    monkeypatch.setattr('os.kill', absent)
    assert _native_uncertainty(tmp_path) == []


def test_deployment_reports_saved_running_without_mutating_progress(source_case, tmp_path):
    manifest, atlas, _ = inputs(tmp_path, source_case, ('a',))
    output = tmp_path / 'deployment'
    deployment.plan_deployment(manifest, atlas, output)
    state = read(output / 'state.json')
    state.update(status='running', active_chunk=1)
    state['chunks'][0]['status'] = 'running'
    state['methods']['a']['status'] = 'running'
    write(output / 'state.json', state)
    batch = output / 'chunks/0001/batch'
    batch_state = read(batch / 'state.json')
    batch_state['status'] = batch_state['jobs']['a']['status'] = 'running'
    write(batch / 'state.json', batch_state)
    before = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}
    observed = deployment.status_deployment(output)
    assert observed['status'] == 'interrupted' and observed['recorded_status'] == 'running'
    assert observed['methods']['a']['status'] == 'interrupted'
    assert observed['status_counts'] == {'interrupted': 1}
    assert observed['recorded_status_counts'] == {'running': 1}
    assert {p: p.read_bytes() for p in output.rglob('*') if p.is_file()} == before


async def test_invalid_publication_remains_diagnostic_but_cannot_resume_or_publish(source_case, tmp_path, capsys):
    from genomics_discovery.curation import batch_graph

    manifest, atlas, cases = inputs(tmp_path, source_case, ('a',))
    output = tmp_path / 'deployment'
    deployment.plan_deployment(manifest, atlas, output, config=config())
    result = await deployment.run_deployment(output, worker_factory=worker_factory(cases))
    publication = output / 'chunks/0001/publications/0001'
    receipt = read(publication / 'publication.json')
    receipt.pop('source_files')  # An old publication requiring explicit recovery.
    write(publication / 'publication.json', receipt)
    result['status'] = 'running'
    write(output / 'state.json', result)
    before = {p: p.read_bytes() for p in output.rglob('*') if p.is_file()}

    observed = deployment.status_deployment(output)
    assert observed['status'] == 'interrupted' and observed['recorded_status'] == 'running'
    assert observed['controller']['status'] == 'inactive'
    assert observed['publication_validation']['status'] == 'failed'
    assert observed['publication_validation']['error_type'] == 'ValueError'
    assert 'Legacy publication lacks source pins' in observed['publication_validation']['error']
    assert observed['input_status'] == 'unchanged'
    assert deployment.main(['status', str(output)]) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary['publication_validation']['status'] == 'failed'
    assert summary['recorded_status'] == 'running' and summary['controller_status'] == 'inactive'
    assert {p: p.read_bytes() for p in output.rglob('*') if p.is_file()} == before

    with pytest.raises(ValueError, match='Legacy publication lacks source pins'):
        await deployment.run_deployment(output, worker_factory=forbidden)
    with pytest.raises(ValueError, match='Legacy publication lacks source pins'):
        batch_graph.publish(output / 'chunks/0001/batch', atlas, tmp_path / 'rejected', previous=publication)
    assert not (tmp_path / 'rejected').exists()
    assert {p: p.read_bytes() for p in before if p.name == 'state.json'} == {
        p: content for p, content in before.items() if p.name == 'state.json'}
