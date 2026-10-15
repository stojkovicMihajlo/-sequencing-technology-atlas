"""Explicit, contextual operation-concept membership; no inferred ancestry edges.

Modules may have many concepts. Open definitions preserve different abstraction
levels; a broad function never becomes an equivalent mechanism. Post-extraction
annotations bind to unchanged profiles so new indexing need not rewrite protocols.
"""

from __future__ import annotations

import copy
import re
from collections import defaultdict

from genomics_discovery.curation.module_annotations import validate_operation_concepts
from genomics_discovery.curation.validation import canonical_hash

ANNOTATION_FORMAT = "operation_concept_annotations/1.0"


def _state_summary(state):
    if state is None:
        return None
    description = state.get("description") if isinstance(state, dict) else str(state)
    return {"description": description[:1000] if isinstance(description, str) else None,
            "description_truncated": isinstance(description, str) and len(description) > 1000,
            "details_require_module_lookup": True}


def source_module(component):
    return component.get("source_module", component)


class OperationConceptIndex:
    def __init__(self, data: dict):
        self.uses, self.concepts = [], {}
        self.unavailable_modules = []
        profiles = {p["profile_id"]: p for p in data.get("component_profiles", [])}
        sources = data.get("component_sources", {})
        for pid, profile in profiles.items():
            for ci, component in enumerate(profile.get("components", [])):
                module = source_module(component)
                if module is None:
                    self.unavailable_modules.append({
                        "profile_id": pid, "module_id": component.get("source_module_id")})
                    continue
                if not isinstance(module, dict):
                    raise ValueError("Invalid source module for operation-concept indexing")
                rows = module.get("operation_concepts", [])
                if rows:
                    validate_operation_concepts(rows, sources, f"{pid}.operation_concepts")
                pointer = f"/components/{ci}"
                if "source_module" in component:
                    pointer += "/source_module"
                for ai, annotation in enumerate(rows):
                    self._add(profile, component, ci, annotation, {
                        "profile_id": pid, "json_pointer": f"{pointer}/operation_concepts/{ai}"},
                        {"outcome": "inspect_profile_review_coverage", "profile_verification":
                         copy.deepcopy(profile.get("verification"))})
                # Retain existing mechanism concepts as a compatibility view.
                # Function tags and material names do not create concept membership.
                for ai, mechanism in enumerate(module.get("mechanisms", [])):
                    annotation = {**mechanism, "concept_id": mechanism["mechanism_id"],
                                  "kind": "mechanism"}
                    self._add(profile, component, ci, annotation, {
                        "profile_id": pid, "json_pointer": f"{pointer}/mechanisms/{ai}"},
                        {"outcome": "inspect_profile_review_coverage", "profile_verification":
                         copy.deepcopy(profile.get("verification"))})
        packet = data.get("operation_concept_annotations")
        if packet is not None:
            base = {k: v for k, v in data.items()
                    if k not in {"operation_concept_annotations", "relation_candidate_citations",
                                 "operation_concept_groups"}}
            if (not isinstance(packet, dict) or not isinstance(packet.get("modules"), list)
                    or packet.get("format_version") != ANNOTATION_FORMAT
                    or packet.get("base_snapshot_sha256") != canonical_hash(base)):
                raise ValueError("Operation-concept packet is stale or has an invalid format")
            seen = set()
            for pi, entry in enumerate(packet["modules"]):
                if not isinstance(entry, dict):
                    raise ValueError("Invalid operation-concept module entry")
                pid, mid = entry["profile_id"], entry["module_id"]
                if (pid, mid) in seen or pid not in profiles:
                    raise ValueError("Duplicate or unknown operation-concept module")
                seen.add((pid, mid))
                profile = profiles[pid]
                matches = [(i, c) for i, c in enumerate(profile.get("components", []))
                           if c.get("source_module_id") == mid]
                if len(matches) != 1:
                    raise ValueError("Operation-concept annotation needs one exact module")
                ci, component = matches[0]
                if source_module(component) is None:
                    raise ValueError("Concept annotation targets an unavailable source module")
                if (entry.get("profile_sha256") != canonical_hash(profile)
                        or entry.get("module_sha256") != canonical_hash(source_module(component))):
                    raise ValueError("Concept annotation targets a changed profile/module")
                rows = entry["operation_concepts"]
                validate_operation_concepts(rows, sources, f"{pid}/{mid}.operation_concepts")
                reviews = entry.get("annotation_reviews", {})
                if (not isinstance(reviews, dict)
                        or set(reviews) - {a["concept_id"] for a in rows}):
                    raise ValueError("Concept review targets an unknown annotation")
                for ai, annotation in enumerate(rows):
                    review = reviews.get(annotation["concept_id"], {"outcome": "awaiting_review"})
                    if not isinstance(review, dict) or review.get("outcome") not in {
                        "supported", "unsupported", "not_inspected", "awaiting_review"}:
                        raise ValueError("Invalid operation-concept review outcome")
                    if review["outcome"] != "awaiting_review":
                        if (review.get("annotation_sha256") != canonical_hash(annotation)
                                or not review.get("reason") or not review.get("reviewer")):
                            raise ValueError("Concept review is stale or lacks attribution/reason")
                    self._add(profile, component, ci, annotation, {
                        "json_pointer": f"/operation_concept_annotations/modules/{pi}"
                                        f"/operation_concepts/{ai}",
                        "base_snapshot_sha256": packet["base_snapshot_sha256"]}, review)
        for concept_id, concept in self.concepts.items():
            concept["definition_variants"] = sorted(concept["definition_variants"],
                                                     key=lambda v: (v["kind"], v["definition"]))
            concept["labels"] = sorted(concept["labels"])
            concept["method_ids"] = sorted(concept["method_ids"])
            concept["identity_status"] = (
                "consistent_recorded_definition" if len(concept["definition_variants"]) == 1
                else "definition_variants_require_review")
            concept["use_count"] = sum(u["concept_id"] == concept_id for u in self.uses)
            concept["compatibility"] = "not_assessed"

    def _add(self, profile, component, ci, annotation, annotation_ref, review):
        cid = annotation["concept_id"]
        concept = self.concepts.setdefault(cid, {
            "concept_id": cid, "labels": set(), "definition_variants": [], "method_ids": set()})
        definition = {"kind": annotation["kind"], "definition": annotation["definition"]}
        if definition not in concept["definition_variants"]:
            concept["definition_variants"].append(definition)
        concept["labels"].add(annotation["name"])
        concept["method_ids"].update(profile.get("method_ids", []))
        module = source_module(component)
        self.uses.append({
            **copy.deepcopy(annotation), "profile_id": profile["profile_id"],
            "method_ids": list(profile.get("method_ids", [])),
            "module_id": component.get("source_module_id"),
            "annotation_ref": annotation_ref,
            "module_ref": {"profile_id": profile["profile_id"],
                           "json_pointer": f"/components/{ci}"},
            "context": {"operation": module.get("operation_description", component.get("label")),
                        "input_state": _state_summary(module.get(
                            "input_state", component.get("input_state_detail"))),
                        "output_state": _state_summary(module.get(
                            "output_state", component.get("output_state_detail"))),
                        "implementation": copy.deepcopy(profile.get("scope"))},
            "concept_review": copy.deepcopy(review),
            "profile_verification": copy.deepcopy(profile.get("verification")),
        })

    def search(self, query: str):
        terms = set(re.findall(r"[a-z0-9]+", query.casefold()))
        result = []
        for concept in self.concepts.values():
            text = " ".join([concept["concept_id"], *concept["labels"],
                             *(v["definition"] for v in concept["definition_variants"])])
            matched = terms & set(re.findall(r"[a-z0-9]+", text.casefold()))
            if matched:
                result.append({**copy.deepcopy(concept), "matched_terms": sorted(matched)})
        return sorted(result, key=lambda r: (-len(r["matched_terms"]), r["concept_id"]))

    def find_uses(self, concept_id: str):
        return [copy.deepcopy(row) for row in self.uses if row["concept_id"] == concept_id]

    def method_memberships(self):
        result = defaultdict(lambda: defaultdict(list))
        for use in self.uses:
            if use["concept_review"].get("outcome") == "unsupported":
                continue
            for mid in use["method_ids"]:
                result[mid][use["concept_id"]].append(use)
        return result


def attach_concept_annotations(data: dict, packet: dict) -> dict:
    """Make a separate graph snapshot, checking bindings without rewriting protocols."""
    if "operation_concept_annotations" in data:
        raise ValueError("Start from the declared unannotated base snapshot")
    result = copy.deepcopy(data)
    result["operation_concept_annotations"] = copy.deepcopy(packet)
    OperationConceptIndex(result)
    return result
