"""Run source review, focused scope comparisons, and coordinated publication.

Parallel callers share a database, not model conversations. A changed canonical
target gets a fresh source review. Unresolved judgments remain inspectable tasks.
"""

import asyncio
import argparse
import json
import re
import time
import uuid
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker, assess_archived_attempt, DEFAULT_MODEL, DEFAULT_EFFORT
from genomics_discovery.curation.run_state import write, sha, now
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.operation_registry.definition_comparison import ComparisonReport, prompt
from genomics_discovery.curation.operation_registry.proposal_queue import (
    missing_plan, attach_comparisons, submission,
)
from genomics_discovery.curation.operation_registry.comparison_claims import claim_pairs, release_pairs
from genomics_discovery.curation.operation_registry.publication import publish, reuse_proposal
from genomics_discovery.curation.operation_registry.storage import connect
from genomics_discovery.curation.operation_registry.store import Registry, catalogue
from playground.workflow_concepts_live import execute as source_review


async def compare(worker, plan, attempt):
    attempt.mkdir()
    write(attempt / "plan.json", plan)
    write(attempt / "schema.json", ComparisonReport.model_json_schema())
    (attempt / "plan_sha256.txt").write_text(digest(plan))
    instruction = prompt(attempt / "plan.json", attempt)
    write(attempt / "instruction.json", {"prompt": instruction})
    pins = {path: sha(path) for path in attempt.iterdir() if path.is_file()}
    result = await worker.run(instruction, attempt)
    checked = assess_archived_attempt(attempt)
    if (not checked.complete or not checked.thread_id or result.exit_code != 0 or result.error
        or checked.thread_id != result.thread_id or checked.usage != result.usage
        or (attempt / "prompt.txt").read_text() != instruction
        or any(sha(path) != expected for path, expected in pins.items())):
        raise ValueError("Scope comparison lacks a complete unchanged native archive")
    report = ComparisonReport.model_validate_json((attempt / "artifact.json").read_bytes())
    return report, {"kind": "fresh_native_comparison", "thread_id": checked.thread_id,
                    "archive_path": str(attempt), "elapsed_seconds": result.elapsed_seconds,
                    "usage": result.usage, "artifact_sha256": sha(attempt / "artifact.json")}


async def resolve(database, key, run, worker, *, max_updates=4, forbidden_threads=(),
                  embeddings=None):
    """Resume from durable proposals/reviews; bounded updates prevent livelock."""
    if max_updates < 1:
        raise ValueError("Use at least one admission attempt")
    runs = []
    # A lease covers the worker's existing execution timeout plus shutdown time.
    # Waiting consumes no model-call update. Expiry permits recovery after a crash.
    ttl = (getattr(worker, "timeout_seconds", None) or 1800) + 60
    deadline = time.monotonic() + ttl
    while len(runs) < max_updates:
        result = await asyncio.to_thread(publish, database, key, embeddings=embeddings)
        if result["status"] != "awaiting_comparisons":
            return {**result, "comparison_runs": runs}
        owner = uuid.uuid4().hex
        claim = await asyncio.to_thread(claim_pairs, database, key, owner, ttl=ttl,
                                        embeddings=embeddings)
        plan = claim["plan"]
        write(run / "coordination.json", {"owner": owner, "waiting": claim["waiting"],
              "claimed_pairs": len(plan["pairs"]) if plan else 0, "model_calls": len(runs)})
        if not plan or not plan["pairs"]:
            if time.monotonic() >= deadline:
                return {**result, "comparison_runs": runs, "reason": "Other pair claims remain pending; resume saved work."}
            await asyncio.sleep(1)
            continue
        try:
            report, execution = await compare(worker, plan, run / f"comparison_{len(runs) + 1:03d}")
            if execution["thread_id"] in set(forbidden_threads) | {r["thread_id"] for r in runs}:
                raise ValueError("Scope comparisons require fresh native threads")
            runs.append(execution)
            await asyncio.to_thread(attach_comparisons, database, key, plan, report,
                                    provenance=execution, embeddings=embeddings)
        finally:
            await asyncio.to_thread(release_pairs, database, owner)
    result = await asyncio.to_thread(publish, database, key, embeddings=embeddings)
    return {**result, "comparison_runs": runs}


async def execute(manifest, database, run, *, worker=None, supplied_proposal=None,
                  max_revisions=2, max_updates=4, embeddings=None, semantic_limit=10,
                  require_comparison_profiles=False):
    database, run = Path(database).resolve(), Path(run).resolve()
    worker = worker or CodexWorker(web_search=False, network_access=False)
    if isinstance(worker, CodexWorker) and (worker.web_search or worker.network_access):
        raise ValueError("Shared curation uses registered source packets")
    if max_revisions < 0:
        raise ValueError("max_revisions must be nonnegative")
    run.mkdir(parents=True, exist_ok=False)
    state = {"status": "running", "started_at": now(), "source_runs": [], "resolutions": [],
             "limits": {"canonical_revisions": max_revisions, "candidate_updates": max_updates}}
    # Bind implementation throughout orchestration, not only within each child run.
    code_root = Path(__file__).resolve().parents[1]
    code = [*code_root.joinpath("src").rglob("*.py"), Path(__file__).resolve(),
            code_root / "playground/workflow_concepts_live.py"]
    pins = {p: sha(p) for p in code}
    write(run / "code_bindings.json", {str(p): h for p, h in pins.items()})
    try:
        for revision in range(max_revisions + 1):
            source_path = run / f"source_{revision:03d}"
            source = await source_review(manifest, database, source_path, worker=worker,
                                        supplied_proposal=supplied_proposal, curate=True, defer_admission=True,
                                        embeddings=embeddings, semantic_limit=semantic_limit,
                                        require_comparison_profiles=require_comparison_profiles)
            state["source_runs"].append(source)
            resolution_path = run / f"resolution_{revision:03d}"
            resolution_path.mkdir()
            if any(sha(p) != h for p, h in pins.items()):
                raise ValueError("Implementation changed during shared admission")
            result = await resolve(database, source["submission_id"], resolution_path, worker,
                max_updates=max_updates, forbidden_threads=[
                    *[a["thread_id"] for s in state["source_runs"] for a in s["attempts"]],
                    *[a["thread_id"] for r in state["resolutions"] for a in r["comparison_runs"]]],
                embeddings=embeddings)
            state["resolutions"].append(result)
            state["status"] = result["status"]
            if any(sha(p) != h for p, h in pins.items()):
                raise ValueError("Implementation changed during shared admission")
            if result["status"] != "reuse_review_required" or revision == max_revisions:
                break
            with connect(database) as db:
                saved = submission(db, source["submission_id"])
            revised = reuse_proposal(saved, result["targets"], catalogue(Registry(database).snapshot()))
            supplied_proposal = run / f"reuse_proposal_{revision + 1:03d}.json"
            write(supplied_proposal, revised.model_dump(mode="json"))
    except BaseException as exc:
        state.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                     error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        state["finished_at"] = now()
        write(run / "state.json", state)
    return state


async def batch(jobs_path, database, run, *, concurrency=4, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT,
                reconcile=True, embeddings=None, semantic_limit=10,
                require_comparison_profiles=False):
    """Bounded parallel jobs; a failed job does not discard successful neighbors."""
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    jobs = json.loads(jobs_path.read_text())
    if (not isinstance(jobs,list) or not jobs or len({j['id'] for j in jobs}) != len(jobs)
        or any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',j['id']) for j in jobs)):
        raise ValueError("Supply distinct safe job IDs")
    Registry(database).snapshot()
    run.mkdir(parents=True,exist_ok=False)
    write(run/'jobs.json',jobs)
    state={'status':'running','started_at':now(),'concurrency':concurrency,'model':model,'effort':effort,
           'jobs':{}, 'reconciliations':[]}
    lock=asyncio.Semaphore(concurrency)
    async def task(job):
        async with lock:
            state['jobs'][job['id']]={'status':'running'}
            write(run/'batch_state.json',state)
            try:
                manifest=(jobs_path.parent/job['manifest']).resolve()
                proposal=(jobs_path.parent/job['proposal']).resolve() if job.get('proposal') else None
                result=await execute(manifest,database,run/job['id'],supplied_proposal=proposal,
                    worker=CodexWorker(model=model,effort=effort,web_search=False,network_access=False),
                    embeddings=embeddings, semantic_limit=semantic_limit,
                    require_comparison_profiles=require_comparison_profiles)
                state['jobs'][job['id']]={'status':result['status'],'run':str(run/job['id'])}
            except Exception as exc:
                state['jobs'][job['id']]={'status':'failed','error':f'{type(exc).__name__}: {exc}'}
            finally:
                write(run/'batch_state.json',state)
    try:
        await asyncio.gather(*(task(j) for j in jobs))
        if reconcile:
            from genomics_discovery.curation.operation_registry.reconciliation import unresolved_components
            from playground.workflow_reconciliation_live import execute as recover
            # One post-batch pass, limited to components wholly owned by this run.
            # Do not silently revise another experiment's or a running job's drafts.
            owned = set()
            for job in state['jobs'].values():
                if job.get('run'):
                    saved = json.loads((Path(job['run']) / 'state.json').read_text())
                    owned.update(s['submission_id'] for s in saved['source_runs'])
            for component in unresolved_components(database, embeddings=embeddings, submission_ids=owned):
                if not set(component) <= owned:
                    continue
                path = run / f"reconciliation_{len(state['reconciliations']) + 1:03d}"
                try:
                    result = await recover(database, component, path,
                        worker=CodexWorker(model=model,effort=effort,web_search=False,network_access=False),
                        embeddings=embeddings, semantic_limit=semantic_limit,
                        require_comparison_profiles=require_comparison_profiles)
                    state['reconciliations'].append({'status':result['status'],'run':str(path),
                        'submission_ids':component, 'replacement_submission_id':result['replacement_submission_id']})
                    for job in state['jobs'].values():
                        if job.get('run'):
                            saved = json.loads((Path(job['run']) / 'state.json').read_text())
                            if any(s['submission_id'] in component for s in saved['source_runs']):
                                job.update(recovery_status=result['status'], reconciliation_run=str(path))
                except Exception as exc:
                    state['reconciliations'].append({'status':'failed','run':str(path),
                        'submission_ids':component, 'error':f'{type(exc).__name__}: {exc}'})
                write(run/'batch_state.json',state)
        state['status']='finished_with_failures' if any(j['status']=='failed' for j in state['jobs'].values()) else 'finished'
        if any(r['status']=='failed' for r in state['reconciliations']):
            state['status']='finished_with_failures'
    except asyncio.CancelledError:
        state['status']='cancelled'
        raise
    finally:
        state['finished_at']=now();write(run/'batch_state.json',state)
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('jobs',type=Path)
    parser.add_argument('database',type=Path)
    parser.add_argument('run',type=Path)
    parser.add_argument('--concurrency',type=int,default=4)
    parser.add_argument('--model',default=DEFAULT_MODEL)
    parser.add_argument('--effort',default=DEFAULT_EFFORT)
    parser.add_argument('--no-reconcile', action='store_true', help='Leave ambiguous drafts for explicit later recovery')
    parser.add_argument('--semantic-cache', type=Path, help='Existing local embedding cache; no model download')
    parser.add_argument('--semantic-limit', type=int)
    parser.add_argument('--require-comparison-profiles', action='store_true')
    args=parser.parse_args()
    if args.semantic_limit is not None and args.semantic_cache is None:
        parser.error('--semantic-limit requires --semantic-cache')
    embeddings = None
    if args.semantic_cache is not None:
        from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache, LocalEmbedder
        embeddings = EmbeddingCache(args.semantic_cache / 'vectors.sqlite3',
                                    LocalEmbedder(args.semantic_cache / 'models', download=False))
    print(json.dumps(asyncio.run(batch(args.jobs.resolve(),args.database.resolve(),args.run.resolve(),
                                     concurrency=args.concurrency,model=args.model,effort=args.effort,
                                     reconcile=not args.no_reconcile, embeddings=embeddings,
                                     semantic_limit=10 if args.semantic_limit is None else args.semantic_limit,
                                     require_comparison_profiles=args.require_comparison_profiles)),indent=2))


if __name__=='__main__':
    main()
