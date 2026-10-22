"""Source-bound, structurally valid negative controls for the real reviewer.

These mutations deliberately violate source support or omit required content.
Their validity as JSON/Workflow objects is necessary to test semantic review,
not scientific acceptance. The expected findings are host-only and never belong
in the candidate supplied to the reviewer. Historical inputs are not modified.
"""

from __future__ import annotations

from copy import deepcopy

from .field_review import _resolve
from .models import Workflow, digest, validate_evidence


def _assign(data: dict, pointer: str, value) -> None:
    prefix, token = pointer.rsplit("/", 1)
    parent = _resolve(data, prefix)
    token = token.replace("~1", "/").replace("~0", "~")
    if isinstance(parent, list):
        parent[int(token)] = deepcopy(value)
    else:
        parent[token] = deepcopy(value)


def seed_workflow_controls(workflow: Workflow, packet: dict, definitions: list[dict],
                           selected_ids: list[str]) -> tuple[Workflow, dict]:
    """Apply declared controls, then independently recheck graph/quote integrity.

    Expected source judgments come from the independently audited definitions;
    this function does not establish them. Field mutations are applied before
    operation deletions so original JSON pointers cannot drift during authoring.
    Final pointers are recomputed by stable record ID for the host receipt.
    """
    if not selected_ids or len(selected_ids) != len(set(selected_ids)):
        raise ValueError("Select nonempty unique control IDs")
    known = {item["id"]: item for item in definitions}
    if len(known) != len(definitions) or set(selected_ids) - known.keys():
        raise ValueError("Duplicate definitions or unknown selected control")
    for key in ("method_id", "title", "scope", "variant"):
        if getattr(workflow, key) != packet[key]:
            raise ValueError("Workflow and packet scope differ")
    if errors := validate_evidence(workflow, packet):
        raise ValueError("; ".join(errors))
    original = workflow.model_dump(mode="json")
    data, mutations, deletions = deepcopy(original), [], []
    used_pointers = set()
    for ident in selected_ids:
        definition = known[ident]
        if definition["kind"] != "field_support":
            continue
        pointer = definition["field_pointer"]
        parts = pointer.split("/")
        if len(parts) < 4 or parts[1] not in {"states", "operations"} or parts[3] == "id":
            raise ValueError("Control fields must belong to an existing State or Operation, excluding its ID")
        if any(pointer == prior or pointer.startswith(prior + "/") or prior.startswith(pointer + "/")
               for prior in used_pointers):
            raise ValueError("Overlapping field mutations are not allowed")
        used_pointers.add(pointer)
        value = _resolve(original, pointer)
        if digest(value) != definition["original_value_sha256"]:
            raise ValueError("Original field hash differs from audited control")
        changed = definition["changed_value"]
        if digest(value) == digest(changed):
            raise ValueError("Control must change its field")
        record = original[parts[1]][int(parts[2])]
        _assign(data, pointer, changed)
        mutations.append({"definition_id": ident, "collection": parts[1], "record_id": record["id"],
            "target_id": ("state:" if parts[1] == "states" else "operation:") + record["id"],
            "original_field_pointer": pointer, "field_suffix": "/".join(parts[3:]),
            "original_value_sha256": digest(value), "candidate_value_sha256": digest(changed),
            "original_value": value, "candidate_value": changed,
            "expected_source_judgment": deepcopy(definition["changed_label"])})
    deleted = set()
    for ident in selected_ids:
        definition = known[ident]
        if definition["kind"] == "field_support":
            continue
        if definition["kind"] != "local_coverage":
            raise ValueError("Unknown control kind")
        operation_id = definition["omit_operation_id"]
        matches = [item for item in data["operations"] if item["id"] == operation_id]
        if len(matches) != 1 or operation_id in deleted:
            raise ValueError("Omitted operation must exist exactly once")
        if any(item["collection"] == "operations" and item["record_id"] == operation_id for item in mutations):
            raise ValueError("Cannot both modify and delete an operation")
        deleted.add(operation_id)
        removed_flows = [item for item in data["flows"]
                         if operation_id in (item["source"], item["target"])]
        data["operations"] = [item for item in data["operations"] if item["id"] != operation_id]
        data["flows"] = [item for item in data["flows"] if item not in removed_flows]
        membership_edits = []
        for module in data["modules"]:
            if operation_id in module["operation_ids"]:
                before = list(module["operation_ids"])
                module["operation_ids"] = [item for item in before if item != operation_id]
                membership_edits.append({"module_id": module["id"], "original_members": before,
                                         "candidate_members": list(module["operation_ids"])})
        data["modules"] = [module for module in data["modules"] if module["operation_ids"]]
        deletions.append({"definition_id": ident, "omitted_operation_id": operation_id,
            "original_operation_sha256": digest(matches[0]),
            "original_inputs": matches[0]["inputs"], "original_outputs": matches[0]["outputs"],
            "removed_flows": [{"id": item["id"], "sha256": digest(item)} for item in removed_flows],
            "module_membership_changes": membership_edits,
            "expected_missing_requirement": definition["requirement"],
            "expected_source_judgment": deepcopy(definition["changed_label"])})
    candidate = Workflow.model_validate(data)
    if errors := validate_evidence(candidate, packet):
        raise ValueError("; ".join(errors))
    final = candidate.model_dump(mode="json")
    if final["states"] != data["states"]:
        raise ValueError("Unexpected State mutation during model validation")
    for item in mutations:
        index = next(i for i, record in enumerate(final[item["collection"]])
                     if record["id"] == item["record_id"])
        pointer = f"/{item['collection']}/{index}/{item['field_suffix']}"
        if digest(_resolve(final, pointer)) != item["candidate_value_sha256"]:
            raise ValueError("Final changed field no longer matches its receipt")
        item["candidate_field_pointer"] = pointer
    return candidate, {"format_version": "workflow_mutation_controls/1.0",
        "original_workflow_sha256": digest(workflow), "candidate_workflow_sha256": digest(candidate),
        "source_packet_sha256": digest(packet), "definitions_sha256": digest(definitions),
        "selected_control_ids": list(selected_ids), "field_mutations": mutations,
        "operation_deletions": deletions, "structural_validation": "passed",
        "quote_integrity": "passed", "new_edges": [], "material_states_deleted": [],
        "boundary": "Deliberately flawed negative-control candidate, not an atlas record. Expected errors are host-only; structurally valid does not mean source-supported."}
