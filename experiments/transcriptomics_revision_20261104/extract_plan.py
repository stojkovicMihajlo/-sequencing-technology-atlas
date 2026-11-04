"""Extract reviewed method work units, then reopen and publish their workflows.

One unit can use several depictions. It does not create historical edges. The
native pilot is inspected before authorizing the separate larger worklist.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import os

from genomics_discovery.execution.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.illumina.extraction import (
    checkpoint, extract_registered_workflow, now, read, sha, verify_stage_inputs, write,
)
from genomics_discovery.illumina.graph import build_graph, protocol_text

try:
    from .native_planning import execute as plan_sources, validate_artifact
    from .planning import admit, digest
except ImportError:
    from native_planning import execute as plan_sources, validate_artifact
    from planning import admit, digest


def load_plan(directory):
    """Reopen review and custody before spending another extraction call."""
    state = read(directory / 'state.json')
    if state['status'] != 'reviewed':
        raise ValueError('Worklist requires completed independent source-identity review')
    stages, seen = state['stages'], set()
    if len(stages) < 2 or [s['role'] for s in stages[-2:]] != ['author', 'reviewer']:
        raise ValueError('Missing final author/reviewer planning stages')
    for stage in stages:
        attempt = Path(stage['path'])
        native = assess_archived_attempt(attempt)
        if (stage['status'] != 'validated' or not native.complete or not native.thread_id
                or native.thread_id in seen or native.thread_id != stage['thread_id']
                or sha(attempt / 'artifact.json') != stage['artifact_sha256']):
            raise ValueError('Planning native custody/freshness failed')
        seen.add(native.thread_id)
        verify_stage_inputs(attempt, read(attempt / 'image_delivery.json'), read(attempt / 'input_pins.json'))
    inputs = directory / 'inputs'
    plan = validate_artifact('author', directory / 'plan.json', inputs)
    review = validate_artifact('reviewer', directory / 'review.json', inputs, directory / 'plan.json')
    authored = validate_artifact('author', Path(stages[-2]['path']) / 'artifact.json', inputs)
    reviewed = validate_artifact('reviewer', Path(stages[-1]['path']) / 'artifact.json', inputs, directory / 'plan.json')
    if authored != plan or reviewed != review:
        raise ValueError('Worklist inputs differ from the final native artifacts')
    worklist = admit(read(inputs / 'census.json'), plan, review)
    if read(directory / 'worklist.json') != worklist or digest(worklist) != state['worklist_sha256']:
        raise ValueError('Published worklist differs from its reviewed plan')
    manifest_path = directory / 'workflow_bundles/manifest.json'
    if sha(manifest_path) != state.get('bundles_sha256'):
        raise ValueError('Workflow bundle manifest differs from its published pin')
    manifest = read(manifest_path)
    if manifest['worklist_sha256'] != digest(worklist):
        raise ValueError('Workflow sources are bound to a different plan')
    units = {u['unit_id']: u for u in worklist['units']}
    bundles = manifest['bundles']
    if len(bundles) != len(units) or {b['unit_id'] for b in bundles} != set(units):
        raise ValueError('Bundle coverage differs from unique planned work units')
    for bundle in bundles:
        source_dir = Path(bundle['card_path']).parent
        if (Path(bundle['card_path']).resolve() != (source_dir / 'card.json').resolve()
                or Path(bundle['registry_path']).resolve() != (source_dir / 'source_registry.json').resolve()
                or not source_dir.resolve().is_relative_to((directory / 'workflow_bundles').resolve())):
            raise ValueError('Bundle paths must point to their pinned files under workflow_bundles')
        required = {'card.json', 'source_registry.json', 'source_cards.json', 'images.json', 'worklist_binding.json'}
        if not required <= set(bundle['pins']) or any(Path(n).name != n for n in bundle['pins']):
            raise ValueError('Incomplete/unsafe bundle pins')
        if any(sha(source_dir / name) != pin for name, pin in bundle['pins'].items()):
            raise ValueError('Workflow source bundle changed')
        card = read(bundle['card_path'])
        if card.get('work_unit') != units[bundle['unit_id']] or card['record_id'] != bundle['unit_id']:
            raise ValueError('Workflow card differs from its reviewed method unit')
        if read(source_dir / 'images.json') != bundle['images']:
            raise ValueError('Image delivery list differs from registered bundle')
    return worklist, bundles


def select_bundles(worklist, bundles, unit_ids=None):
    """Select exact reviewed units without changing their identity or source plan."""
    planned_ids = [unit['unit_id'] for unit in worklist['units']]
    requested = list(planned_ids if unit_ids is None else unit_ids)
    if not requested:
        raise ValueError('Select at least one reviewed work unit')
    if len(requested) != len(set(requested)):
        raise ValueError('Duplicate requested work unit IDs')
    unknown = set(requested) - set(planned_ids)
    if unknown:
        raise ValueError('Unknown reviewed work unit IDs: ' + ', '.join(sorted(unknown)))
    by_id = {bundle['unit_id']: bundle for bundle in bundles}
    selected = set(requested)
    return [by_id[unit_id] for unit_id in planned_ids if unit_id in selected]


async def execute(planning_run, output, *, workers=2, worker=None, unit_ids=None):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native extraction must run in a Sherlock Slurm allocation')
    if not 1 <= workers <= 16:
        raise ValueError('Use 1–16 explicitly selected workers')
    # Reopen the complete reviewed plan and every source pin before selection.
    # A partial retry must not hide a modified or inconsistent parent plan.
    worklist, all_bundles = load_plan(planning_run)
    bundles = select_bundles(worklist, all_bundles, unit_ids)
    output.mkdir(parents=True, exist_ok=False)
    cards = [read(b['card_path']) for b in bundles]
    inventory = output / 'inventory.json'
    write(inventory, {'cards': cards, 'worklist_sha256': digest(worklist)})
    policy = {'representation_version': 'illumina_diagram/1.1',
        'model': 'gpt-6.1-sol', 'effort': 'xhigh', 'workers': workers, 'max_revisions': 1,
        'inventory_sha256': sha(inventory), 'record_ids': [c['record_id'] for c in cards],
        'worklist_sha256': digest(worklist), 'planning_run': str(planning_run),
        'unit_selection': {'mode': 'all_reviewed' if unit_ids is None else 'explicit_subset',
            'requested_unit_ids': None if unit_ids is None else list(unit_ids),
            'selected_unit_ids': [b['unit_id'] for b in bundles],
            'unselected_unit_ids': [u['unit_id'] for u in worklist['units']
                                    if u['unit_id'] not in {b['unit_id'] for b in bundles}]},
        'prior_extractions_used_as_evidence': False}
    state = {'format_version': 'illumina_batch/1.0', 'started_at': now(),
        'slurm_job_id': os.environ['SLURM_JOB_ID'], 'policy': policy,
        'cards': {c['record_id']: {'record_id': c['record_id'], 'title': c['source_title'],
                   'status': 'pending'} for c in cards}}
    checkpoint(output, state, cards)
    worker = worker or CodexWorker(model=policy['model'], effort=policy['effort'], timeout_seconds=None,
        web_search=False, network_access=False, pin_shell_environment=True)
    semaphore = asyncio.Semaphore(workers)

    async def one(bundle, card):
        async with semaphore:
            ident = bundle['unit_id']
            directory = output / 'cards' / ident / 'attempt_001'
            row = state['cards'][ident]
            row.update(status='running', attempt_directory=str(directory), started_at=now())
            checkpoint(output, state, cards)
            try:
                row.update(await extract_registered_workflow(card, read(bundle['registry_path']),
                    [(r['source_id'], Path(r['path'])) for r in bundle['images']], directory, worker,
                    max_revisions=1, representation='illumina_diagram/1.1'))
            except Exception as exc:
                row.update(status='failed', reason=f'{type(exc).__name__}: {exc}',
                    failure_category=getattr(exc, 'category', 'unclassified_stage_error'))
            row['finished_at'] = now()
            checkpoint(output, state, cards)
    await asyncio.gather(*(one(b, c) for b, c in zip(bundles, cards)))
    state['finished_at'] = now(); checkpoint(output, state, cards)
    graph = build_graph(output, inventory)
    write(output / 'atlas.json', graph)
    errors = [key for key, row in state['cards'].items() if row['status'] == 'supported'
              and graph['cards'][key].get('admission') != 'supported']
    for key, row in state['cards'].items():
        path = Path(row['attempt_directory']) / 'graph.json'
        if path.exists():
            (path.parent / 'workflow.txt').write_text(protocol_text(read(path)))
    report = {'extraction_counts': state['counts'], 'publication_errors': errors,
        'passed': bool(cards) and not errors and all(r['status'] == 'supported' for r in state['cards'].values()),
        'boundary': 'Model-reviewed source schematics. Not independent expert accuracy or historical-edge validation.'}
    write(output / 'RESULT.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--census', type=Path, help='First run independent source-identity planning.')
    source.add_argument('--planning-run', type=Path, help='Reuse an exact completed reviewed plan.')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--unit-id', action='append', dest='unit_ids',
                        help='Extract only this exact reviewed work unit; repeat for a subset.')
    args = parser.parse_args(argv)

    async def both():
        if args.census:
            planning = args.output.resolve() / 'planning'
            if await plan_sources(args.census.resolve(), planning) is None:
                return {'passed': False, 'reason': 'source identity plan held; extraction not launched'}
        else:
            planning = args.planning_run.resolve()
        return await execute(planning, args.output.resolve() / 'extractions', workers=args.workers,
                             unit_ids=args.unit_ids)

    report = asyncio.run(both())
    print(report)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
