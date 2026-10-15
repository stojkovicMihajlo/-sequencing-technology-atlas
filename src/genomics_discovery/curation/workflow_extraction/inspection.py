"""Lossless reading view: display repeated evidence once, without summarizing claims."""

from __future__ import annotations

import json
from copy import deepcopy

from .models import Workflow, digest


def inspection_view(workflow: Workflow) -> dict:
    """Replace evidence occurrences by references to their exact original objects.

    This is a display encoding of an unaccepted or accepted candidate, not a new
    scientific record. All text, list order, resources, topology and uncertainty
    remain present. Equal quotes with different rationale/support remain distinct.
    """
    registry: dict[str, dict] = {}
    identities: dict[str, str] = {}

    def encode(value):
        if isinstance(value, list):
            return [encode(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "evidence":
                references = []
                for evidence in item:
                    identity = json.dumps(evidence, sort_keys=True, ensure_ascii=False)
                    if identity not in identities:
                        eid = f"e{len(registry) + 1:04d}"
                        identities[identity] = eid
                        registry[eid] = deepcopy(evidence)
                    references.append(identities[identity])
                result["evidence_refs"] = references
            else:
                result[key] = encode(item)
        return result

    return {
        "format_version": "workflow_inspection/1.0",
        "workflow_sha256": digest(workflow),
        "candidate": encode(workflow.model_dump(mode="json")),
        "evidence_registry": registry,
        "boundary": "Lossless candidate display; no source entailment or acceptance judgment.",
    }


def expand_inspection(view: dict) -> Workflow:
    """Recover the exact scientific object and reject altered or missing evidence."""
    if view.get("format_version") != "workflow_inspection/1.0":
        raise ValueError("Unknown workflow inspection format")
    registry = view["evidence_registry"]
    used = set()

    def decode(value):
        if isinstance(value, list):
            return [decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "evidence_refs":
                if "evidence" in value:
                    raise ValueError("Inspection contains conflicting evidence representations")
                try:
                    result["evidence"] = [deepcopy(registry[eid]) for eid in item]
                    used.update(item)
                except (KeyError, TypeError) as exc:
                    raise ValueError("Inspection has an unknown evidence reference") from exc
            else:
                result[key] = decode(item)
        return result

    workflow = Workflow.model_validate(decode(view["candidate"]))
    if used != registry.keys():
        raise ValueError("Inspection contains unreferenced evidence")
    if digest(workflow) != view["workflow_sha256"]:
        raise ValueError("Inspection differs from its bound workflow")
    return workflow
