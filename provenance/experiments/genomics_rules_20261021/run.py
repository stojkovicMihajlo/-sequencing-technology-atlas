"""Fresh open selection and source-reviewed matching on preserved workflows.

Usage: python run.py CONFIG_JSON FRESH_OUTPUT_DIRECTORY
Required config paths: parents, base_graph (relative to CONFIG_JSON if needed).
This experiment reruns comparison, not protocol extraction or source recovery.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import shutil
import traceback

from genomics_discovery.curation.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.curation.operation_registry.graph import attach_to_graph
from genomics_discovery.curation.operation_registry.store import Registry
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.technology_graph.queries import load_graph
from genomics_discovery.curation.workflow_extraction.models import Workflow, digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from playground.workflow_shared_live import execute as shared
from playground.workflow_unit_selection import load_parents
from playground.workflow_unit_selection_live import execute as select_units


def native_trace_audit(run: Path) -> dict:
    stages, threads, usage = [], [], Counter()
    for events in sorted(run.rglob("events.jsonl")):
        checked = assess_archived_attempt(events.parent)
        if checked.thread_id:
            threads.append(checked.thread_id)
        if checked.complete:
            usage.update({key: value for key, value in checked.usage.items()
                          if type(value) in (int, float)})
        stages.append({
            "attempt": str(events.parent), "complete": checked.complete,
            "thread_id": checked.thread_id, "usage": checked.usage,
            "assessment": checked.error,
            "archives": {name: sha(events.with_name(name)) for name in (
                "prompt.txt", "events.jsonl", "result.json", "command.json",
                "started.json", "stderr.log", "final.txt")
                if events.with_name(name).is_file()},
        })
    return {
        "stages": stages, "stage_count": len(stages), "usage": dict(usage),
        "duplicate_thread_ids": sorted(key for key, count in Counter(threads).items()
                                       if count > 1),
        "boundary": "Archived process evidence; not proof of comprehension or scientific truth.",
    }


def compare_files(pins: dict[Path, str]) -> None:
    changed = [str(path) for path, expected in pins.items()
               if not path.is_file() or sha(path) != expected]
    if changed:
        raise ValueError("Frozen input or implementation changed: " + "; ".join(changed))


async def execute(config_path: Path, output: Path) -> dict:
    config_path, output = config_path.resolve(), output.resolve()
    config = json.loads(config_path.read_text())
    if config.get("model", "gpt-6.1-sol") != "gpt-6.1-sol" or config.get(
            "effort", "xhigh") != "xhigh":
        raise ValueError("This recorded experiment requires gpt-6.1-sol and xhigh")
    for field, default, minimum in (("max_units", 10, 1), ("max_revisions", 1, 0),
                                    ("max_updates", 2, 1)):
        value = config.setdefault(field, default)
        if type(value) is not int or value < minimum:
            raise ValueError(f"Invalid {field}")
    parents_path = (config_path.parent / config["parents"]).resolve()
    graph_path = (config_path.parent / config["base_graph"]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    state = {
        "format_version": "genomics_rules_comparison_rerun/1.0",
        "status": "preparing", "started_at": now(), "config": config,
        "model": "gpt-6.1-sol", "effort": "xhigh", "initial_concept_count": 0,
        "source_boundary": "Preserved workflow/source/review inputs; no new extraction, sources, expected answers or historical edges.",
        "retrieval_policy": "Empty registry; existing lexical candidate generation; no embedding backend supplied.",
        "scientific_boundary": "Development diagnostic, not expert gold evaluation or proof of compatibility, ancestry or novelty.",
    }
    write(output / "state.json", state)
    write(output / "config.json", config)
    pins = {}
    try:
        parents = load_parents(parents_path)
        if not parents:
            raise ValueError("No preserved parents supplied")
        graph = load_graph(graph_path)
        if "concept_registry" in graph:
            raise ValueError("Supply the unannotated base graph, not an earlier registry overlay")
        files = {config_path, parents_path, graph_path, Path(__file__).resolve()}
        from playground import workflow_shared_live, workflow_unit_selection, workflow_unit_selection_live
        files.update(Path(module.__file__).resolve() for module in (
            workflow_shared_live, workflow_unit_selection, workflow_unit_selection_live))
        import genomics_discovery
        files.update(Path(genomics_discovery.__file__).resolve().parent.rglob("*.py"))
        graph_versions = {(row["workflow_sha256"], row["packet_sha256"])
                          for row in graph["workflows"].values()}
        frozen_parents, declared_sources = {}, []
        for index, (ident, parent) in enumerate(parents.items()):
            files.update(value for value in parent.model_dump().values() if value is not None)
            packet = load_packet(parent.packet)
            workflow = Workflow.model_validate_json(parent.workflow.read_bytes())
            if (digest(workflow), digest(packet)) not in graph_versions:
                raise ValueError("Parent workflow/source version is absent from the base graph")
            files.update(Path(source["original_path"]) for source in packet["sources"])
            directory = output / "intake" / f"{index:03d}"
            directory.mkdir(parents=True)
            copied = {}
            for field, path in parent.model_dump().items():
                if path is not None:
                    target = directory / f"{field}.json"
                    shutil.copyfile(path, target)
                    if sha(path) != sha(target):
                        raise ValueError("Preserved parent copy differs from original")
                    copied[field] = target
                    files.add(target)
            frozen_parents[ident] = parent.model_copy(update=copied)
            declared_sources.append({"method_id": packet["method_id"],
                                     "packet": str(copied["packet"]),
                                     "packet_sha256": sha(copied["packet"])})
        files.update(Path(path) for path in graph["input_files"])
        embeddings = None
        if embedding := config.get("embedding"):
            paths = {key: (config_path.parent / embedding[key]).resolve()
                     for key in ("image", "environment", "models", "bridge")}
            if (sha(paths["image"]) != embedding["image_sha256"] or
                    sha(paths["bridge"]) != embedding["bridge_sha256"]):
                raise ValueError("Frozen embedding image or bridge changed")
            files.update((paths["image"], paths["bridge"]))
            spec = importlib.util.spec_from_file_location("atlas_experiment_embeddings", paths["bridge"])
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache
            backend = module.ContainerEmbedder(paths["image"], paths["environment"],
                                               Path(os.environ["ATLAS_REPOSITORY"]), paths["models"])
            embeddings = EmbeddingCache(output / "semantic_vectors.sqlite3", backend)
            vectors = embeddings.encode(["Experimental source-bound transformation retrieval readiness."])
            write(output / "embedding_setup.json", {"metadata": embeddings.metadata,
                  "namespace": embeddings.namespace, "dimensions": len(vectors[0]),
                  "native_codex_location": "host"})
            state["retrieval_policy"] = "Same byte-pinned container embedding bridge; fresh isolated cache."
        write(output / "parents.json", {key: row.model_dump(mode="json")
                                        for key, row in frozen_parents.items()})
        write(output / "declared_sources.json", {"cases": declared_sources})
        shutil.copyfile(graph_path, output / "base_graph.json")
        files.update(output / name for name in (
            "config.json", "parents.json", "base_graph.json", "declared_sources.json"))
        pins = {path: sha(path) for path in files}
        write(output / "input_code_bindings.json", {str(path): value for path, value in sorted(
            pins.items(), key=lambda item: str(item[0]))})
        state.update(parent_count=len(parents), parent_ids=sorted(parents),
                     base_graph_sha256=sha(graph_path), status="selecting")
        write(output / "state.json", state)
        worker = CodexWorker(model="gpt-6.1-sol", effort="xhigh", timeout_seconds=None,
                             web_search=False, network_access=False)
        chosen = await select_units(output / "parents.json", output / "selection", worker=worker,
                                    max_units=config["max_units"])
        state["selection"] = chosen
        compare_files(pins)
        if chosen["status"] != "completed":
            raise ValueError("Open selection did not complete: " + str(chosen.get("error")))
        database = output / "operation_registry.sqlite3"
        if database.exists():
            raise ValueError("The isolated registry was created before host initialization")
        registry = Registry(database)
        registry.initialize()
        if chosen["units"]:
            state["status"] = "reviewing"
            write(output / "state.json", state)
            curated = await shared(Path(chosen["manifest"]), database, output / "shared", worker=worker,
                                   max_revisions=config["max_revisions"],
                                   max_updates=config["max_updates"], embeddings=embeddings)
            state["shared"] = curated
        else:
            state["shared"] = {"status": "not_run_without_candidates", "resolutions": []}
        compare_files(pins)
        snapshot = registry.snapshot()
        write(output / "registry_snapshot.json", snapshot)
        projected = attach_to_graph(graph, snapshot)
        write(output / "graph_with_shared_operations.json", projected)
        load_graph(output / "graph_with_shared_operations.json")
        last = state["shared"].get("resolutions", [])
        resolution = last[-1] if last else {}
        outcomes = Counter(row["status"] for row in resolution.get("outcomes", []))
        admitted = [row for row in snapshot["assignments"].values()
                    if row.get("admission_decision_sha256")]
        state.update(
            selected_units=chosen["units"], admitted_assignments=len(admitted),
            admitted_concepts=len({row["concept_id"] for row in admitted}),
            outcome_counts=dict(outcomes), shared_terminal_status=state["shared"]["status"],
            graph_statistics=projected["statistics"],
            graph_sha256=sha(output / "graph_with_shared_operations.json"),
            registry_sha256=sha(database),
        )
        terminal = state["shared"]["status"]
        if terminal in {"committed", "completed_without_admission", "not_run_without_candidates"}:
            state["status"] = ("withheld" if not admitted else "completed"
                               if len(admitted) == chosen["units"] else "partial")
        else:
            state["status"] = "pending"
        state["selected_units_without_admission"] = chosen["units"] - len(admitted)
        state["coverage_boundary"] = "The cut cap is a sample; accepted memberships do not establish exhaustive operation discovery."
    except asyncio.CancelledError:
        state.update(status="cancelled", error="Driver cancelled")
        raise
    except Exception as error:
        state.update(status="failed", error=f"{type(error).__name__}: {error}")
        (output / "traceback.log").write_text(traceback.format_exc())
    finally:
        try:
            audit = native_trace_audit(output)
            write(output / "native_trace_audit.json", audit)
            if audit["duplicate_thread_ids"] or any(not stage["complete"] for stage in audit["stages"]):
                state.update(status="failed", trace_error="Incomplete or duplicate native thread archive")
        except Exception as error:
            state.update(status="failed", trace_error=f"{type(error).__name__}: {error}")
        state["finished_at"] = now()
        write(output / "state.json", state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(execute(args.config, args.output))
    print(json.dumps(result, indent=2))
    if result["status"] in {"failed", "cancelled", "pending"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
