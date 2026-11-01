"""Cheap, auditable method-pair shortlists. Nothing here accepts a relationship.

Reference-context leads, explicit concepts and description overlap are separate
retrieval reasons. Common concepts are downweighted for ranking, never erased.
Direction, transfer, historical dependence and novelty require source review.
"""

import math
from collections import Counter, defaultdict
from pathlib import Path


def references_from_source_plan(plan: dict, plan_path: Path, plan_sha256: str) -> list[dict]:
    """Retain XML citation leads and their unreconciled method association explicitly."""
    if plan.get("format_version") != "curation_source_preparation_plan/1.0":
        raise ValueError("Unsupported source-preparation plan")
    documents = {d["sha256"]: d for d in plan["xml_documents"]}
    result, seen = [], set()
    for gi, group in enumerate(plan["dependency_candidate_groups"]):
        for oi, occurrence in enumerate(group["occurrences"]):
            if occurrence["identity_status"] != "embedded_identity_matches_inventory":
                continue
            for mid in group["method_ids"]:
                for pid in group["inventory_paper_ids"]:
                    key = (mid, pid, occurrence["xml_sha256"], occurrence["xref_locator"])
                    if key in seen:
                        continue
                    seen.add(key)
                    doc = documents[occurrence["xml_sha256"]]
                    result.append({
                        "anchor_method_id": mid, "cited_paper_ids": [pid],
                        "anchor_association_status": "metadata_lead_requires_implementation_audit",
                        "source_file": doc["files"][0]["path"],
                        "source_sha256": doc["sha256"],
                        "xref_locator": occurrence["xref_locator"],
                        "reference_locator": occurrence["reference_locator"],
                        "source_plan_ref": {"path": str(plan_path.resolve()),
                            "sha256": plan_sha256,
                            "json_pointer": f"/dependency_candidate_groups/{gi}/occurrences/{oi}"},
                        "dependency_status": "candidate_requires_procedure_dependency_audit",
                    })
    return result


def suggest_candidates(graph, method_id: str):
    methods = graph._methods
    memberships = graph._operation_concepts.method_memberships()
    concept_frequency = Counter(cid for uses in memberships.values() for cid in uses)
    annotated_method_count = len(memberships)
    group_methods = defaultdict(set)
    for gid in graph._operation_groups.groups:
        for use in graph._operation_groups.uses(gid, graph._operation_concepts):
            group_methods[gid].update(use["method_ids"])
    anchor_concepts = memberships.get(method_id, {})
    anchor_papers = set(methods[method_id].get("paper_ids", [])) | set(
        methods[method_id].get("origin_paper_ids", []))
    anchor_terms = graph._field_terms[method_id]
    terms = set().union(*(anchor_terms[k] for k in (
        "measurement", "mechanism", "innovation", "workflow")))
    citations = graph._data.get("relation_candidate_citations", [])
    result = []
    for other, method in methods.items():
        if other == method_id:
            continue
        reasons = []
        other_papers = set(method.get("paper_ids", [])) | set(method.get("origin_paper_ids", []))
        for citation in citations:
            source = citation.get("anchor_method_id")
            targets = set(citation.get("cited_paper_ids", []))
            if ((source == method_id and targets & other_papers)
                    or (source == other and targets & anchor_papers)):
                reasons.append({"kind": "procedure_reference_candidate",
                                "record": citation, "dependency_status": "not_assessed"})
        shared = sorted(set(anchor_concepts) & set(memberships.get(other, {})))
        for gid, method_ids in group_methods.items():
            if {method_id, other} <= method_ids:
                group = graph._operation_groups.groups[gid]
                reasons.append({"kind": "shared_operation_group", "group_id": gid,
                                "name": group["name"], "classification_kind": group["kind"],
                                "scope": "Reviewed collection membership; neither identical "
                                         "mechanisms nor historical or physical compatibility"})
        rarity = 0.0
        for cid in shared:
            concept = graph._operation_concepts.concepts[cid]
            rows = [*anchor_concepts[cid], *memberships[other][cid]]
            statuses = sorted({u["concept_review"].get("outcome") for u in rows})
            reasons.append({
                "kind": "shared_operation_concept", "concept_id": cid,
                "identity_status": concept["identity_status"], "review_outcomes": statuses,
                "anchor_modules": sorted({u["module_id"] for u in anchor_concepts[cid]}),
                "other_modules": sorted({u["module_id"] for u in memberships[other][cid]}),
            })
            if concept["identity_status"] == "consistent_recorded_definition":
                # Ranking only: common actions still remain discoverable through
                # concept lookup and candidate pagination. This is not a confidence.
                rarity += math.log((annotated_method_count + 1) / (concept_frequency[cid] + 1))
        if overlap := sorted(anchor_papers & other_papers):
            reasons.append({"kind": "shared_recorded_paper", "paper_ids": overlap,
                            "scope": "Shared bibliography or source association, not inheritance"})
        other_terms = graph._field_terms[other]
        b = set().union(*(other_terms[k] for k in (
            "measurement", "mechanism", "innovation", "workflow")))
        matched = terms & b
        lexical = len(matched) / len(terms | b) if terms | b else 0.0
        if matched:
            reasons.append({"kind": "description_overlap", "jaccard": round(lexical, 6),
                            "matched_terms": sorted(matched)})
        if not reasons:
            continue
        result.append({
            "anchor_method_id": method_id, "other_method_id": other,
            "other_method_name": method.get("name"), "status": "unclassified_candidate",
            "historical_direction": "not_assessed", "compatibility": "not_assessed",
            "novelty": "not_assessed", "retrieval_reasons": reasons,
            "ranking": {"reference_context_leads": sum(
                r["kind"] == "procedure_reference_candidate" for r in reasons),
                "shared_concept_rarity": round(rarity, 6), "description_overlap": lexical,
                "annotated_method_count": annotated_method_count,
                "corpus_method_count": len(methods),
                "shared_reviewed_groups": sum(r["kind"] == "shared_operation_group"
                                              for r in reasons),
                "frequency_scope": "Recorded non-rejected memberships; coverage is incomplete",
                "scope": "Retrieval priority, not an evidence confidence or relationship verdict"},
        })
    return sorted(result, key=lambda r: (
        -r["ranking"]["reference_context_leads"], -r["ranking"]["shared_concept_rarity"],
        -bool(r["ranking"]["shared_reviewed_groups"]),
        -r["ranking"]["description_overlap"], r["other_method_id"]))
