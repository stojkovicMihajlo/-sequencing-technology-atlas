"""Read-only graph tools with typed traversal and on-demand source access."""

import json
import re
from pathlib import Path

from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from genomics_discovery.curation.workflow_matching.models import ProfileBatch
from genomics_discovery.curation.workflow_matching.tools import search_profiles

from .assembly import FORMAT, node_id


def load_graph(path: Path) -> dict:
    graph = json.loads(path.read_text())
    if graph.get("format_version") != FORMAT:
        raise ValueError("Unsupported integrated technology graph")
    if graph.get("revision_id") != digest({k: v for k, v in graph.items() if k != "revision_id"}):
        raise ValueError("Graph content differs from its recorded revision")
    if not graph.get("input_files") or any(
        not Path(p).is_file() or sha(Path(p)) != expected
        for p, expected in graph["input_files"].items()
    ):
        raise ValueError("Graph input/source/review changed or is unavailable; rebuild explicitly")
    ids = {n["id"] for n in graph["nodes"]}
    if len(ids) != len(graph["nodes"]) or len({e["id"] for e in graph["edges"]}) != len(
        graph["edges"]
    ):
        raise ValueError("Graph IDs must be unique")
    if any(e["source"] not in ids or e["target"] not in ids for e in graph["edges"]):
        raise ValueError("Graph contains an unresolved endpoint")
    return graph


def inspect(graph: dict, identifier: str) -> dict:
    for name in ("comparisons", "historical_claims", "views", "workflows", "matching_reviews",
                 "concept_assignments"):
        if identifier in graph.get(name, {}):
            record = graph[name][identifier]
            details = [
                d
                for d in graph["development_details"]
                if identifier in {d["comparison_id"], d["historical_claim_id"]}
            ]
            return {"record_type": name, "record": record, "development_details": details}
    for name in ("historical_evidence", "change_events"):
        for record in graph[name]:
            if record["id"] == identifier:
                return {"record_type": name, "record": record}
    for node in graph["nodes"]:
        if node["id"] == identifier:
            return {"record_type": "node", "record": node}
    for edge in graph["edges"]:
        if edge["id"] == identifier:
            return {"record_type": "edge", "record": edge}
    raise KeyError(f"Unknown graph record: {identifier}")


def development(graph: dict, claim_id: str):
    claim = graph["historical_claims"][claim_id]
    if claim.get("status") != "supported" or claim.get("relationship_kind") not in {
        "method_development",
        "component_reuse",
    }:
        raise ValueError("Choose a stored supported development or component-reuse claim")
    accounts = []
    for detail in graph["development_details"]:
        if detail["historical_claim_id"] != claim_id:
            continue
        comparison = graph["comparisons"][detail["comparison_id"]]
        claims = comparison["alignment"]["claims"]
        selected = {}
        for side in ("left", "right"):
            view = graph["views"][comparison[side + "_view"]]
            workflow = graph["workflows"][view["workflow_id"]]
            selected[side] = {
                "method_id": view["method_id"],
                "workflow_id": view["workflow_id"],
                "variant": workflow["workflow"]["variant"],
                "operations": [
                    {"id": op["id"], "name": op["name"]} for op in view["cut"]["operations"]
                ],
            }
        accounts.append(
            {
                **detail,
                "selected_protocols": selected,
                "requested_question": comparison["case"]["question"],
                "comparison_disposition": comparison["alignment"]["disposition"],
                "shared_parts": [claims[i] for i in detail["shared_claim_indices"]],
                "different_parts": [claims[i] for i in detail["different_claim_indices"]],
                "unestablished_parts": [claims[i] for i in detail["unestablished_claim_indices"]],
                "review_sha256": comparison["review_sha256"],
            }
        )
    return {
        "historical_claim": claim,
        "protocol_comparisons": accounts,
        "boundary": "Historical attribution and local comparison support remain separate. "
        "Shared parts are observed overlap, not automatically proven inherited components. "
        "A difference between selected variants need not be the historically decisive change.",
    }


def neighbors(graph: dict, identifier: str, *, layer=None, direction="both", offset=0, limit=20):
    if direction not in {"in", "out", "both"}:
        raise ValueError("Choose in, out or both")
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        raise ValueError("Pagination requires nonnegative offset and positive limit")
    by_id = {n["id"]: n for n in graph["nodes"]}
    if identifier not in by_id:
        raise KeyError(f"Unknown graph node: {identifier}")
    if layer is not None and layer not in {
        "history",
        "atlas_relations",
        "membership",
        "protocol",
        "cross_protocol",
        "concept",
    }:
        raise ValueError("Unknown graph layer")
    matches = []
    for edge in graph["edges"]:
        if layer is not None and edge["layer"] != layer:
            continue
        outgoing = edge["source"] == identifier
        incoming = edge["target"] == identifier
        if not (
            (outgoing and (direction != "in" or not edge["directed"]))
            or (incoming and (direction != "out" or not edge["directed"]))
        ):
            continue
        other = edge["target"] if outgoing else edge["source"]
        matches.append(
            {"edge": edge, "neighbor": {k: v for k, v in by_id[other].items() if k != "record"}}
        )
    end = offset + limit
    return {
        "node": {k: v for k, v in by_id[identifier].items() if k != "record"},
        "results": matches[offset:end],
        "total": len(matches),
        "offset": offset,
        "next_offset": end if end < len(matches) else None,
        "boundary": "Each edge retains its own kind and support. Traversal does not "
        "compose comparisons into ancestry or biochemical compatibility.",
    }


def ancestors(graph: dict, method_id: str, *, depth=1):
    if type(depth) is not int or depth < 1:
        raise ValueError("Ancestor depth must be a positive integer")
    own = node_id("technology", method_id)
    if not any(n["id"] == own for n in graph["nodes"]):
        raise KeyError(f"Unknown technology: {method_id}")
    selected, visited, frontier = {}, {own}, {own}
    for _ in range(depth):
        next_frontier = set()
        for edge in graph["edges"]:
            if edge["kind"] == "method_development" and edge["target"] in frontier:
                selected[edge["id"]] = edge
                if edge["source"] not in visited:
                    next_frontier.add(edge["source"])
        visited.update(next_frontier)
        frontier = next_frontier
        if not frontier:
            break
    return {
        "method_id": method_id,
        "edges": list(selected.values()),
        "ancestors": sorted(visited - {own}),
        "requested_depth": depth,
        "boundary": "Only stored supported method_development claims. Component reuse, "
        "shared purpose and shared mechanism are excluded. This does not establish a "
        "direct component contribution from every reachable ancestor.",
    }


def discover(graph: dict, query: str, *, dimension=None, exclude_method=None, limit=10):
    records = list(graph["profiles"].values())
    if exclude_method is not None:
        if not any(
            n["kind"] == "technology" and n["method_id"] == exclude_method for n in graph["nodes"]
        ):
            raise KeyError(f"Unknown excluded technology: {exclude_method}")
        records = [
            r for r in records if graph["views"][r["view_id"]]["method_id"] != exclude_method
        ]
    batch = ProfileBatch.model_validate({"profiles": [r["profile"] for r in records]})
    result = search_profiles(batch, query, dimension=dimension, limit=limit)
    lookup = {r["profile"]["occurrence_id"]: r["view_id"] for r in records}
    for hit in result["hits"]:
        vid = lookup[hit["occurrence_id"]]
        hit.update(
            view_id=vid,
            workflow_id=graph["views"][vid]["workflow_id"],
            method_id=graph["views"][vid]["method_id"],
            comparison_leads=comparison_leads(graph, vid)["results"],
        )
    result["boundary"] = (
        "Candidate retrieval only. No scientific edge or inheritance follows "
        "from a hit or its score. Unknown facets are excluded; inferred assertions remain labelled."
    )
    return result


def find_operations(graph: dict, query: str, *, method_id=None, limit=5):
    """Retrieve actual transformations even before comparison profiles are authored.

    Lexical retrieval does not establish correspondence. Return the surrounding
    material states and resources so a consumer need not infer them from a label.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("Operation retrieval requires a positive limit")
    terms = set(re.findall(r"\w+", query.casefold()))
    if not terms:
        raise ValueError("Supply a nonempty operation query")
    if method_id is not None and not any(
        n["kind"] == "technology" and n["method_id"] == method_id for n in graph["nodes"]
    ):
        raise KeyError(f"Unknown technology: {method_id}")
    hits = []
    for wid, row in graph["workflows"].items():
        if method_id is not None and row["atlas_method_id"] != method_id:
            continue
        workflow = row["workflow"]
        states = {s["id"]: s for s in workflow["states"]}
        modules = {op: module["id"] for module in workflow["modules"]
                   for op in module["operation_ids"]}
        for op in workflow["operations"]:
            inputs = [states[s] for s in op["inputs"]]
            outputs = [states[s] for s in op["outputs"]]
            text = " ".join([
                op["name"], op["action"], op["information_effect"],
                *(s["description"] for s in [*inputs, *outputs]),
                *(r["description"] for r in op["resources"]),
            ])
            matched = terms & set(re.findall(r"\w+", text.casefold()))
            if not matched:
                continue
            version = wid.removeprefix("workflow:")
            hits.append({
                "node_id": node_id("operation", version, op["id"]),
                "workflow_id": wid, "method_id": row["atlas_method_id"],
                "variant": workflow["variant"], "review_status": row["review_status"],
                "module_id": modules[op["id"]], "operation": op,
                "inputs": inputs, "outputs": outputs,
                "matched_terms": sorted(matched), "score": len(matched) / len(terms),
            })
    hits.sort(key=lambda h: (-h["score"], h["method_id"], h["node_id"]))
    return {
        "query": query, "method_id": method_id, "total": len(hits),
        "hits": hits[:limit], "truncated": len(hits) > limit,
        "boundary": "Lexical retrieval over reviewed operation descriptions, states and "
                    "resources; not a similarity, feasibility, ancestry or novelty score. "
                    "Follow protocol neighbors for surrounding routes and inspect the workflow "
                    "for scope and unresolved details. No edge is created.",
    }


def comparison_leads(graph: dict, view_id: str):
    """Navigate from a retrieved seed to comparisons of overlapping operation groups.

    Set overlap is a mechanical lookup, not a scientific relationship between views.
    """
    query = graph["views"][view_id]
    requested = {op["id"] for op in query["cut"]["operations"]}
    results = []
    for cid, comparison in graph["comparisons"].items():
        for side in ("left_view", "right_view"):
            candidate = graph["views"][comparison[side]]
            selected = {op["id"] for op in candidate["cut"]["operations"]}
            overlap = requested & selected
            if candidate["workflow_id"] != query["workflow_id"] or not overlap:
                continue
            coverage = (
                "exact"
                if requested == selected
                else "query_subset"
                if requested < selected
                else "query_superset"
                if selected < requested
                else "partial_overlap"
            )
            results.append(
                {
                    "comparison_id": cid,
                    "aligned_view_id": comparison[side],
                    "overlap_kind": coverage,
                    "shared_operation_ids": sorted(overlap),
                    "relation": comparison["case"]["relation"],
                    "disposition": comparison["alignment"]["disposition"],
                }
            )
    return {
        "view_id": view_id,
        "results": results,
        "total": len(results),
        "boundary": "Navigation by shared operation IDs within one bound workflow. "
        "Read the original comparison question and selected groups; its conclusions "
        "are not automatically claims about the smaller/larger queried view.",
    }


def read_source(graph: dict, workflow_id: str, source_id: str, passage_id: str):
    record = graph["workflows"][workflow_id]
    packet = load_packet(Path(record["packet_path"]))
    if digest(packet) != record["packet_sha256"]:
        raise ValueError("Source packet differs from the assembled graph")
    sources = [s for s in packet["sources"] if s["source_id"] == source_id]
    if len(sources) != 1:
        raise KeyError("Unknown workflow-scoped source")
    passages = [p for p in sources[0]["passages"] if p["passage_id"] == passage_id]
    if len(passages) != 1:
        raise KeyError("Unknown source-scoped passage")
    return {
        "workflow_id": workflow_id,
        "source": {k: v for k, v in sources[0].items() if k != "passages"},
        "passage": passages[0],
        "boundary": "Registered original-backed source passage; text occurrence does "
        "not establish scientific entailment.",
    }


def comparison_claims(graph: dict, comparison_id: str, *, kind=None, dimension=None,
                      offset=0, limit=5):
    """Read selected claims/interfaces without dumping an entire comparison archive."""
    if kind is not None and kind not in {"correspondence", "difference", "not_established"}:
        raise ValueError("Unknown comparison claim kind")
    if dimension is not None and dimension not in {
        "states", "transformation", "information", "dependencies", "scope_limits"
    }:
        raise ValueError("Unknown comparison dimension")
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        raise ValueError("Use a nonnegative offset and a positive limit")
    comparison = graph["comparisons"][comparison_id]
    rows = [{"claim_index": i, **c}
            for i, c in enumerate(comparison["alignment"]["claims"])
            if (kind is None or c["kind"] == kind)
            and (dimension is None or c["dimension"] == dimension)]
    endpoints = {}
    for side in ("left", "right"):
        view = graph["views"][comparison[side + "_view"]]
        workflow = graph["workflows"][view["workflow_id"]]
        endpoints[side] = {"workflow_id": view["workflow_id"], "method_id": view["method_id"],
                           "variant": workflow["workflow"]["variant"],
                           "operation_ids": [o["id"] for o in view["cut"]["operations"]],
                           "packet_sha256": workflow["packet_sha256"]}
    return {
        "comparison_id": comparison_id, "question": comparison["case"]["question"],
        "disposition": comparison["alignment"]["disposition"],
        "review_sha256": comparison["review_sha256"], "endpoints": endpoints,
        "claims": rows[offset:offset + limit], "total": len(rows), "offset": offset,
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "boundary": "Exact scoped comparison claims; each retains its own inference tags "
                    "and source context. Use the endpoint workflow IDs with the source tool. "
                    "No claim or empty filter result establishes component interchangeability, "
                    "universal absence, experimental novelty or historical descent.",
    }
