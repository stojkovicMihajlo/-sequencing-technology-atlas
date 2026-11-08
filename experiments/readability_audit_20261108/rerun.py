"""Fresh extraction of pinned, unchanged sources for a bounded prompt comparison.

This adapter calls the production extractor. Prior outputs and evaluation criteria
are retained for comparison, never inserted into author or reviewer prompts.
"""
import argparse
import asyncio
import copy
import os
from pathlib import Path

from genomics_discovery.execution.codex_worker import CodexWorker
from genomics_discovery.illumina.extraction import (
    checkpoint, extract_registered_workflow, now, read, revalidate_completed,
    sha, verify_registry_files, write,
)
from genomics_discovery.illumina.graph import _load_card, protocol_text


def prepare(plan):
    methods = plan['methods']
    ids = [m['method_id'] for m in methods]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Select distinct method identities')
    if not plan.get('reason_for_fresh_extraction'):
        raise ValueError('Record why previously completed entries are revisited')
    baseline = read(plan['baseline_state'])
    prepared = []
    for method in methods:
        mid = method['method_id']
        row = baseline['cards'][mid]
        directory = Path(row['attempt_directory'])
        for name, expected in method['expected_pins'].items():
            if sha(directory / name) != expected:
                raise ValueError(f'Baseline bytes changed: {mid}/{name}')
        revalidate_completed(row)
        card = read(directory / 'card.json')
        if card['record_id'] != mid:
            raise ValueError('Baseline method identity mismatch')
        sources = read(directory / 'source_registry.json')
        verify_registry_files(sources)
        prepared.append((method, copy.deepcopy(row), card, sources))
    return prepared


async def execute(plan_path, output, workers=2):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Live extraction requires a Slurm allocation')
    if not 1 <= workers <= 2:
        raise ValueError('This bounded comparison permits one or two workers')
    plan = read(plan_path)
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'plan.json', plan)
    prepared = prepare(plan)  # Validate every bundle before spending model calls.
    cards = [card for _, _, card, _ in prepared]
    write(output / 'inventory.json', {'cards': cards})
    write(output / 'selection.json', {'methods': plan['methods']})
    write(output / 'baseline.json', {m['method_id']: row for m, row, _, _ in prepared})
    state = {'started_at': now(), 'finished_at': None,
             'slurm_job_id': os.environ['SLURM_JOB_ID'],
             'policy': {'model': 'gpt-6.1-sol', 'effort': 'xhigh',
                        'max_revisions': 1, 'workers': workers,
                        'inventory_sha256': sha(output / 'inventory.json'),
                        'plan_sha256': sha(output / 'plan.json'),
                        'representation_version': 'illumina_diagram/1.1'},
             'cards': {c['record_id']: {'record_id': c['record_id'],
                       'title': c['source_title'], 'status': 'pending'} for c in cards}}
    checkpoint(output, state, cards)
    gate = asyncio.Semaphore(workers)

    async def one(method, baseline, card, sources):
        async with gate:
            mid = method['method_id']
            destination = output / 'cards' / mid / 'attempt_001'
            row = state['cards'][mid]
            row.update(status='running', started_at=now(), attempt_directory=str(destination))
            checkpoint(output, state, cards)
            worker = CodexWorker(model='gpt-6.1-sol', effort='xhigh', timeout_seconds=None,
                                 web_search=False, network_access=False, pin_shell_environment=True)
            images = [(sid, Path(s['path'])) for sid, s in sources.items() if s['kind'] == 'image']
            try:
                result = await extract_registered_workflow(
                    card, sources, images, destination, worker, max_revisions=1)
                row.update(result)
                # These are frozen inputs, not a newly curated or enlarged bundle.
                for name in ('card.json', 'source_registry.json'):
                    if sha(destination / name) != method['expected_pins'][name]:
                        raise ValueError('Fresh run changed registered inputs: ' + name)
                graph, _ = _load_card(row, card)
                (destination / 'workflow.txt').write_text(protocol_text(graph, sources=sources))
            except Exception as exc:
                row.update(status='failed', reason=f'{type(exc).__name__}: {exc}')
            finally:
                row['finished_at'] = now()
                checkpoint(output, state, cards)

    await asyncio.gather(*(one(*entry) for entry in prepared))
    state['finished_at'] = now()
    checkpoint(output, state, cards)
    write(output / 'RESULT.json', {
        'counts': state['counts'], 'comparison': 'fresh revised harness versus archived extraction',
        'source_control': 'Same card, registry, diagrams and registered paper bytes',
        'scientific_comparison': 'Pending source-based inspection against predeclared criteria',
        'limitation': 'Two purposively selected development examples, one fresh run each; '
                      'not an ablation, blinded expert evaluation or estimate of general accuracy.'})
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    result = asyncio.run(execute(args.plan.resolve(), args.output.resolve(), args.workers))
    raise SystemExit(2 if result['counts'].get('failed') else 0)
