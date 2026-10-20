"""Experiment-only JSON bridge to the frozen LocalEmbedder; Codex stays on the host."""

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

from genomics_discovery.curation.workflow_extraction.models import digest


CHILD = r'''
import contextlib, json, platform, sys
from importlib.metadata import version
from pathlib import Path
from genomics_discovery.curation.technology_graph.embeddings import LocalEmbedder
from genomics_discovery.curation.workflow_extraction.models import digest
request = json.load(sys.stdin)
with contextlib.redirect_stdout(sys.stderr):
    backend = LocalEmbedder(Path('/models'), download=False)
    vectors = backend.encode(request['texts']) if request['texts'] else []
    runtime = {'python': sys.version, 'numpy': version('numpy'),
               'tokenizers': version('tokenizers'), 'machine': platform.machine(),
               'libc': platform.libc_ver()}
json.dump({'format_version': 'container_embeddings/1.0', 'request_sha256': digest(request),
           'metadata': backend.metadata, 'runtime': runtime, 'vectors': vectors},
          sys.stdout, ensure_ascii=False, allow_nan=False)
sys.stdout.write('\n')
'''


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class ContainerEmbedder:
    def __init__(self, image, venv_directory, code_root, models_directory, *,
                 timeout_seconds=300, runner=None):
        if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('Embedding subprocess timeout must be positive and finite')
        image, venv, code, models = (Path(p).resolve(strict=True) for p in
                                    (image, venv_directory, code_root, models_directory))
        if not image.is_file() or not all(p.is_dir() for p in (venv, code, models)):
            raise ValueError('Use an existing image file and explicit environment/code/model directories')
        if any(':' in str(p) or ',' in str(p) for p in (venv, code, models)):
            raise ValueError('Embedding bind paths cannot contain colon or comma delimiters')
        module = code / 'src/genomics_discovery/curation/technology_graph/embeddings.py'
        self.pins = {path: file_sha(path) for path in (image, Path(__file__).resolve(), module)}
        self.timeout_seconds, self.runner = timeout_seconds, runner or subprocess.run
        self.command = ['/usr/bin/apptainer', 'exec', '--cleanenv', '--containall', '--no-home',
            '--no-mount', 'hostfs,cwd,bind-paths', '--pwd', '/tmp',
            '--bind', f'{code}:/atlas:ro', '--bind', f'{venv}:/opt/semantic:ro',
            '--bind', f'{models}:/models:ro', '--bind', '/etc/agents:/etc/agents:ro',
            '--env', 'PYTHONPATH=/atlas/src', '--env', 'PYTHONDONTWRITEBYTECODE=1',
            '--env', 'OMP_NUM_THREADS=1', '--env', 'TOKENIZERS_PARALLELISM=false',
            '--env', 'HF_HOME=/tmp/huggingface', '--env', 'HF_XET_CACHE=/tmp/xet',
            '--env', 'XDG_CACHE_HOME=/tmp/xdg',
            str(image), '/opt/semantic/bin/python', '-c', CHILD]
        self._expected = None
        initial = self._request([])
        self._expected = (initial['metadata'], initial['runtime'])
        self.metadata = {**initial['metadata'], 'execution': {
            'bridge': 'apptainer_json/1.0', 'image_sha256': self.pins[image],
            'bridge_sha256': self.pins[Path(__file__).resolve()],
            'embedding_module_sha256': self.pins[module], 'runtime': initial['runtime']}}

    def _check_pins(self):
        if any(not path.is_file() or file_sha(path) != expected for path, expected in self.pins.items()):
            raise ValueError('Embedding image, bridge or frozen adapter changed')

    def _request(self, texts):
        self._check_pins()
        request = {'texts': texts}
        # Prevent inherited Apptainer settings from adding mounts or forwarded credentials.
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('APPTAINER_', 'APPTAINERENV_',
                                              'SINGULARITY_', 'SINGULARITYENV_'))}
        completed = self.runner(self.command, input=json.dumps(request, ensure_ascii=False, allow_nan=False),
            text=True, capture_output=True, check=False, timeout=self.timeout_seconds, env=environment)
        if completed.stderr:
            sys.stderr.write(completed.stderr)
        if completed.returncode:
            raise RuntimeError(f'Container embedding subprocess failed with exit code {completed.returncode}')
        self._check_pins()
        response = json.loads(completed.stdout)
        if (response.get('format_version') != 'container_embeddings/1.0'
                or response.get('request_sha256') != digest(request)
                or not isinstance(response.get('metadata'), dict)
                or not isinstance(response.get('runtime'), dict)
                or not isinstance(response.get('vectors'), list)
                or len(response['vectors']) != len(texts)):
            raise ValueError('Invalid or mismatched container embedding response')
        if self._expected is not None and (response['metadata'], response['runtime']) != self._expected:
            raise ValueError('Container embedding model or runtime metadata changed')
        return response

    def encode(self, texts):
        texts = list(texts)
        if any(not isinstance(text, str) for text in texts):
            raise ValueError('Embedding inputs must be text strings')
        return self._request(texts)['vectors'] if texts else []
