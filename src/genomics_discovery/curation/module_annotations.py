"""Optional source-bound properties, requirements and mechanism annotations.

These describe a contextual published use. A recorded input is not automatically
a necessary requirement; a named mechanism is not an interchangeable primitive.
Open property names avoid imposing one molecular architecture on every assay.
"""

import re

from .validation import _refs, _require, _text

EVIDENCE_STATUSES = {"reported", "inferred", "unknown", "not_applicable"}
CONCEPT_KINDS = {"operation", "mechanism", "function"}


def validate_operation_concepts(rows: list, sources: dict, label: str) -> None:
    """Validate explicit contextual membership, not semantic equivalence or transfer."""
    _require(isinstance(rows, list) and len(rows) <= 32, f"Unbounded {label}")
    seen = set()
    for row in rows:
        _require(isinstance(row, dict), f"Invalid {label} annotation")
        identifier = row.get("concept_id")
        _require(isinstance(identifier, str) and len(identifier) <= 240
                 and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", identifier) and identifier not in seen,
                 f"Invalid/duplicate {label}.concept_id")
        seen.add(identifier)
        _require(_text(row.get("kind")) and row["kind"] in CONCEPT_KINDS,
                 f"Invalid {label}.kind")
        for key in ("name", "definition", "role"):
            _require(_text(row.get(key)), f"Missing {label}.{key}")
        status = row.get("evidence_status")
        _require(_text(status) and status in {"reported", "inferred"},
                 f"Unestablished operation concept belongs in module unknowns: {label}")
        _refs(row.get("source_refs"), sources, label)
        if status == "inferred":
            _require(_text(row.get("reason")), f"Inferred {label} needs reasoning")


def _properties(rows, sources, label, *, value_key="value"):
    _require(isinstance(rows, list) and len(rows) <= 64, f"Unbounded {label}")
    seen = set()
    for row in rows:
        _require(isinstance(row, dict) and _text(row.get("property")), f"Invalid {label} property")
        name = row["property"]
        _require(name not in seen, f"Duplicate {label} property: {name}")
        seen.add(name)
        status = row.get("evidence_status")
        _require(_text(status) and status in EVIDENCE_STATUSES, f"Invalid {label} evidence status")
        _require(value_key in row, f"Missing {label}.{value_key}")
        if status in {"reported", "inferred"}:
            _require(row[value_key] is not None, f"Known {label} property has null value")
            _refs(row.get("source_refs"), sources, label)
        else:
            _require(row[value_key] is None and _text(row.get("reason")),
                     f"Unknown/inapplicable {label} needs null value and reason")
        if status == "inferred":
            _require(_text(row.get("reason")), f"Inferred {label} needs reasoning")


def validate_module_annotations(module: dict, sources: dict) -> None:
    mid = module["module_id"]
    for side in ("input_state", "output_state"):
        if "properties" in module[side]:
            _properties(module[side]["properties"], sources, f"{mid}.{side}")
    if "input_requirements" in module:
        _properties(module["input_requirements"], sources, f"{mid}.input_requirements",
                    value_key="required_value")
    if "operation_concepts" in module:
        validate_operation_concepts(module["operation_concepts"], sources,
                                    f"{mid}.operation_concepts")
    if "mechanisms" not in module:
        return
    rows = module["mechanisms"]
    _require(isinstance(rows, list) and len(rows) <= 32, f"Unbounded {mid}.mechanisms")
    seen = set()
    for row in rows:
        _require(isinstance(row, dict), f"Invalid {mid}.mechanism")
        identifier = row.get("mechanism_id")
        _require(isinstance(identifier, str) and len(identifier) <= 240
                 and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", identifier) and identifier not in seen,
                 f"Invalid/duplicate {mid}.mechanism_id")
        seen.add(identifier)
        for key in ("name", "definition", "role"):
            _require(_text(row.get(key)), f"Missing {mid}.mechanism.{key}")
        _require(row.get("evidence_status") in {"reported", "inferred"},
                 f"Unestablished mechanism belongs in {mid}.unknowns")
        _refs(row.get("source_refs"), sources, f"{mid}.mechanism")
        if row["evidence_status"] == "inferred":
            _require(_text(row.get("reason")), f"Inferred {mid}.mechanism needs reasoning")
