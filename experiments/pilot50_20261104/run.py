"""One bounded, recorded pilot: sources -> extraction -> history -> comparison.

Reuses the production extraction and fresh-review stages. Engineering selection
defines the 50 requested scopes; it does not assert identity or ancestry.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import os
from pathlib import Path

from genomics_discovery.execution.codex_worker import CodexWorker
from genomics_discovery.illumina.extraction import (
    checkpoint, extract_registered_workflow, now, read, sha, write,
)
from genomics_discovery.illumina.graph import build_graph, protocol_text
from experiments.pilot50_20261104 import evaluation, history_stage, prepare


async def extract(manifest, output, *, workers=4, worker=None):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native extraction requires Sherlock Slurm')
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('Use one to four concurrent workers')
    output = Path(output)
    bundles = manifest['bundles']
    cards = [read(Path(b['card'])) for b in bundles]
    ids = [c['record_id'] for c in cards]
    if len(ids) != len(set(ids)) or ids != [b['record_id'] for b in bundles]:
        raise ValueError('One dispatch per declared method/version is required')
    for bundle in bundles:
        parent = Path(bundle['card']).parent
        if any(sha(parent / name) != expected for name, expected in bundle['pins'].items()):
            raise ValueError('Prepared source bundle changed')
    output.mkdir(parents=True, exist_ok=False)
    inventory = output / 'inventory.json'
    write(inventory, {'cards': cards, 'selection_sha256': manifest['selection_sha256']})
    state = {'format_version': 'illumina_batch/1.0', 'started_at': now(),
        'slurm_job_id': os.environ['SLURM_JOB_ID'],
        'policy': {'representation_version': 'illumina_diagram/1.1',
            'model': 'gpt-6.1-sol', 'effort': 'xhigh', 'workers': workers,
            'max_revisions': 1, 'inventory_sha256': sha(inventory),
            'selection_sha256': manifest['selection_sha256'], 'record_ids': ids,
            'prior_extractions_used_as_evidence': False},
        'cards': {c['record_id']: {'record_id': c['record_id'],
            'title': c['source_title'], 'status': 'pending'} for c in cards}}
    checkpoint(output, state, cards)
    worker = worker or CodexWorker(model='gpt-6.1-sol', effort='xhigh', timeout_seconds=None,
        web_search=False, network_access=False, pin_shell_environment=True)
    semaphore = asyncio.Semaphore(workers)

    async def one(bundle, card):
        async with semaphore:
            directory = output / 'cards' / card['record_id'] / 'attempt_001'
            row = state['cards'][card['record_id']]
            row.update(status='running', started_at=now(), attempt_directory=str(directory))
            checkpoint(output, state, cards)
            try:
                row.update(await extract_registered_workflow(card, read(Path(bundle['registry'])),
                    [(r['source_id'], Path(r['path'])) for r in bundle['images']],
                    directory, worker, max_revisions=1, representation='illumina_diagram/1.1'))
            except Exception as exc:
                row.update(status='failed', reason=f'{type(exc).__name__}: {exc}',
                    failure_category=getattr(exc, 'category', 'unclassified_stage_error'))
            finally:
                row['finished_at'] = now()
                checkpoint(output, state, cards)

    await asyncio.gather(*(one(b, c) for b, c in zip(bundles, cards)))
    state['finished_at'] = now()
    checkpoint(output, state, cards)
    graph = build_graph(output, inventory)
    write(output / 'atlas.json', graph)
    publication_errors = [rid for rid, row in state['cards'].items()
        if row['status'] == 'supported' and graph['cards'][rid].get('admission') != 'supported']
    for row in state['cards'].values():
        graph_path = Path(row['attempt_directory']) / 'graph.json'
        if graph_path.exists():
            (graph_path.parent / 'workflow.txt').write_text(protocol_text(read(graph_path)))
    result = {'counts': state['counts'], 'publication_errors': publication_errors,
        'graph': str(output / 'atlas.json'), 'graph_sha256': sha(output / 'atlas.json')}
    write(output / 'RESULT.json', result)
    return result


def comparison_manifest(selection, sources, batch):
    baselines = read(sources / 'baseline_references.json')
    state = read(batch / 'state.json')
    rows = []
    for method in selection['methods']:
        mid = method['method_id']
        options = baselines.get(mid, [])
        # Selection order was fixed before any new result was available.
        baseline = next((option['reference'] for option in options
            if option.get('comparison_eligible', True) and option.get('reference')), None)
        current = state['cards'][mid]
        candidate = ({'attempt_directory': current['attempt_directory'],
            'artifact_pins': current['artifact_pins'], 'declared_status': current['status']}
            if current.get('artifact_pins') else None)
        rows.append({'method_id': mid, 'scope_target': method['scope'],
            'identity_match': {'status': 'pending',
                'rationale': 'Preselected baseline; evaluator must verify same implementation before judging differences'},
            'baseline': baseline, 'candidate': candidate,
            'all_predeclared_baselines': options,
            'baseline_selection_rule': 'First intact, comparison-eligible recorded baseline in pre-run selection order; known different versions excluded'})
    return {'format_version': 'atlas_pilot_comparison/1', 'methods': rows}


async def compare(manifest, output, *, workers=4):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native comparison requires Sherlock Slurm')
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('Use one to four concurrent workers')
    output.mkdir(parents=True, exist_ok=False)
    write(output / 'manifest.json', manifest)
    diagnostics = evaluation.evaluate_manifest(output / 'manifest.json', output / 'diagnostics.json')
    state = {'status': 'running', 'methods': {}, 'started_at': now()}
    semaphore = asyncio.Semaphore(workers)

    async def one(row, diagnostic, seed):
        mid = row['method_id']
        if diagnostic['comparison_status'] in {'unpaired', 'artifact_or_schema_issue'}:
            state['methods'][mid] = {'status': 'held', 'reason': diagnostic['comparison_status']}
            write(output / 'state.json', state)
            return
        async with semaphore:
            state['methods'][mid] = {'status': 'running'}
            write(output / 'state.json', state)
            try:
                result = await evaluation.evaluate_pair_native(row, output / 'methods' / mid, seed=seed)
                state['methods'][mid] = {'status': 'completed',
                    'model_assessed_outcome': result['model_assessed_outcome']}
            except Exception as exc:
                state['methods'][mid] = {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'}
            write(output / 'state.json', state)

    await asyncio.gather(*(one(r, d, i) for i, (r, d) in enumerate(zip(manifest['methods'], diagnostics['methods']))))
    state.update(status='completed', finished_at=now())
    state['counts'] = dict(Counter(x['status'] for x in state['methods'].values()))
    state['assessed_outcomes'] = dict(Counter(x['model_assessed_outcome'] for x in state['methods'].values()
        if x['status'] == 'completed'))
    state['boundary'] = 'Source-grounded model comparisons, not expert gold or causal proof of harness improvement.'
    write(output / 'state.json', state)
    return state


async def execute(selection_path, output, runtime, *, workers=4):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native pilot requires Sherlock Slurm')
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('Use one to four concurrent workers')
    selection_path, output, runtime = (Path(p).resolve() for p in (selection_path, output, runtime))
    selection = read(selection_path)
    prepare.check_selection(selection)
    output.mkdir(parents=True, exist_ok=False)
    state = {'format_version': 'atlas_pilot50/1', 'started_at': now(),
        'job_id': os.environ['SLURM_JOB_ID'], 'selection_sha256': sha(selection_path),
        'runtime': str(runtime), 'workers': workers, 'status': 'preparing_sources', 'stages': {}}
    write(output / 'state.json', state)
    try:
        sources = output / 'sources'
        manifest = prepare.prepare(selection_path, sources)
        state.update(status='extracting')
        state['source_manifest'] = str(sources / 'manifest.json')
        write(output / 'state.json', state)
        state['stages']['extraction'] = await extract(manifest, output / 'extractions', workers=workers)
        state['status'] = 'tracing_citations_and_reviewing_pairs'
        write(output / 'state.json', state)
        try:
            state['stages']['history'] = await history_stage.execute(output / 'extractions', output / 'history', runtime,
                workers=workers, primary_selection=sources / 'primary_selection.json',
                source_cache=sources / 'reference_cache.json')
        except Exception as exc:
            state['stages']['history'] = {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'}
        state['status'] = 'comparing_with_previous_results'
        write(output / 'state.json', state)
        comparison = comparison_manifest(selection, sources, output / 'extractions')
        state['stages']['evaluation'] = await compare(comparison, output / 'evaluation', workers=workers)
        state.update(status='completed', finished_at=now(),
            boundary='Completed scheduling is not 50 scientific successes; consult per-method and per-edge holds/reviews.')
        write(output / 'RESULT.json', state)
        return state
    except BaseException as exc:
        state.update(status='failed', reason=f'{type(exc).__name__}: {exc}', finished_at=now())
        raise
    finally:
        write(output / 'state.json', state)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('selection', 'output', 'runtime'):
        parser.add_argument('--' + argument, type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    asyncio.run(execute(args.selection, args.output, args.runtime, workers=args.workers))
