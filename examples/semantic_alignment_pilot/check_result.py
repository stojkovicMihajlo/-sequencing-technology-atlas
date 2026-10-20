"""Recheck distributed artifact pins; optionally test pure graph projection.

No model calls, downloads, source-path rewriting or canonical publication.
Run from the repository root with PYTHONPATH=src.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from genomics_discovery.curation.operation_registry.graph import attach_to_graph
from genomics_discovery.curation.operation_registry.store import graph_overlay
from genomics_discovery.curation.workflow_extraction.models import digest


def file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(base_graph=None):
    root = Path(__file__).resolve().parent / "native_result"
    manifest = json.loads((root / "distribution_manifest.json").read_text())
    for name, record in manifest["files"].items():
        path = root / name
        if file_sha(path) != record["sha256"] or path.stat().st_size != record["bytes"]:
            raise ValueError(f"Distributed artifact changed: {name}")
    snapshot = json.loads((root / "registry_snapshot.json").read_text())
    overlay = graph_overlay(snapshot)  # Also validates the archived registry contract.
    if overlay != json.loads((root / "concept_overlay.json").read_text()):
        raise ValueError("Distributed overlay does not reproduce from the registry")
    report = {
        "format_version": "alignment_result_structural_check/1.0",
        "distribution_manifest_sha256": file_sha(root / "distribution_manifest.json"),
        "distributed_files_verified": len(manifest["files"]),
        "registry_digest": digest(snapshot),
        "concept_count": len(snapshot["concepts"]),
        "assignment_count": len(snapshot["assignments"]),
        "overlay_digest": digest(overlay),
        "overlay_node_kinds": dict(Counter(n["kind"] for n in overlay["nodes"])),
        "overlay_edge_kinds": dict(Counter(e["kind"] for e in overlay["edges"])),
        "graph_projection": None,
        "boundary": (
            "Artifact identity, registry structure and pure projection only. Original remote "
            "paper/input files are not reopened; this is not a source-custody-validated "
            "canonical graph publication, scientific re-review or retrieval benchmark."
        ),
    }
    if base_graph is not None:
        pin = manifest["base_graph_for_structural_check"]
        if file_sha(base_graph) != pin["sha256"]:
            raise ValueError("Base graph bytes differ from the recorded development input")
        graph = json.loads(base_graph.read_text())
        without_revision = {k: v for k, v in graph.items() if k != "revision_id"}
        if graph["revision_id"] != pin["revision_id"] or digest(without_revision) != graph["revision_id"]:
            raise ValueError("Base graph internal revision does not verify")
        before = digest(graph)
        projected = attach_to_graph(graph, snapshot)
        if digest(graph) != before:
            raise ValueError("Projection mutated the input graph")
        if projected["workflows"] != graph["workflows"]:
            raise ValueError("Projection changed original workflow records")
        node_count, edge_count = len(graph["nodes"]), len(graph["edges"])
        if projected["nodes"][:node_count] != graph["nodes"] or projected["edges"][:edge_count] != graph["edges"]:
            raise ValueError("Projection changed original nodes or edges")
        ids = [n["id"] for n in projected["nodes"]]
        if len(set(ids)) != len(ids):
            raise ValueError("Projection produced duplicate node IDs")
        node_ids = set(ids)
        if any(e["source"] not in node_ids or e["target"] not in node_ids for e in projected["edges"]):
            raise ValueError("Projected edge endpoint is missing")
        new_edges = projected["edges"][edge_count:]
        if any(e["kind"] not in {"instance_of_concept", "selects_operation"} or e["layer"] != "concept" for e in new_edges):
            raise ValueError("Projection added an unintended edge type")
        report["graph_projection"] = {
            "base_graph_sha256": pin["sha256"],
            "base_graph_revision": graph["revision_id"],
            "projected_graph_revision": projected["revision_id"],
            "existing_workflows_preserved": len(graph["workflows"]),
            "existing_nodes_preserved": node_count,
            "existing_edges_preserved": edge_count,
            "added_node_kinds": dict(Counter(n["kind"] for n in projected["nodes"][node_count:])),
            "added_edge_kinds": dict(Counter(e["kind"] for e in new_edges)),
            "all_edge_endpoints_resolve": True,
            "canonical_publication_performed": False,
        }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-graph", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(check(args.base_graph), indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    print(result, end="")
