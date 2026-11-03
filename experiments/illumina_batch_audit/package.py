"""Package the audit runtime with its tracked scripts and test dependencies.

Run the packaged tests from an isolated extraction before uploading. A working
checkout does not establish that a deployable package contains its imports.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


def package(root, output, name):
    if Path(name).name != name or not name.startswith('runtime_'):
        raise ValueError('Use a single runtime_ directory name')
    roots = ['src', 'scripts', 'tests/illumina', 'pyproject.toml',
        'experiments/illumina_batch_audit', 'tests/execution/test_curation_codex_worker.py',
        'tests/execution/test_codex_terminal_failure_diagnostics.py',
        'tests/protocols/test_workflow_extraction_runtime.py',
        'tests/fixtures/codex_capacity_terminal_0_163', 'tests/__init__.py',
        'tests/execution/__init__.py', 'tests/protocols/__init__.py']
    files = subprocess.run(['git', 'ls-files', *roots], cwd=root, check=True,
                           capture_output=True, text=True).stdout.splitlines()
    files = [p for p in files if '__pycache__' not in p and not p.endswith('.pyc')]
    if not any(p.startswith('scripts/illumina/') for p in files):
        raise ValueError('Runtime lacks source-preparation script dependencies')
    output.mkdir(parents=True, exist_ok=True)
    archive = output / (name + '.tar.gz')
    manifest_path = output / (name + '_manifest.json')
    if archive.exists() or manifest_path.exists():
        raise FileExistsError('Preserve frozen packages; use a new runtime name')
    manifest = {'runtime': name, 'source_commit': subprocess.run(
        ['git', 'rev-parse', 'HEAD'], cwd=root, check=True, capture_output=True,
        text=True).stdout.strip(), 'packaged_roots': roots,
        'files': {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in files}}
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    with tarfile.open(archive, 'w:gz') as handle:
        for p in files:
            handle.add(root / p, arcname=name + '/' + p)
        handle.add(manifest_path, arcname=manifest_path.name)
    return {'files': len(files), 'bytes': archive.stat().st_size,
            'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'source_commit': manifest['source_commit']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    print(json.dumps(package(Path(__file__).resolve().parents[2], args.output, args.name)))
