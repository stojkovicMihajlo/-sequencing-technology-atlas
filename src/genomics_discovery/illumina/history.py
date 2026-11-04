"""Query documented contributions across explicitly pinned relationship overlays.

Two overlays share a workflow only when both record ID AND extraction byte hash
match. This is exact artifact reuse, not semantic method/version resolution.
External cited contributions remain scoped to their original evidence overlay.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from genomics_discovery.illumina.relationships import (
    HISTORICAL_RELATIONS, RELATIONS, load_reviewed_pair, relationship_neighbors,
)


def _read(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("History input changed: " + str(path))
    return json.loads(raw)


def _check_review(claim, review):
    if review["claim_id"] != claim["id"] or review["decision"] != "accept":
        raise ValueError("Historical context requires the matching accepted review")
    issues = review["unresolved_assessments"]
    if (sorted(x["issue_index"] for x in issues) != list(range(len(claim["unresolved"]))) or
            any(x["blocks_relation"] for x in issues)):
        raise ValueError("Unreviewed or blocking relationship qualification")


def build_history_index(manifest_path):
    manifest = json.loads(manifest_path.read_text())
    if manifest["format_version"] != "illumina_history_inputs/1":
        raise ValueError("Unknown history manifest")
    nodes, edges, loaded = {}, [], set()

    def workflow(record_id, extraction_sha, title):
        if len(extraction_sha) != 64:
            raise ValueError("Workflow binding requires its recorded extraction hash")
        key = "workflow:" + record_id + ":" + extraction_sha
        nodes.setdefault(key, {"id": key, "name": title, "record_id": record_id,
                               "extraction_sha256": extraction_sha, "kind": "bound_workflow"})
        return key

    for entry in manifest["overlays"]:
        path = (manifest_path.parent / entry["path"]).resolve()
        pin = entry["sha256"]
        if pin in loaded:
            raise ValueError("Duplicate relationship overlay")
        loaded.add(pin)
        graph = _read(path, pin)
        source = {"path": str(path), "sha256": pin, "format_version": graph["format_version"]}
        if graph["format_version"] == "illumina_method_pair/1":
            # Reuse the pair reader's direction, anchor, evidence and review checks.
            graph = load_reviewed_pair(path, pin)
            sides = {s: workflow(b["record_id"], b["extraction_sha256"], b["title"])
                     for s, b in graph["bindings"].items()}
            rows = relationship_neighbors(graph, graph["bindings"]["left"]["record_id"], layer="all")
            items = {x["claim"]["id"]: x for x in graph["historical_links"] + graph["correspondences"]}
            for row in rows["relationships"]:
                item = items[row["claim_id"]]; c = item["claim"]
                a, b = (sides["right"], sides["left"]) if c["direction"] == "right_to_left" else (sides["left"], sides["right"])
                edges.append({"id": pin + ":" + c["id"], "source": a, "target": b,
                              "relation": c["relation"], "claim": copy.deepcopy(c),
                              "review": copy.deepcopy(item["review"]), "artifact": source,
                              "claim_id": c["id"], "status": item["status"]})
        elif graph["format_version"] == "citation_lineage_pilot/2":
            original_nodes = {n["id"]: n for n in graph["nodes"]}
            if len(original_nodes) != len(graph["nodes"]):
                raise ValueError("Duplicate citation node")
            seed = original_nodes["seed"]; binding = seed["bindings"]
            pin_extraction = binding["parent_artifact_pins"]["extraction.json"]
            extraction = _read((manifest_path.parent / entry["seed_extraction"]).resolve(), pin_extraction)
            if extraction["record_id"] != binding["record_id"]:
                raise ValueError("Seed extraction identity mismatch")
            op_ids = {o["id"] for o in extraction["operations"]}
            target = workflow(binding["record_id"], pin_extraction, seed["name"])
            for item in graph["links"]:
                c, review, kind = item["claim"], item["review"], item["kind"]
                _check_review(c, review)
                if (kind not in RELATIONS or RELATIONS[kind]["layer"] == "excluded" or c["relation"] != kind
                        or item["target"] != "seed" or item["source"] == "seed"
                        or item["status"] != "model_reviewed_pilot_overlay"):
                    raise ValueError("Invalid published citation relation")
                if not c["seed_evidence"] or not c["predecessor_evidence"]:
                    raise ValueError("Missing two-sided citation support")
                if (any(e["source_id"] != "seed" for e in c["seed_evidence"]) or
                        any(e["source_id"] != c["reference_id"] for e in c["predecessor_evidence"])):
                    raise ValueError("Wrong citation-evidence side")
                if set(c["seed_operation_ids"]) - op_ids:
                    raise ValueError("Unknown seed Operation anchor")
                contributor = original_nodes[item["source"]]
                if contributor["reference_id"] != c["reference_id"]:
                    raise ValueError("Contributor/reference mismatch")
                origin = "external:" + pin + ":" + contributor["id"]
                nodes[origin] = {"id": origin, "name": contributor["name"], "kind": contributor["kind"],
                                 "reference_id": contributor["reference_id"],
                                 "identity_status": contributor["identity_status"],
                                 "catalogue_candidates": contributor["catalogue_candidates"]}
                edges.append({"id": pin + ":" + c["id"], "source": origin, "target": target,
                              "relation": kind, "claim": copy.deepcopy(c), "review": copy.deepcopy(review),
                              "artifact": source, "claim_id": c["id"], "status": item["status"]})
        else:
            raise ValueError("Unsupported relationship overlay format")
    if len({e["id"] for e in edges}) != len(edges):
        raise ValueError("Duplicate relationship claim")
    # Detect contradictions rather than silently looping or choosing an arbitrary parent.
    pending = {n: set() for n in nodes}
    for e in edges:
        if e["relation"] in HISTORICAL_RELATIONS:
            pending[e["target"]].add(e["source"])
    while pending:
        ready = {n for n, predecessors in pending.items() if not predecessors}
        if not ready:
            raise ValueError("Historical contribution cycle needs review")
        pending = {n: predecessors - ready for n, predecessors in pending.items() if n not in ready}
    return {"nodes": nodes, "edges": edges, "overlay_sha256": sorted(loaded),
            "boundary": "Explicitly loaded, previously model-reviewed pilot overlays; no new scientific validation, "
                        "date/priority verification, or canonical method identity resolution."}


def technology_history(index, record_id, *, extraction_sha256=None, max_depth=2):
    if not 1 <= max_depth <= 10:
        raise ValueError("Choose an explicit traversal depth between 1 and 10")
    candidates = [n for n in index["nodes"].values() if n.get("record_id") == record_id and
                  (extraction_sha256 is None or n.get("extraction_sha256") == extraction_sha256)]
    if len(candidates) != 1:
        raise ValueError("Expected one exact workflow; unknown record or multiple versions require an extraction hash")
    target = candidates[0]; visited = {target["id"]}; frontier = [(target["id"], 0)]
    selected, stops = {}, []
    while frontier:
        node, depth = frontier.pop(0)
        incoming = [e for e in index["edges"] if e["target"] == node and e["relation"] in HISTORICAL_RELATIONS]
        if depth == max_depth or not incoming:
            stops.append({"node": copy.deepcopy(index["nodes"][node]),
                          "reason": "depth_limit" if depth == max_depth and incoming else "no_further_history_in_loaded_evidence"})
            continue
        for edge in incoming:
            selected[edge["id"]] = copy.deepcopy(edge)
            if edge["source"] not in visited:
                visited.add(edge["source"]); frontier.append((edge["source"], depth + 1))
    return {
        "technology": copy.deepcopy(target), "max_depth": max_depth,
        "nodes": [copy.deepcopy(index["nodes"][n]) for n in sorted(visited)],
        "historical_contributions": list(selected.values()),
        "other_direct_relationships": [copy.deepcopy(e) for e in index["edges"]
            if target["id"] in {e["source"], e["target"]} and e["relation"] not in HISTORICAL_RELATIONS],
        "frontier": stops, "overlay_sha256": index["overlay_sha256"],
        "boundary": index["boundary"] + " Paths join distinct scoped claims; they do not establish transitive "
                    "inheritance of a component or whole assay. A traversal endpoint is not proof of first invention.",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--record-id", required=True)
    p.add_argument("--extraction-sha256")
    p.add_argument("--max-depth", type=int, default=2)
    args = p.parse_args()
    result = technology_history(build_history_index(args.manifest), args.record_id,
        extraction_sha256=args.extraction_sha256, max_depth=args.max_depth)
    print(json.dumps(result, ensure_ascii=False, indent=2))
