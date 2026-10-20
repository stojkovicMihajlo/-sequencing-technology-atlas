"""Two source-bound profile sessions and one experiment-only shared curation run.

Run only in Sherlock Slurm with the frozen runtime on PYTHONPATH. This diagnostic
prescribes two existing operation boundaries, never a correspondence or mechanism.
There is no retry, repair, alternate artifact parser or production registry write.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
from collections import Counter
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.curation.operation_registry.store import Registry
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction import models as workflow_models
from genomics_discovery.curation.workflow_extraction.models import (
    Workflow, WorkflowReview, digest, review_outcome, validate_review,
)
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from genomics_discovery.curation.workflow_extraction.transformations import (
    cut_transformation, load_occurrences,
)
from genomics_discovery.curation.workflow_matching.comparison_profile_cli import run as profile_tool
from genomics_discovery.curation.workflow_matching.comparison_profiles import (
    ComparisonProfile, comparison_profile_schema, iter_profile_assertions,
    load_comparison_profile, validate_profile_selection,
)
from playground.workflow_shared_live import execute as shared_execute


EXPERIMENT = Path("technology_atlas/v2/work/qiu_experiments_20261019/semantic_alignment_23")
COHORT = Path("technology_atlas/v2/work/qiu_experiments_20261019/cohort_21/batch")
SELECTIONS = (("chipmentation", "tagment_v1"), ("drop-seq", "tagmentation"))
BOUNDARY = (
    "Bounded diagnostic on two prescribed existing operation boundaries. Profiles are "
    "unreviewed interpretations; source reviews independently inspect original passages. "
    "Hashes/quote checks establish provenance, not entailment or scientific validation. "
    "No expected shared mechanism, substitution, ancestry or match is supplied. Registry "
    "admission is confined to this fresh experiment database, never the production atlas."
)


def read(path):
    return json.loads(Path(path).read_bytes())


def project_path(root, value):
    """Resolve supplied/recorded locations without changing any historical record."""
    path = Path(value)
    relative = path.relative_to(root) if path.is_absolute() else path
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path leaves the selected project root: {value}")
    return resolved


def verify(pins):
    for name, expected in pins.items():
        path = Path(name)
        if not path.is_file() or sha(path) != expected:
            raise ValueError(f"Frozen pilot input or artifact changed: {path}")


def discover(root):
    """Use accepted reviewer candidates, not an integrator's earlier draft."""
    batch_path = root / COHORT / "state.json"
    batch = read(batch_path)
    pins = {str(batch_path): sha(batch_path)}
    parents = {}
    for method, operation_id in SELECTIONS:
        accepted = batch["accepted_parents"][method]
        if accepted["outcome"] != "supported" or accepted["primary_outcome"] != "supported":
            raise ValueError(f"The cohort has no supported accepted parent for {method}")
        runtime = project_path(root, accepted["runtime_run"])
        state_path = runtime / "state.json"
        state = read(state_path)
        if state["status"] != "supported":
            raise ValueError(f"Accepted runtime state is not supported: {method}")
        paths = {"workflow": project_path(root, accepted["workflow"]),
                 "review": project_path(root, accepted["review"]),
                 "packet": runtime / "source_packet.json"}
        for recorded, expected in accepted["pins"].items():
            path = project_path(root, recorded)
            if sha(path) != expected:
                raise ValueError(f"Accepted parent byte pin differs: {path}")
            pins[str(path)] = expected
        if sha(paths["packet"]) != accepted["packet_sha256"]:
            raise ValueError(f"Accepted source packet byte pin differs: {method}")
        if (sha(paths["workflow"]) != state["workflow_sha256"]
                or sha(paths["review"]) != state["review_sha256"]):
            raise ValueError(f"Accepted parent and runtime state differ: {method}")
        workflow = Workflow.model_validate_json(paths["workflow"].read_bytes())
        review = WorkflowReview.model_validate_json(paths["review"].read_bytes())
        packet = load_packet(paths["packet"])
        errors = validate_review(review, workflow, packet)
        if errors or review_outcome(review, workflow) != "supported":
            raise ValueError(f"Accepted source review binding failed for {method}: {errors}")
        occurrence = cut_transformation(workflow, packet, [operation_id], review=review)
        if occurrence.workflow_review_status != "supported":
            raise ValueError(f"Selected parent review is not supported: {method}")
        pins.update({str(path): sha(path) for path in (*paths.values(), state_path)})
        for source in packet["sources"]:
            original = project_path(root, source["original_path"])
            pins[str(original)] = sha(original)
        for item in accepted.get("accepted_source_coverage", {}).values():
            path = project_path(root, item["path"])
            if sha(path) != item["sha256"]:
                raise ValueError(f"Accepted source coverage pin differs: {path}")
            pins[str(path)] = item["sha256"]
        parents[method] = {"paths": paths, "workflow": workflow, "packet": packet,
                           "occurrence": occurrence, "operation_id": operation_id}
    code_root = Path(workflow_models.__file__).resolve().parents[2]
    code = [*code_root.rglob("*.py"), Path(__file__).resolve(),
            Path(shared_execute.__code__.co_filename).resolve(),
            Path(shared_execute.__code__.co_filename).resolve().with_name("workflow_concepts_live.py")]
    pins.update({str(path): sha(path) for path in code})
    return parents, pins


def archive_summary(run):
    """Reuse transport assessment; never interpret provider event contents here."""
    archives, usage, threads = [], Counter(), set()
    for result_path in sorted(run.rglob("result.json")):
        result = read(result_path)
        if "exit_code" not in result:
            continue
        assessment = assess_archived_attempt(result_path.parent)
        if not assessment.complete or not assessment.thread_id:
            raise ValueError(f"Unusable native transport archive: {result_path.parent}")
        if assessment.thread_id in threads:
            raise ValueError("Profiles, source reviews and comparisons require distinct fresh threads")
        threads.add(assessment.thread_id)
        usage.update({key: value for key, value in assessment.usage.items()
                      if isinstance(value, (int, float)) and not isinstance(value, bool)})
        archives.append({"attempt": str(result_path.parent), "thread_id": assessment.thread_id,
                         "usage": assessment.usage, "result_sha256": sha(result_path),
                         "artifact_sha256": sha(result_path.with_name("artifact.json"))})
    return {"native_attempts": archives, "usage": dict(usage),
            "usage_scope": "Sum of separately assessed completed native result.json archives only."}


def profile_summary(profile, occurrence, path):
    statuses = Counter()
    unknowns = []
    for record, field, assertion in iter_profile_assertions(profile):
        statuses[assertion.status] += 1
        if assertion.status in {"unknown", "not_assessed"}:
            unknowns.append({"target_id": record.target_id, "field": field,
                             "assertion_id": assertion.id, "status": assertion.status,
                             "unknown_reason": assertion.unknown_reason,
                             "rationale": assertion.rationale})
    return {"profile": str(path), "profile_sha256": digest(profile),
            "profile_file_sha256": sha(path), "workflow_sha256": profile.workflow_sha256,
            "source_packet_sha256": profile.source_packet_sha256,
            "occurrence_id": occurrence.id, "occurrence_sha256": digest(occurrence),
            "status_counts": dict(statuses), "uncertainties": unknowns,
            "review_status": "unreviewed_comparison_annotations"}


async def execute(args):
    if not os.environ.get("SLURM_JOB_ID"):
        raise RuntimeError("This experiment driver requires a Sherlock Slurm allocation")
    root = args.project_root.resolve()
    run = project_path(root, args.run)
    if not run.is_relative_to(root / EXPERIMENT):
        raise ValueError("Pilot output must be inside the semantic_alignment_23 experiment")
    run.mkdir(parents=True, exist_ok=False)
    state = {"status": "preparing", "started_at": now(), "job_id": os.environ["SLURM_JOB_ID"],
             "hostname": socket.gethostname(), "project_root": str(root),
             "model": "gpt-6.1-sol", "effort": "xhigh", "profiles": {}, "boundary": BOUNDARY}
    write(run / "state.json", state)
    try:
        parents, pins = discover(root)
        tasks = {}
        for method, parent in parents.items():
            attempt = run / "profiles" / method
            attempt.mkdir(parents=True)
            task = profile_tool(["prompt", "--workflow", str(parent["paths"]["workflow"]),
                "--packet", str(parent["paths"]["packet"]), "--operation", parent["operation_id"],
                "--output", str(attempt / "artifact.json")])
            task["prompt"] += (
                "\nWork independently. Do not inspect sibling profile attempts, other methods' "
                "annotations, prior matching reports or registry memberships. The host supplies "
                "an operation boundary, never an expected scientific answer or correspondence.\n")
            write(attempt / "task.json", task)
            write(attempt / "schema.json", comparison_profile_schema())
            pins.update({str(path): sha(path) for path in
                         (attempt / "task.json", attempt / "schema.json")})
            tasks[method] = (attempt, task)
        write(run / "bindings.json", pins)
        state.update(status="prepared", selections=[
            {"method": method, "operation_id": parent["operation_id"],
             **{field: str(path) for field, path in parent["paths"].items()},
             "workflow_sha256": digest(parent["workflow"]),
             "source_packet_sha256": digest(parent["packet"]),
             "input_state_ids": [state.id for state in parent["occurrence"].inputs],
             "output_state_ids": [state.id for state in parent["occurrence"].outputs]}
            for method, parent in parents.items()])
        write(run / "state.json", state)
        if args.prepare_only:
            state["finished_at"] = now()
            write(run / "summary.json", {**state, "native_attempts": [], "usage": {},
                  "note": "Only unassessed scaffolds and source bindings prepared; no model ran."})
            return state
        from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache, LocalEmbedder
        semantic_cache = project_path(root, args.semantic_cache)
        if not (semantic_cache / "models").is_dir():
            raise ValueError("Use an existing semantic-cache/models directory; no downloads")
        embeddings = EmbeddingCache(semantic_cache / "vectors.sqlite3",
                                    LocalEmbedder(semantic_cache / "models", download=False))
        write(run / "embedding_backend.json", embeddings.metadata)
        pins[str(run / "embedding_backend.json")] = sha(run / "embedding_backend.json")
        profile_bindings = {}
        for method, (attempt, _) in tasks.items():
            write(attempt / "input_bindings.json", pins)
            profile_bindings[method] = sha(attempt / "input_bindings.json")
        verify(pins)
        state.update(status="annotating", active_methods=list(parents))
        write(run / "state.json", state)

        async def annotate(method):
            attempt, task = tasks[method]
            worker = CodexWorker(model="gpt-6.1-sol", effort="xhigh",
                timeout_seconds=args.timeout_seconds, web_search=False, network_access=False)
            return await worker.run(task["prompt"], attempt)

        native_tasks = [asyncio.create_task(annotate(method)) for method in parents]
        try:
            results = await asyncio.gather(*native_tasks)
        except BaseException:
            for native_task in native_tasks:
                native_task.cancel()
            await asyncio.gather(*native_tasks, return_exceptions=True)
            raise
        used_threads, manifest = set(), {}
        for (method, parent), result in zip(parents.items(), results):
            attempt, task = tasks[method]
            checked = assess_archived_attempt(attempt)
            if (not checked.complete or not checked.thread_id or result.exit_code != 0
                    or result.error is not None or result.thread_id != checked.thread_id
                    or result.usage != checked.usage or checked.thread_id in used_threads
                    or (attempt / "prompt.txt").read_text() != task["prompt"]
                    or sha(attempt / "input_bindings.json") != profile_bindings[method]):
                raise ValueError(f"Profile {method} lacks a fresh complete unchanged native archive")
            verify(pins)
            used_threads.add(checked.thread_id)
            profile_path = attempt / "artifact.json"
            profile = load_comparison_profile(profile_path, parent["paths"]["workflow"],
                                               parent["paths"]["packet"])
            if all(assertion.status == "not_assessed"
                   for _, _, assertion in iter_profile_assertions(profile)):
                raise ValueError("Profile returned only an unassessed scaffold, not an annotation")
            scaffold = ComparisonProfile.model_validate(task["template"])
            if any({row.target_id for row in getattr(profile, field)} !=
                   {row.target_id for row in getattr(scaffold, field)}
                   for field in ("states", "operations")):
                raise ValueError("Profile changed the exact selected target set")
            # Rebuild after annotation; do not trust any worker-authored graph.
            occurrence = cut_transformation(parent["workflow"], parent["packet"],
                [parent["operation_id"]],
                review=WorkflowReview.model_validate_json(parent["paths"]["review"].read_bytes()))
            if errors := validate_profile_selection(profile, occurrence):
                raise ValueError("; ".join(errors))
            occurrence_path = run / "occurrences" / f"{method}.json"
            write(occurrence_path, occurrence.model_dump(mode="json"))
            manifest[occurrence.id] = {
                **{field: str(path) for field, path in parent["paths"].items()},
                "occurrence": str(occurrence_path), "comparison_profile": str(profile_path)}
            state["profiles"][method] = profile_summary(profile, occurrence, profile_path)
            pins.update({str(path): sha(path) for path in
                (*attempt.iterdir(), occurrence_path) if path.is_file()})
            write(run / "bindings.json", pins)
            write(run / "state.json", state)
        manifest_path = run / "manifest.json"
        write(manifest_path, manifest)
        load_occurrences(manifest)
        pins[str(manifest_path)] = sha(manifest_path)
        write(run / "bindings.json", pins)
        verify(pins)
        registry_path = run / "registry.sqlite"
        Registry(registry_path).initialize()
        state.pop("active_methods", None)
        state.update(status="shared_source_review")
        write(run / "state.json", state)
        shared = await shared_execute(manifest_path, registry_path, run / "shared",
            worker=CodexWorker(model="gpt-6.1-sol", effort="xhigh",
                timeout_seconds=args.timeout_seconds, web_search=False, network_access=False),
            embeddings=embeddings, semantic_limit=args.semantic_limit,
            require_comparison_profiles=True, max_revisions=0, max_updates=1)
        verify(pins)
        load_occurrences(manifest)
        snapshot = Registry(registry_path).snapshot()
        write(run / "registry_snapshot.json", snapshot)
        state.update(status=shared["status"], shared_state=str(run / "shared/state.json"),
                     registry_snapshot_sha256=sha(run / "registry_snapshot.json"),
                     concept_count=len(snapshot["concepts"]), assignment_count=len(snapshot["assignments"]),
                     finished_at=now())
        summary = {**state, **archive_summary(run), "semantic_backend": embeddings.metadata,
                   "limits": {"profile_sessions": 2, "shared_execute_calls": 1,
                              "canonical_revisions": 0, "candidate_updates": 1}}
        write(run / "summary.json", summary)
        return summary
    except BaseException as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write(run / "failure.json", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        state["finished_at"] = now()
        write(run / "state.json", state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--run", type=Path, default=EXPERIMENT / "live")
    parser.add_argument("--semantic-cache", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=3600)
    parser.add_argument("--semantic-limit", type=int, default=10)
    args = parser.parse_args()
    if args.timeout_seconds <= 0 or args.semantic_limit < 1:
        parser.error("Use a positive timeout and semantic limit")
    print(json.dumps(asyncio.run(execute(args)), ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()

