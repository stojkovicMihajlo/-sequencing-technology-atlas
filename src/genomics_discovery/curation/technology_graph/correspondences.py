"""Join exact source selections to recorded comparisons and concept memberships."""

from .candidates import cut


def _overlap(requested, stored):
    return ("exact" if requested == stored else "query_subset" if requested < stored
            else "query_superset" if stored < requested else "partial_overlap")


def lookup_correspondences(graph, workflow_id, operation_ids):
    """Read-only scope lookup. Claim targets do not prove whole-unit equivalence."""
    selected = cut(graph, workflow_id, operation_ids)
    requested = {o.id for o in selected.operations}
    workflow = graph["workflows"][workflow_id]
    comparisons, memberships = [], []
    for cid, comparison in graph["comparisons"].items():
        for side, other in (("left", "right"), ("right", "left")):
            view = graph["views"][comparison[side + "_view"]]
            stored = {o["id"] for o in view["cut"]["operations"]}
            if view["workflow_id"] != workflow_id or not requested & stored:
                continue
            if (view["cut"]["workflow_sha256"] != workflow["workflow_sha256"]
                    or view["cut"]["source_packet_sha256"] != workflow["packet_sha256"]):
                raise ValueError("Comparison selection differs from its bound workflow/source")
            claims, direct = [], set()
            external = {ref["operation_id"] for ref in view["cut"]["external_operations"]}
            for index, claim in enumerate(comparison["alignment"]["claims"]):
                targets = {t.removeprefix("operation:") for t in claim[side + "_targets"]
                           if t.startswith("operation:")}
                if targets - stored - external:
                    raise ValueError("Claim targets an operation outside its comparison selection/context")
                if claim["kind"] == "correspondence":
                    direct.update(targets & stored)
                claims.append({"claim_index": index, **claim,
                               "queried_operation_targets": sorted(requested & targets & stored),
                               "external_context_operation_targets": sorted(targets & external)})
            counterpart = graph["views"][comparison[other + "_view"]]
            comparisons.append({
                "comparison_id": cid, "query_side": side,
                "overlap_kind": _overlap(requested, stored),
                "stored_operation_ids": sorted(stored),
                "overlapping_operation_ids": sorted(requested & stored),
                "direct_correspondence_operation_targets": sorted(direct),
                "selected_operations_without_direct_correspondence_target": sorted(stored - direct),
                "requested_operations_without_direct_correspondence_target": sorted(requested - direct),
                "counterpart": {"view_id": comparison[other + "_view"],
                                "workflow_id": counterpart["workflow_id"],
                                "method_id": counterpart["method_id"],
                                "operation_ids": [o["id"] for o in counterpart["cut"]["operations"]]},
                "question": comparison["case"]["question"],
                "relation": comparison["case"]["relation"],
                "disposition": comparison["alignment"]["disposition"],
                "review_sha256": comparison["review_sha256"],
                "rationale": comparison["alignment"]["rationale"], "claims": claims,
            })
    selections = {n["id"]: n for n in graph["nodes"] if n["kind"] == "protocol_selection"
                  and n.get("workflow_id") == workflow_id}
    for edge in graph["edges"]:
        if edge["kind"] != "instance_of_concept" or edge["source"] not in selections:
            continue
        node = selections[edge["source"]]
        if (node["workflow_sha256"] != workflow["workflow_sha256"]
                or node["source_packet_sha256"] != workflow["packet_sha256"]):
            raise ValueError("Concept selection differs from its bound workflow/source")
        stored = set(node["operation_ids"])
        if requested & stored:
            memberships.append({"selection_id": node["id"], "concept_id": edge["target"],
                                "overlap_kind": _overlap(requested, stored),
                                "stored_operation_ids": sorted(stored),
                                "overlapping_operation_ids": sorted(requested & stored),
                                "assignment_id": edge["assignment_id"], "basis": edge["basis"],
                                "review_contract": edge["review_contract"],
                                "review_sha256": edge["review_sha256"]})
    return {
        "graph_revision_id": graph["revision_id"], "workflow_id": workflow_id,
        "operation_ids": sorted(requested), "workflow_sha256": workflow["workflow_sha256"],
        "source_packet_sha256": workflow["packet_sha256"],
        "comparisons": comparisons, "concept_memberships": memberships,
        "boundary": "Target coverage describes recorded claim scope, not proof that every action "
        "of a targeted operation is equivalent. State/resource targets remain in the original "
        "claims and are not mapped to operations by inference. Contextual overlap does not "
        "transfer a conclusion or concept membership to a subset/superset. Read differences, "
        "unknowns and source passages. No ancestry, substitution or transitive equivalence follows.",
    }
