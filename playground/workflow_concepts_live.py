"""Label one source-bound operation group using lookup, proposal and fresh review.

Only the host applies the reviewed assignment. Stale new-concept proposals stop
for reconciliation; reviewed reuse of an unchanged definition may still commit.
"""

import argparse
import asyncio
import json
from pathlib import Path

from genomics_discovery.curation.codex_worker import (
    CodexWorker, DEFAULT_EFFORT, DEFAULT_MODEL, assess_archived_attempt,
)
from genomics_discovery.curation.operation_registry.__main__ import load_manifest
from genomics_discovery.curation.operation_registry.models import Decision
from genomics_discovery.curation.operation_registry.prompts import build_prompt
from genomics_discovery.curation.operation_registry.store import (
    Registry, catalogue, check_proposal,
)
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipReview, load_occurrences, review_membership,
)
from playground.workflow_unit_review_inputs import review_view


async def execute(manifest_path: Path, database: Path, run: Path, *, worker=None):
    manifest_path, run = manifest_path.resolve(), run.resolve()
    registry = Registry(database)
    # Explicit initialization prevents a path typo from silently starting a new vocabulary.
    snapshot = catalogue(registry.snapshot())
    manifest = load_manifest(manifest_path)
    if len(manifest) != 1:
        raise ValueError("Each labeling job targets one connected operation selection")
    occurrences, packets = load_occurrences(manifest)
    occurrence = next(iter(occurrences.values()))
    packet = Path(manifest[occurrence.id]["packet"])
    if run.exists():
        raise ValueError("Use a fresh run directory")
    run.mkdir(parents=True)
    write(run / "manifest.json", manifest)
    write(run / "registry.json", snapshot)
    write(run / "occurrence_view.json", review_view(occurrence))
    worker = worker or CodexWorker(web_search=False, network_access=False)
    if isinstance(worker, CodexWorker) and (worker.web_search or worker.network_access):
        raise ValueError("Labeling uses registered source packets")
    state = {"status": "running", "started_at": now(), "attempts": [],
             "model": getattr(worker, "model", DEFAULT_MODEL),
             "effort": getattr(worker, "effort", DEFAULT_EFFORT),
             "registry_revision": snapshot["registry_revision"]}
    write(run / "state.json", state)
    files = {manifest_path, run / "manifest.json", run / "registry.json",
             run / "occurrence_view.json", Path(__file__).resolve()}
    files.update(Path(p) for row in manifest.values() for p in row.values() if p)
    files.update(Path(s["original_path"]) for p in packets.values() for s in p["sources"])
    src = Path(__file__).resolve().parents[1] / "src" / "genomics_discovery"
    files.update(src.rglob("*.py"))
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
        prompt = build_prompt(role, run, attempt, packet, proposal_hash=proposal_hash)
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
        decision = await stage("compiler", Decision)
        write(run / "decision.json", decision.model_dump(mode="json"))
        if decision.assignment is None:
            state.update(status="abstained", explanation=decision.explanation)
        else:
            proposal = decision.assignment
            # The worker cannot silently switch to another selection in the manifest.
            if proposal.members[0].occurrence_id != occurrence.id:
                raise ValueError("Proposal changed the requested occurrence")
            lookups = check_proposal(snapshot, proposal, occurrences, packets)
            write(run / "proposal.json", proposal.model_dump(mode="json"))
            write(run / "lookup_results.json", lookups)
            pins[run / "proposal.json"] = sha(run / "proposal.json")
            review = await stage("reviewer", MembershipReview, digest(proposal))
            write(run / "review.json", review.model_dump(mode="json"))
            checked = review_membership(proposal, review, occurrences, packets)
            state["membership_status"] = checked["status"]
            if checked["status"] != "supported_membership_candidate":
                state["status"] = checked["status"]
            else:
                check()
                provenance = {"kind": "fresh_native_threads", "independent_execution_verified": True,
                              "threads": [a["thread_id"] for a in state["attempts"]],
                              "archive_paths": [a["attempt"] for a in state["attempts"]]}
                state["commit"] = registry.apply(proposal, review, occurrences, packets,
                                                  provenance=provenance)
                state["status"] = "committed"
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
    args = parser.parse_args()
    result = asyncio.run(execute(args.manifest, args.database, args.run,
        worker=CodexWorker(model=args.model, effort=args.effort, web_search=False, network_access=False)))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
