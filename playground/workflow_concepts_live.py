"""Match a cohort against a frozen vocabulary; --curate explicitly enables admission.

Default matching has no registry writes. Two fresh sessions assess host-enumerated
pairs; extensions stay pending. Curation retains the earlier source/utility gates.
"""

import argparse
import asyncio
import json
from pathlib import Path

from genomics_discovery.curation.codex_worker import (
    CodexWorker, DEFAULT_EFFORT, DEFAULT_MODEL, assess_archived_attempt,
)
from genomics_discovery.curation.operation_registry.__main__ import load_manifest
from genomics_discovery.curation.operation_registry.admission import (
    VocabularyProposal, CurrentVocabularyReview, validate_decision, validate_batch,
)
from genomics_discovery.curation.operation_registry.prompts import build_prompt
from genomics_discovery.curation.operation_registry.store import Registry, catalogue
from genomics_discovery.curation.operation_registry.frozen_matching import (
    MatchingReport, make_plan, validate_report, reconcile_reports,
)
from genomics_discovery.curation.operation_registry.frozen_prompts import build_frozen_prompt
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import load_occurrences
from playground.workflow_unit_review_inputs import review_view


async def execute(manifest_path: Path, database: Path, run: Path, *, worker=None,
                  supplied_proposal=None, curate=False, defer_admission=False,
                  reconciliation_context=None, embeddings=None, semantic_limit=10,
                  require_comparison_profiles=False):
    manifest_path, run = manifest_path.resolve(), run.resolve()
    registry = Registry(database)
    if defer_admission and not curate:
        raise ValueError("Deferred admission requires curation mode")
    if embeddings is not None and not defer_admission:
        raise ValueError("Semantic registry retrieval requires coordinated shared admission")
    if reconciliation_context is not None and (not curate or supplied_proposal):
        raise ValueError("Reconciliation requires a fresh curation compiler")
    # Explicit initialization prevents a path typo from silently starting a new vocabulary.
    snapshot = catalogue(registry.snapshot())
    if supplied_proposal and not curate:
        raise ValueError("A supplied vocabulary proposal requires explicit curation mode")
    manifest = load_manifest(manifest_path)
    if not manifest:
        raise ValueError("A vocabulary job needs source-bound selections")
    if require_comparison_profiles and any(not row.get("comparison_profile") for row in manifest.values()):
        raise ValueError("Required comparison profiles are missing from the selection manifest")
    occurrences, packets = load_occurrences(manifest)
    packet_paths = {ident: Path(row["packet"]) for ident, row in manifest.items()}
    if run.exists():
        raise ValueError("Use a fresh run directory")
    run.mkdir(parents=True)
    write(run / "manifest.json", manifest)
    write(run / "registry.json", snapshot)
    write(run / "selections.json", {ident: review_view(occ) for ident, occ in occurrences.items()})
    if any(row.get("comparison_profile") for row in manifest.values()):
        from genomics_discovery.curation.workflow_matching.comparison_profile_cli import selection_profile_views
        write(run / "comparison_profiles.json", selection_profile_views(manifest, occurrences))
    if reconciliation_context is not None:
        write(run / "reconciliation.json", reconciliation_context)
    plan = make_plan(snapshot, occurrences)
    if not curate:
        write(run / "plan.json", plan)
    worker = worker or CodexWorker(web_search=False, network_access=False)
    if isinstance(worker, CodexWorker) and (worker.web_search or worker.network_access):
        raise ValueError("Labeling uses registered source packets")
    state = {"status": "running", "started_at": now(), "attempts": [],
             "model": getattr(worker, "model", DEFAULT_MODEL),
             "effort": getattr(worker, "effort", DEFAULT_EFFORT),
             "proposal_origin": ("supplied" if supplied_proposal else "native_compiler") if curate else None,
             "assessment_origin": None if curate else "two_fresh_native_sessions",
             "mode": "curation" if curate else "frozen_matching",
             "registry_revision": snapshot["registry_revision"]}
    if require_comparison_profiles or any(row.get("comparison_profile") for row in manifest.values()):
        state["require_comparison_profiles"] = require_comparison_profiles
    if embeddings is not None:
        from genomics_discovery.curation.operation_registry.proposal_queue import retrieval_policy
        state["retrieval_policy"] = retrieval_policy(embeddings, semantic_limit)
    write(run / "state.json", state)
    files = {manifest_path, run / "manifest.json", run / "registry.json",
             run / "selections.json", Path(__file__).resolve(),
             Path(__file__).with_name("workflow_unit_review_inputs.py")}
    if not curate:
        files.add(run / "plan.json")
    if reconciliation_context is not None:
        files.add(run / "reconciliation.json")
    if (run / "comparison_profiles.json").is_file():
        files.add(run / "comparison_profiles.json")
    files.update(Path(p) for row in manifest.values() for p in row.values() if p)
    files.update(Path(s["original_path"]) for p in packets.values() for s in p["sources"])
    src = Path(__file__).resolve().parents[1] / "src" / "genomics_discovery"
    files.update(src.rglob("*.py"))
    if supplied_proposal:
        supplied_proposal = Path(supplied_proposal).resolve()
        files.add(supplied_proposal)
    pins = {path: sha(path) for path in files}
    write(run / "bindings.json", {str(path): value for path, value in pins.items()})

    def check():
        if any(not path.is_file() or sha(path) != expected for path, expected in pins.items()):
            raise ValueError("Frozen labeling inputs, implementation or prior artifacts changed")
        load_occurrences(manifest)

    async def stage(role, model, proposal_hash=""):
        check()
        attempt = run / f"{len(state['attempts']) + 1:03d}_{role}"
        attempt.mkdir()
        schema = model.model_json_schema()
        write(attempt / "schema.json", schema)
        prompt = (build_prompt(role, run, attempt, packet_paths, proposal_hash=proposal_hash)
                  if curate else build_frozen_prompt(role, run, attempt, packet_paths))
        write(attempt / "instruction.json", {"prompt": prompt})
        for path in (attempt / "schema.json", attempt / "instruction.json"):
            pins[path] = sha(path)
        row = {"role": role, "attempt": str(attempt), "status": "running", "started_at": now()}
        state["attempts"].append(row)
        write(run / "state.json", state)
        try:
            result = await worker.run(prompt, attempt)
            checked = assess_archived_attempt(attempt)
            if (not checked.complete or not checked.thread_id or result.exit_code != 0
                or result.error is not None or result.thread_id != checked.thread_id
                or result.usage != checked.usage
                or any(a is not row and a.get("thread_id") == checked.thread_id
                       for a in state["attempts"])):
                raise ValueError("Expected a completed, independently archived fresh native thread")
            if (attempt / "prompt.txt").read_text() != prompt:
                raise ValueError("Worker prompt differs from the recorded instruction")
            check()
            artifact = attempt / "artifact.json"
            document = model.model_validate_json(artifact.read_bytes())
            row.update(status="complete", thread_id=checked.thread_id, usage=result.usage,
                       elapsed_seconds=result.elapsed_seconds, artifact_sha256=sha(artifact))
            for name in ("artifact.json", "prompt.txt", "events.jsonl", "result.json",
                         "command.json", "started.json", "stderr.log", "final.txt"):
                path = attempt / name
                if path.is_file():
                    pins[path] = sha(path)
            return document
        except BaseException as exc:
            row.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                       error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            metadata_path = attempt / "result.json"
            if metadata_path.is_file():
                try:
                    metadata = json.loads(metadata_path.read_text())
                    row.update({key: metadata[key] for key in
                                ("elapsed_seconds", "usage", "exit_code", "thread_id") if key in metadata})
                except (OSError, ValueError, TypeError) as exc:
                    row["metadata_error"] = f"{type(exc).__name__}: {exc}"
            row["finished_at"] = now()
            write(run / "state.json", state)

    try:
        if not curate:
            first = await stage("matcher", MatchingReport)
            validate_report(first, plan, snapshot, occurrences, packets)
            second = await stage("checker", MatchingReport)
            result = reconcile_reports(first, second, plan, snapshot, occurrences, packets)
            check()
            if catalogue(registry.snapshot()) != snapshot:
                raise ValueError("Registry changed during the frozen matching run")
            result["execution"] = {
                "threads": [a["thread_id"] for a in state["attempts"]],
                "fresh_native_threads": True,
                "reviewer_blinding": "prompt_instructed_not_access_controlled",
                "source_inspection": "declared_passages_with_quote_integrity_not_read_attestation",
            }
            write(run / "matching_results.json", result)
            write(run / "pending_extensions.json", result["pending_extensions"])
            state.update(status="completed_frozen", result=str(run / "matching_results.json"),
                         registry_mutated=False, pending_extensions=len(result["pending_extensions"]),
                         pair_count=len(result["decisions"]))
            return state
        proposal = (VocabularyProposal.model_validate_json(supplied_proposal.read_bytes())
                    if supplied_proposal else await stage("compiler", VocabularyProposal))
        if set(occurrences) != {d.occurrence_id for d in proposal.decisions}:
            raise ValueError("Proposal must account for every supplied selection exactly once")
        lookups = [validate_decision(d, snapshot, occurrences, packets) for d in proposal.decisions]
        write(run / "proposal.json", proposal.model_dump(mode="json"))
        write(run / "lookup_results.json", lookups)
        pins[run / "proposal.json"] = sha(run / "proposal.json")
        if defer_admission:
            from genomics_discovery.curation.operation_registry.proposal_queue import enqueue
            state["submission_id"] = enqueue(registry.path, proposal, snapshot, manifest,
                                             embeddings=embeddings, semantic_limit=semantic_limit)
            write(run / "state.json", state)
        review = await stage("reviewer", CurrentVocabularyReview, digest(proposal))
        write(run / "review.json", review.model_dump(mode="json"))
        validate_batch(proposal, review, snapshot, occurrences, packets)
        check()
        provenance = {"kind": "supplied_proposal_native_review" if supplied_proposal else "fresh_native_threads",
                      "independent_execution_verified": supplied_proposal is None,
                      "fresh_review_execution_verified": True,
                      "source_inspection": "reviewer_declared_not_host_attested",
                      "definition_review_contract": "concept_admission_review/2.0",
                      "threads": [a["thread_id"] for a in state["attempts"]],
                      "archive_paths": [a["attempt"] for a in state["attempts"]]}
        if defer_admission:
            from genomics_discovery.curation.operation_registry.proposal_queue import attach_review
            attach_review(registry.path, state["submission_id"], review, provenance=provenance)
            state["status"] = "reviewed_pending_admission"
        else:
            state["commit"] = registry.apply_batch(proposal, review, occurrences, packets, provenance=provenance)
            state["status"] = ("committed" if any(r["status"] == "admit" for r in state["commit"]["outcomes"])
                               else "completed_without_admission")
    except asyncio.CancelledError:
        state["status"] = "cancelled"
        raise
    except Exception as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        state["finished_at"] = now()
        write(run / "bindings.json", {str(path): value for path, value in pins.items()})
        write(run / "state.json", state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("database", type=Path)
    parser.add_argument("run", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    parser.add_argument("--proposal", type=Path, help="Review an externally supplied vocabulary proposal")
    parser.add_argument("--curate", action="store_true",
                        help="Explicit vocabulary admission mode; default matching never writes the registry")
    parser.add_argument("--shared", action="store_true", help="Use indexed pending proposals and concurrent-safe admission")
    parser.add_argument("--semantic-cache", type=Path, help="Existing local embedding cache; requires --shared")
    parser.add_argument("--semantic-limit", type=int)
    parser.add_argument("--require-comparison-profiles", action="store_true")
    args = parser.parse_args()
    if args.semantic_cache is not None and not args.shared:
        parser.error("--semantic-cache requires --shared")
    if args.semantic_limit is not None and args.semantic_cache is None:
        parser.error("--semantic-limit requires --semantic-cache")
    embeddings = None
    if args.semantic_cache is not None:
        from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache, LocalEmbedder
        embeddings = EmbeddingCache(args.semantic_cache / "vectors.sqlite3",
                                    LocalEmbedder(args.semantic_cache / "models", download=False))
    worker = CodexWorker(model=args.model, effort=args.effort, web_search=False, network_access=False)
    if args.shared:
        from playground.workflow_shared_live import execute as execute_shared
        result = asyncio.run(execute_shared(args.manifest, args.database, args.run,
                            supplied_proposal=args.proposal, worker=worker,
                            embeddings=embeddings, semantic_limit=10 if args.semantic_limit is None else args.semantic_limit,
                            require_comparison_profiles=args.require_comparison_profiles))
    else:
        result = asyncio.run(execute(args.manifest, args.database, args.run,
            supplied_proposal=args.proposal, curate=args.curate, worker=worker,
            require_comparison_profiles=args.require_comparison_profiles))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
