"""Reopen completed source graphs under new gates without changing old results.

Run within an existing Sherlock Slurm allocation. This publishes source-depiction
graphs, not a deduplicated method catalogue or new scientific relationships.
"""
import argparse
from collections import Counter
import os
from pathlib import Path

from genomics_discovery.illumina.extraction import read, sha, write
from genomics_discovery.illumina.graph import build_graph, search_operations, workflow


def main(run_root, output):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Graph revalidation belongs in a Slurm allocation")
    output.mkdir(parents=True, exist_ok=False)
    counts = Counter()
    records = {}
    publications = []
    for name in ("initial_two", "catalogue", "posters", "footer_panels"):
        batch = run_root / "extractions_03" / name
        state = read(batch / "state.json")
        inventory = run_root / "sources_03" / ("catalogue" if name == "initial_two" else name) / "inventory.json"
        graph = build_graph(batch, inventory)
        path = output / (name + "_atlas.json")
        write(path, graph)
        # An inventory also contains cards intentionally assigned to another batch.
        # Count the checkpoint selection once, never its not_selected placeholders.
        for ident in state["cards"]:
            if ident in records:
                raise ValueError("Duplicate selected source occurrence: " + ident)
            entry = graph["cards"][ident]
            records[ident] = {"record_id": ident, "batch": name,
                "baseline_status": state["cards"][ident]["status"],
                "current_status": entry["status"], "admission": entry.get("admission"),
                "reason": entry.get("reason"), "graph": str(path)}
            counts[entry["status"]] += 1
        publications.append({"batch": name, "graph": str(path), "sha256": sha(path),
                             "input_pins": graph["input_pins"]})
        if name == "initial_two":
            drop = workflow(graph, "drop-seq")
            if not drop["nodes"] or drop["admission"] != "supported":
                raise ValueError("Supported Drop-seq source graph is not retrievable")
            if not search_operations(graph, input_text="RNA"):
                raise ValueError("Supported source Operations are not searchable")
        for ident in state["cards"]:
            if graph["cards"][ident]["status"] != "integrity_error":
                continue
            # Failed evidence admission must not leak Operations through retrieval.
            if graph["cards"][ident]["operation_ids"]:
                raise ValueError("Rejected source has published Operations")
            if workflow(graph, ident)["nodes"]:
                raise ValueError("Rejected source leaks through workflow retrieval")
    result = {"format_version": "illumina_graph_revalidation/1.0",
        "slurm_job_id": os.environ["SLURM_JOB_ID"],
        "runtime": os.environ["ATLAS_REPOSITORY"],
        "experiment_path": str(Path(__file__).resolve()), "experiment_sha256": sha(__file__),
        "baseline_unchanged": True, "selected_source_occurrences": len(records),
        "counts": dict(counts), "publications": publications,
        "new_admission_failures": [r for r in records.values()
            if r["baseline_status"] == "supported" and r["admission"] != "supported"],
        "boundary": "Revalidated source occurrences only; no canonical method identity, ancestry, new model review or experimental validation."}
    write(output / "publication.json", result)
    print({k: v for k, v in result.items() if k not in ("publications", "new_admission_failures")})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.run_root, args.output)
