"""Build inspectable component views without deriving new historical claims.

Summary items are provisional blocks, not independently validated reactions.
Detailed source-scoped profiles preserve the curator's topology and versions.
"""

import copy
import hashlib
import json


def digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ref(source_id, meta, locator):
    return {
        "source_id": source_id,
        "source_title": meta.get("title"),
        "source_url": meta.get("primary_url"),
        "inspected_url": meta.get("inspected_url", meta.get("primary_url")),
        "locator": locator,
        "local_evidence": copy.deepcopy(meta.get("local_evidence")),
        "source_sha256": copy.deepcopy(meta.get("sha256")),
        "source_version": copy.deepcopy(meta.get("source_version", meta.get("version"))),
        "scope": "Source-located summary; boundary/state descriptions are curator interpretations.",
        "access_limits": copy.deepcopy(meta.get("access_limits")),
    }


def _state_display(state):
    if not state:
        return None
    if state.get("description"):
        return state["description"]
    attributes = state.get("attributes", {})
    return "; ".join(f"{key}: {value}" for key, value in attributes.items()) or None


def _summary(method):
    method_id = method["id"]
    components = []
    for position, action in enumerate(method.get("workflow", [])):
        components.append(
            {
                "id": "component:" + digest([method_id, position, action])[:20],
                "label": action,
                "description": action,
                "position_in_summary": position + 1,
                "input_state": None,
                "output_state": None,
                "information_preserved": [],
                "function_tags": [],
                "source_refs": [
                    {
                        "source_id": "method_profile:" + method_id,
                        "source_url": None,
                        "locator": f"method:{method_id}/workflow/{position}",
                        "annotation_sha256": digest(action),
                        "scope": (
                            "Existing curated workflow annotation, not an original paper passage."
                        ),
                    }
                ],
                "paper_leads": list(method.get("paper_ids", [])),
                "evidence_status": "curator_profile_annotation",
                "boundary_status": "provisional_summary_block",
                "unknowns": [
                    "Per-block input/output properties, conditions and information "
                    "effects uncurated.",
                    "Summary order does not establish a material dependency or "
                    "compatible interface.",
                ],
            }
        )
    connections = [
        {
            "id": "connection:" + digest([a["id"], b["id"], "next_in_summary"])[:20],
            "source": a["id"],
            "target": b["id"],
            "relation": "next_in_summary",
            "status": "curator_summary_order",
            "basis": "Presentation order in the existing workflow summary; no compatibility claim.",
        }
        for a, b in zip(components, components[1:], strict=False)
    ]
    return {
        "id": "chain:" + method_id,
        "method_id": method_id,
        "method_ids": [method_id],
        "method_name": method["name"],
        "profile_id": "overview:" + method_id,
        "representation": "overview_component_chain",
        "representation_status": "overview_component_chain",
        "scope": (
            "Provisional component blocks from existing method summaries; "
            "not a decomposed bench protocol."
        ),
        "source_profile_ids": [],
        "available_representations": ["overview:" + method_id],
        "biological_input": method.get("biological_input"),
        "measurement": method.get("measurement"),
        "catalogue_source_records": copy.deepcopy(method.get("source_records", [])),
        "legacy_method_review_status": method.get("review_status"),
        "components": components,
        "connections": connections,
        "source_topology": {"summary_order": [c["id"] for c in components]},
        "unknowns": [
            "Primary methods/supplements and component boundaries require source-specific review."
        ],
    }


def _profile(profile, sources, revisions):
    pid = profile["profile_id"]
    metadata = copy.deepcopy(profile.get("profile_metadata", {}))
    component_ids = {}
    components = []
    for module in profile["modules"]:
        mid = module["module_id"]
        if mid in component_ids:
            raise ValueError("Duplicate profile module ID: " + mid)
        cid = "component:" + digest([pid, mid])[:20]
        component_ids[mid] = cid
        refs = []
        for ref in module.get("source_refs", []):
            if ref["source_id"] not in sources:
                raise ValueError("Unknown component source: " + ref["source_id"])
            refs.append(_ref(ref["source_id"], sources[ref["source_id"]], ref.get("locator")))
        components.append(
            {
                "id": cid,
                "source_module_id": mid,
                # The indexed view is a projection. Retain the complete original so
                # future/open fields and reported absence survive graph export.
                "source_module": copy.deepcopy(module),
                "label": module["operation_description"],
                "description": module["operation_description"],
                "purpose": module.get("purpose"),
                "boundary_rationale": module.get("boundary_rationale"),
                "steps": copy.deepcopy(module.get("steps", [])),
                "materials": copy.deepcopy(module.get("materials", [])),
                "equipment": copy.deepcopy(module.get("equipment", [])),
                "parameters": copy.deepcopy(module.get("parameters", [])),
                "interfaces": copy.deepcopy(module.get("interfaces", [])),
                "dependencies_and_interfaces": copy.deepcopy(
                    module.get("dependencies_and_interfaces")
                ),
                "information_effect": copy.deepcopy(module.get("information_effect")),
                "input_state": _state_display(module.get("input_state")),
                "output_state": _state_display(module.get("output_state")),
                "input_state_detail": copy.deepcopy(module.get("input_state")),
                "output_state_detail": copy.deepcopy(module.get("output_state")),
                **{key: copy.deepcopy(module[key]) for key in (
                    "input_requirements", "mechanisms", "operation_concepts")
                   if key in module},
                "state_scope": (
                    "Source-scoped curator description; property completeness not established."
                ),
                "function_tags": list(module.get("function_tags", [])),
                "information_preserved": copy.deepcopy(module.get("information_preserved", [])),
                "source_refs": refs,
                "evidence_status": (
                    "independently_source_checked_module"
                    if (profile.get("verification") or {}).get("outcome")
                    in ("accepted", "accepted_with_explicit_gaps")
                    else "source_linked_operation_summary"
                ),
                "boundary_status": "curator_conceptual_boundary",
                "evidence_scope": module.get("evidence_scope"),
                "unknowns": copy.deepcopy(module.get("unknowns", [])),
            }
        )
    topology = copy.deepcopy(profile.get("topology", {}))
    # Evidence may support an interface/edge or a nested parameter rather than
    # appear in a module's top-level source_refs. Keep those bindings as well.
    referenced_source_ids = set()

    def collect_source_ids(value):
        if isinstance(value, dict):
            if isinstance(value.get("source_id"), str):
                referenced_source_ids.add(value["source_id"])
            for nested in value.values():
                collect_source_ids(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_source_ids(nested)

    collect_source_ids(profile)
    used_source_ids = sorted(referenced_source_ids)
    if missing_sources := referenced_source_ids - sources.keys():
        raise ValueError("Unknown profile evidence sources: " + ", ".join(sorted(missing_sources)))
    raw_edges = []
    for field in ("edges", "connections", "control_edges"):
        raw_edges.extend(topology.get(field, []))
    connections = []
    for edge in raw_edges:
        source, target = edge.get("source", edge.get("from")), edge.get("target", edge.get("to"))
        if source not in component_ids or target not in component_ids:
            raise ValueError(f"Unknown topology endpoint in {pid}: {source} -> {target}")
        relation = edge.get("relation", edge.get("kind", edge.get("type", "unclassified")))
        revision = next(
            (
                r
                for r in revisions
                if r["profile_id"] == pid and r["source"] == source and r["target"] == target
            ),
            None,
        )
        if revision:
            relation = revision["relation"]
        record = {
            "id": "connection:" + digest([pid, source, target, relation, edge])[:20],
            "source": component_ids[source],
            "target": component_ids[target],
            "source_module_id": source,
            "target_module_id": target,
            "relation": relation,
            "status": "source_scoped_curator_topology",
            "basis": (revision or edge).get(
                "basis",
                "Explicit connection in the source-scoped profile; "
                "not independently certified compatibility.",
            ),
            "condition": edge.get("condition"),
            "source_edge": copy.deepcopy(edge),
        }
        if revision:
            record["normalization_review"] = copy.deepcopy(revision)
        connections.append(record)
    # default_order remains a presentation/ordering annotation. It is NOT
    # expanded into implicit material edges, even for apparently linear assays.
    return {
        "id": "profile:" + pid,
        "profile_id": pid,
        "method_id": None,
        "method_ids": list(profile.get("method_ids", [])),
        "method_name": metadata.get("name", pid),
        "representation": "source_scoped_component_profile",
        "representation_status": "source_scoped_component_profile",
        "scope": profile.get(
            "status", "source_scoped_conceptual_workflow_profile_not_complete_recipe"
        ),
        "source_profile_ids": [pid],
        "available_representations": [pid],
        "external_precedent_id": profile.get("external_precedent_id"),
        "profile_metadata": metadata,
        "function_coverage": copy.deepcopy(profile.get("function_coverage", [])),
        "verification": copy.deepcopy(profile.get("verification")),
        "prompt_provenance": copy.deepcopy(profile.get("prompt_provenance")),
        "source_inspection_scopes": {
            sid: copy.deepcopy(sources[sid].get("sections_inspected")) for sid in used_source_ids
        },
        "source_registry": {sid: copy.deepcopy(sources[sid]) for sid in used_source_ids},
        "components": components,
        "connections": connections,
        "source_topology": topology,
        "unknowns": copy.deepcopy(profile.get("unknowns", [])),
    }


def validate_component_layer(layer, method_ids):
    errors = []
    chains = layer.get("component_chains", [])
    mids = [c.get("method_id") for c in chains]
    if len(mids) != len(set(mids)) or set(mids) != set(method_ids):
        errors.append("Component chains must cover every method exactly once.")
    profiles = layer.get("component_profiles", [])
    if len({p["profile_id"] for p in profiles}) != len(profiles):
        errors.append("Duplicate component profile ID.")
    for chain in chains + profiles:
        components = chain.get("components", [])
        ids = [c.get("id") for c in components]
        if len(ids) != len(set(ids)):
            errors.append("Duplicate component ID in " + chain["id"])
        for edge in chain.get("connections", []):
            if edge.get("source") not in ids or edge.get("target") not in ids:
                errors.append("Unknown component connection endpoint in " + chain["id"])
        for component in components:
            if not component.get("description") or not component.get("source_refs"):
                errors.append(
                    "Component needs a description and provenance: " + str(component.get("id"))
                )
        if chain["representation_status"] == "overview_component_chain":
            if any(
                c.get("input_state") is not None or c.get("output_state") is not None
                for c in components
            ):
                errors.append("Summary ports must remain unknown in " + chain["id"])
            if any(e["relation"] != "next_in_summary" for e in chain.get("connections", [])):
                errors.append("Summary sequence is not a physical dependency in " + chain["id"])
    return errors
