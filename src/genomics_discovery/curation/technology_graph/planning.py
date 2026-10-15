"""Prioritize historical relationships with newly available operation-level evidence."""

from genomics_discovery.curation.workflow_extraction.models import digest


def plan_comparisons(graph: dict, *, method_id=None):
    """Return work requests, never scientific edges or an exhaustive candidate search."""
    by_method = {}
    for wid, record in graph["workflows"].items():
        by_method.setdefault(record["atlas_method_id"], []).append(wid)
    if method_id is not None and not any(
        n["kind"] == "technology" and n["method_id"] == method_id for n in graph["nodes"]
    ):
        raise KeyError(f"Unknown technology: {method_id}")
    requests = []
    for ident, claim in graph["historical_claims"].items():
        if (
            claim.get("status") != "supported"
            or claim.get("relationship_kind") not in {"method_development", "component_reuse"}
            or method_id is not None
            and method_id not in {claim["source"], claim["target"]}
        ):
            continue
        for left in sorted(by_method.get(claim["source"], [])):
            for right in sorted(by_method.get(claim["target"], [])):
                existing = []
                for detail in graph["development_details"]:
                    if detail["historical_claim_id"] != ident:
                        continue
                    comparison = graph["comparisons"][detail["comparison_id"]]
                    selected = {
                        graph["views"][comparison[side]]["workflow_id"]
                        for side in ("left_view", "right_view")
                    }
                    if selected == {left, right}:
                        existing.append(detail["comparison_id"])
                requests.append(
                    {
                        "request_id": "compare:" + digest([ident, left, right]),
                        "historical_claim_id": ident,
                        "left_workflow_id": left,
                        "right_workflow_id": right,
                        "existing_local_comparisons": existing,
                        "next_action": "inspect_existing_scope" if existing else "compare_sources",
                        "historical_annotation_as_lead": claim,
                        "question": "For these exact protocol variants, identify retained/shared "
                        "transformations, changed operations/interfaces, motivation, capability "
                        "and trade-offs. Read primary sources; the historical annotation is a "
                        "lead, not evidence. Separate local similarity from demonstrated "
                        "inheritance; "
                        "allow no supported match or unresolved details.",
                    }
                )
    return {
        "requests": requests,
        "total": len(requests),
        "boundary": "Recorded historical pairs with extracted versions. Existing local "
        "comparisons are leads to inspect, not automatic completion of every historical "
        "question. This worklist neither executes jobs nor proves variant-level inheritance. "
        "It is not exhaustive relationship discovery.",
    }
