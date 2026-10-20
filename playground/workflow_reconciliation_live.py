"""One bounded source-backed reconciliation of ambiguous pending definitions."""

import argparse
import asyncio
import json
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker, DEFAULT_MODEL, DEFAULT_EFFORT
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.operation_registry.reconciliation import request, replace_drafts, unresolved_components
from genomics_discovery.curation.operation_registry.proposal_queue import withdraw, submission
from genomics_discovery.curation.operation_registry.storage import connect
from playground.workflow_concepts_live import execute as source_review


async def execute(database, keys, run, *, worker=None, max_updates=4,
                  embeddings=None, semantic_limit=None, require_comparison_profiles=False):
    """Do not retry the scientific result until success; retain unresolved results."""
    from playground.workflow_shared_live import resolve
    database, run = Path(database).resolve(), Path(run).resolve()
    worker = worker or CodexWorker(web_search=False, network_access=False)
    if isinstance(worker, CodexWorker) and (worker.web_search or worker.network_access):
        raise ValueError('Reconciliation uses the registered source packets')
    context = request(database, keys, embeddings=embeddings)
    if context.get('retrieval_policy') is not None:
        from genomics_discovery.curation.operation_registry.semantic_candidates import validate_retrieval_policy
        validate_retrieval_policy(context['retrieval_policy'], embeddings, semantic_limit)
        semantic_limit = context['retrieval_policy']['semantic_limit']
    elif embeddings is not None:
        raise ValueError('Reconciliation cannot change an existing retrieval policy')
    else:
        semantic_limit = 10 if semantic_limit is None else semantic_limit
    run.mkdir(parents=True, exist_ok=False)
    write(run / 'request.json', context)
    write(run / 'manifest.json', context['manifest'])
    state = {'status': 'running', 'started_at': now(), 'submission_ids': sorted(keys),
             'replacement_submission_id': None, 'limits': {'source_reconciliations': 1,
                                                            'candidate_updates': max_updates}}
    root = Path(__file__).resolve().parents[1]
    files = [*root.joinpath('src').rglob('*.py'), Path(__file__).resolve(),
             root / 'playground/workflow_shared_live.py', root / 'playground/workflow_concepts_live.py',
             root / 'playground/workflow_unit_review_inputs.py', run / 'request.json', run / 'manifest.json']
    pins = {p: sha(p) for p in files}
    write(run / 'code_bindings.json', {str(p): h for p, h in pins.items()})
    def check():
        if any(not p.is_file() or sha(p) != h for p, h in pins.items()):
            raise ValueError('Reconciliation inputs or implementation changed')
    def cleanup_draft():
        source_state = run / 'source/state.json'
        if source_state.is_file() and not state.get('replacement', {}).get('status') == 'drafts_replaced':
            key = json.loads(source_state.read_text()).get('submission_id')
            if key:
                with connect(database) as db:
                    saved = submission(db, key)
                if not saved['result'] and key not in keys:
                    withdraw(database, key, 'Reconciliation not completed/approved; original drafts retained.')
    write(run / 'state.json', state)
    try:
        source = await source_review(run / 'manifest.json', database, run / 'source', worker=worker,
            curate=True, defer_admission=True, reconciliation_context=context,
            embeddings=embeddings, semantic_limit=semantic_limit,
            require_comparison_profiles=require_comparison_profiles)
        state['source'] = source
        key = state['replacement_submission_id'] = source['submission_id']
        check()
        result = replace_drafts(database, keys, key)
        state['replacement'] = result
        write(run / 'state.json', state)
        if result['status'] != 'drafts_replaced':
            cleanup_draft()
            state['status'] = result['status']
            return state
        resolution = run / 'resolution'
        resolution.mkdir()
        state['resolution'] = await resolve(database, key, resolution, worker,
            max_updates=max_updates, forbidden_threads=[a['thread_id'] for a in source['attempts']],
            embeddings=embeddings)
        check()
        state['status'] = state['resolution']['status']
        return state
    except BaseException as exc:
        state.update(status='cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed',
                     error=f'{type(exc).__name__}: {exc}')
        cleanup_draft()
        raise
    finally:
        state['finished_at'] = now()
        write(run / 'state.json', state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('run', type=Path)
    parser.add_argument('--submission', action='append', default=[])
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--effort', default=DEFAULT_EFFORT)
    parser.add_argument('--semantic-cache', type=Path, help='Existing local embedding cache; no model download')
    parser.add_argument('--semantic-limit', type=int)
    parser.add_argument('--require-comparison-profiles', action='store_true')
    args = parser.parse_args()
    if args.semantic_limit is not None and args.semantic_cache is None:
        parser.error('--semantic-limit requires --semantic-cache')
    embeddings = None
    if args.semantic_cache is not None:
        from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache, LocalEmbedder
        embeddings = EmbeddingCache(args.semantic_cache / 'vectors.sqlite3',
                                    LocalEmbedder(args.semantic_cache / 'models', download=False))
    database = args.database.resolve()
    keys = args.submission
    if not keys:
        groups = unresolved_components(database, embeddings=embeddings)
        if len(groups) != 1:
            parser.error(f'Found {len(groups)} unresolved groups; choose --submission IDs explicitly')
        keys = groups[0]
    result = asyncio.run(execute(database, keys, args.run,
        worker=CodexWorker(model=args.model, effort=args.effort, web_search=False, network_access=False),
        embeddings=embeddings, semantic_limit=args.semantic_limit,
        require_comparison_profiles=args.require_comparison_profiles))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
