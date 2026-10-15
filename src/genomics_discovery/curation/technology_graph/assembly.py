"""Deterministic atlas/comparison integration; assembly makes no new scientific claims."""

import json
from collections import Counter
from graphlib import TopologicalSorter
from pathlib import Path
from urllib.parse import quote

from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.models import (
    digest,
    review_outcome,
    validate_evidence,
    validate_review,
)
from genomics_discovery.curation.workflow_matching.consumer import load_reviewed

from .admission import load_extractions

FORMAT = "integrated_technology_graph/1.0"
HISTORICAL_KINDS = {"method_development", "component_reuse", "comparison", "analogy"}


def node_id(kind, namespace, local_id=None):
    return f"{kind}:{namespace}" + (f":{quote(local_id, safe='')}" if local_id else "")


def assemble_graph(atlas: dict, bindings: dict, reviewed_runs: list[tuple]) -> dict:
    """Pure projection of already loaded records; production calls must use build_graph.

    A reviewed_runs entry is (origin, load_reviewed result). A method identity binding
    is an explicit curator assertion, not fuzzy name matching or publication identity proof.
    """
    nodes, edges, workflows, views, profiles, comparisons = {}, {}, {}, {}, {}, {}
    matching_reviews = {}
    history = {c["id"]: c for c in atlas["claims"]}
    methods = {m["id"]: m for m in atlas["methods"]}
    if len(methods) != len(atlas["methods"]) or len(history) != len(atlas["claims"]):
        raise ValueError("Atlas method and claim IDs must be unique")
    atlas_method_ids = set(methods)
    used_parents = {p for _, loaded in reviewed_runs for p in loaded[0].parents}
    for parent in sorted(used_parents):
        binding = bindings.get(parent, {})
        external = binding.get("external_method")
        if external is None:
            continue
        if (
            not isinstance(external, dict)
            or set(external) != {"id", "name"}
            or external["id"] != binding.get("atlas_method_id")
            or not all(isinstance(v, str) and v.strip() for v in external.values())
            or external["id"] in atlas_method_ids
        ):
            raise ValueError(
                "External identity requires a new explicit ID/name, not an atlas override"
            )
        if external["id"] in methods:
            if methods[external["id"]]["name"] != external["name"]:
                raise ValueError("Conflicting external method identity")
            continue
        methods[external["id"]] = {
            **external,
            "outside_catalogue": True,
            "original_categories": [],
            "identity_basis": binding.get("basis"),
            "record_scope": "Explicit external method; no Illumina classification inherited",
        }

    def add_node(record):
        if record["id"] in nodes and nodes[record["id"]] != record:
            raise ValueError("Conflicting version-scoped node")
        nodes[record["id"]] = record

    def add_edge(source, target, kind, layer, *, identity, directed=True, **details):
        if source not in nodes or target not in nodes:
            raise ValueError("Graph edge has an unresolved endpoint")
        record = {
            "id": identity,
            "source": source,
            "target": target,
            "kind": kind,
            "layer": layer,
            "directed": directed,
            **details,
        }
        if identity in edges and edges[identity] != record:
            raise ValueError("Conflicting assertion/edge identity")
        edges[identity] = record

    for ident, method in methods.items():
        add_node(
            {
                "id": node_id("technology", ident),
                "kind": "technology",
                "method_id": ident,
                "label": method["name"],
                "record": method,
            }
        )
    for ident, claim in history.items():
        if claim.get("status") != "supported":
            continue
        kind = claim.get("relationship_kind")
        if kind not in HISTORICAL_KINDS:
            raise ValueError("Supported atlas claim lacks an explicit relationship kind")
        add_edge(
            node_id("technology", claim["source"]),
            node_id("technology", claim["target"]),
            kind,
            "history" if kind == "method_development" else "atlas_relations",
            identity=node_id("atlas_claim", ident),
            directed=kind not in {"comparison", "analogy"},
            claim_id=ident,
            review_status="imported_atlas_support_not_rereviewed",
            relation=claim["relation"],
        )
    historical_dependencies = {node_id("technology", mid): set() for mid in methods}
    for edge in edges.values():
        if edge["kind"] == "method_development":
            historical_dependencies[edge["target"]].add(edge["source"])
    # Only the historical projection is acyclic; symmetric comparisons are not ancestry.
    tuple(TopologicalSorter(historical_dependencies).static_order())

    for origin, loaded in reviewed_runs:
        context, batch, alignments, cuts, review, cases = loaded
        if review is not None:
            matching_reviews[digest(review)] = {
                "origin": origin,
                "review": review.model_dump(mode="json"),
            }
        parent_workflows, parent_methods = {}, {}
        for parent, row in context.parents.items():
            workflow, packet, parent_review = row["workflow"], row["packet"], row["review"]
            binding = bindings.get(parent, {})
            if (
                binding.get("workflow_method_id") != workflow.method_id
                or binding.get("atlas_method_id") not in methods
                or not isinstance(binding.get("basis"), str)
                or not binding["basis"].strip()
            ):
                raise ValueError(f"Missing or mismatched explicit identity binding: {parent}")
            if any(
                getattr(workflow, k) != packet[k]
                for k in ("method_id", "title", "scope", "variant")
            ):
                raise ValueError("Workflow and source packet scopes differ")
            errors = validate_evidence(workflow, packet)
            if parent_review is not None:
                errors += validate_review(parent_review, workflow, packet)
            if errors:
                raise ValueError("Invalid parent provenance/review: " + "; ".join(errors))
            status = review_outcome(parent_review, workflow) if parent_review else "not_reviewed"
            version = digest(
                {
                    "workflow": digest(workflow),
                    "packet": digest(packet),
                    "review": digest(parent_review) if parent_review else None,
                }
            )
            wid = node_id("workflow", version)
            mid = binding["atlas_method_id"]
            parent_workflows[parent], parent_methods[parent] = wid, mid
            record = {
                "workflow": workflow.model_dump(mode="json"),
                "workflow_sha256": digest(workflow),
                "packet_sha256": digest(packet),
                "review_sha256": digest(parent_review) if parent_review else None,
                "packet_path": str(row["paths"]["packet"]),
                "atlas_method_id": mid,
                "identity_binding": binding,
                "review_status": status,
                "review": parent_review.model_dump(mode="json") if parent_review else None,
                "sources": [
                    {k: v for k, v in s.items() if k != "passages"} for s in packet["sources"]
                ],
            }
            # Identical scientific records from different runs share one workflow.
            if wid in workflows:
                previous = workflows[wid]
                if {k: v for k, v in previous.items() if k != "packet_path"} != {
                    k: v for k, v in record.items() if k != "packet_path"
                }:
                    raise ValueError(
                        "One workflow version has conflicting identity/review bindings"
                    )
            else:
                workflows[wid] = record
            add_node(
                {
                    "id": wid,
                    "kind": "workflow",
                    "method_id": mid,
                    "label": workflow.title,
                    "variant": workflow.variant,
                    "review_status": status,
                    "workflow_sha256": digest(workflow),
                }
            )
            add_edge(
                node_id("technology", mid),
                wid,
                "has_workflow",
                "membership",
                identity=node_id("membership", digest([mid, wid])),
            )
            ids = {}
            for kind, items in (
                ("state", workflow.states),
                ("operation", workflow.operations),
                ("module", workflow.modules),
            ):
                for item in items:
                    ident = node_id(kind, version, item.id)
                    ids[item.id] = ident
                    add_node(
                        {
                            "id": ident,
                            "kind": kind,
                            "workflow_id": wid,
                            "method_id": mid,
                            "local_id": item.id,
                            "label": getattr(item, "name", getattr(item, "description", item.id)),
                            "record": item.model_dump(mode="json"),
                        }
                    )
                    add_edge(
                        wid,
                        ident,
                        "contains",
                        "membership",
                        identity=node_id("membership", digest([wid, ident])),
                    )
            for flow in workflow.flows:
                add_edge(
                    ids[flow.source],
                    ids[flow.target],
                    flow.kind,
                    "protocol",
                    identity=node_id("flow", version, flow.id),
                    workflow_id=wid,
                    record=flow.model_dump(mode="json"),
                    review_status=status,
                )
            for module in workflow.modules:
                for operation in module.operation_ids:
                    add_edge(
                        ids[module.id],
                        ids[operation],
                        "groups_operation",
                        "membership",
                        identity=node_id("membership", digest([wid, module.id, operation])),
                        basis="curator_boundary_not_reaction_equivalence",
                    )

        def add_view(parent, cut, parent_workflows=parent_workflows, parent_methods=parent_methods):
            wid = parent_workflows[parent]
            vid = node_id("view", digest([wid, sorted(o["id"] for o in cut["operations"])]))
            record = {"workflow_id": wid, "method_id": parent_methods[parent], "cut": cut}
            if vid in views and views[vid] != record:
                raise ValueError("Conflicting contextual view")
            views[vid] = record
            add_node(
                {
                    "id": vid,
                    "kind": "view",
                    "workflow_id": wid,
                    "method_id": parent_methods[parent],
                    "label": "; ".join(o["name"] for o in cut["operations"]),
                    "operation_ids": [o["id"] for o in cut["operations"]],
                }
            )
            add_edge(
                wid,
                vid,
                "has_view",
                "membership",
                identity=node_id("membership", digest([wid, vid])),
            )
            version = wid.removeprefix("workflow:")
            for operation in cut["operations"]:
                add_edge(
                    vid,
                    node_id("operation", version, operation["id"]),
                    "selects_operation",
                    "membership",
                    identity=node_id("membership", digest([vid, operation["id"]])),
                )
            return vid

        for profile in batch.profiles:
            parent = context.unit_parents[profile.occurrence_id]
            vid = add_view(
                parent, context.occurrences[profile.occurrence_id].model_dump(mode="json")
            )
            key = digest(profile)
            record = {
                "view_id": vid,
                "profile": profile.model_dump(mode="json"),
                "review_sha256": digest(review),
                "origin": origin,
            }
            profiles[key] = record
        by_id = {c.id: c for c in cases}
        for alignment in alignments.alignments:
            case = by_id[alignment.case_id]
            left = add_view(alignment.left.parent_id, cuts[case.id]["left"])
            right = add_view(alignment.right.parent_id, cuts[case.id]["right"])
            cid = node_id("comparison", digest([case, alignment, digest(review)]))
            comparisons[cid] = {
                "case": case.model_dump(mode="json"),
                "alignment": alignment.model_dump(mode="json"),
                "left_view": left,
                "right_view": right,
                "left_method": parent_methods[alignment.left.parent_id],
                "right_method": parent_methods[alignment.right.parent_id],
                "review_sha256": digest(review),
                "origin": origin,
                "review_status": "fresh_model_source_reviewed",
            }
            if alignment.disposition == "candidate_correspondence":
                add_edge(
                    left,
                    right,
                    case.relation,
                    "cross_protocol",
                    identity=cid,
                    directed=False,
                    comparison_id=cid,
                    review_status="fresh_model_source_reviewed",
                    scope=case.question,
                    assertion_level="reviewed_candidate_correspondence",
                    claim_summary={
                        "correspondence_indices": [
                            i for i, c in enumerate(alignment.claims)
                            if c.kind == "correspondence"
                        ],
                        "inference_tagged_indices": [
                            i for i, c in enumerate(alignment.claims)
                            if any(e.support == "inferred"
                                   for e in (*c.left_evidence, *c.right_evidence))
                        ],
                        "not_established_indices": [
                            i for i, c in enumerate(alignment.claims)
                            if c.kind == "not_established"
                        ],
                    },
                    boundary="Read the indexed claims and their exact source contexts. "
                             "A reviewed comparison can retain inferences and unknowns. "
                             "No ancestry, component interchangeability or transfer "
                             "compatibility follows from this edge label.",
                )

    details = []
    for cid, comparison in comparisons.items():
        pair = (comparison["left_method"], comparison["right_method"])
        for claim_id, claim in history.items():
            if (
                claim.get("status") != "supported"
                or claim.get("relationship_kind") not in {"method_development", "component_reuse"}
                or (claim["source"], claim["target"]) not in (pair, pair[::-1])
            ):
                continue
            claims = comparison["alignment"]["claims"]
            details.append(
                {
                    "historical_claim_id": claim_id,
                    "comparison_id": cid,
                    "earlier_side": "left" if claim["source"] == pair[0] else "right",
                    "shared_claim_indices": [
                        i for i, c in enumerate(claims) if c["kind"] == "correspondence"
                    ],
                    "different_claim_indices": [
                        i for i, c in enumerate(claims) if c["kind"] == "difference"
                    ],
                    "unestablished_claim_indices": [
                        i for i, c in enumerate(claims) if c["kind"] == "not_established"
                    ],
                    "exact_variant_inheritance_assessed": False,
                    "boundary": "Historical support is inherited unchanged from the atlas. "
                    "Local overlap/differences characterize the selected variants; "
                    "shared parts are not automatically proven inherited components.",
                }
            )
    return {
        "format_version": FORMAT,
        "atlas_revision_id": atlas["revision_id"],
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "workflows": workflows,
        "views": views,
        "profiles": profiles,
        "comparisons": comparisons,
        "matching_reviews": matching_reviews,
        "historical_claims": history,
        "historical_evidence": atlas.get("evidence", []),
        "change_events": atlas.get("change_events", []),
        "development_details": details,
        "boundary": "Assembly preserves source and review scopes. No new historical ancestry, "
        "interchangeability, experimental compatibility or novelty is inferred.",
    }


def build_graph(
    atlas_path: Path,
    bindings_path: Path,
    indexes: list[Path],
    *,
    extractions_path: Path | None = None,
) -> dict:
    if not indexes and extractions_path is None:
        raise ValueError("Supply completed extraction runs and/or matching indexes")
    files = {p.resolve() for p in (atlas_path, bindings_path, *indexes)}
    if extractions_path is not None:
        files.add(extractions_path.resolve())
    pins = {str(p): sha(p) for p in files}
    atlas, bindings = json.loads(atlas_path.read_text()), json.loads(bindings_path.read_text())
    runs = []
    if extractions_path is not None:
        loaded = load_extractions(extractions_path)
        runs.append((str(extractions_path.resolve()), loaded))
        files.update(loaded[0].files)
    for index in indexes:
        index = index.resolve()
        loaded = load_reviewed(index)
        runs.append((str(index), loaded))
        files.update(loaded[0].files)
        descriptor = json.loads(index.read_text())
        files.update((index.parent / p).resolve() for p in descriptor["files"].values())
        files.add(index.parent / "config.json")
        review_dir = (index.parent / descriptor["files"]["review"]).resolve().parent
        files.update(p for p in review_dir.iterdir() if p.is_file())
    for path in files:
        pins.setdefault(str(path), sha(path))
    result = assemble_graph(atlas, bindings, runs)
    # Reopen bound sources after pin discovery, closing a load-to-pin mutation window.
    for index in indexes:
        load_reviewed(index.resolve())
    if extractions_path is not None:
        load_extractions(extractions_path)
    if any(sha(Path(p)) != expected for p, expected in pins.items()):
        raise ValueError("Graph input changed during assembly")
    result["input_files"] = pins
    result["builder_implementation"] = {
        p.name: sha(p) for p in sorted(Path(__file__).parent.glob("*.py"))
    }
    result["statistics"] = summarize(result)
    result["revision_id"] = digest(result)
    return result


def summarize(graph: dict) -> dict:
    detailed = {w["atlas_method_id"] for w in graph["workflows"].values()}
    methods = {n["method_id"] for n in graph["nodes"] if n["kind"] == "technology"}
    return {
        "technologies": len(methods),
        "technologies_with_operation_graphs": len(detailed),
        "technologies_without_operation_graphs": len(methods - detailed),
        "workflows": len(graph["workflows"]),
        "views": len(graph["views"]),
        "profiles": len(graph["profiles"]),
        "comparisons": len(graph["comparisons"]),
        "edge_kinds": dict(Counter(e["kind"] for e in graph["edges"])),
        "nonpositive_comparisons": sum(
            c["alignment"]["disposition"] != "candidate_correspondence"
            for c in graph["comparisons"].values()
        ),
        "historical_claims_with_local_details": len(graph["development_details"]),
    }
