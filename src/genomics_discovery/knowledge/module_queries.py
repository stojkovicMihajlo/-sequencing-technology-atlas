"""Inspect contextual mechanisms and state requirements without asserting transfer.

Explicit concept annotations are indexed; words/reagents do not create membership.
Literal property comparisons expose questions for source review, not chemical
compatibility. Different module partitions remain distinct contextual records.
"""

import copy


def _module(profile, module_id):
    for index, component in enumerate(profile.get("components", [])):
        if component.get("source_module_id") == module_id:
            return component.get("source_module", component), index
    raise ValueError("Unknown source module ID: " + module_id)


def mechanism_uses(profiles, mechanism_id, snapshot_sha256):
    rows, definitions = [], set()
    for pid, profile in profiles.items():
        for index, component in enumerate(profile.get("components", [])):
            module = component.get("source_module", component)
            for mi, annotation in enumerate(module.get("mechanisms", [])):
                if annotation["mechanism_id"] != mechanism_id:
                    continue
                definitions.add(annotation["definition"])
                rows.append({
                    "profile_id": pid, "method_ids": profile.get("method_ids", []),
                    "module_id": component.get("source_module_id"),
                    "name": annotation["name"], "definition": annotation["definition"],
                    "role": annotation["role"], "evidence_status": annotation["evidence_status"],
                    "verification": copy.deepcopy(profile.get("verification")),
                    "annotation_ref": {"profile_id": pid, "snapshot_sha256": snapshot_sha256,
                        "json_pointer": f"/components/{index}/source_module/mechanisms/{mi}"},
                })
    return rows, {
        "mechanism_id": mechanism_id,
        "identity_status": "definition_variants_require_review" if len(definitions) > 1 else (
            "consistent_recorded_definition" if definitions else "not_curated"),
        "definition_variants": sorted(definitions),
        "compatibility": "not_assessed",
        "scope": "Explicit contextual annotations; same concept is not interchangeable use "
                 "or evidence of historical borrowing. Different wording requires semantic "
                 "review; it does not by itself establish conflicting mechanisms.",
    }


def inspect_interface(left, right, module_id, other_module_id, snapshot_sha256):
    producer, pi = _module(left, module_id)
    consumer, ci = _module(right, other_module_id)
    output = producer.get("output_state", producer.get("output_state_detail")) or {}
    input_state = consumer.get("input_state", consumer.get("input_state_detail")) or {}
    if not isinstance(output, dict) or not isinstance(input_state, dict):
        raise ValueError("Choose source-scoped modules with structured states")
    produced = {r["property"]: r for r in output.get("properties", [])}
    requirements = consumer.get("input_requirements", [])
    rows = []
    # Canonical JSON equality preserves representations; no aliases, conversions,
    # truthy missing values, or inferred biochemical entailment are introduced.
    from genomics_discovery.curation.validation import canonical_hash
    for requirement in requirements:
        actual = produced.get(requirement["property"])
        known = (actual is not None and actual.get("evidence_status") == "reported"
                 and requirement.get("evidence_status") == "reported")
        assessment = "unknown_or_inferred"
        if known:
            assessment = "same_recorded_value" if canonical_hash(actual["value"]) == (
                canonical_hash(requirement["required_value"])) else "different_recorded_value"
        rows.append({"property": requirement["property"], "recorded_output": actual,
                     "consumer_requirement": requirement, "assessment": assessment})
    refs = [{"profile_id": p["profile_id"], "snapshot_sha256": snapshot_sha256,
             "json_pointer": f"/components/{i}/source_module"}
            for p, i in ((left, pi), (right, ci))]
    return {
        "producer": {"module_id": module_id, "output_state": output, "record_ref": refs[0]},
        "consumer": {"module_id": other_module_id, "input_state": input_state,
                     "record_ref": refs[1]},
        "requirements": rows, "compatibility": "not_assessed",
        "scope": "Recorded input state is not a necessary requirement. Literal matches do "
                 "not establish transfer; differences require contextual source inspection. "
                 "No requirements means none recorded, not absence of restrictions.",
    }
