"""Closed contracts and host-only scoring for a bounded grounding diagnostic.

Passage integrity is deterministic; source entailment remains a model judgment.
The labels are model-authored diagnostic candidates, never human gold.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet

Text = Annotated[str, Field(min_length=1)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
BOUNDARY = ("Model-authored reference diagnostic, not human gold, whole-graph precision, "
            "or a full current-harness ablation. Passage validity does not prove entailment.")


class Closed(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PassageRef(Closed):
    source_id: Text
    passage_id: Text


class Passage(PassageRef):
    text: Text


class Context(Closed):
    id: Text
    method_id: Text
    family: Text
    split: Text
    source_packet_sha256: Hash
    source_packet_digest: Hash
    source_packet_path: Text
    passages: Annotated[list[Passage], Field(min_length=1)]


class Case(Closed):
    id: Text
    context_id: Text
    field_pointer: Annotated[str, Field(pattern=r"^/")]
    value: JsonValue


class Dataset(Closed):
    format_version: Literal["protocol_grounding_cases/1.0"]
    annotation_status: Literal["model_authored_diagnostic_candidate"] | None = None
    boundary: Text | None = None
    contexts: Annotated[list[Context], Field(min_length=1)]
    cases: Annotated[list[Case], Field(min_length=1)]


class Label(Closed):
    case_id: Text
    outcome: Literal["supported", "not_established", "contradicted"]
    phenomenon: Text
    rationale: Text
    decisive_passages: list[PassageRef]


class Labels(Closed):
    format_version: Literal["protocol_grounding_labels/1.0"]
    annotation_status: Literal["model_authored_diagnostic_candidate"] | None = None
    boundary: Text | None = None
    cases: Annotated[list[Label], Field(min_length=1)]


class Judgment(Closed):
    case_id: Text
    value_sha256: Hash
    outcome: Literal["supported", "not_established", "contradicted", "unassessed"]
    rationale: Text
    inspected_passages: list[PassageRef]
    unsupported_clause: Text | None = None


class Result(Closed):
    format_version: Literal["protocol_grounding_result/1.0"]
    results: list[Judgment]


def read_closed(path, model):
    """Reject duplicate JSON keys and non-JSON constants as well as extra fields."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"Invalid JSON constant: {value}")

    value = json.loads(Path(path).read_text(encoding="utf-8"),
                       object_pairs_hook=pairs, parse_constant=invalid)
    return model.model_validate(value)


def _unique(items, name):
    if len(items) != len(set(items)):
        raise ValueError(f"Duplicate {name}")


def passage_keys(context):
    return {(p.source_id, p.passage_id) for p in context.passages}


def verify_dataset(dataset):
    """Replay original packets on the host, never expose their paths to readers."""
    _unique([c.id for c in dataset.contexts], "context IDs")
    _unique([c.id for c in dataset.cases], "case IDs")
    contexts = {c.id: c for c in dataset.contexts}
    if any(c.context_id not in contexts for c in dataset.cases):
        raise ValueError("Case has an unknown context_id")
    pins = {}
    for context in dataset.contexts:
        path = Path(context.source_packet_path)
        if not path.is_absolute() or sha(path) != context.source_packet_sha256:
            raise ValueError(f"Source packet byte hash differs: {context.id}")
        packet = load_packet(path)
        if digest(packet) != context.source_packet_digest or packet["method_id"] != context.method_id:
            raise ValueError(f"Source packet canonical digest/method differs: {context.id}")
        original = {(s["source_id"], p["passage_id"]): p["text"]
                    for s in packet["sources"] for p in s["passages"]}
        _unique([(p.source_id, p.passage_id) for p in context.passages], "context passages")
        for passage in context.passages:
            if original.get((passage.source_id, passage.passage_id)) != passage.text:
                raise ValueError(f"Visible passage differs from registered source: {context.id}")
        pins[str(path)] = sha(path)
        pins.update({s["original_path"]: s["original_sha256"] for s in packet["sources"]})
    return pins


def public_input(dataset):
    """A whitelist projection: no labels, families, splits or original paths."""
    used = {case.context_id for case in dataset.cases}
    return {"contexts": [{"id": context.id,
                          "passages": [p.model_dump(mode="json") for p in context.passages]}
                         for context in dataset.contexts if context.id in used],
            "cases": [{"id": case.id, "context_id": case.context_id,
                       "field_pointer": case.field_pointer, "value": case.value,
                       "value_sha256": digest(case.value)} for case in dataset.cases]}


def _validate_refs(refs, context):
    keys = [(p.source_id, p.passage_id) for p in refs]
    _unique(keys, "cited passages")
    if not set(keys) <= passage_keys(context):
        raise ValueError("Cited passage is not in this case's visible context")


def validate_result(result, dataset):
    cases = {c.id: c for c in dataset.cases}
    contexts = {c.id: c for c in dataset.contexts}
    _unique([row.case_id for row in result.results], "result case IDs")
    if {row.case_id for row in result.results} != set(cases):
        raise ValueError("Result must contain every assigned case exactly once")
    for row in result.results:
        case = cases[row.case_id]
        if row.value_sha256 != digest(case.value):
            raise ValueError("Result value_sha256 differs from host canonical value")
        _validate_refs(row.inspected_passages, contexts[case.context_id])
        if row.outcome != "unassessed" and not row.inspected_passages:
            raise ValueError("A substantive judgment requires inspected passages")


def validate_labels(labels, dataset):
    cases = {c.id: c for c in dataset.cases}
    contexts = {c.id: c for c in dataset.contexts}
    _unique([row.case_id for row in labels.cases], "label case IDs")
    if {row.case_id for row in labels.cases} != set(cases):
        raise ValueError("Labels must cover every case exactly once")
    for row in labels.cases:
        _validate_refs(row.decisive_passages, contexts[cases[row.case_id].context_id])


def score_result(dataset, labels, result):
    """Failures have no rate; explicit abstentions earn no correctness credit."""
    validate_labels(labels, dataset)
    if result is not None:
        validate_result(result, dataset)
    contexts = {c.id: c for c in dataset.contexts}
    cases = {c.id: c for c in dataset.cases}
    predictions = {r.case_id: r for r in result.results} if result is not None else {}

    def counts(rows):
        n = len(rows)
        unavailable = sum(row.case_id not in predictions for row in rows)
        explicit_abstentions = sum(predictions[row.case_id].outcome == "unassessed"
                                  for row in rows if row.case_id in predictions)

        def metric(eligible, positive):
            denominator = sum(eligible(row) for row in rows)
            numerator = sum(eligible(row) and row.case_id in predictions and
                            positive(row, predictions[row.case_id]) for row in rows)
            return {"numerator": numerator, "denominator": denominator,
                    "rate": numerator / denominator if denominator and not unavailable else None}

        return {"case_count": n, "failure_item_count": unavailable,
                "unassessed_item_count": explicit_abstentions,
                "judged_item_count": n - unavailable - explicit_abstentions,
                "unsupported_acceptance": metric(lambda r: r.outcome != "supported",
                                                  lambda r, p: p.outcome == "supported"),
                "supported_retention": metric(lambda r: r.outcome == "supported",
                                              lambda r, p: p.outcome == "supported"),
                "abstention": metric(lambda r: True, lambda r, p: p.outcome == "unassessed"),
                "label_accuracy": metric(lambda r: True, lambda r, p: p.outcome == r.outcome)}

    groups = {"overall": counts(labels.cases)}
    for axis in ("family", "split", "phenomenon"):
        def key(row):
            return row.phenomenon if axis == "phenomenon" else getattr(contexts[cases[row.case_id].context_id], axis)
        groups["by_" + axis] = {value: counts([r for r in labels.cases if key(r) == value])
                                for value in sorted({key(r) for r in labels.cases})}
    return {"boundary": BOUNDARY, "label_status": "model_authored_diagnostic_candidate",
            "whole_graph_precision": None, **groups}
