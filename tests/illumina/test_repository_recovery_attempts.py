"""Recovery in a cloned vault must preserve earlier acquisition attempts."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


FILE = (Path(__file__).resolve().parents[2] / 'provenance/experiments/'
        'technology_sources_20261105/release_01/task2_03/recover_repositories.py')
SPEC = importlib.util.spec_from_file_location('task2_repository_recovery', FILE)
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


@pytest.mark.parametrize('attempt', ['new_repository_attempt', 'acquisition_repositories', '../unsafe'])
def test_recovery_namespace_preserves_old_downloads(tmp_path, monkeypatch, attempt):
    old = tmp_path / 'acquisition_repositories'
    old.mkdir(); (old / 'original.txt').write_text('Preserved original bytes')
    title, doi = 'Original assay paper', '10.1234/assay'
    (tmp_path / 'collection.json').write_text(json.dumps({'papers': {'p': {'id': 'p',
        'status': 'metadata_only', 'files': [],
        'metadata': {'title': title, 'identifiers': {'doi': [doi]}}}}, 'files': {}}))
    for name, obj in [('targets', ['p']), ('holds', {'holds': []}),
                      ('urls', {'p': ['https://source.example/article.pdf']})]:
        (tmp_path / (name + '.json')).write_text(json.dumps(obj))
    def fetch(url, path, **kwargs):
        if 'crossref.org' in url:
            path.write_text(json.dumps({'message': {'DOI': doi, 'title': [title]}}))
        elif 'openalex.org' in url:
            path.write_text(json.dumps({'title': title, 'locations': []}))
        else:
            path.write_bytes(b'%PDF-test-original')
        return {'url': url}
    def convert(cmd, **kwargs):
        if cmd[-1] == '-':
            return subprocess.CompletedProcess(cmd, 0, stdout=(title + ' ' + doi).encode())
        Path(cmd[-1]).write_text('Original article methods. ' * 120)
        return subprocess.CompletedProcess(cmd, 0, stdout=b'')
    monkeypatch.setattr(RECOVERY, 'fetch', fetch)
    monkeypatch.setattr(RECOVERY.subprocess, 'run', convert)
    args = ['recover', '--root', str(tmp_path), '--targets', str(tmp_path/'targets.json'),
            '--holds', str(tmp_path/'holds.json'), '--additional-urls', str(tmp_path/'urls.json'),
            '--attempt-name', attempt]
    monkeypatch.setattr(sys, 'argv', args)
    if attempt == 'acquisition_repositories':
        with pytest.raises(FileExistsError): RECOVERY.main()
    elif attempt == '../unsafe':
        with pytest.raises(SystemExit): RECOVERY.main()
    else:
        RECOVERY.main()
        state = json.loads((tmp_path/'collection.json').read_text())
        assert state['papers']['p']['status'] == 'fulltext_acquired'
        assert all(f['path'].startswith('new_repository_attempt/') for f in state['papers']['p']['files'])
    assert (old / 'original.txt').read_text() == 'Preserved original bytes'
