"""Stable-ID repair audit; unchanged claims are not thereby scientifically supported."""

from .boundaries import audit_boundaries
from .models import Workflow, digest

COLLECTIONS = ("states", "operations", "flows", "modules", "unresolved", "gap_resolutions")
_MISSING = object()


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def normalized_workflow(workflow: Workflow) -> dict:
    """Index record collections by stable identity, preserving nested values exactly.

    JSON Pointers in the report address this view, not positional arrays in the
    original file. Resolution identity is ``reader_role:gap_id``; module names,
    record positions and free-text descriptions are never used as identity keys.
    """
    data = workflow.model_dump(mode="json")
    for collection in COLLECTIONS:
        data[collection] = {
            (f"{item['reader_role']}:{item['gap_id']}" if collection == "gap_resolutions"
             else item["id"]): item for item in data[collection]}
    return data


def _value_ref(value, workflow_sha256, pointer):
    return {"workflow_sha256": workflow_sha256, "pointer": pointer,
            "view": ("boundary_audit_by_state_id" if pointer.startswith("/boundaries/")
                     else "normalized_workflow"),
            "value_sha256": digest(value)}


def _changes(before, after, pointer, before_sha256, after_sha256):
    if before is not _MISSING and after is not _MISSING:
        if type(before) is type(after) and before == after:
            return []
        if isinstance(before, dict) and isinstance(after, dict):
            return [change for key in sorted(before.keys() | after.keys())
                    for change in _changes(before.get(key, _MISSING), after.get(key, _MISSING),
                        pointer + "/" + _escape(key), before_sha256, after_sha256)]
        if isinstance(before, list) and isinstance(after, list):
            return [change for index in range(max(len(before), len(after)))
                    for change in _changes(before[index] if index < len(before) else _MISSING,
                        after[index] if index < len(after) else _MISSING,
                        pointer + "/" + str(index), before_sha256, after_sha256)]
    change = {"pointer": pointer,
              "change": "added" if before is _MISSING else
                        "removed" if after is _MISSING else "replaced"}
    for name, value, pin in (("old", before, before_sha256), ("new", after, after_sha256)):
        if value is not _MISSING:
            change[name + "_ref"] = _value_ref(value, pin, pointer)
            # Added/removed compound values remain exactly recoverable by reference;
            # do not copy whole evidence arrays or resource objects into this report.
            if not isinstance(value, (dict, list)):
                change[name + "_value"] = value
    return [change]


def _record_delta(before, after, prefix, before_pin, after_pin):
    delta = {"added": [], "removed": [], "changed": [], "unchanged_count": 0}
    for ident in sorted(before.keys() | after.keys()):
        old, new = before.get(ident, _MISSING), after.get(ident, _MISSING)
        pointer = prefix + "/" + _escape(ident)
        if old is _MISSING or new is _MISSING:
            kind = "added" if old is _MISSING else "removed"
            value, pin = (new, after_pin) if old is _MISSING else (old, before_pin)
            delta[kind].append({"record_id": ident, "record_ref": _value_ref(value, pin, pointer)})
        elif old != new:
            delta["changed"].append({
                "record_id": ident, "old_record_sha256": digest(old),
                "new_record_sha256": digest(new),
                "value_changes": _changes(old, new, pointer, before_pin, after_pin)})
        else:
            delta["unchanged_count"] += 1
    return delta


def diff_workflows(before: Workflow, after: Workflow) -> dict:
    """Compare exact record values without an edit whitelist or scientific judgment.

    Top-level record-array reordering is ignored for change discovery. Original
    Workflow pins still record it. Nested array order is preserved and compared
    by position; this report does not infer semantic equivalence or rename IDs.
    Boundary pointers address the corresponding ``audit_boundaries`` report with
    its ``boundaries`` list indexed by State ID, not the normalized Workflow view.
    """
    old, new = normalized_workflow(before), normalized_workflow(after)
    old_pin, new_pin = digest(before), digest(after)
    records = {name: _record_delta(old[name], new[name], "/" + name, old_pin, new_pin)
               for name in COLLECTIONS}
    old_metadata = {key: value for key, value in old.items() if key not in COLLECTIONS}
    new_metadata = {key: value for key, value in new.items() if key not in COLLECTIONS}
    old_boundaries, new_boundaries = audit_boundaries(before), audit_boundaries(after)
    boundaries = _record_delta(
        {item["state_id"]: item for item in old_boundaries["boundaries"]},
        {item["state_id"]: item for item in new_boundaries["boundaries"]},
        "/boundaries", old_pin, new_pin)
    return {
        "format_version": "workflow_changes/1.0",
        "before_workflow_sha256": old_pin, "after_workflow_sha256": new_pin,
        "before_normalized_sha256": digest(old), "after_normalized_sha256": digest(new),
        "stable_record_content_changed": old != new,
        "metadata_changes": _changes(old_metadata, new_metadata, "", old_pin, new_pin),
        "records": records,
        "boundary_inventory_delta": {"before_counts": old_boundaries["counts"],
                                     "after_counts": new_boundaries["counts"], **boundaries},
        "pointer_space": "RFC 6901 pointers into normalized_workflow: record collections "
                         "keyed by ID; gap_resolutions keyed by reader_role:gap_id. Boundary "
                         "delta pointers address audit_boundaries indexed by State ID.",
        "boundary": "Exact structural/value changes only. No edit is approved or classified "
                    "scientifically; unchanged records are not independently validated. "
                    "No unrelated-edit whitelist or semantic ID matching was imposed.",
    }
