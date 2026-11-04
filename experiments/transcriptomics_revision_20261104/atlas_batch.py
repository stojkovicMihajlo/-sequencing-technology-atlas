"""Build this atlas batch: reviewed identities -> source bundles -> reviewed graphs.

Uses the existing planners and extraction/review gates. The scope receipt records
reused and held source occurrences; neither labels nor hashes establish identity.
Historical connections are a subsequent, separately reviewed research stage.
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from genomics_discovery.execution.codex_worker import CodexWorker
from genomics_discovery.illumina.extraction import checkpoint, extract_registered_workflow, now, read, sha, write
from genomics_discovery.illumina.graph import build_graph, protocol_text

try:
    from . import batching, global_plan, native_planning
    from .planning import digest
except ImportError:
    import batching, global_plan, native_planning
    from planning import digest


def validate_scope(manifest, receipt, parent, manifest_sha256):
    if (receipt.get('manifest_sha256') != manifest_sha256
            or receipt.get('source_census_sha256') != manifest['parent_census_file_sha256']):
        raise ValueError('Scope receipt does not bind this manifest and parent census')
    selected = receipt['selected_record_ids']
    if len(selected) != len(set(selected)) or set(selected) != set(manifest['selected_record_ids']):
        raise ValueError('Scope receipt differs from selected source occurrences')
    held, reused = receipt['held_records'], receipt['reused_records']
    if not isinstance(held, dict) or not isinstance(reused, dict):
        raise ValueError('Held/reused records require explicit per-source reasons or receipts')
    if set(selected) & (set(held) | set(reused)) or set(held) & set(reused):
        raise ValueError('Held/reused sources must not enter new model work')
    if any(not value for value in [*held.values(), *reused.values()]):
        raise ValueError('Every held/reused source needs a reason or prior-result receipt')
    pending = receipt['pending_records']
    coverage = list(selected) + list(held) + list(reused) + list(pending)
    if len(coverage) != len(set(coverage)) or set(coverage) != {r['record_id'] for r in parent['rows']}:
        raise ValueError('Scope receipt must account for every parent source exactly once')
    return receipt


def materialize_dispatch(index_path, decisions, output):
    """Adapt already admitted units; never manufacture a new scientific plan."""
    index, packet = global_plan.load_index(index_path)
    if decisions['global_index_sha256'] != sha(index_path):
        raise ValueError('Dispatch decisions refer to another global index')
    unit_ids = [item['unit']['unit_id'] for item in decisions['admitted_units']]
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError('Duplicate admitted dispatch unit')
    wanted = {b['record_id'] for item in decisions['admitted_units'] for b in item['unit']['bindings']}
    rows = {}
    for directory in sorted({u['planning_run'] for u in packet['reviewed_units']}):
        for row in read(Path(directory) / 'inputs/census.json')['rows']:
            if row['record_id'] not in wanted:
                continue
            if row['record_id'] in rows and rows[row['record_id']] != row:
                raise ValueError('Conflicting original source occurrence across reviewed plans')
            rows[row['record_id']] = row
    if set(rows) != wanted:
        raise ValueError('Admitted implementation lacks an original source binding')
    output.mkdir(parents=True, exist_ok=False)
    census = output / 'source_census.json'
    write(census, {'rows': [rows[k] for k in sorted(rows)], 'global_index_sha256': sha(index_path)})
    inputs = native_planning.prepare_inputs(census, output)
    worklist = {'format_version': 'atlas_admitted_worklist/1', 'global_index_sha256': sha(index_path),
                'dispatch_decisions_sha256': digest(decisions),
                'units': [item['unit'] for item in decisions['admitted_units']]}
    write(output / 'worklist.json', worklist)
    write(output / 'dispatch_decisions.json', decisions)
    write(output / 'source_plan_index.json', {'input_pins': index['input_pins'],
        'planning_runs': sorted({u['planning_run'] for u in packet['reviewed_units']}),
        'component_runs': decisions['component_runs']})
    bundles = native_planning.write_workflow_bundles(inputs, worklist, output / 'workflow_bundles')
    return worklist, bundles


async def extract_dispatch(worklist, bundles, output, *, workers=4, worker=None):
    """Same extraction, fresh-review and graph-publication path as the pilot."""
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native extraction requires a Sherlock Slurm allocation')
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError('Select one to four concurrent extraction workers')
    expected = [u['unit_id'] for u in worklist['units']]
    if len(expected) != len(set(expected)) or [b['unit_id'] for b in bundles] != expected:
        raise ValueError('Bundle coverage differs from admitted unique methods')
    output.mkdir(parents=True, exist_ok=False)
    cards = [read(b['card_path']) for b in bundles]
    for bundle, card, unit in zip(bundles, cards, worklist['units']):
        directory = Path(bundle['card_path']).parent
        if any(sha(directory / name) != pin for name, pin in bundle['pins'].items()):
            raise ValueError('Admitted source bundle changed')
        if card['record_id'] != bundle['unit_id'] or card.get('work_unit') != unit:
            raise ValueError('Source bundle changed admitted implementation scope')
    inventory = output / 'inventory.json'; write(inventory, {'cards': cards, 'worklist_sha256': digest(worklist)})
    policy = {'representation_version': 'illumina_diagram/1.1', 'model': 'gpt-6.1-sol',
              'effort': 'xhigh', 'workers': workers, 'max_revisions': 1,
              'inventory_sha256': sha(inventory), 'worklist_sha256': digest(worklist),
              'global_index_sha256': worklist['global_index_sha256'], 'record_ids': expected,
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
            directory = output / 'cards' / bundle['unit_id'] / 'attempt_001'
            row = state['cards'][bundle['unit_id']]
            row.update(status='running', started_at=now(), attempt_directory=str(directory)); checkpoint(output, state, cards)
            try:
                row.update(await extract_registered_workflow(card, read(bundle['registry_path']),
                    [(r['source_id'], Path(r['path'])) for r in bundle['images']], directory, worker,
                    max_revisions=1, representation='illumina_diagram/1.1'))
            except Exception as exc:
                row.update(status='failed', reason=f'{type(exc).__name__}: {exc}',
                           failure_category=getattr(exc, 'category', 'unclassified_stage_error'))
            finally:
                row['finished_at'] = now(); checkpoint(output, state, cards)
    await asyncio.gather(*(one(b, c) for b, c in zip(bundles, cards)))
    state['finished_at'] = now(); checkpoint(output, state, cards)
    graph = build_graph(output, inventory); write(output / 'atlas.json', graph)
    errors = [key for key, row in state['cards'].items() if row['status'] == 'supported'
              and graph['cards'][key].get('admission') != 'supported']
    for row in state['cards'].values():
        path = Path(row['attempt_directory']) / 'graph.json'
        if path.exists(): (path.parent / 'workflow.txt').write_text(protocol_text(read(path)))
    result = {'extraction_counts': state['counts'], 'publication_errors': errors,
              'passed': bool(cards) and not errors and all(r['status'] == 'supported' for r in state['cards'].values()),
              'boundary': 'Model-reviewed source schematics; historical relationships require separate review.'}
    write(output / 'RESULT.json', result)
    return result


async def execute(manifest_path, receipt_path, output, *, workers=4, global_workers=2, max_global_sources=12):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('Native atlas batch requires a Sherlock Slurm allocation')
    if (type(workers) is not int or not 1 <= workers <= 4
            or type(global_workers) is not int or not 1 <= global_workers <= 4 or max_global_sources < 1):
        raise ValueError('Select one to four workers and a positive global context cap')
    manifest, parent = batching.load_batches(manifest_path)
    receipt = validate_scope(manifest, read(receipt_path), parent, sha(manifest_path))
    output.mkdir(parents=True, exist_ok=False); write(output / 'source_scope.json', receipt)
    pins = {str(manifest_path): sha(manifest_path), str(receipt_path): sha(receipt_path)}
    state = {'format_version': 'atlas_batch_run/1', 'status': 'planning', 'started_at': now(),
             'job_id': os.environ['SLURM_JOB_ID'], 'input_pins': pins, 'workers': workers, 'global_workers': global_workers,
             'model': 'gpt-6.1-sol', 'effort': 'xhigh',
             'held_oversized_batches': [b['batch_id'] for b in manifest['batches'] if b['oversized']]}
    write(output / 'state.json', state)
    try:
        batch_ids = [b['batch_id'] for b in manifest['batches'] if not b['oversized']]
        if not batch_ids: raise ValueError('No bounded source-planning batches available')
        await batching.execute_batches(manifest_path, output / 'planning', batch_ids, workers=workers)
        if any(sha(p) != pin for p, pin in pins.items()): raise ValueError('Batch scope changed during planning')
        state['status'] = 'global_identity_review'; write(output / 'state.json', state)
        index = global_plan.prepare_global(manifest_path, output / 'planning/state.json', output / 'global_inputs')
        index_path = output / 'global_inputs/index.json'
        component_runs, statuses, semaphore = {}, {}, asyncio.Semaphore(global_workers)
        state['global_components'] = statuses
        async def review_component(row):
            ident = row['component_id']
            if row['source_occurrences'] > max_global_sources:
                statuses[ident] = {'status': 'held_context_limit', 'source_occurrences': row['source_occurrences']}
                write(output / 'state.json', state); return
            async with semaphore:
                directory = output / 'global_reviews' / ident
                statuses[ident] = {'status': 'running', 'path': str(directory)}; write(output / 'state.json', state)
                try:
                    result = await global_plan.execute_component(index_path, ident, directory,
                                                                  max_source_occurrences=max_global_sources)
                    statuses[ident].update(status=result['status'])
                    component_runs[ident] = str(directory)
                except Exception as exc:
                    statuses[ident].update(status='failed', error=f'{type(exc).__name__}: {exc}')
                finally: write(output / 'state.json', state)
        await asyncio.gather(*(review_component(row) for row in index['components']))
        write(output / 'component_runs.json', component_runs)
        decisions = global_plan.admit_global(index_path, component_runs)
        write(output / 'dispatch_decisions.json', decisions)
        source_dispositions = []
        planning_state = read(output / 'planning/state.json')
        for planned in manifest['batches']:
            entry = planning_state['batches'].get(planned['batch_id'])
            if entry and entry['status'] == 'reviewed':
                source_dispositions.extend(read(Path(entry['planning_run']) / 'plan.json')['dispositions'])
            else:
                source_dispositions.extend({'record_id': rid, 'decision': 'planning_unresolved',
                    'reason': entry.get('error', entry['status']) if entry else 'held_oversized_component'}
                    for rid in planned['record_ids'])
        write(output / 'source_dispositions.json', source_dispositions)
        state['identity_research_sources'] = [d['record_id'] for d in source_dispositions
                                              if d['decision'] == 'identity_research_needed']
        state.update(status='preparing_extraction', admitted_methods=len(decisions['admitted_units']),
                     held_prior_units=decisions['held_prior_units'], excluded_prior_units=decisions['excluded_prior_units'],
                     pending_source_record_ids=decisions['pending_source_record_ids'],
                     pending_selected_record_ids=sorted(set(decisions['pending_source_record_ids']) & set(manifest['selected_record_ids'])),
                     additional_alias_requests=decisions['additional_alias_requests'])
        write(output / 'state.json', state)
        if not decisions['admitted_units']:
            state['status'] = 'held_no_admitted_methods'
        else:
            worklist, bundles = materialize_dispatch(index_path, decisions, output / 'dispatch_sources')
            state['status'] = 'extracting'; write(output / 'state.json', state)
            result = await extract_dispatch(worklist, bundles, output / 'extractions', workers=workers)
            state['extraction_result'] = result
            state['status'] = ('completed' if result['passed'] and not decisions['held_prior_units']
                               and not state['held_oversized_batches'] and not state['identity_research_sources']
                               and not state['pending_selected_record_ids'] and not state['additional_alias_requests']
                               and all(v['status'] == 'reviewed' for v in read(output / 'planning/state.json')['batches'].values())
                               else 'completed_with_unresolved_records')
    except BaseException as exc:
        state.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        state['finished_at'] = now(); write(output / 'state.json', state)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path); parser.add_argument('scope_receipt', type=Path)
    parser.add_argument('output', type=Path); parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--max-global-sources', type=int, default=12)
    parser.add_argument('--global-workers', type=int, default=2)
    args = parser.parse_args(argv)
    result = asyncio.run(execute(args.manifest.resolve(), args.scope_receipt.resolve(), args.output.resolve(),
                                workers=args.workers, global_workers=args.global_workers, max_global_sources=args.max_global_sources))
    print(result['status'])
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__': raise SystemExit(main())
