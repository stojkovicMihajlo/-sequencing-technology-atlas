"""Record and run the existing three-stage pipeline; scientific gates stay in each stage."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile

from .batch.storage import check_pins, controller_lock, read, write


def now():
    return datetime.now(UTC).isoformat()


def sha(path):
    """Stream large files and reject partial/cloud-placeholder reads or concurrent edits."""
    path = Path(path)
    before = path.stat()
    value, count = hashlib.sha256(), 0
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block); count += len(block)
    after = path.stat()
    if count != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('File changed or was incompletely read: ' + str(path))
    return value.hexdigest()


def load_map(path):
    path = Path(path).resolve(); data = read(path)
    if data.get('format_version') != 'atlas_pipeline/1':
        raise ValueError('Unknown pipeline map version')
    ids = [row['id'] for row in data['stages']]
    if ids != ['sources', 'extraction', 'evolution']:
        raise ValueError('Pipeline order must be sources, extraction, evolution')
    for ident, action in data['actions'].items():
        if action['stage'] not in ids or not action['entrypoint']:
            raise ValueError('Invalid pipeline action: ' + ident)
        if (set(action['inputs']) & set(action['parameters']) or
                (set(action['inputs']) | set(action['parameters'])) & {'repo', 'python', 'output'}):
            raise ValueError('Input names conflict with parameters: ' + ident)
    return path, data


def resolve_input(value, root):
    if not value:
        raise ValueError('A required input is unset; supply --input NAME=PATH')
    expanded = os.path.expandvars(str(value))
    if re.search(r'\$(?:\{|[A-Za-z_])', expanded):
        raise ValueError('Input contains an unset environment variable: ' + str(value))
    path = Path(expanded).expanduser()
    path = (root / path if not path.is_absolute() else path).resolve()
    if not path.is_file():
        raise ValueError('Required input file is missing: ' + str(path))
    return path


def assignments(values):
    result = {}
    for text in values:
        key, sep, value = text.partition('=')
        if not sep or not key or key in result:
            raise ValueError('Use each NAME=VALUE override once')
        result[key] = value
    return result


def plan(map_path, action_id, directory, *, inputs=None, parameters=None):
    map_path, mapping = load_map(map_path); root = map_path.parent
    action = mapping['actions'][action_id]
    inputs, parameters = inputs or {}, parameters or {}
    if set(inputs) - set(action['inputs']) or set(parameters) - set(action['parameters']):
        raise ValueError('Unknown input or parameter override')
    directory = Path(directory).resolve()
    supplied = {key: resolve_input(value, root) for key, value in {**action['inputs'], **inputs}.items()}
    if any(directory == path or directory in path.parents for path in supplied.values()):
        raise ValueError('Run directory would contain an original input')
    values = {**{key: str(value) for key, value in action['parameters'].items()},
              **{key: str(value) for key, value in parameters.items()},
              **{key: str(value) for key, value in supplied.items()},
              'repo': str(root), 'python': sys.executable, 'output': str(directory / 'output')}
    if any(not value for value in values.values()):
        raise ValueError('A required parameter is unset; supply --set NAME=VALUE')
    command = [part.format_map(values) for part in action['entrypoint'] + action['arguments']]
    files = {map_path}
    for pattern in mapping['code_patterns'] + action['code_patterns']:
        found = list(root.glob(pattern))
        if not found:
            raise ValueError('Declared code pattern matches no files: ' + pattern)
        for path in found:
            if path.is_file():
                if not path.resolve().is_relative_to(root):
                    raise ValueError('Code must stay inside the repository: ' + str(path))
                files.add(path.resolve())
    code_pins = {str(path): sha(path) for path in sorted(files)}
    input_pins = {str(path): sha(path) for path in supplied.values()}
    git = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, text=True)
    directory.mkdir(parents=True, exist_ok=False)
    # One code archive per run preserves dirty working bytes without inventing a new implementation.
    archive_path = directory / 'code.tar'
    with tarfile.open(archive_path, 'w') as archive:
        for filename, pin in code_pins.items():
            path = Path(filename); raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != pin:
                raise ValueError('Code changed while preparing: ' + filename)
            info = tarfile.TarInfo(path.relative_to(root).as_posix())
            info.size = len(raw); info.mode = 0o644
            archive.addfile(info, io.BytesIO(raw))
    check_pins(input_pins)
    check_pins(code_pins)
    document = {'format_version': 'atlas_pipeline_run/1', 'prepared_at': now(),
        'action': action_id, 'stage': action['stage'], 'native_models': action['native_models'],
        'git_commit': git.stdout.strip() if git.returncode == 0 else None,
        'code_snapshot_authoritative': True, 'pipeline_map': str(map_path),
        'recipe': action, 'resolved_values': values, 'command': command, 'cwd': str(root),
        'input_pins': input_pins, 'code_pins': code_pins,
        'code_archive_sha256': sha(archive_path), 'python': sys.version,
        'packages': {name: importlib.metadata.version(name) for name in ('pydantic',)},
        'requested_model': mapping['model'] if action['native_models'] else None,
        'requested_effort': mapping['effort'] if action['native_models'] else None,
        'boundary': 'Exact supplied files and code snapshot. Stage registries validate underlying papers/images. Model answers need not regenerate identically. No scientific acceptance is conferred.'}
    write(directory / 'manifest.json', document)
    (directory / 'manifest.sha256').write_text(sha(directory / 'manifest.json') + '\n')
    return document


def verify(directory, *, current_code=False):
    directory = Path(directory).resolve()
    if sha(directory / 'manifest.json') != (directory / 'manifest.sha256').read_text().strip():
        raise ValueError('Run manifest changed')
    record = read(directory / 'manifest.json')
    if record.get('format_version') != 'atlas_pipeline_run/1':
        raise ValueError('Unknown run manifest')
    if sha(directory / 'code.tar') != record['code_archive_sha256']:
        raise ValueError('Saved code archive changed')
    check_pins(record['input_pins'])
    if current_code:
        check_pins(record['code_pins'])
    if (directory / 'result.json').is_file():
        if sha(directory / 'result.json') != (directory / 'result.sha256').read_text().strip():
            raise ValueError('Run result record changed')
        result = read(directory / 'result.json')
        for relative, pin in result['output_pins'].items():
            if sha(directory / relative) != pin:
                raise ValueError('Recorded output changed: ' + relative)
    return record


def execute(directory):
    if not os.environ.get('SLURM_JOB_ID') or os.environ.get('GROUP_SCRATCH') != '/scratch/groups/xiaojie':
        raise ValueError('Execute prepared runs inside Qiu Sherlock Slurm')
    directory = Path(directory).resolve()
    if not (directory / 'manifest.json').is_file():
        raise ValueError('Prepare this run before execution')
    with controller_lock(directory):
        record = verify(directory, current_code=True)
        if record['native_models'] and os.environ.get('ATLAS_CODEX_ACCOUNT_LABEL') != 'atlas-agent-runs':
            raise ValueError('Source the project Codex account helper before model runs')
        if (directory / 'result.json').exists() or (directory / 'started.json').exists():
            raise ValueError('Run already started; inspect it and use the stage-specific resume policy')
        write(directory / 'started.json', {'started_at': now(), 'job_id': os.environ['SLURM_JOB_ID'],
            'account_label': os.environ.get('ATLAS_CODEX_ACCOUNT_LABEL'),
            'archive_root': os.environ.get('ATLAS_ARCHIVE_ROOT')})
        env = os.environ.copy()
        env['PYTHONPATH'] = os.pathsep.join([record['cwd'], str(Path(record['cwd']) / 'src'),
                                           *([env['PYTHONPATH']] if env.get('PYTHONPATH') else [])])
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        with (directory / 'stdout.log').open('wb') as out, (directory / 'stderr.log').open('wb') as err:
            child = subprocess.run(record['command'], cwd=record['cwd'], env=env, stdout=out, stderr=err)
        drift = None
        try:
            verify(directory, current_code=True)
        except (OSError, ValueError) as exc:
            drift = str(exc)
        outputs = [directory / 'stdout.log', directory / 'stderr.log']
        if (directory / 'output').is_dir():
            outputs += [p for p in (directory / 'output').rglob('*') if p.is_file()]
        result = {'finished_at': now(), 'job_id': os.environ['SLURM_JOB_ID'],
            'process_exit_code': child.returncode, 'input_or_code_drift': drift,
            'status': 'completed' if child.returncode == 0 and not drift else 'failed',
            'output_pins': {str(path.relative_to(directory)): sha(path) for path in sorted(outputs)},
            'boundary': 'Process completion and file integrity; inspect stage review/admission results separately.'}
        write(directory / 'result.json', result)
        (directory / 'result.sha256').write_text(sha(directory / 'result.json') + '\n')
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--map', type=Path, default=Path('pipeline.json'))
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    prepare = sub.add_parser('plan')
    prepare.add_argument('action'); prepare.add_argument('--run-dir', type=Path, required=True)
    prepare.add_argument('--input', action='append', default=[])
    prepare.add_argument('--set', action='append', default=[])
    for name in ('execute', 'verify'):
        command = sub.add_parser(name); command.add_argument('--run-dir', type=Path, required=True)
        if name == 'verify': command.add_argument('--current-code', action='store_true')
    args = parser.parse_args()
    if args.command == 'list':
        _, data = load_map(args.map)
        result = {'stages': [{**stage, 'actions': [name for name, action in data['actions'].items()
                                                  if action['stage'] == stage['id']]}
                             for stage in data['stages']]}
    elif args.command == 'plan':
        result = plan(args.map, args.action, args.run_dir,
                      inputs=assignments(args.input), parameters=assignments(args.set))
    elif args.command == 'execute': result = execute(args.run_dir)
    else:
        verify(args.run_dir, current_code=args.current_code)
        result = {'status': 'file_integrity_verified', 'scientific_validation': False}
    print(json.dumps(result, indent=2))
    if args.command == 'execute' and result['status'] != 'completed': raise SystemExit(1)


if __name__ == '__main__': main()
