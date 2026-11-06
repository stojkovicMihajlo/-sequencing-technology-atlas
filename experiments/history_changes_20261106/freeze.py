"""Freeze a portable history-test runtime, including pinned fixture dependencies.

The release must pass from an isolated extracted directory before transfer.
Archived scientific files are copied unchanged; only deployment packaging changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


TESTS = ['tests/illumina/test_history_bindings.py', 'tests/illumina/test_illumina_history.py',
    'tests/ideation/test_context.py', 'experiments/citation_lineage_pilot/test_contract.py',
    'experiments/citation_lineage_pilot/test_pair.py', 'experiments/citation_lineage_pilot/test_change_records.py',
    'experiments/citation_lineage_pilot/test_binding_run.py', 'experiments/citation_lineage_pilot/test_review_coverage.py',
    'experiments/history_changes_20261106/test_connections.py']


def fixture_files(repository, manifest):
    files = {str(manifest.relative_to(repository))}
    document = json.loads(manifest.read_text())
    for entry in document['overlays']:
        for spec in (entry, entry.get('source_registry')):
            if not spec: continue
            path = (manifest.parent / spec['path']).resolve()
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != spec['sha256']:
                raise ValueError('Fixture input changed: ' + str(path))
            files.add(str(path.relative_to(repository)))
        if 'seed_extraction' in entry:
            files.add(str((manifest.parent / entry['seed_extraction']).resolve().relative_to(repository)))
    return files


def main(args):
    repository = Path(__file__).resolve().parents[2]
    base = json.loads(args.base_manifest.read_text())
    files = set(base['files']) | fixture_files(repository,
        repository / 'experiments/evolution_review_20261103/history_inputs.json')
    files.add(str(Path(__file__).relative_to(repository)))
    files.update(('src/genomics_discovery/illumina/history_coverage.py',
        'experiments/citation_lineage_pilot/test_review_coverage.py',
        'experiments/history_changes_20261106/selection_10.json',
        'docs/decisions/2026-11-06-history-review-coverage.txt'))
    args.output.mkdir(parents=True, exist_ok=False)
    pins = {name: hashlib.sha256((repository / name).read_bytes()).hexdigest() for name in sorted(files)}
    archive = args.output / (args.name + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        for name in pins: tar.add(repository / name, arcname=name, recursive=False)
    # A relocated checkout exposes missing fixtures that tests in the original
    # repository silently obtain from unbundled local directories.
    with tempfile.TemporaryDirectory(prefix='atlas-history-release-') as temp:
        isolated = Path(temp)
        with tarfile.open(archive) as tar: tar.extractall(isolated, filter='data')
        env = {**os.environ, 'PYTHONPATH': str(isolated) + os.pathsep + str(isolated / 'src'),
            'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
        result = subprocess.run([sys.executable, '-m', 'pytest', '-p', 'asyncio', '-q', *TESTS],
            cwd=isolated, env=env, text=True, capture_output=True)
        (args.output / 'relocated_tests.txt').write_text(result.stdout + result.stderr)
        if result.returncode:
            raise ValueError('Relocated release tests failed; inspect relocated_tests.txt before transfer')
    manifest = {'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip(),
        'files': pins, 'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'relocated_software_tests': 'passed; see relocated_tests.txt',
        'reason': 'Typed coverage review, explicit reuse-versus-change instructions, generic batch selection, and complete pinned test fixtures. Existing scientific outputs stay unchanged.'}
    (args.output / (args.name + '_manifest.json')).write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'archive': str(archive), 'files': len(pins), 'sha256': manifest['archive_sha256']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--name', required=True)
    main(parser.parse_args())
