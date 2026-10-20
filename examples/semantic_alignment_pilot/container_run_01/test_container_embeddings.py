"""Subprocess-contract checks only; no container, embedding model or Codex is run."""

import json
import subprocess

import pytest

from container_embeddings import ContainerEmbedder
from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache
from genomics_discovery.curation.workflow_extraction.models import digest


class StubRunner:
    def __init__(self):
        self.calls, self.fault = [], None
        self.metadata = {'model': 'fixture', 'artifacts': {'weights': 'a' * 64}}

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.fault == 'timeout':
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        request = json.loads(kwargs['input'])
        response = {'format_version': 'container_embeddings/1.0',
            'request_sha256': digest(request), 'metadata': self.metadata,
            'runtime': {'python': 'fixture'},
            'vectors': [[float(len(text)), 1.0] for text in request['texts']]}
        if self.fault == 'request':
            response['request_sha256'] = '0' * 64
        if self.fault == 'count':
            response['vectors'] = []
        return subprocess.CompletedProcess(command, 9 if self.fault == 'exit' else 0,
            json.dumps(response), 'stub diagnostics\n' if self.fault == 'exit' else '')


def make_bridge(tmp_path, runner):
    image = tmp_path / 'atlas.sif'
    image.write_bytes(b'not a real image')
    directories = [tmp_path / name for name in ('venv', 'candidate', 'models')]
    for directory in directories:
        directory.mkdir()
    adapter = directories[1] / 'src/genomics_discovery/curation/technology_graph/embeddings.py'
    adapter.parent.mkdir(parents=True)
    adapter.write_text('# frozen adapter fixture, never executed\n')
    return ContainerEmbedder(image, *directories, timeout_seconds=3, runner=runner), image


def test_bridge_uses_explicit_readonly_mounts_and_host_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('APPTAINER_BIND', '/unwanted')
    monkeypatch.setenv('APPTAINERENV_SECRET', 'fixture')
    monkeypatch.setenv('SINGULARITYENV_SECRET', 'fixture')
    runner = StubRunner()
    backend, _ = make_bridge(tmp_path, runner)
    cache = EmbeddingCache(tmp_path / 'vectors.sqlite', backend)
    assert cache.encode(['abc', 'abc', 'de']) == [[3.0, 1.0], [3.0, 1.0], [2.0, 1.0]]
    assert cache.encode(['abc']) == [[3.0, 1.0]]
    assert len(runner.calls) == 2  # Metadata bootstrap and one cache-miss batch.
    command, arguments = runner.calls[1]
    assert json.loads(arguments['input']) == {'texts': ['abc', 'de']}
    assert arguments['timeout'] == 3
    assert '--cleanenv' in command and '--containall' in command and '--no-home' in command
    assert f'{tmp_path / "candidate"}:/atlas:ro' in command
    assert f'{tmp_path / "venv"}:/opt/semantic:ro' in command
    assert f'{tmp_path / "models"}:/models:ro' in command
    assert not any(key.startswith(('APPTAINER', 'SINGULARITY')) for key in arguments['env'])
    assert backend.metadata['execution']['image_sha256']
    assert backend.metadata['execution']['embedding_module_sha256']


def test_bridge_rejects_changed_image_before_starting_another_process(tmp_path):
    runner = StubRunner()
    backend, image = make_bridge(tmp_path, runner)
    image.write_bytes(b'a different image')
    with pytest.raises(ValueError, match='changed'):
        backend.encode(['example'])
    assert len(runner.calls) == 1


def test_bridge_rejects_changed_backend_metadata(tmp_path):
    runner = StubRunner()
    backend, _ = make_bridge(tmp_path, runner)
    runner.metadata = {**runner.metadata, 'model': 'different-fixture'}
    with pytest.raises(ValueError, match='metadata changed'):
        backend.encode(['example'])


@pytest.mark.parametrize('fault', ['request', 'count'])
def test_bridge_rejects_mismatched_response(tmp_path, fault):
    runner = StubRunner()
    backend, _ = make_bridge(tmp_path, runner)
    runner.fault = fault
    with pytest.raises(ValueError, match='response'):
        backend.encode(['example'])


def test_bridge_preserves_subprocess_failure_and_timeout(tmp_path, capsys):
    runner = StubRunner()
    backend, _ = make_bridge(tmp_path, runner)
    runner.fault = 'exit'
    with pytest.raises(RuntimeError, match='exit code 9'):
        backend.encode(['example'])
    assert 'stub diagnostics' in capsys.readouterr().err
    runner.fault = 'timeout'
    with pytest.raises(subprocess.TimeoutExpired):
        backend.encode(['example'])
