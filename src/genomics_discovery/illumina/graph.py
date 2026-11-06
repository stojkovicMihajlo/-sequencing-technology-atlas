"""Assemble and query reviewed Illumina architectures without inventing relations.

Retrieval yields unreviewed comparison candidates. Names never merge operation
instances, and no historical/equivalence edge is inferred by this module.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from genomics_discovery.execution.codex_worker import assess_archived_attempt
from genomics_discovery.illumina.extraction import (
    card_references,
    card_title,
    paper_coverage,
    read,
    requires_paper_matches,
    sha,
    verify_registry_files,
    verify_stage_inputs,
    write,
)
from genomics_discovery.illumina.models import (
    DiagramExtraction,
    DiagramReview,
    parse_extraction,
    parse_review,
    property_target,
    PaperMatch,
    digest,
    export_graph,
    is_catalogue_annotation,
    review_outcome,
    validate_extraction,
)

FLOW_KINDS = frozenset({"material_input", "material_output", "data_input", "data_output", "precedes"})
INCIDENCE_SEMANTICS = (
    "Declared State/Operation incidence and dependency links only. A path does not establish "
    "molecular lineage or pair a particular input with a particular output."
)


def _source_scope(card):
    """Retain source-depiction identity without assigning canonical assay identity."""
    fields = ("scope_kind", "source_row_id", "source_method_labels", "verified_method_names",
              "canonical_technology_id", "source_category", "source_category_path")
    scope = {key: copy.deepcopy(card[key]) for key in fields if key in card}
    scope.setdefault("scope_kind", "source_card_kind_not_recorded")
    poster = card.get("poster_scope", {})
    context_fields = ("grouped_source_row", "literal_label_fragments", "line_wrap_reconstruction", "context_scope_warning")
    if poster:
        scope["poster_context"] = {key: copy.deepcopy(poster[key]) for key in context_fields if key in poster}
    return scope


def _load_card(row, card):
    """Reopen source, native-stage and scientific-admission gates for one workflow."""
    directory = Path(row["attempt_directory"])
    required = {"card.json", "source_registry.json", "extraction.json", "review.json",
                "graph.json", "stages.json"}
    pins = row.get("artifact_pins", {})
    if not required.issubset(pins) or any(Path(p).name != p for p in pins):
        raise ValueError("Missing or unsafe completed-artifact pins")
    if any(sha(directory / name) != expected for name, expected in pins.items()):
        raise ValueError("Completed artifact bytes changed")
    if read(directory / "card.json") != card:
        raise ValueError("Attempt card does not match the pinned inventory")
    sources = read(directory / "source_registry.json")
    candidate = parse_extraction((directory / "extraction.json").read_bytes())
    review = parse_review((directory / "review.json").read_bytes())
    mode = card.get('source_mode', 'diagram')
    errors = validate_extraction(candidate, sources, expected_record_id=card["record_id"],
        expected_title=card_title(card),
        expected_method_names=tuple(card["verified_method_names"]) if card.get("verified_method_names") else None,
        require_paper_matches=requires_paper_matches(card), source_mode=mode)
    errors += paper_coverage(candidate, card)
    if errors:
        raise ValueError("; ".join(errors))
    outcome = review_outcome(review, candidate, sources, source_mode=mode)
    if row["status"] != outcome or row.get("candidate_sha256") != digest(candidate) or row.get(
            "review_sha256") != digest(review):
        raise ValueError("Batch status/digests disagree with reopened current review gates")
    stages, threads = read(directory / "stages.json"), set()
    if len(stages) < 2 or stages[-2]["role"] != "author" or stages[-1]["role"] != "reviewer":
        raise ValueError("Missing author/fresh-review stage sequence")
    for stage in stages:
        attempt = Path(stage["path"])
        assessment = assess_archived_attempt(attempt)
        if (stage.get("status") != "validated" or not assessment.complete or not assessment.thread_id
                or assessment.thread_id in threads or stage.get("thread_id") != assessment.thread_id):
            raise ValueError("Missing completed independent native transport")
        threads.add(assessment.thread_id)
        if (sha(attempt / "stage_prompt.txt") != stage["prompt_sha256"] or
                (attempt / "stage_prompt.txt").read_bytes() != (attempt / "prompt.txt").read_bytes() or
                sha(attempt / "artifact.json") != stage["artifact_sha256"]):
            raise ValueError("Native prompt or stage output changed")
        delivery = read(attempt / "image_delivery.json")
        if (not delivery and mode == 'diagram') or any(sha(x["original"]) != x["original_sha256"] or
                               sha(x["attachment"]) != x["attachment_sha256"] for x in delivery):
            raise ValueError("Missing or changed original/delivered image")
        if "input_pins" in stage:
            verify_stage_inputs(attempt, delivery, stage["input_pins"])
    authored = parse_extraction((Path(stages[-2]["path"]) / "artifact.json").read_bytes())
    reviewed = parse_review((Path(stages[-1]["path"]) / "artifact.json").read_bytes())
    if digest(authored) != digest(candidate) or digest(reviewed) != digest(review):
        raise ValueError("Final extraction/review are not the last archived native artifacts")
    verify_registry_files(sources)
    if any(sha(directory / name) != expected for name, expected in pins.items()):
        raise ValueError("Completed artifacts changed while being reopened")
    return export_graph(candidate, review, sources, source_mode=mode), sources


def load_reviewed_workflow(row, card):
    """Public gate; preserve the private entry used by older frozen tools."""
    return _load_card(row, card)


def build_graph(batch_directory: Path, inventory_path: Path) -> dict:
    """Retain every inventory card; publish records only after reopening their gates."""
    batch_directory, inventory_path = Path(batch_directory), Path(inventory_path)
    state_path = batch_directory / "state.json"
    pins = {"inventory": {"path": str(inventory_path), "sha256": sha(inventory_path)},
            "batch_state": {"path": str(state_path), "sha256": sha(state_path)}}
    state, inventory = read(state_path), read(inventory_path)
    if pins["inventory"]["sha256"] != state["policy"]["inventory_sha256"]:
        raise ValueError("Inventory differs from the batch policy")
    identities = [card["record_id"] for card in inventory["cards"]]
    if len(identities) != len(set(identities)) or set(state["cards"]) - set(identities):
        raise ValueError("Duplicate inventory identity or batch card absent from inventory")
    if set(state["policy"]["record_ids"]) != set(state["cards"]):
        raise ValueError("Batch checkpoint omitted a selected card")
    cards, nodes, edges, papers = {}, [], [], {}
    for card in inventory["cards"]:
        ident = card["record_id"]
        row = state["cards"].get(ident, {"status": "not_selected"})
        entry = {"record_id": ident, "title": card_title(card), "status": row["status"],
                 "reason": row.get("reason"), "workflow_id": None, "operation_ids": [],
                 "publication_ids": [], "source_reference_count": len(card_references(card)),
                 "source_page_url": card.get("source_page_url"), "source_scope": _source_scope(card),
                 "source_provenance": {"inventory": copy.deepcopy(pins["inventory"]),
                     **{key: copy.deepcopy(card[key]) for key in ("source_id", "source_url", "source_page_url",
                         "source_title", "recovered_title", "diagram_asset_ids") if key in card}}}
        cards[ident] = entry
        if row["status"] not in {"supported", "needs_revision", "research_needed"}:
            continue
        try:
            graph, sources = load_reviewed_workflow(row, card)
        except Exception as exc:
            entry.update(status="integrity_error", reported_status=row["status"], reason=str(exc))
            continue
        admission = "supported" if row["status"] == "supported" else "diagnostic"
        namespace = "illumina:" + quote(ident, safe="")
        entry.update(workflow_id=namespace, admission=admission,
                     artifact_pins=row["artifact_pins"], attempt_directory=row["attempt_directory"],
                     candidate_sha256=graph["candidate_sha256"], review_sha256=graph["review_sha256"],
                     source_registry=graph["source_registry"], unknowns=graph["workflow"]["unknowns"],
                     scope=graph["workflow"]["scope"])
        entry["review_format_version"] = graph["review"]["format_version"]
        entry["label_checks"] = {c["target"]: copy.deepcopy(c) for c in graph["review"]["checks"]
                                 if c["target"].startswith(("state_label:", "operation_label:"))}
        if graph["workflow"]["format_version"] == "illumina_diagram/1.1":
            entry["representation_version"] = graph["workflow"]["format_version"]
            entry["semantic_checks"] = {c["target"]: copy.deepcopy(c) for c in graph["review"]["checks"]
                if c["target"] == "construction" or c["target"].startswith(
                    ("state_property:", "state_properties:", "operation_semantics:"))}
        for node in graph["nodes"]:
            nodes.append({**node, "record_id": ident, "admission": admission})
            if node["node_type"] == "operation":
                entry["operation_ids"].append(node["id"])
        edges.extend({**edge, "record_id": ident, "admission": admission} for edge in graph["edges"])
        for scope in graph["workflow"]["method_scopes"]:
            method_id = namespace + ":method:" + quote(scope["method"], safe="")
            nodes.append({"id": method_id, "node_type": "method_scope", "record_id": ident,
                          "admission": admission, "original": scope})
            edges.append({"source": namespace, "target": method_id, "kind": "describes_method",
                          "record_id": ident, "admission": admission})
            edges.extend({"source": namespace + ":operation:" + quote(op, safe=""),
                          "target": method_id, "kind": "applies_to_method" if scope["status"] == "resolved"
                          else "unresolved_applicability", "record_id": ident, "admission": admission,
                          "evidence": scope["evidence"]} for op in scope["operation_ids"])
        for match in graph["workflow"]["paper_matches"]:
            # A catalogue-only citation remains an evidenced PaperMatch annotation.
            # It does not identify a primary publication or merge all mentions on
            # the same catalogue page into one supposed paper.
            if is_catalogue_annotation(PaperMatch.model_validate(match), sources):
                continue
            source = sources[match["source_id"]]
            pmid = source.get("pmid")
            metadata = sources.get("metadata:" + str(pmid), source)
            verified = bool(pmid and metadata.get("identity_status") == "pubmed_record_retrieved")
            paper_id = ("publication:pmid:" + str(pmid) if verified else
                        namespace + ":publication:" + quote(match["source_id"], safe=""))
            paper = papers.setdefault(paper_id, {"id": paper_id, "node_type": "publication",
                "pmid": pmid, "title": source.get("title"), "url": source.get("url"),
                "identity_verified": verified, "record_ids": [], "has_supported_link": False})
            if ident not in paper["record_ids"]:
                paper["record_ids"].append(ident)
            paper["has_supported_link"] |= admission == "supported"
            entry["publication_ids"].append(paper_id)
            edges.append({"source": namespace + ":paper_match:" + quote(match["id"], safe=""),
                "target": paper_id, "kind": "references_publication", "record_id": ident,
                "admission": admission, "assessment": match["assessment"],
                "relationship": match["relationship"], "evidence": match["evidence"]})
    nodes.extend(papers.values())
    if any(sha(value["path"]) != value["sha256"] for value in pins.values()):
        raise ValueError("Batch state or inventory changed during assembly; retry from a fresh checkpoint")
    return {"format_version": "illumina_architecture_atlas/1.0", "cards": cards,
            "nodes": nodes, "edges": edges, "counts": dict(Counter(x["status"] for x in cards.values())),
            "input_pins": pins,
            "relationship_boundary": "Within-workflow/source links only; no automatic ancestry, equivalence or interchangeability."}


def _record_context(graph, node, incident):
    """Expose exact-target caveats without reading or repeating paper bodies."""
    card = graph["cards"][node["record_id"]]
    targets = {node["node_type"] + ":" + node["original"]["id"]}
    targets.update("flow:" + e["original"]["id"] for e in incident
                   if e["kind"] in FLOW_KINDS and e.get("original", {}).get("id"))
    corrections = [{"node_id": n["id"], **copy.deepcopy(n["original"])} for n in graph["nodes"]
        if n.get("record_id") == node["record_id"] and n["node_type"] == "correction" and
        n["original"]["target_id"] in targets]
    unknowns = card.get("unknowns", [])
    affected = [copy.deepcopy(u) for u in unknowns if set(u.get("affected_ids", [])) & targets]
    general = [copy.deepcopy(u) for u in unknowns if not (set(u.get("affected_ids", [])) & targets) and
               (not u.get("affected_ids") or "record" in u["affected_ids"])]
    registry_pin = card.get("artifact_pins", {}).get("source_registry.json")
    registry_artifact = ({"path": str(Path(card["attempt_directory"]) / "source_registry.json"),
                         "sha256": registry_pin, "status": "recorded_custody_pointer_verify_before_read"}
                        if registry_pin and card.get("attempt_directory") else None)
    label_target = node["node_type"] + "_label:" + node["original"]["id"]
    label_check = copy.deepcopy(card.get("label_checks", {}).get(label_target))
    return {"source_scope": copy.deepcopy(card.get("source_scope", {})),
            "source_provenance": copy.deepcopy(card.get("source_provenance", {})),
            "context_target_ids": sorted(targets), "affected_corrections": corrections,
            "label_review": {"target": label_target, "check": label_check,
                             "status": "separately_reviewed" if label_check else "not_separately_reviewed",
                             "review_format_version": card.get("review_format_version")},
            "affected_unknowns": affected, "record_unknowns": general,
            "context_matching": "Exact record target and incident Flow target IDs; whole-record unknowns are separate. No inferred equivalence or new scientific edges.",
            "full_source_registry_artifact": registry_artifact}


def get_record(graph: dict, kind: str, ident: str, *, include_diagnostic=False) -> dict:
    if kind == "card":
        return graph["cards"][ident]
    node = next(x for x in graph["nodes"] if x["id"] == ident and
                x["node_type"] == {"state": "state", "operation": "operation", "paper": "publication"}[kind])
    if not include_diagnostic and (node.get("admission") == "diagnostic" or
            node["node_type"] == "publication" and not node["has_supported_link"]):
        raise ValueError("Diagnostic record requires include_diagnostic=True")
    result = dict(node)
    if kind in {"operation", "state"}:
        registry = {x["id"]: x for x in graph["nodes"]}
        incident = [e for e in graph["edges"] if ident in (e["source"], e["target"]) and (
            include_diagnostic or e.get("admission") == "supported")]
        result.update(flows=[e for e in incident if e["kind"] in FLOW_KINDS], links=incident,
            provenance=graph["cards"][node["record_id"]]["source_registry"])
        result.update(_record_context(graph, node, incident))
        if "properties" in node["original"]:
            from .representation import property_view
            result["property_view"] = property_view(node["original"])
        checks = graph["cards"][node["record_id"]].get("semantic_checks", {})
        if checks:
            local = node["original"]["id"]
            targets = {"construction", "state_properties:" + local} if kind == "state" else {"construction", "operation_semantics:" + local}
            if kind == "state":
                targets.update(property_target(local, p["id"]) for p in node["original"].get("properties", []))
            result["semantic_review"] = {k: copy.deepcopy(v) for k, v in checks.items() if k in targets}
        if kind == "operation":
            result.update(inputs=[registry[e["source"]] for e in incident
                if e["target"] == ident and e["kind"].endswith("_input")],
                outputs=[registry[e["target"]] for e in incident
                if e["source"] == ident and e["kind"].endswith("_output")])
        else:
            result["producer_operation_ids"] = sorted({e["source"] for e in result["flows"]
                if e["target"] == ident and e["kind"].endswith("_output")})
            result["consumer_operation_ids"] = sorted({e["target"] for e in result["flows"]
                if e["source"] == ident and e["kind"].endswith("_input")})
            result["adjacent_operation_ids"] = sorted(set(result["producer_operation_ids"]) |
                                                        set(result["consumer_operation_ids"]))
    else:
        result["links"] = [e for e in graph["edges"] if e["target"] == ident and (
            include_diagnostic or e.get("admission") == "supported")]
        records = {e["record_id"] for e in result["links"]}
        result["record_ids"] = sorted(records)
        result["provenance"] = {r: graph["cards"][r]["source_registry"] for r in records}
    return result


def workflow(graph: dict, record_id: str, *, include_diagnostic=False) -> dict:
    """One card's exact graph, with its method scopes and publication links."""
    card = graph["cards"][record_id]
    if card.get("admission") == "diagnostic" and not include_diagnostic:
        raise ValueError("Diagnostic workflow requires include_diagnostic=True")
    nodes = [n for n in graph["nodes"] if n.get("record_id") == record_id]
    publications = set(card["publication_ids"])
    nodes.extend({**n, "record_ids": [record_id]} for n in graph["nodes"] if n["id"] in publications)
    edges = [e for e in graph["edges"] if e.get("record_id") == record_id]
    return {"record_id": record_id, "status": card["status"], "admission": card.get("admission"),
            "card": card, "nodes": nodes, "edges": edges,
            "flows": [e for e in edges if e["kind"] in FLOW_KINDS],
            "provenance": card.get("source_registry", {})}


def operation_group(graph: dict, record_id: str, operation_ids, *, include_diagnostic=False) -> dict:
    """Prepare exact connected source occurrences for a later scientific comparison.

    The host retains side outputs and surrounding routes. It neither defines a
    reusable component nor compares meanings, assigns a label, or admits an edge.
    """
    view = workflow(graph, record_id, include_diagnostic=include_diagnostic)
    card = view["card"]
    if card.get("admission") != "supported" and not include_diagnostic:
        raise ValueError("Comparison needs a supported workflow or explicit diagnostic access")
    requested = list(operation_ids)
    selected = set(requested)
    operations = {n["id"]: n for n in view["nodes"] if n["node_type"] == "operation"}
    if not selected or len(selected) != len(requested) or selected - operations.keys():
        raise ValueError("Choose distinct exact Operation IDs from this workflow")
    flows = view["flows"]
    touching = [e for e in flows if selected & {e["source"], e["target"]}]
    state_ids = {n["id"] for n in view["nodes"] if n["node_type"] == "state"}
    node_ids = selected | (state_ids & {x for e in touching for x in (e["source"], e["target"])})
    incident = [e for e in flows if e["source"] in node_ids and e["target"] in node_ids]
    nodes = {n["id"]: n for n in view["nodes"] if n["id"] in node_ids}
    # Connectivity describes recorded topology, not a common biochemical role.
    adjacency = {key: set() for key in node_ids}
    for edge in incident:
        adjacency[edge["source"]].add(edge["target"])
        adjacency[edge["target"]].add(edge["source"])
    reached, pending = set(), [min(selected)]
    while pending:
        current = pending.pop()
        if current not in reached:
            reached.add(current)
            pending.extend(adjacency[current] - reached)
    if not selected <= reached:
        raise ValueError("Operations must form one connected source group")
    producers, consumers = {}, {}
    for edge in flows:
        if edge["kind"].endswith("_output"):
            producers.setdefault(edge["target"], set()).add(edge["source"])
        elif edge["kind"].endswith("_input"):
            consumers.setdefault(edge["source"], set()).add(edge["target"])
    states = {key for key, node in nodes.items() if node["node_type"] == "state"}
    inputs, outputs, internal = [], [], []
    for state in sorted(states):
        made, used = producers.get(state, set()), consumers.get(state, set())
        if used & selected and (not made & selected or made - selected): inputs.append(state)
        if made & selected and (not used & selected or used - selected): outputs.append(state)
        if made & selected and used & selected: internal.append(state)
    boundary = [e for e in flows if (e["source"] in node_ids) != (e["target"] in node_ids)]
    context_ids = {x for e in boundary for x in (e["source"], e["target"])} - node_ids
    identity = {"record_id": record_id, "operation_ids": sorted(selected),
        "candidate_sha256": card.get("candidate_sha256"), "review_sha256": card.get("review_sha256"),
        "artifact_pins": card.get("artifact_pins", {})}
    caveats = get_record(graph, "operation", min(selected), include_diagnostic=include_diagnostic)
    result = {"format_version": "illumina_operation_group/1.0", "selection_sha256": digest(identity),
        **identity, "scope": card.get("scope"), "source_scope": card.get("source_scope", {}),
        "admission": card.get("admission"), "nodes": [copy.deepcopy(nodes[k]) for k in sorted(nodes)],
        "flows": copy.deepcopy(incident), "input_state_ids": inputs, "output_state_ids": outputs,
        "internal_state_ids": internal, "boundary_flows": copy.deepcopy(boundary),
        "surrounding_operations": [copy.deepcopy(n) for n in view["nodes"] if n["id"] in context_ids],
        "method_scopes": [copy.deepcopy(n["original"]) for n in view["nodes"]
                          if n["node_type"] == "method_scope"],
        "corrections": [copy.deepcopy(n["original"]) for n in view["nodes"]
                        if n["node_type"] == "correction"],
        "unknowns": copy.deepcopy(card.get("unknowns", [])),
        "review_format_version": card.get("review_format_version"),
        "label_checks": {target: copy.deepcopy(check) for target, check in card.get("label_checks", {}).items()
                         if target in {n["node_type"] + "_label:" + n["original"]["id"] for n in nodes.values()}},
        "label_review_boundary": "Only explicit label judgments from the recorded review; absent checks were not separately assessed.",
        "full_source_registry_artifact": caveats["full_source_registry_artifact"],
        "routing_ambiguities": [row for key, row in
            _routing_ambiguities(graph, {n["id"]: n for n in graph["nodes"]}, include_diagnostic).items()
            if key in selected],
        "provenance": copy.deepcopy(view["provenance"]),
        "status": "source_group_only; scientific_comparison_not_performed",
        "boundary": "Connectivity is not a shared transformation, substitute compatibility or ancestry claim. A boundary State may also be internal when a side branch exits the selected group."}
    if card.get("semantic_checks"):
        targets = {"construction"}
        for node in nodes.values():
            raw = node["original"]
            if node["node_type"] == "state":
                targets.add("state_properties:" + raw["id"])
                targets.update(property_target(raw["id"], p["id"]) for p in raw.get("properties", []))
            else:
                targets.add("operation_semantics:" + raw["id"])
        result["semantic_checks"] = {t: copy.deepcopy(c) for t, c in card["semantic_checks"].items() if t in targets}
    return result


def _route_context(graph, ident, direction, edge_kinds, include_diagnostic):
    if direction not in {"out", "in", "both"}:
        raise ValueError("Direction must be out, in or both")
    kinds = FLOW_KINDS if edge_kinds is None else frozenset(edge_kinds)
    if not kinds or not kinds.issubset(FLOW_KINDS):
        raise ValueError("Route traversal accepts only material/data I/O and precedes Flow kinds")
    nodes = {n["id"]: n for n in graph["nodes"]}
    if ident not in nodes or nodes[ident]["node_type"] not in {"state", "operation"}:
        raise ValueError("Route traversal starts at an exact State or Operation ID")
    if nodes[ident].get("admission") == "diagnostic" and not include_diagnostic:
        raise ValueError("Diagnostic route requires include_diagnostic=True")
    edges = [e for e in graph["edges"] if e["kind"] in kinds and (include_diagnostic or
             e.get("admission") == "supported") and all(endpoint in nodes and
             (include_diagnostic or nodes[endpoint].get("admission") == "supported")
             for endpoint in (e["source"], e["target"]))]
    return nodes, sorted(edges, key=lambda e: (e["id"], e["source"], e["target"])), kinds


def _incident_routes(edges, ident, direction):
    return [(e, e["target"] if e["source"] == ident else e["source"]) for e in edges if
            direction in {"out", "both"} and e["source"] == ident or
            direction in {"in", "both"} and e["target"] == ident]


def _routing_ambiguities(graph, nodes, include_diagnostic):
    """Detect missing input/output pairing, not whether an operation is a join.

    Inspect all I/O kinds even when the caller filters traversal kinds. A filter
    must not hide another input/output and create apparent routing certainty.
    """
    inputs, outputs, incident = {}, {}, {}
    for edge in graph["edges"]:
        if edge["kind"] not in FLOW_KINDS - {"precedes"} or (
                not include_diagnostic and edge.get("admission") != "supported"):
            continue
        source, target = nodes.get(edge["source"]), nodes.get(edge["target"])
        if not source or not target or (not include_diagnostic and any(
                n.get("admission") != "supported" for n in (source, target))):
            continue
        if edge["kind"].endswith("_input") and target["node_type"] == "operation":
            operation, state = target["id"], source["id"]
            inputs.setdefault(operation, set()).add(state)
        elif edge["kind"].endswith("_output") and source["node_type"] == "operation":
            operation, state = source["id"], target["id"]
            outputs.setdefault(operation, set()).add(state)
        else:
            continue
        incident.setdefault(operation, []).append(edge)
    return {operation: {"operation_id": operation,
        "input_state_ids": sorted(inputs.get(operation, ())),
        "output_state_ids": sorted(outputs.get(operation, ())),
        "incident_flows": sorted(incident[operation], key=lambda e: e["id"]),
        "status": "input_output_pairing_not_encoded",
        "interpretation": "Structural many-to-many incidence, not evidence of mixing, independent processing, a source error, or a valid molecular-lineage route."}
        for operation in sorted(set(inputs) & set(outputs))
        if len(inputs[operation]) > 1 and len(outputs[operation]) > 1}


def neighbors(graph: dict, ident: str, *, direction="out", limit=20,
              edge_kinds=None, include_diagnostic=False) -> dict:
    """Inspect one Flow hop, retaining evidence and actual arrow orientation."""
    if not 1 <= limit <= 200:
        raise ValueError("Neighbor edge limit must be from 1 to 200")
    nodes, edges, kinds = _route_context(graph, ident, direction, edge_kinds, include_diagnostic)
    routes = _incident_routes(edges, ident, direction)
    selected = routes[:limit]
    records = {nodes[n]["record_id"] for _, n in selected} | {nodes[ident]["record_id"]}
    ambiguity = _routing_ambiguities(graph, nodes, include_diagnostic)
    shown = {ident, *(n for _, n in selected)}
    return {"start_id": ident, "direction": direction, "edge_kinds": sorted(kinds),
            "neighbor_ids": sorted({n for _, n in selected}),
            "nodes": [nodes[n] for n in sorted({n for _, n in selected})],
            "flows": [e for e, _ in selected], "truncated": len(routes) > limit,
            "semantics": INCIDENCE_SEMANTICS,
            "routing_ambiguities": [ambiguity[n] for n in sorted(shown & set(ambiguity))],
            "provenance": {r: graph["cards"][r]["source_registry"] for r in sorted(records)}}


def walk(graph: dict, ident: str, *, direction="out", depth=4, limit=50,
         edge_kinds=None, include_diagnostic=False, expand_ambiguous_incidence=False) -> dict:
    """Inspect incidence; stop at unpaired many-to-many boundaries by default.

    The opt-in only expands raw declared links; it never supplies input/output
    pairing or upgrades a route into a molecular-lineage or mixing claim.
    """
    if not 0 <= depth <= 12 or not 1 <= limit <= 200:
        raise ValueError("Use a depth from 0 to 12 and a Flow limit from 1 to 200")
    if not isinstance(expand_ambiguous_incidence, bool):
        raise ValueError("expand_ambiguous_incidence must be a boolean, not a routing interpretation")
    nodes, edges, kinds = _route_context(graph, ident, direction, edge_kinds, include_diagnostic)
    ambiguity = _routing_ambiguities(graph, nodes, include_diagnostic)
    queue, depths, selected = [ident], {ident: 0}, {}
    routing_frontier, routing_options = set(), {}
    truncated = False
    while queue:
        current = queue.pop(0)
        if depths[current] >= depth:
            continue
        if current in ambiguity and not expand_ambiguous_incidence:
            options = [e for e, _ in _incident_routes(edges, current, direction) if e["id"] not in selected]
            if options:
                routing_frontier.add(current)
                routing_options[current] = options
            continue
        for edge, target in _incident_routes(edges, current, direction):
            if edge["id"] not in selected:
                if len(selected) >= limit:
                    truncated = True
                    break
                selected[edge["id"]] = edge
            if target not in depths:
                depths[target] = depths[current] + 1
                queue.append(target)
        if truncated:
            break
    frontier = [node for node in depths if any(e["id"] not in selected for e, _ in
                 _incident_routes(edges, node, direction))]
    records = {nodes[node]["record_id"] for node in depths}
    return {"start_id": ident, "direction": direction, "depth": depth, "edge_kinds": sorted(kinds),
            "nodes": [nodes[n] for n in sorted(depths, key=lambda n: (depths[n], n))],
            "flows": list(selected.values()), "node_depths": depths, "truncated": truncated,
            "frontier_ids": sorted(frontier),
            "depth_limited": any(depths[n] >= depth for n in frontier) and not truncated,
            "semantics": INCIDENCE_SEMANTICS,
            "expand_ambiguous_incidence": expand_ambiguous_incidence,
            "routing_limited": bool(routing_frontier), "routing_frontier_ids": sorted(routing_frontier),
            "untraversed_incidence_options": routing_options,
            "routing_ambiguities": [ambiguity[n] for n in sorted(set(depths) & set(ambiguity))],
            "provenance": {r: graph["cards"][r]["source_registry"] for r in sorted(records)}}


def search_operations(graph: dict, *, input_text="", transformation="", output_text="",
                      context="", limit=10, include_diagnostic=False) -> list[dict]:
    """Lexical shortlist only; scores are not biochemical equivalence probabilities."""
    tokens = lambda text: set(re.findall(r"\w+", text.casefold()))
    queries = {k: tokens(v) for k, v in (("input", input_text), ("transformation", transformation),
                                       ("output", output_text), ("context", context)) if v.strip()}
    if not queries or not 1 <= limit <= 100:
        raise ValueError("Supply a search field and a limit from 1 to 100")
    matches = []
    for node in graph["nodes"]:
        if node["node_type"] != "operation" or (node["admission"] != "supported" and not include_diagnostic):
            continue
        record = get_record(graph, "operation", node["id"], include_diagnostic=include_diagnostic)
        original = record["original"]
        state_text = lambda items: " ".join(" ".join((x["original"]["label"],
            *x["original"].get("features", []))) for x in items)
        fields = {"input": state_text(record["inputs"]),
                  "output": state_text(record["outputs"]),
                  "transformation": " ".join(filter(None, (original["label"], original["description"],
                                                             original.get("mechanism")))),
                  "context": " ".join(filter(None, (graph["cards"][node["record_id"]].get("title"),
                    graph["cards"][node["record_id"]].get("scope"), original.get("information_effect"),
                    state_text(record["inputs"]), state_text(record["outputs"]))))}
        scores = {key: len(query & tokens(fields[key])) / len(query) for key, query in queries.items()}
        if sum(scores.values()):
            matches.append({"operation_id": node["id"], "record_id": node["record_id"],
                "label": original["label"], "admission": node["admission"], "score": sum(scores.values()),
                "field_scores": scores, "status": "unreviewed_comparison_candidate",
                "evidence": original["evidence"]})
    return sorted(matches, key=lambda x: (-x["score"], x["operation_id"]))[:limit]


def protocol_text(candidate: DiagramExtraction | dict, review: DiagramReview | None = None,
                  sources: dict | None = None, *, source_mode: str = 'diagram') -> str:
    """Deterministic reading view; no model call, invented reaction or new Flow.

    A per-card graph from export_graph may be passed directly. Display order is
    topological where dependencies exist; it does not impose order on branches.
    """
    if isinstance(candidate, dict):
        graph = candidate
        source_mode = graph.get('source_mode', source_mode)
        candidate = parse_extraction(graph["workflow"])
        review = parse_review(graph["review"])
    outcome = review_outcome(review, candidate, sources, source_mode=source_mode) if review else "unreviewed"
    lines = ["Protocol architecture: " + candidate.title, "Record: " + candidate.record_id,
             "Scope: " + candidate.scope, "Review outcome: " + outcome,
             "This is a reading view of saved records, not a fresh scientific review.", ""]
    records = {x.id: x for x in (*candidate.states, *candidate.operations)}
    arrows = {ident: [] for ident in records}
    indegree = Counter()
    for flow in candidate.flows:
        arrows[flow.source].append(flow.target)
        indegree[flow.target] += 1
    ready, order = sorted(x for x in records if not indegree[x]), []
    while ready:
        ident = ready.pop(0)
        if any(x.id == ident for x in candidate.operations): order.append(ident)
        for target in arrows[ident]:
            indegree[target] -= 1
            if not indegree[target]: ready.append(target)
        ready.sort()

    def refs(evidence):
        return "; ".join(e.source_id + " @ " + e.locator + " [" + e.support + "]" +
            (" quote=" + json.dumps(e.quote, ensure_ascii=False) if e.quote else "") +
            (" rationale=" + e.rationale if e.rationale else "") for e in evidence)

    lines.extend(("Method applicability",))
    for scope in candidate.method_scopes:
        lines.extend((f"  {scope.method}: {scope.status}; Operations: {', '.join(scope.operation_ids) or '(unresolved)'}",
                      "    " + scope.rationale, "    Evidence: " + refs(scope.evidence)))
    lines.extend(("", "States"))
    for state in candidate.states:
        lines.extend((f"  STATE {state.id} ({state.kind}): {state.label}",
                      *( ["    Features: " + "; ".join(state.features)] if state.features else []),
                      "    Evidence: " + refs(state.evidence)))
        for component in getattr(state, "components", ()):
            lines.append(f"    Component {component.id}: {component.label}; Evidence: " + refs(component.evidence))
        for prop in getattr(state, "properties", ()):
            value = prop.model_dump(mode="json", exclude={"evidence", "id"})
            lines.extend(("    Property " + prop.id + ": " + json.dumps(value, ensure_ascii=False),
                          "      Evidence: " + refs(prop.evidence)))
    lines.extend(("", "Operations", "Reading order follows declared dependencies; use the Flows below to follow each branch."))
    for ident in order:
        op = records[ident]
        lines.extend((f"  OPERATION {op.id}: {op.label}", "    " + op.description,
            "    Input States: " + "; ".join(x + " (" + records[x].label + ")" for x in op.inputs),
            "    Output States: " + "; ".join(x + " (" + records[x].label + ")" for x in op.outputs)))
        if op.mechanism: lines.append("    Mechanism: " + op.mechanism)
        if op.information_effect: lines.append("    Information: " + op.information_effect)
        if hasattr(op, "resolution"):
            lines.append("    Source resolution: " + op.resolution + "; output grouping: " + op.output_grouping)
            for role in op.input_roles:
                subject = "" if role.subject == "self" else " / constituent " + role.subject
                lines.append("    Input role " + role.state_id + subject + ": " + ", ".join(role.roles) + "; " + role.explanation)
                lines.append("      Evidence: " + refs(role.evidence))
            for effect in op.effects:
                lines.append("    Effect [" + effect.aspect + "] " + ", ".join(effect.input_state_ids) + " -> " +
                             ", ".join(effect.output_state_ids) + ": " + effect.statement)
                lines.append("      Evidence: " + refs(effect.evidence))
        lines.append("    Evidence: " + refs(op.evidence))
    lines.extend(("", "Flows"))
    for flow in candidate.flows:
        source, target = records[flow.source], records[flow.target]
        lines.extend((f"  FLOW {flow.id}: {flow.source} ({source.label}) -> {flow.target} ({target.label}) [{flow.kind}]",
                      "    Evidence: " + refs(flow.evidence)))
    lines.extend(("", "Paper matches"))
    for paper in candidate.paper_matches:
        lines.extend((f"  {paper.id}: {paper.source_id}; methods={', '.join(paper.methods)}; "
                      f"{paper.assessment}; {paper.relationship}", "    Evidence: " + refs(paper.evidence)))
    if candidate.corrections:
        lines.extend(("", "Explicit primary-paper corrections"))
        for correction in candidate.corrections:
            lines.extend((f"  {correction.id} affects {correction.target_id}: {correction.disposition}",
                "    Diagram observation: " + correction.diagram_observation,
                "    Primary-source statement: " + correction.primary_statement,
                "    Evidence: " + refs(correction.evidence)))
    if candidate.unknowns:
        lines.extend(("", "Unknowns"))
        lines.extend(f"  {x.id}: {x.question}; blocking={x.blocking}; affects={', '.join(x.affected_ids)}"
                     for x in candidate.unknowns)
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build"); build.add_argument("batch", type=Path)
    build.add_argument("inventory", type=Path); build.add_argument("--output", type=Path, required=True)
    protocol = sub.add_parser("protocol"); protocol.add_argument("graph", type=Path)
    group = sub.add_parser("group"); group.add_argument("graph", type=Path)
    group.add_argument("id", help="Exact workflow record ID")
    group.add_argument("--operation", action="append", required=True, help="Exact global Operation ID; repeat for a connected group")
    group.add_argument("--include-diagnostic", action="store_true")
    for command in ("workflow", "neighbors", "walk"):
        p = sub.add_parser(command); p.add_argument("graph", type=Path); p.add_argument("id")
        p.add_argument("--include-diagnostic", action="store_true")
        if command != "workflow":
            p.add_argument("--direction", choices=("out", "in", "both"), default="out")
            p.add_argument("--kind", action="append", choices=sorted(FLOW_KINDS))
            p.add_argument("--limit", type=int, default=50 if command == "walk" else 20)
        if command == "walk":
            p.add_argument("--depth", type=int, default=4)
            p.add_argument("--expand-ambiguous-incidence", action="store_true", help=
                "Expand raw many-to-many incidence only; does not establish input-output pairing or molecular lineage")
    for command in ("list", "get", "search"):
        p = sub.add_parser(command); p.add_argument("graph", type=Path)
        p.add_argument("--include-diagnostic", action="store_true")
        if command in {"list", "get"}:
            p.add_argument("kind", choices=("card", "state", "operation", "paper"))
            if command == "get": p.add_argument("id")
            else: p.add_argument("--limit", type=int, default=20); p.add_argument("--offset", type=int, default=0)
        else:
            for field in ("input", "transformation", "output", "context"): p.add_argument("--" + field, default="")
            p.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    if args.command == "build":
        graph = build_graph(args.batch, args.inventory); write(args.output, graph)
        print(json.dumps(graph["counts"])); return int("integrity_error" in graph["counts"])
    graph = read(args.graph)
    if args.command == "protocol":
        print(protocol_text(graph), end=""); return 0
    if args.command == "workflow": value = workflow(graph, args.id, include_diagnostic=args.include_diagnostic)
    elif args.command == "group":
        value = operation_group(graph, args.id, args.operation, include_diagnostic=args.include_diagnostic)
    elif args.command in {"neighbors", "walk"}:
        options = dict(direction=args.direction, limit=args.limit, edge_kinds=args.kind,
                       include_diagnostic=args.include_diagnostic)
        value = (walk(graph, args.id, depth=args.depth,
                      expand_ambiguous_incidence=args.expand_ambiguous_incidence, **options) if args.command == "walk" else
                 neighbors(graph, args.id, **options))
    elif args.command == "get": value = get_record(graph, args.kind, args.id, include_diagnostic=args.include_diagnostic)
    elif args.command == "search":
        value = search_operations(graph, input_text=args.input, transformation=args.transformation,
            output_text=args.output, context=args.context, limit=args.limit, include_diagnostic=args.include_diagnostic)
    elif args.kind == "card": value = list(graph["cards"].values())[max(args.offset, 0):max(args.offset, 0) + min(max(args.limit, 1), 100)]
    else:
        kind = {"state": "state", "operation": "operation", "paper": "publication"}[args.kind]
        selected = [n for n in graph["nodes"] if n["node_type"] == kind and (args.include_diagnostic
            or n.get("admission") == "supported" or n.get("has_supported_link"))]
        value = selected[max(args.offset, 0):max(args.offset, 0) + min(max(args.limit, 1), 100)]
    print(json.dumps(value, indent=2, ensure_ascii=False)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
