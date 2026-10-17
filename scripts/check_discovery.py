"""Reproducible development diagnostic against existing reviewed comparisons.

This measures retrieval of recorded regions, not scientific precision or novelty.
No counterpart names, comparison questions or target claims enter query text.
"""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.technology_graph import discovery
from genomics_discovery.curation.technology_graph.candidates import operation_candidates
from genomics_discovery.curation.technology_graph.correspondences import lookup_correspondences
from genomics_discovery.curation.technology_graph.discovery import discover_operations
from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache, LocalEmbedder
from genomics_discovery.curation.technology_graph.queries import load_graph


def first_rank(candidates, predicate):
    return next((i for i, row in enumerate(candidates, 1) if predicate(row)), None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--questions", type=Path, help="Optional manually specified question probes")
    parser.add_argument("--only-questions", action="store_true")
    args = parser.parse_args()
    if args.only_questions and not args.questions:
        parser.error("--only-questions requires --questions")
    if args.output.exists():
        raise FileExistsError("Choose a new result artifact")
    code_dir = Path(discovery.__file__).parent
    implementation = {p.name: sha(p) for p in [Path(__file__), *sorted(code_dir.glob("*.py"))]}
    start = time.perf_counter()
    graph = load_graph(args.graph)
    backend = LocalEmbedder(args.cache / "models")
    cache = EmbeddingCache(args.cache / "vectors.sqlite3", backend)
    startup = time.perf_counter() - start
    records = []
    for cid, comparison in ([] if args.only_questions else graph["comparisons"].items()):
        for side, other in (("left", "right"), ("right", "left")):
            query, target = [graph["views"][comparison[s + "_view"]] for s in (side, other)]
            oids = [op["id"] for op in query["cut"]["operations"]]
            target_ids = {op["id"] for op in target["cut"]["operations"]}
            direct_ids = {t.removeprefix("operation:")
                          for c in comparison["alignment"]["claims"] if c["kind"] == "correspondence"
                          for t in c[other + "_targets"] if t.startswith("operation:")}
            for mode in ("baseline", "lexical", "hybrid"):
                start = time.perf_counter()
                if mode == "baseline":
                    result = operation_candidates(graph, query["workflow_id"], oids)
                else:
                    result = discover_operations(graph, workflow_id=query["workflow_id"],
                                                 operation_ids=oids,
                                                 embeddings=cache if mode == "hybrid" else None)
                elapsed = time.perf_counter() - start
                hits = result["candidates"]
                match = lambda row: row["workflow_id"] == target["workflow_id"]
                records.append({
                    "comparison_id": cid, "case_id": comparison["case"]["id"],
                    "disposition": comparison["alignment"]["disposition"],
                    "side": side, "mode": mode, "seconds": round(elapsed, 3),
                    "source_method": query["method_id"], "source_operation_ids": oids,
                    "target_method": target["method_id"], "target_operation_ids": sorted(target_ids),
                    "direct_target_ids": sorted(direct_ids),
                    "method_rank": first_rank(hits, match),
                    "region_rank": first_rank(hits, lambda r: match(r) and bool(set(r["operation_ids"]) & target_ids)),
                    "claim_target_rank": first_rank(hits, lambda r: match(r) and bool(set(r["operation_ids"]) & direct_ids)),
                    "cache": result.get("embedding_cache"),
                    "segmentations": result.get("segmented_embedding_texts", []),
                    "hits": [{key: h[key] for key in ("workflow_id", "method_id", "operation_ids", "score")}
                             for h in hits],
                })
                print(json.dumps({k: records[-1][k] for k in
                                  ("case_id", "side", "mode", "seconds", "region_rank", "claim_target_rank")}),
                      flush=True)
    summary = defaultdict(dict)
    for mode in ("baseline", "lexical", "hybrid"):
        positive = [r for r in records if r["mode"] == mode and r["disposition"] == "candidate_correspondence"]
        direct = [r for r in positive if r["direct_target_ids"]]
        summary[mode] = {
            "queries": len(positive), "method_hits_at_10": sum(r["method_rank"] is not None for r in positive),
            "region_hits_at_10": sum(r["region_rank"] is not None for r in positive),
            "queries_with_direct_operation_targets": len(direct),
            "direct_target_hits_at_10": sum(r["claim_target_rank"] is not None for r in direct),
            "total_query_seconds": round(sum(r["seconds"] for r in positive), 3),
        }
    questions = json.loads(args.questions.read_text()) if args.questions else []
    question_results = []
    for probe in questions:
        sources = [wid for wid, row in graph["workflows"].items()
                   if row["atlas_method_id"] == probe["source_method"]]
        if len(sources) != 1:
            raise ValueError("Question probe needs one unambiguous source workflow")
        for mode in ("source_context", "question_only", "question_and_context"):
            start = time.perf_counter()
            result = discover_operations(
                graph, workflow_id=sources[0] if mode != "question_only" else None,
                operation_ids=probe["operation_ids"] if mode != "question_only" else (),
                question=probe.get("question") if mode != "source_context" else None,
                roles=probe.get("roles", []) if mode != "source_context" else (), embeddings=cache,
            )
            question_results.append({"probe": probe, "mode": mode,
                                     "seconds": round(time.perf_counter() - start, 3),
                                     "request_id": result["request_id"],
                                     "cache": result["embedding_cache"],
                                     "configuration": result["configuration"],
                                     "segmentations": result["segmented_embedding_texts"],
                                     "hits": [{k: h[k] for k in ("workflow_id", "method_id", "operation_ids", "score")}
                                              for h in result["candidates"]]})
            print(json.dumps({"probe": probe["id"], "mode": mode,
                              "seconds": question_results[-1]["seconds"],
                              "hits": [(h["method_id"], h["operation_ids"]) for h in result["candidates"]]}), flush=True)
    # Mechanical scope example, preserving full claims in the tool output.
    first = next(iter(graph["comparisons"].values()))
    view = graph["views"][first["left_view"]]
    coverage = lookup_correspondences(graph, view["workflow_id"], [o["id"] for o in view["cut"]["operations"]])
    result = {
        "format_version": "operation_discovery_diagnostic/1.0",
        "implementation_sha256": implementation,
        "graph_revision_id": graph["revision_id"], "graph_statistics": graph["statistics"],
        "embedding_backend": backend.metadata, "startup_seconds": round(startup, 3),
        "limit": 10, "per_method": 2, "summary": dict(summary), "records": records,
        "question_results": question_results,
        "scope_example": {k: coverage[k] for k in ("workflow_id", "operation_ids")},
        "interpretation": "Paired development diagnostic on previously model-reviewed selected regions. "
        "Target labels are used only for scoring. Query contains the source selection's descriptions. "
        "Region overlap is easier than mechanism correspondence and neither is independent scientific "
        "validation. Direct-target IDs describe syntactic scope, not complete equivalence. Negative "
        "comparisons may be retrieved; retrieval is not acceptance. No edge is created.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
