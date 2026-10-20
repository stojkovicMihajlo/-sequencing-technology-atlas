"""Sequential frozen inventory chunks using the existing extraction and graph gates."""
from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from . import batch_graph, extraction_batch as batch
from .extraction_batch.controller import _implementation_pins
from .extraction_batch.models import Inventory
from .extraction_batch.storage import (
    atomic_bytes, check_pins, controller_liveness, controller_lock, pin_health, read, sha, write,
)
from .workflow_extraction.sources import load_packet


def _publication(path, batch_run=None, previous=None, supported=None):
    path = Path(path).resolve()
    registry, _, _, pins, receipt, _ = batch_graph._previous(path)
    if batch_run is not None:
        if (receipt['batch_run'] != str(batch_run)
                or receipt['batch_plan_sha256'] != sha(batch_run / 'plan.json')
                or receipt['batch_state_sha256'] != sha(batch_run / 'state.json')
                or set(receipt['new_batch_methods']) != set(supported)):
            raise ValueError('Publication differs from its completed batch')
        ancestor = receipt.get('previous_publication')
        if ((ancestor or {}).get('directory') != (previous['path'] if previous else None)
                or (previous and ancestor['publication_sha256'] != previous['sha256'])
                or set(registry) != set(supported) | set(previous['accepted_methods'] if previous else ())):
            raise ValueError('Publication chain differs from deployment progress')
    return {'path': str(path), 'sha256': sha(path / 'publication.json'),
            'accepted_methods': sorted(registry)}, pins


def _add_pins(pins, additions):
    for path, value in additions.items():
        if path in pins and pins[path] != value:
            raise ValueError(f'Conflicting deployment pin: {path}')
        pins[path] = value


def plan_deployment(inventory, atlas, output, previous=None, batch_size=10, config=None):
    """Freeze all inventory IDs exactly once; planning launches no workers."""
    inventory, atlas, output = (Path(p).resolve() for p in (inventory, atlas, output))
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError('batch_size must be a positive integer')
    config = batch.BatchConfig.model_validate(config or batch.BatchConfig(
        concurrency=10, require_source_coverage=True))
    document = Inventory.model_validate_json(inventory.read_bytes())
    canonical = read(atlas)
    if not isinstance(canonical.get('methods'), list) or not isinstance(canonical.get('claims'), list) or not canonical.get('revision_id'):
        raise ValueError('Use the canonical atlas with methods, claims and revision_id')
    curation = Path(batch_graph.__file__).parent
    code = {Path(__file__), Path(batch_graph.__file__), *(curation / 'technology_graph').glob('*.py'),
            *(curation / 'workflow_matching').glob('*.py')}
    pins = {str(p.resolve()): sha(p) for p in {*code, inventory, atlas}}
    _add_pins(pins, _implementation_pins())
    prior = None
    if previous is not None:
        prior, prior_pins = _publication(previous)
        _add_pins(pins, prior_pins)
    if document.source_receipts is not None:
        receipt = (inventory.parent / document.source_receipts).resolve()
        _add_pins(pins, {str(receipt): sha(receipt)})
        files = read(receipt).get('files')
        if not isinstance(files, dict):
            raise ValueError('Source receipt requires a files mapping')
        for path, value in files.items():
            _add_pins(pins, {str((receipt.parent / path).resolve()): value})
    jobs = []
    for job in document.jobs:
        row = job.model_dump(mode='json', exclude_none=True)
        if row.get('seed_runtime'):
            row['seed_runtime'] = str((inventory.parent / row['seed_runtime']).resolve())
        if job.packet is not None:
            path = (inventory.parent / job.packet).resolve(); row['packet'] = str(path)
            if path.is_file():
                _add_pins(pins, {str(path): sha(path)})
                try:
                    packet = load_packet(path)
                    _add_pins(pins, {s['original_path']: s['original_sha256'] for s in packet['sources']})
                except (OSError, ValueError, KeyError):
                    pass  # Existing batch.plan isolates invalid ready jobs.
        jobs.append(row)
    check_pins(pins)
    with controller_lock(output):
        if any(p.name != '.controller.lock' for p in output.iterdir()):
            raise FileExistsError('Deployment destination must be empty')
        # Every batch checks the shared global inputs at each native boundary.
        receipt = output / 'input_receipts.json'; write(receipt, {'files': pins.copy()})
        pins[str(receipt)] = sha(receipt)
        chunks, methods = [], {}
        reused = set(prior['accepted_methods'] if prior else ())
        for start in range(0, len(jobs), batch_size):
            group = jobs[start:start + batch_size]; number = len(chunks) + 1
            directory = output / 'chunks' / f'{number:04d}'
            pending = [j for j in group if j['id'] not in reused]
            chunk = {'id': number, 'method_ids': [j['id'] for j in group], 'batch': None}
            if pending:
                manifest = directory / 'inventory.json'
                write(manifest, {'jobs': pending, 'source_receipts': str(receipt)})
                destination = directory / 'batch'; batch.plan(manifest, destination, config=config)
                chunk['batch'] = str(destination)
                frozen = read(destination / 'plan.json')
                for entry in [frozen, *frozen['jobs']]:
                    _add_pins(pins, entry['pins'])
                _add_pins(pins, {str(destination / name): sha(destination / name) for name in ('plan.json', 'plan.sha256')})
            chunks.append(chunk)
            for job in group:
                methods[job['id']] = {'chunk': number, 'status': 'already_accepted' if job['id'] in reused else job['status']}
        check_pins(pins)
        write(output / 'plan.json', {'format_version': 'atlas_deployment_plan/1.0',
              'inventory': str(inventory), 'atlas': str(atlas), 'config': config.model_dump(mode='json'),
              'batch_size': batch_size, 'jobs': jobs, 'chunks': chunks, 'previous': prior, 'pins': pins})
        atomic_bytes(output / 'plan.sha256', (sha(output / 'plan.json') + '\n').encode())
        state = {'format_version': 'atlas_deployment_state/1.0', 'status': 'planned',
                 'methods': methods, 'chunks': [{'id': c['id'], 'status': 'queued'} for c in chunks],
                 'inherited_methods': sorted(reused), 'latest_publication': prior, 'active_chunk': None}
        write(output / 'state.json', state)
        return state


def _load(output, *, verify_input_pins=True, verify_publications=True):
    if sha(output / 'plan.json') != (output / 'plan.sha256').read_text().strip():
        raise ValueError('Immutable deployment plan changed')
    plan, state = read(output / 'plan.json'), read(output / 'state.json')
    if plan.get('format_version') != 'atlas_deployment_plan/1.0' or state.get('format_version') != 'atlas_deployment_state/1.0':
        raise ValueError('Expected deployment plan/state')
    if verify_input_pins:
        check_pins(plan['pins'])
    if (set(state['methods']) != {j['id'] for j in plan['jobs']}
            or [c['id'] for c in state['chunks']] != [c['id'] for c in plan['chunks']]):
        raise ValueError('Deployment progress differs from frozen inventory')
    previous = plan['previous']
    if previous and verify_publications and _publication(previous['path'])[0] != previous:
        raise ValueError('Previous publication changed')
    incomplete = False
    for chunk, progress in zip(plan['chunks'], state['chunks']):
        if progress['status'] != 'complete':
            incomplete = True; continue
        if incomplete:
            raise ValueError('Deployment chunks completed out of order')
        if chunk['batch']:
            run = Path(chunk['batch'])
            if sha(run / 'state.json') != progress['batch_state_sha256']:
                raise ValueError('Completed batch progress changed')
            rows = read(run / 'state.json')['jobs']
            if (set(progress['supported']) != {mid for mid, row in rows.items() if row['status'] == 'supported'}
                    or bool(progress['supported']) != bool(progress.get('publication'))
                    or any(state['methods'][mid]['status'] != row['status'] for mid, row in rows.items())):
                raise ValueError('Completed chunk outcomes or publication are missing/changed')
            if progress.get('publication'):
                actual = progress['publication']
                if verify_publications:
                    actual, _ = _publication(progress['publication']['path'], run, previous, progress['supported'])
                if actual != progress['publication']:
                    raise ValueError('Committed publication changed')
                previous = actual
    if state['latest_publication'] != previous:
        raise ValueError('Latest publication differs from committed chunk chain')
    return plan, state


def status_deployment(output):
    output = Path(output).resolve()
    publication_validation = {'status': 'passed'}
    try:
        plan, state = _load(output, verify_input_pins=False)
    except (OSError, ValueError, RuntimeError) as exc:
        # A historical publication can fail newer admission rules while its
        # saved controller progress remains useful diagnostic evidence. Recheck
        # plan/state integrity, retaining publication pointers only as records.
        plan, state = _load(output, verify_input_pins=False, verify_publications=False)
        publication_validation = {'status': 'failed', 'error_type': type(exc).__name__,
                                  'error': str(exc)}
    controller = controller_liveness(output)
    methods = {mid: dict(row) for mid, row in state['methods'].items()}; active = None
    if state['active_chunk'] is not None:
        index = state['active_chunk'] - 1; chunk = plan['chunks'][index]
        if chunk['batch'] and state['chunks'][index]['status'] != 'complete':
            active = batch.status(Path(chunk['batch']))
            for mid, row in active['jobs'].items():
                methods[mid].update(recorded_status=methods[mid]['status'], status=row['status'])
    effective_status = state['status']
    if effective_status == 'running' and controller['status'] == 'inactive':
        effective_status = 'interrupted'
    health = pin_health(plan['pins'])
    input_status = 'pin_drift' if (any(value != 'unchanged' for value in health.values())
                                 or (active and active['input_status'] == 'pin_drift')) else 'unchanged'
    return {**state, 'status': effective_status, 'recorded_status': state['status'],
            'controller': controller, 'global_input_health': health, 'input_status': input_status,
            'publication_validation': publication_validation,
            'methods': methods, 'active_batch': active,
            'recorded_status_counts': dict(Counter(m['status'] for m in state['methods'].values())),
            'status_counts': dict(Counter(m['status'] for m in methods.values()))}


async def run_deployment(output, *, worker_factory=None):
    """Finish one chunk, publish its supported rows, then start the next chunk."""
    output = Path(output).resolve()
    with controller_lock(output):
        plan, state = _load(output); previous = plan['previous']
        state['status'] = 'running'; write(output / 'state.json', state)
        try:
            for chunk, progress in zip(plan['chunks'], state['chunks']):
                if progress['status'] == 'complete':
                    previous = progress.get('publication') or previous; continue
                check_pins(plan['pins'])
                if previous and _publication(previous['path'])[0] != previous:
                    raise ValueError('Previous publication changed')
                state['active_chunk'] = chunk['id']; progress['status'] = 'running'
                write(output / 'state.json', state)
                if chunk['batch']:
                    destination = Path(chunk['batch'])
                    if not progress.get('batch_state_sha256'):
                        result = await batch.run(destination, worker_factory=worker_factory)
                        if result['status'] == 'blocked_native_sessions':
                            state['status'] = result['status']; write(output / 'state.json', state); return state
                        progress['batch_state_sha256'] = sha(destination / 'state.json')
                        progress['supported'] = [mid for mid, row in result['jobs'].items() if row['status'] == 'supported']
                        for mid, row in result['jobs'].items():
                            state['methods'][mid].update(status=row['status'], result=row.get('result'),
                                reason=row.get('error', row.get('reason')), next_evidence=row.get('next_evidence'))
                        write(output / 'state.json', state)
                    if sha(destination / 'state.json') != progress['batch_state_sha256']:
                        raise ValueError('Stopped batch progress changed')
                    check_pins(plan['pins'])
                    if progress['supported']:
                        attempt = Path(progress.get('publication_attempt', destination.parent / 'publications' / '0001'))
                        while attempt.exists() and not (attempt / 'publication.json').exists():
                            attempt = attempt.with_name(f'{int(attempt.name) + 1:04d}')
                        progress.update(status='publishing', publication_attempt=str(attempt))
                        write(output / 'state.json', state)
                        if not attempt.exists():
                            batch_graph.publish(destination, Path(plan['atlas']), attempt,
                                                previous=Path(previous['path']) if previous else None)
                        previous, _ = _publication(attempt, destination, previous, progress['supported'])
                        progress['publication'] = previous
                progress['status'] = 'complete'; state['latest_publication'] = previous
                write(output / 'state.json', state)
            check_pins(plan['pins'])
            if previous and _publication(previous['path'])[0] != previous:
                raise ValueError('Latest publication changed')
            state.update(status='completed' if all(m['status'] in {'supported', 'already_accepted'} for m in state['methods'].values()) else 'partial', active_chunk=None)
        except BaseException as exc:
            state.update(status='interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed', error=f'{type(exc).__name__}: {exc}')
            write(output / 'state.json', state)
            raise
        state.pop('error', None); write(output / 'state.json', state)
        return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('plan'); create.add_argument('inventory', type=Path)
    create.add_argument('--atlas', type=Path, required=True); create.add_argument('--output', type=Path, required=True)
    create.add_argument('--previous', type=Path); create.add_argument('--batch-size', type=int, default=10)
    create.add_argument('--concurrency', type=int, default=10); create.add_argument('--model', default='gpt-6.1-sol')
    create.add_argument('--effort', default='xhigh'); create.add_argument('--timeout', type=float)
    create.add_argument('--require-source-coverage', action=argparse.BooleanOptionalAction, default=True)
    create.add_argument('--json', action='store_true', help='Full machine-readable progress')
    for name in ('run', 'status'):
        command = commands.add_parser(name); command.add_argument('output', type=Path)
        command.add_argument('--json', action='store_true', help='Full machine-readable progress')
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan':
            result = plan_deployment(args.inventory, args.atlas, args.output, args.previous, args.batch_size,
                batch.BatchConfig(concurrency=args.concurrency, model=args.model, effort=args.effort,
                                  timeout_seconds=args.timeout, require_source_coverage=args.require_source_coverage))
        else:
            if args.command == 'run':
                asyncio.run(run_deployment(args.output))
            result = status_deployment(args.output)
        display = result if args.json else {
            'status': result['status'], 'deployment': str(args.output.resolve()),
            **({'recorded_status': result['recorded_status'],
                'controller_status': result['controller']['status']} if 'controller' in result else {}),
            **({'input_status': result['input_status']} if 'input_status' in result else {}),
            **({'publication_validation': result['publication_validation']} if 'publication_validation' in result else {}),
            'method_count': len(result['methods']), 'status_counts': result.get('status_counts') or dict(Counter(m['status'] for m in result['methods'].values())),
            'completed_chunks': sum(c['status'] == 'complete' for c in result['chunks']),
            'chunk_count': len(result['chunks']), 'active_chunk': result['active_chunk'],
            'latest_publication': (result['latest_publication'] or {}).get('path')}
        print(json.dumps(display))
        if (result.get('input_status') == 'pin_drift'
                or result.get('publication_validation', {}).get('status') == 'failed'):
            return 2
        return 0 if result['status'] in {'planned', 'completed'} else 1
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({'status': 'error', 'error': f'{type(exc).__name__}: {exc}'})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
