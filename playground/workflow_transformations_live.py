"""Compile and freshly review common transformations from verified parent-bound cuts.

This small experiment driver reuses the native transport and scientific gates.
It writes a comparison overlay, never imports into the production atlas.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from pathlib import Path

from genomics_discovery.curation.codex_worker import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    CodexWorker,
    assess_archived_attempt,
)
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.prompts import source_command
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipCandidate,
    MembershipProposal,
    MembershipReview,
    build_membership_prompt,
    load_occurrences,
    review_membership,
    validate_membership,
)
from playground.workflow_unit_review_inputs import review_view


def comparison_graph(occurrences: dict, reviewed: list[dict]) -> dict:
    """Keep all contextual occurrences; add only direct reviewed collection links."""
    nodes, edges = {}, []
    for occurrence in occurrences.values():
        method = f"workflow:{occurrence.workflow_sha256}"
        parent = nodes.setdefault(method, {
            "id": method, "kind": "workflow", "method_id": occurrence.method_id,
            "review_bindings": []})
        binding = {"review_sha256": occurrence.workflow_review_sha256,
                   "status": occurrence.workflow_review_status}
        if binding not in parent["review_bindings"]:
            parent["review_bindings"].append(binding)
        nodes[occurrence.id] = {"id": occurrence.id, "kind": "transformation_occurrence",
                                "record": occurrence.model_dump(mode="json")}
        edges.append({"source": method, "target": occurrence.id, "kind": "contains_unit"})
    for parent in (node for node in nodes.values() if node["kind"] == "workflow"):
        parent["review_bindings"].sort(
            key=lambda item: (item["review_sha256"] or "", item["status"]))
        statuses = {item["status"] for item in parent["review_bindings"]}
        parent["review_status"] = (next(iter(statuses)) if len(statuses) == 1
                                   else "multiple_review_contexts")
    for record in reviewed:
        if record["status"] != "supported_membership_candidate":
            continue
        definition = f"transformation:{record['candidate_sha256']}"
        nodes[definition] = {"id": definition, "kind": "transformation_definition", **record}
        for member in record["members"]:
            edges.append({"source": member["occurrence_id"], "target": definition,
                          "kind": record["relation"], "review_sha256": record["review_sha256"]})
    return {"format_version": "transformation_comparison_graph/1.0",
            "nodes": sorted(nodes.values(), key=lambda node: node["id"]),
            "edges": sorted(edges, key=lambda edge: (
                edge["source"], edge["target"], edge["kind"], edge.get("review_sha256", ""))),
            "comparisons": reviewed,
            "boundary": "Model-reviewed direct comparison overlay; ancestry, substitution, "
                        "compatibility and production acceptance are not inferred."}


async def execute(manifest_path: Path, run: Path, *, worker=None,
                  initial_proposal: Path | None = None) -> dict:
    manifest_path, run = manifest_path.resolve(), run.resolve()
    if run.exists() and (not run.is_dir() or any(run.iterdir())):
        raise ValueError("Use a fresh comparison run directory")
    worker = worker or CodexWorker(web_search=False, network_access=False)
    if isinstance(worker, CodexWorker) and (worker.web_search or worker.network_access):
        raise ValueError("Comparison runs use frozen source packets")
    run.mkdir(parents=True, exist_ok=True)
    state = {"status": "preparing", "started_at": now(), "attempts": [], "comparisons": []}
    with (run / "state.json").open("x", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
    pins = {}

    def check():
        if any(sha(path) != expected for path, expected in pins.items()):
            raise ValueError("Frozen comparison input or implementation changed")
        load_occurrences(manifest)

    async def stage(role, model, candidate=None):
        check()
        attempt = run / f"{len(state['attempts']) + 1:03d}_{role}"
        attempt.mkdir()
        schema, artifact = attempt / "schema.json", attempt / "artifact.json"
        stage_manifest, schema_data = manifest, model.model_json_schema()
        local_candidate = None
        if candidate:
            member_ids = [member.occurrence_id for member in
                          MembershipCandidate.model_validate_json(candidate.read_bytes()).members]
            stage_manifest = {key: manifest[key] for key in member_ids}
            schema_data["properties"]["checks"].update(
                minItems=len(member_ids), maxItems=len(member_ids))
            schema_data["$defs"]["MemberCheck"]["properties"]["occurrence_id"]["enum"] = member_ids
            local_candidate = attempt / "candidate.json"
            local_candidate.write_bytes(candidate.read_bytes())
            pins[local_candidate] = sha(local_candidate)
        write(schema, schema_data)
        occurrence_inputs = {key: Path(row["occurrence"])
                             for key, row in stage_manifest.items()}
        if role == "reviewer":
            for index, key in enumerate(stage_manifest):
                path = attempt / f"unit_{index:03d}_review_view.json"
                write(path, review_view(occurrences[key]))
                pins[path] = sha(path)
                occurrence_inputs[key] = path
        prompt = build_membership_prompt(
            role, occurrences=occurrence_inputs,
            packets={key: Path(row["packet"]) for key, row in stage_manifest.items()},
            schema=schema, artifact=artifact, candidate=local_candidate,
            manifest=run / "manifest.json")
        helper = source_command(manifest_path)
        prompt += f"\nSource helper prefix: {helper}\nUse catalog, read-batch and read with "
        prompt += "the corresponding packet paths; never dump whole papers repeatedly.\n"
        if role == "reviewer":
            prompt += ("Occurrence inputs are transformation_review_view/1.0 envelopes. "
                       "Read record for all scientific claims, resources, flows and unknowns. "
                       "occurrence_sha256 binds the complete host-retained cut, not the view. "
                       "Previous per-target verdicts/rationales and parent review disposition "
                       "are withheld. Do not recover them from original occurrences, parent "
                       "records or other artifacts. The host source/binding check replays "
                       "originals without making their judgments your scientific inputs.\n")
        write(attempt / "instruction.json", {"prompt": prompt})
        for path in (schema, attempt / "instruction.json"):
            pins[path] = sha(path)
        record = {"role": role, "path": str(attempt), "status": "running",
                  "started_at": now()}
        state["attempts"].append(record)
        write(run / "state.json", state)
        started, result = time.monotonic(), None
        try:
            result = await worker.run(prompt, attempt)
            assessment = assess_archived_attempt(attempt)
            record["transport_complete"] = assessment.complete
            if (not assessment.complete or not assessment.thread_id
                    or type(result.exit_code) is not int or result.exit_code != 0
                    or result.error is not None or result.thread_id != assessment.thread_id
                    or result.usage != assessment.usage
                    or any(a is not record and a.get("thread_id") == assessment.thread_id
                           for a in state["attempts"])):
                raise ValueError("Native comparison requires a completed fresh verified thread")
            if (attempt / "prompt.txt").read_text() != prompt:
                raise ValueError("Native comparison prompt differs from its instruction record")
            check()
            document = model.model_validate_json(artifact.read_bytes())
            if local_candidate is not None:
                review_membership(MembershipCandidate.model_validate_json(local_candidate.read_bytes()),
                                  document, occurrences, packets)
            pins[artifact] = sha(artifact)
            record.update(status="validated", artifact_sha256=pins[artifact])
            return document, artifact
        except asyncio.CancelledError:
            record["status"] = "cancelled"
            raise
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            metadata = {}
            try:
                value = json.loads((attempt / "result.json").read_text())
                if isinstance(value, dict):
                    metadata = value
            except (OSError, ValueError, UnicodeDecodeError):
                pass
            elapsed = metadata.get("elapsed_seconds", getattr(result, "elapsed_seconds", None))
            if (not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
                    or not math.isfinite(elapsed) or elapsed < 0):
                elapsed = time.monotonic() - started
            usage = metadata.get("usage", getattr(result, "usage", {}))
            record.update(elapsed_seconds=elapsed, usage=usage if isinstance(usage, dict) else {},
                          thread_id=metadata.get("thread_id", getattr(result, "thread_id", None)),
                          exit_code=metadata.get("exit_code", getattr(result, "exit_code", None)),
                          transport_error=metadata.get("error", getattr(result, "error", None)),
                          finished_at=now())
            archives = {name: sha(attempt / name) for name in (
                "prompt.txt", "events.jsonl", "result.json", "command.json", "started.json",
                "stderr.log", "final.txt") if (attempt / name).is_file()}
            pins.update({attempt / name: value for name, value in archives.items()})
            record["archive_sha256"] = archives
            write(run / "state.json", state)

    try:
        manifest = json.loads(manifest_path.read_text())
        for row in manifest.values():
            for key, value in row.items():
                if key != "review" or value is not None:
                    row[key] = str((manifest_path.parent / value).resolve())
        occurrences, packets = load_occurrences(manifest)
        write(run / "manifest.json", manifest)
        files = {Path(value) for row in manifest.values() for value in row.values()
                 if value is not None}
        files |= {Path(source["original_path"]) for packet in packets.values()
                  for source in packet["sources"]}
        files |= {Path(__file__), Path(__file__).with_name("workflow_unit_review_inputs.py"),
                  manifest_path, run / "manifest.json"}
        package = Path(__file__).resolve().parents[1] / "src/genomics_discovery/curation"
        files |= set((package / "workflow_extraction").glob("*.py"))
        files |= {package / "codex_worker.py", package / "run_state.py",
                  package / "document_manifest.py", package / "evidence_views.py",
                  package.parent / "core/artifacts.py"}
        pins.update({path: sha(path) for path in files})
        seed = None
        if initial_proposal is not None:
            initial_proposal = Path(initial_proposal).resolve()
            pins[initial_proposal] = sha(initial_proposal)
            snapshot = run / "initial_proposal.json"
            snapshot.write_bytes(initial_proposal.read_bytes())
            pins[snapshot] = sha(snapshot)
            if pins[snapshot] != pins[initial_proposal]:
                raise ValueError("Initial proposal changed while being copied")
            seed = {"path": str(initial_proposal), "sha256": pins[initial_proposal],
                    "snapshot": str(snapshot), "snapshot_sha256": pins[snapshot],
                    "role": "unaccepted_proposal", "prior_transport_verified": False,
                    "prior_review_inherited": False}
        write(run / "config.json", {"model": getattr(worker, "model", DEFAULT_MODEL),
                                    "effort": getattr(worker, "effort", DEFAULT_EFFORT),
                                    "initial_proposal": seed,
                                    "files": {str(path): value for path, value in pins.items()}})
        pins[run / "config.json"] = sha(run / "config.json")
        state["status"] = "running"
        write(run / "state.json", state)
        if seed is None:
            proposal, _ = await stage("compiler", MembershipProposal)
        else:
            check()
            proposal = MembershipProposal.model_validate_json(snapshot.read_bytes())
        for candidate in proposal.candidates:
            errors = validate_membership(candidate, occurrences, packets)
            if errors:
                raise ValueError("; ".join(errors))
        for index, candidate in enumerate(proposal.candidates):
            path = run / f"candidate_{index:03d}.json"
            write(path, candidate.model_dump(mode="json"))
            pins[path] = sha(path)
            review, _ = await stage("reviewer", MembershipReview, candidate=path)
            state["comparisons"].append(review_membership(candidate, review, occurrences, packets))
        check()
        graph = comparison_graph(occurrences, state["comparisons"])
        write(run / "graph_candidate.json", graph)
        state.update(status="completed", finished_at=now(),
                     graph_sha256=sha(run / "graph_candidate.json"), notes=proposal.notes)
    except asyncio.CancelledError:
        state.update(status="cancelled", finished_at=now())
        write(run / "state.json", state)
        raise
    except Exception as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}", finished_at=now())
    write(run / "state.json", state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--proposal", type=Path, help="Unaccepted proposal; run only fresh reviews")
    args = parser.parse_args()
    worker = CodexWorker(model=args.model, effort=args.effort, timeout_seconds=args.timeout,
                         web_search=False, network_access=False)
    state = asyncio.run(execute(args.manifest, args.run, worker=worker,
                               initial_proposal=args.proposal))
    print(json.dumps(state, indent=2, ensure_ascii=False))
    raise SystemExit(0 if state["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
