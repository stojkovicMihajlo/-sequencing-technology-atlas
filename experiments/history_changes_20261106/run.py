"""Pilot explicit changes from full papers over already accepted extractions."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import subprocess
from collections import Counter

from genomics_discovery.illumina.extraction import now, read, sha, write
from genomics_discovery.illumina.history_inputs import resolve_article, snapshot_accepted
from experiments.diagram_paper_pilot_20261106.run import checked_files, inside
from experiments.pilot50_20261104.history_stage import prepare_index, pair_candidates


def reference_cache(snapshot, library, vault, output):
    """Reuse registered original full texts; cached evidence is still unreviewed."""
    from importlib.util import spec_from_file_location, module_from_spec
    scripts = Path(__file__).resolve().parents[1] / 'citation_lineage_pilot'
    sys.path.insert(0, str(scripts))
    spec = spec_from_file_location('history_citation_runner', scripts / 'run.py')
    runner = module_from_spec(spec); spec.loader.exec_module(runner)
    papers, skipped, duplicates = {}, [], []

    def admit(source, origin):
        pmid = str(source.get('pmid') or '')
        if not pmid.isdecimal() or source.get('document_role') == 'supplement':
            return
        try:
            source = {**source, 'original': resolve_article(source)}
            runner.validate_cached_reference(pmid, source)
        except Exception as exc:
            skipped.append({'origin': origin, 'pmid': pmid, 'reason': str(exc)})
            return
        if pmid in papers:
            duplicates.append({'pmid': pmid, 'origin': origin,
                'same_article_and_text': (source['original']['sha256'] == papers[pmid]['original']['sha256']
                                          and source['text'] == papers[pmid]['text'])})
            return
        papers[pmid] = {**source, 'cache_origin': origin}

    for row in read(snapshot / 'state.json')['cards'].values():
        for sid, source in read(Path(row['attempt_directory']) / 'source_registry.json').items():
            if source.get('content_level') == 'fulltext':
                admit(source, str(row['attempt_directory']) + ':' + sid)
    inventory_path = library / 'canonical_inventory.json'
    inventory = read(inventory_path)
    for method in inventory['methods'].values():
        for reference in method.get('method_references', []):
            pmid = str(reference.get('pmid') or '')
            if pmid in papers or reference.get('availability') != 'fulltext_acquired' or reference.get('held_files'):
                continue
            files = reference.get('cached_files', [])
            texts = [f for f in files if f['kind'] == 'reading-text']
            originals = [f for f in files if f['kind'] == 'article']
            if len(texts) != 1 or len(originals) != 1:
                continue
            try:
                pins = checked_files(vault, files)
                text_path = inside(vault, texts[0]['path'])
                admit({'kind': 'text', 'content_level': 'fulltext', 'pmid': pmid,
                    'title': reference.get('resolved_title') or reference.get('citation'),
                    'url': reference.get('url'), 'path': str(text_path), 'sha256': texts[0]['sha256'],
                    'text': text_path.read_text(), 'original': {
                        'path': str(inside(vault, originals[0]['path'])), 'sha256': originals[0]['sha256']},
                    'custody_files': [*pins, {'path': str(inventory_path), 'sha256': sha(inventory_path)}]},
                    'curated method reference: ' + method['id'])
            except Exception as exc:
                skipped.append({'pmid': pmid, 'reason': str(exc)})
    write(output, {'format_version': 'atlas_reference_cache/1', 'papers': papers})
    write(output.with_name('reference_cache_receipt.json'), {
        'publications': len(papers), 'skipped': skipped, 'duplicate_registrations': duplicates,
        'precedence': 'Existing admitted-workflow source first, then first verified curated publication assignment. Different renditions remain recorded, not merged.',
        'boundary': 'Publication lookup only; no method-version identity, historical adoption or scientific acceptance is established.'})
    return output


MAX_HISTORY_WORKERS = 50


def validate_request(args):
    expected_state=getattr(args,'batch_state',None)
    if expected_state and Path(expected_state).resolve()!=(args.batch/'state.json').resolve():
        raise ValueError('Pinned batch state does not match the selected batch directory')
    if any(not isinstance(mid, str) or not mid for mid in args.seed):
        raise ValueError('Seed methods must be nonempty canonical method IDs')
    if len(set(args.seed)) != len(args.seed):
        raise ValueError('One history work unit per method; duplicate seed methods are not allowed')
    if not args.seed and not getattr(args, 'review_existing', []):
        raise ValueError('Select at least one new method or completed research review')
    if args.workers is None:
        # Resolve only after --selection has added its methods/reviews. Parallel
        # by default: one slot per selected method, up to the supported maximum.
        args.workers = min(len(args.seed) + len(getattr(args, 'review_existing', [])), MAX_HISTORY_WORKERS)
    if type(args.workers) is not int or not 1 <= args.workers <= MAX_HISTORY_WORKERS:
        raise ValueError(f'Choose an explicit history agent count from 1 to {MAX_HISTORY_WORKERS}')


async def execute(args):
    validate_request(args)
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('History pilot requires a Sherlock Slurm allocation')
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    if getattr(args,'software_preflight',False):
        command=[sys.executable,'-m','pytest','-q','--import-mode=importlib','-p','no:cacheprovider',
                 'experiments/citation_lineage_pilot/test_contract.py',
                 'experiments/citation_lineage_pilot/test_change_records.py',
                 'experiments/citation_lineage_pilot/test_review_coverage.py',
                 'experiments/history_parallel_20261112/test_merge_completed.py']
        with (output/'software_preflight.log').open('wb') as log:
            subprocess.run(command,cwd=args.runtime,stdout=log,stderr=subprocess.STDOUT,check=True)
    method_inventory = getattr(args, 'method_inventory', None) or args.batch / 'inventory.json'
    snapshot = output / 'accepted_snapshot'
    snapshot_result = snapshot_accepted(args.batch, snapshot)
    review_runs = {}
    for previous in getattr(args, 'review_existing', []):
        mid = read(previous / 'inputs/seed_binding.json')['record_id']
        if mid in review_runs or mid in args.seed:
            raise ValueError('One history work unit per method; a review must not repeat its original research')
        review_runs[mid] = previous.resolve()
    methods_requested = [*args.seed, *review_runs]
    cards = {c['record_id']: c for c in read(snapshot / 'inventory.json')['cards']}
    selection = {mid: 'paper:' + cards[mid]['curation_binding']['primary_paper_id'].removeprefix('pmid-')
                 for mid in methods_requested if mid in cards}
    write(output / 'primary_selection.json', selection)
    index = prepare_index(snapshot, output / 'primary_selection.json')
    write(output / 'methods.json', index)
    original_candidates=list(args.seed)
    completion_decisions={}
    completed_catalog=getattr(args,'completed_catalog',None)
    if completed_catalog:
        from experiments.history_parallel_20261112.merge_completed import completed_history_registry,select_uninvestigated_histories
        registry=completed_history_registry(completed_catalog)
        write(output/'completed_history_registry.json',registry)
        args.seed,completion_decisions=select_uninvestigated_histories(index['methods'],original_candidates,registry,
            policy_revisits=getattr(args,'policy_revisits',{}),recovery_reasons=getattr(args,'recovery_reasons',{}))
        methods_requested=[*args.seed,*review_runs]
        write(output/'completion_selection.json',{'requested_candidates':original_candidates,
            'selected':args.seed,'decisions':completion_decisions,
            'worker_limit':args.workers,'explicit_policy_revisits':getattr(args,'policy_revisits',{}),
            'interrupted_recovery_reasons':getattr(args,'recovery_reasons',{})})
    limit=getattr(args,'max_ready_seeds',None)
    eligibility={mid:{'status':index['methods'].get(mid,{}).get('status','absent'),
                      'reason':index['methods'].get(mid,{}).get('hold_reason'),
                      'papers':[{k:p.get(k) for k in ('source_id','pmid','format','seed_eligible','hold_reason','relationship')}
                                for p in index['methods'].get(mid,{}).get('papers',[])]}
                 for mid in original_candidates}
    if limit is not None:
        ready=[mid for mid in args.seed if eligibility[mid]['status']=='ready']
        write(output/'selection_eligibility.json',{'candidates':eligibility,'completion_decisions':completion_decisions,'selected':ready[:limit],
              'deferred_ready':ready[limit:],'max_ready_seeds':limit,
              'boundary':'Batch scheduling limit, not a reference or ancestry limit. PDF/identity/source holds remain explicit.'})
        args.seed=ready[:limit]
        methods_requested=[*args.seed,*review_runs]
        if not methods_requested:
            raise ValueError('No source-ready registered JATS seed; see selection_eligibility.json')
    cache = reference_cache(snapshot, args.library, args.vault, output / 'reference_cache.json')
    citation_runs = {}
    for mid in methods_requested:
        method = index['methods'].get(mid)
        item = {'status': 'waiting', 'path': str(output / 'citations' / mid),
                'mode': 'fresh_review_of_unchanged_research' if mid in review_runs else 'new_full_paper_research'}
        if not method or method['status'] != 'ready':
            item.update(status='source_hold', reason=method.get('hold_reason') if method else 'Extraction not yet accepted')
        citation_runs[mid] = item
    state = {'status': 'citation_research', 'job_id': os.environ['SLURM_JOB_ID'],
        'seeds': methods_requested, 'citation_runs': citation_runs, 'snapshot': snapshot_result, 'workers': args.workers,
        'analysis_format': 'citation_analysis/4', 'model': 'gpt-6.1-sol', 'effort': 'xhigh',
        'requested_candidate_count':len(original_candidates),'selected_target_count':len(methods_requested),
        'completed_reused_count':sum(x['reason']=='completed_exact_version_reused' for x in completion_decisions.values()),
        'explicit_policy_revisit_count':sum(x['reason']=='explicit_policy_revisit' for x in completion_decisions.values())}
    def checkpoint():
        # Event-driven progress; waiting work remains visible before a slot opens.
        # A native stage's detailed progress is in its own saved state.json.
        state['counts'] = dict(Counter(row['status'] for row in state['citation_runs'].values()))
        state['updated_at'] = now()
        write(output / 'state.json', state)
    checkpoint()
    semaphore = asyncio.Semaphore(args.workers)

    async def one(mid):
        row = state['citation_runs'][mid]
        if row['status'] == 'source_hold':
            return
        async with semaphore:
            try:
                method = index['methods'][mid]
                destination = Path(row['path'])
                paper = method['selected_primary']
                runner = str(args.runtime / 'experiments/citation_lineage_pilot/run.py')
                if mid in review_runs:
                    old = read(review_runs[mid] / 'inputs/seed_binding.json')
                    if old['parent_artifact_pins']['extraction.json'] != method['extraction_sha256']:
                        row.update(status='source_hold',
                            reason='Existing research review would join a different extracted workflow version')
                        checkpoint(); return
                    command = [sys.executable, runner, 'review-existing', '--previous', str(review_runs[mid]), '--output', str(destination)]
                else:
                    command = [sys.executable, runner, 'run',
                        '--batch', str(snapshot), '--inventory', str(method_inventory), '--record', mid,
                        '--paper', paper['source_id'], '--article', paper['article'], '--source-cache', str(cache),
                        '--output', str(destination)]
                command+=['--reading-policy',getattr(args,'reading_policy','focused')]
                log = output / (mid + '.log')
                row.update(status='running', command=command, started_at=now(), log=str(log))
                checkpoint()
                with log.open('wb') as stream:
                    process = await asyncio.create_subprocess_exec(*command, stdout=stream, stderr=asyncio.subprocess.STDOUT)
                    code = await process.wait()
                native = read(destination / 'state.json') if (destination / 'state.json').exists() else {}
                row.update(status='completed' if code == 0 and native.get('status') == 'completed' else 'failed',
                           exit_code=code, native_state=native.get('status'), reason=native.get('error'), log=str(log))
            except Exception as exc:
                row.update(status='failed', reason=f'{type(exc).__name__}: {exc}')
            row['finished_at'] = now()
            checkpoint()

    await asyncio.gather(*(one(mid) for mid in methods_requested))
    completed = {mid: row['path'] for mid, row in state['citation_runs'].items() if row['status'] == 'completed'}
    write(output / 'pair_candidates.json', pair_candidates(index, completed))
    overlays = []
    for mid, path in completed.items():
        root = Path(path); binding = read(root / 'inputs/seed_binding.json')
        overlays.append({'path': str(root / 'graph.json'), 'sha256': sha(root / 'graph.json'),
            'seed_extraction': str(Path(binding['parent_attempt']) / 'extraction.json'),
            'source_registry': {'path': str(root / 'inputs/sources.json'), 'sha256': sha(root / 'inputs/sources.json')}})
    from genomics_discovery.illumina.history_method_identity import method_registry, connectivity_report
    registry_path = output / 'method_registry.json'
    write(registry_path, method_registry(snapshot, method_inventory))
    write(output / 'history_inputs.json', {'format_version': 'illumina_history_inputs/1', 'overlays': overlays,
        'method_registry': {'path': str(registry_path), 'sha256': sha(registry_path)}})
    # A reviewed method-level relationship does not require extraction or exact
    # Operation/State mapping of the cited donor. Publish the citation graph first.
    from genomics_discovery.illumina.history import build_history_index
    from genomics_discovery.illumina.history_view import build as build_history_view
    history_index = build_history_index(output / 'history_inputs.json')
    write(output / 'history_index.json', history_index)
    connectivity = connectivity_report(history_index, methods_requested)
    write(output / 'CONNECTIVITY.json', connectivity)
    write(output / 'ATLAS_ADDITION_QUEUE.json', {'history_index_sha256': sha(output / 'history_index.json'), 'entries': connectivity['atlas_addition_queue']})
    state['history_view'] = build_history_view(output / 'history_index.json', output / 'view')
    state.update(status='completed_with_issues' if any(r['status'] != 'completed'
                 for r in state['citation_runs'].values()) else 'completed', completed_citation_runs=len(completed))
    checkpoint()
    write(output / 'RESULT.json', state)
    if completed and not getattr(args, 'citation_only', False):
        from types import SimpleNamespace
        from experiments.history_changes_20261106.connect import execute as connect_workflows
        connections = await connect_workflows(SimpleNamespace(history_run=output,
            output=output / 'connections', runtime=args.runtime, workers=args.workers,
            prior_manifests=getattr(args, 'prior_manifest', [])))
        state['connections'] = {'path': str(output / 'connections'),
            'status': connections['status'], 'reviewed_workflow_connections': connections['reviewed_workflow_connections'],
            'unbound_contributions': connections['unbound_contributions']}
        if connections['status'] != 'completed':
            state['status'] = 'completed_with_issues'
        checkpoint(); write(output / 'RESULT.json', state)
    return state


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('batch', 'library', 'vault', 'runtime', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--method-inventory', type=Path, help='Pinned complete method catalogue, including known unextracted methods; defaults to batch inventory.')
    parser.add_argument('--seed', action='append', default=[])
    parser.add_argument('--selection', type=Path)
    parser.add_argument('--agents', '--workers', dest='workers', type=int,
                        choices=range(1, MAX_HISTORY_WORKERS + 1), default=None,
                        help='Maximum concurrent method investigations; default is one per selected method, up to 50. --workers is a compatibility alias.')
    parser.add_argument('--citation-only', action='store_true', help='Publish the reviewed method/contribution graph and viewer; omit optional exact workflow-location mapping.')
    parser.add_argument('--review-existing', type=Path, action='append', default=[])
    parser.add_argument('--prior-manifest', type=Path, action='append', default=[])
    parser.add_argument('--reading-policy',choices=('focused','full'),default='focused')
    parser.add_argument('--max-ready-seeds',type=int,choices=range(1,51),help='Select the first N ready new seeds after source preflight; record all holds/deferred candidates.')
    parser.add_argument('--software-preflight',action='store_true',help='Run bounded citation-contract software checks before source/model work.')
    parser.add_argument('--batch-state',type=Path,help='Optional file pin for recorded plans; must equal batch/state.json.')
    parser.add_argument('--completed-catalog',type=Path,default=Path(__file__).resolve().parents[2]/'data/run_catalog.json',help='Canonical run catalog consulted before launching any new seed; all declared completion sources are joined.')
    return parser


def parse_args(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.selection:
        plan = read(args.selection)
        if plan.get('format_version') != 'citation_history_selection/1':
            parser.error('Unknown history selection format')
        args.seed.extend(plan['seed_methods'])
        args.review_existing.extend(Path(p) for p in plan.get('review_existing', []))
        args.prior_manifest.extend(Path(p) for p in plan.get('prior_manifests', []))
        args.policy_revisits=plan.get('policy_revisits',{})
        args.recovery_reasons=plan.get('recovery_reasons',{})
    else:
        args.policy_revisits={}
        args.recovery_reasons={}
    try:
        validate_request(args)
    except ValueError as exc:
        parser.error(str(exc))
    return args


if __name__ == '__main__':
    args = parse_args()
    result = asyncio.run(execute(args))
    raise SystemExit(2 if any(r['status'] == 'failed' for r in result['citation_runs'].values()) else 0)
