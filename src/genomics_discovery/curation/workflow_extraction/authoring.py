"""Editable, lossless evidence encoding; expansion does not establish source support."""

from __future__ import annotations

import json
from copy import deepcopy

from .models import Evidence, Workflow, WorkflowDraft, WorkflowReview

MODELS = {"workflow": Workflow, "draft": WorkflowDraft, "review": WorkflowReview}
FORMAT = "workflow_authoring/1.0"


def authoring_view(record: Workflow | WorkflowDraft | WorkflowReview) -> dict:
    """Retain bindings/route semantics; deduplicate only equal complete Evidence objects.

    An operation citation and a per-I/O citation remain separate authored claims,
    even when their Evidence objects happen to share a registry entry.
    """
    kind = next((kind for kind, model in MODELS.items() if isinstance(record, model)), None)
    if kind is None:
        raise ValueError("Authoring requires a workflow, draft or review")
    registry, identities = {}, {}

    def encode(value):
        if isinstance(value, list):
            return [encode(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            if key == "evidence":
                refs = []
                for evidence in item:
                    identity = json.dumps(evidence, sort_keys=True, ensure_ascii=False)
                    if identity not in identities:
                        eid = f"e{len(registry) + 1:04d}"
                        identities[identity] = eid
                        registry[eid] = deepcopy(evidence)
                    refs.append(identities[identity])
                result["evidence_refs"] = refs
            else:
                result[key] = encode(item)
        return result

    return {"format_version": FORMAT, "record_kind": kind,
            "record": encode(record.model_dump(mode="json")), "evidence_registry": registry}


def expand_authoring(view: dict) -> Workflow | WorkflowDraft | WorkflowReview:
    """Recover the existing strict model; no digest, review or verdict is inherited."""
    if (set(view) != {"format_version", "record_kind", "record", "evidence_registry"}
            or view["format_version"] != FORMAT or view["record_kind"] not in MODELS):
        raise ValueError("Invalid workflow authoring envelope")
    registry = view["evidence_registry"]
    if not isinstance(registry, dict) or any(not isinstance(k, str) or not k for k in registry):
        raise ValueError("Authoring evidence registry must have nonempty string IDs")
    registry = {key: Evidence.model_validate(value).model_dump(mode="json")
                for key, value in registry.items()}
    used = set()

    def decode(value):
        if isinstance(value, list):
            return [decode(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "evidence" in value:
            raise ValueError("Authoring uses evidence_refs; inline evidence is ambiguous")
        result = {}
        for key, item in value.items():
            if key == "evidence_refs":
                if not isinstance(item, list) or any(not isinstance(ref, str) for ref in item):
                    raise ValueError("Authoring evidence_refs must be a list of strings")
                if any(ref not in registry for ref in item):
                    raise ValueError("Unknown authoring evidence reference")
                result["evidence"] = [deepcopy(registry[ref]) for ref in item]
                used.update(item)
            else:
                result[key] = decode(item)
        return result

    record = MODELS[view["record_kind"]].model_validate(decode(view["record"]))
    if used != registry.keys():
        raise ValueError("Authoring contains unreferenced evidence")
    return record


def authoring_schema(kind: str) -> dict:
    """Use the exact scientific schema with evidence arrays encoded as references."""
    if kind not in MODELS:
        raise ValueError("Unknown authoring record kind")
    schema = deepcopy(MODELS[kind].model_json_schema())

    def transform(value):
        if isinstance(value, list):
            for item in value:
                transform(item)
        elif isinstance(value, dict):
            properties = value.get("properties", {})
            if "evidence" in properties:
                previous = properties.pop("evidence")
                properties["evidence_refs"] = {
                    "type": "array", "items": {"type": "string", "minLength": 1},
                    **({"minItems": previous["minItems"]} if "minItems" in previous else {}),
                }
                value["required"] = ["evidence_refs" if key == "evidence" else key
                                     for key in value.get("required", [])]
            for item in value.values():
                transform(item)

    transform(schema)
    definitions = schema.pop("$defs")
    return {"title": f"{MODELS[kind].__name__}Authoring", "type": "object",
            "additionalProperties": False, "$defs": definitions,
            "required": ["format_version", "record_kind", "record", "evidence_registry"],
            "properties": {"format_version": {"const": FORMAT}, "record_kind": {"const": kind},
                           "record": schema, "evidence_registry": {
                               "type": "object", "propertyNames": {"minLength": 1},
                               "additionalProperties": {"$ref": "#/$defs/Evidence"}}}}
