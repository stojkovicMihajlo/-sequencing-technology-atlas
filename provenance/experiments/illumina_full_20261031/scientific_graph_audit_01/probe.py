"""Reproduce graph-incidence ambiguity from immutable, completed pilot artifacts.

This is an offline audit, not re-admission or a fresh scientific review. It never
changes the pilot extraction, archived review, source registration or graph.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

from genomics_discovery.curation.illumina_diagrams import (
    DiagramExtraction, DiagramReview, digest, review_outcome,
)
from genomics_discovery.curation.illumina_graph import walk


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[3]
PILOT = HERE.parent / "pilot_02" / "cards"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    inputs, controls, views = {}, {}, {}
    for method in ("clickseq", "g-t-seq"):
        directory = PILOT / method / "attempt_001"
        candidate_path, review_path = directory / "extraction.json", directory / "review.json"
        graph_path = directory / "graph.json"
        candidate = DiagramExtraction.model_validate_json(candidate_path.read_bytes())
        review = DiagramReview.model_validate_json(review_path.read_bytes())
        graph = json.loads(graph_path.read_text())
        inputs[method] = {
            filename: {"path": str(directory / filename), "sha256": sha(directory / filename)}
            for filename in ("extraction.json", "review.json", "graph.json")
        }
        inputs[method].update(candidate_sha256=digest(candidate), review_sha256=digest(review))
        expected = {(flow.source, flow.target, flow.kind) for flow in candidate.flows}
        actual = {(edge["original"]["source"], edge["original"]["target"], edge["kind"])
                  for edge in graph["edges"] if "original" in edge}
        controls[method] = {
            "pydantic_graph_validation": "passed",
            "saved_review_bound_to_exact_candidate": review.candidate_sha256 == digest(candidate),
            "archived_review_outcome_without_source_replay": review_outcome(review, candidate),
            "flow_projection_preserves_extracted_endpoints_and_types": expected == actual,
            "unscoped_operations": sorted({op.id for op in candidate.operations} -
                {op for scope in candidate.method_scopes for op in scope.operation_ids}),
            "multi_input_multi_output_operations": [op.id for op in candidate.operations
                if len(op.inputs) > 1 and len(op.outputs) > 1],
        }
        # Exact per-card projection, with only the aggregate query envelope added.
        # It does not execute build_graph or claim remote custody files were replayed.
        views[method] = {
            "cards": {method: {"source_registry": graph["source_registry"]}},
            "nodes": [{**node, "record_id": method, "admission": "supported"}
                      for node in graph["nodes"]],
            "edges": [{**edge, "record_id": method, "admission": "supported"}
                      for edge in graph["edges"]],
        }

    cases = []
    query_guard = "expand_ambiguous_incidence" in inspect.signature(walk).parameters
    for source, unrelated_output in (("s_transcriptome_product", "s_dna_reads"),
                                      ("s_genome_product", "s_rna_reads")):
        namespace = "illumina:g-t-seq:"
        start, finish = namespace + "state:" + source, namespace + "state:" + unrelated_output
        middle = namespace + "operation:o_sequence"
        route = walk(views["g-t-seq"], start, depth=2)
        incidence = walk(views["g-t-seq"], start, depth=2,
                         **({"expand_ambiguous_incidence": True} if query_guard else {}))
        ids = {node["id"] for node in route["nodes"]}
        incidence_ids = {node["id"] for node in incidence["nodes"]}
        edges = {(edge["source"], edge["target"]): edge["id"] for edge in incidence["flows"]}
        cases.append({
            "start_id": start, "unrelated_branch_output_id": finish,
            "default_walk_reaches_unrelated_output": finish in ids,
            "explicit_incidence_walk_reaches_unrelated_output": finish in incidence_ids,
            "default_routing_frontier_ids": route.get("routing_frontier_ids"),
            "default_routing_ambiguities": route.get("routing_ambiguities"),
            "path_node_ids": [start, middle, finish],
            "path_flow_ids": [edges[(start, middle)], edges[(middle, finish)]],
            "meaning": "An incidence path exists; source-molecule attribution is not established by this path.",
        })
        assert finish in incidence_ids
        if query_guard:
            assert finish not in ids
            assert middle in route["routing_frontier_ids"]
        else:
            assert finish in ids

    result = {
        "audit": "offline_completed_pilot_scientific_graph_probe",
        "model_calls": 0,
        "boundary": "Local archived JSON and reacquired byte-identical diagrams only; no remote custody/source-text replay or new reviewer was performed.",
        "input_artifacts": inputs,
        "query_implementation": {"path": "src/genomics_discovery/curation/illumina_graph.py",
            "sha256": sha(PROJECT / "src/genomics_discovery/curation/illumina_graph.py")},
        "controls": controls,
        "query_guard_present": query_guard,
        "cross_branch_incidence_paths": cases,
    }
    output = "probe_result_after_query_guard.json" if query_guard else "probe_result.json"
    (HERE / output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"controls": controls, "cross_branch_incidence_paths": cases}, indent=2))


if __name__ == "__main__":
    main()
