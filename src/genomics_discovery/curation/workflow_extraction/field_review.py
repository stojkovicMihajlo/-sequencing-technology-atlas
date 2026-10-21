"""Factual-field accountability used by extraction; no entailment proof.

One group covers each record, not each sentence. The policy inventories workflow
scope/identity; state kinds/descriptions; operation actions, resources, information
effects and exact I/O bindings; flow roles/qualifiers; module interpretation; and
gap premises/next evidence. Names and module boundaries need contextual judgment,
not literal paper category names. Evidence and topology already have separate
integrity checks. Evidence rationales remain inside the reviewed groups, too.
Source-to-record omissions remain a separate denominator. This sidecar complements
ordinary workflow/source review; it never authorizes export by itself. The
extraction pipeline defaults this additional gate on; standalone graph exporters
do not automatically inherit its policy.

Supported deductions may cite their premises with Evidence.support='inferred';
their wording need not occur in a quotation. not_established records insufficient
support for the recorded assertion in inspected scope, not demonstrated falsity.
A faithful qualification of an unknown may be supported: this judges description
accuracy, not recipe availability, and never clears an unresolved gap's blocking
flag. Neither absent graph edges nor valid quotations prove a negative
experimental fact. Reviewers judge this.

Optional history protects unchanged affected values in the same stable record ID,
including across record-array reorder. It does not detect paraphrases, renamed
records or reorganized nested arrays. Callers must supply the immediate prior
artifacts; this helper cannot discover omitted history or authenticate reviewers.
Historical packet/workflow/review artifacts are never modified.
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Literal

from pydantic import Field

from genomics_discovery.core.artifacts import StrictModel
from ..validation import canonical_hash
from .models import Evidence, PassageRef, Text, Workflow, digest, validate_evidence

FIELD_REVIEW_POLICY = "workflow_factual_field_review/1.0"
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NEGATIVE = {"unsupported", "not_established"}


class FieldBinding(StrictModel):
    field_pointer: Text
    value_sha256: Hash


class FieldCheck(StrictModel):
    group_id: Text
    field_bindings: Annotated[tuple[FieldBinding, ...], Field(min_length=1)]
    verdict: Literal["supported", "unsupported", "not_established", "unassessed"]
    affected_fields: tuple[Text, ...]
    rationale: Text
    inspected_passages: tuple[PassageRef, ...]
    evidence: tuple[Evidence, ...]


class FieldAdjudication(StrictModel):
    """Explicit source-backed reconsideration of one unchanged negative field."""

    prior_group_id: Text
    prior_field_binding: FieldBinding
    rationale: Text
    inspected_passages: Annotated[tuple[PassageRef, ...], Field(min_length=1)]
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


class WorkflowFieldReview(StrictModel):
    format_version: Literal["workflow_factual_field_review/1.0"] = FIELD_REVIEW_POLICY
    workflow_sha256: Hash
    source_packet_sha256: Hash
    checks: tuple[FieldCheck, ...]
    prior_workflow_sha256: Hash | None = None
    prior_review_sha256: Hash | None = None
    adjudications: tuple[FieldAdjudication, ...] = ()


def make_field_checks(workflow: Workflow) -> list[dict]:
    """Deterministic host inventory. No row asserts inspection or support."""
    data = workflow.model_dump(mode="json")
    records = [("workflow", "", data, ("method_id", "title", "scope", "variant"))]
    policy = (
        ("states", "state", ("kind", "description", "evidence")),
        ("operations", "operation", ("name", "action", "inputs", "outputs", "resources",
                                      "information_effect", "evidence", "input_bindings", "output_bindings")),
        ("flows", "flow", ("source", "target", "kind", "basis", "condition", "association",
                            "selection", "alternative_group", "evidence")),
        ("modules", "module", ("name", "operation_ids", "boundary_rationale")),
        ("unresolved", "gap", ("description", "blocking", "next_evidence")),
        ("gap_resolutions", "resolution", ("reader_role", "gap_id", "rationale", "evidence")),
    )
    for collection, kind, fields in policy:
        for index, item in enumerate(data[collection]):
            ident = (f"{item['reader_role']}:{item['gap_id']}" if kind == "resolution" else item["id"])
            records.append((f"{kind}:{ident}", f"/{collection}/{index}", item, fields))
    return [{"group_id": ident, "field_bindings": [
        {"field_pointer": f"{prefix}/{key}", "value_sha256": canonical_hash(item[key])}
        for key in fields if key in item], "verdict": "unassessed", "affected_fields": [],
        "rationale": "Not yet reviewed.", "inspected_passages": [], "evidence": []}
        for ident, prefix, item, fields in records]


def make_field_review(workflow: Workflow, packet: dict, *, prior_review=None,
                      prior_workflow=None) -> WorkflowFieldReview:
    """An unassessed scaffold, optionally bound to immediate prior artifacts."""
    if (prior_review is None) != (prior_workflow is None):
        raise ValueError("Supply both prior_review and prior_workflow")
    return WorkflowFieldReview(workflow_sha256=digest(workflow), source_packet_sha256=digest(packet),
        checks=make_field_checks(workflow),
        prior_workflow_sha256=digest(prior_workflow) if prior_workflow is not None else None,
        prior_review_sha256=digest(prior_review) if prior_review is not None else None)


def _resolve(data, pointer):
    if not pointer.startswith("/"):
        raise ValueError("Field pointer must start with /")
    value = data
    for part in pointer[1:].split("/"):
        if any(index + 1 == len(part) or part[index + 1] not in "01"
               for index, char in enumerate(part) if char == "~"):
            raise ValueError("Invalid field pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not key.isascii() or not key.isdigit() or str(int(key)) != key:
                raise ValueError("Invalid field pointer index")
            value = value[int(key)]
        else:
            value = value[key]
    return value


def _context_errors(item, packet, label):
    errors = validate_evidence(item, packet)
    inspected = [(ref.source_id, ref.passage_id) for ref in item.inspected_passages]
    if len(inspected) != len(set(inspected)):
        errors.append(f"{label}: duplicate inspected passage")
    for ev in item.evidence:
        if (ev.source_id, ev.passage_id) not in inspected:
            errors.append(f"{label}: evidence passage was not inspected")
    return errors


def _integrity(review, workflow, packet):
    errors = validate_evidence(workflow, packet)
    if review.workflow_sha256 != digest(workflow):
        errors.append("Field review workflow digest does not match")
    if review.source_packet_sha256 != digest(packet):
        errors.append("Field review source packet digest does not match")
    expected = {item["group_id"]: item for item in make_field_checks(workflow)}
    counts = Counter(item.group_id for item in review.checks)
    if set(counts) != set(expected) or any(count != 1 for count in counts.values()):
        errors.append("Field review must check every inventory group exactly once")
    data = workflow.model_dump(mode="json")
    for check in review.checks:
        label = check.group_id
        errors.extend(_context_errors(check, packet, label))
        if label not in expected:
            continue
        bindings = [item.model_dump(mode="json") for item in check.field_bindings]
        if bindings != expected[label]["field_bindings"]:
            errors.append(f"{label}: stale or out-of-scope field bindings")
        if len(check.affected_fields) != len(set(check.affected_fields)):
            errors.append(f"{label}: duplicate affected fields")
        if bool(check.affected_fields) != (check.verdict in NEGATIVE):
            errors.append(f"{label}: negative judgments need affected fields; other verdicts need none")
        for pointer in check.affected_fields:
            try:
                if not any(pointer == field["field_pointer"] or pointer.startswith(
                    field["field_pointer"] + "/") for field in expected[label]["field_bindings"]):
                    raise ValueError("outside bound group")
                _resolve(data, pointer)
            except (ValueError, KeyError, IndexError, TypeError):
                errors.append(f"{label}: invalid affected field {pointer}")
        if check.verdict != "unassessed" and not check.inspected_passages:
            errors.append(f"{label}: judged fields require inspected passages")
        if check.verdict in {"supported", "unsupported"} and not check.evidence:
            errors.append(f"{label}: supported/unsupported judgment requires evidence")
    for item in review.adjudications:
        errors.extend(_context_errors(item, packet, f"Adjudication {item.prior_group_id}"))
    return errors


def validate_field_review(review: WorkflowFieldReview, workflow: Workflow, packet: dict, *,
                          prior_review: WorkflowFieldReview | None = None,
                          prior_workflow: Workflow | None = None,
                          require_supported: bool = False) -> list[str]:
    """Validate declared contextual judgments, not their scientific truth.

    History uses the same frozen packet. Immediate prior integrity is rechecked;
    authentic reviewer/session lineage and earlier adjudication chains belong to
    the caller. No evidence absence is automatically labeled unsupported.
    """
    errors = _integrity(review, workflow, packet)
    declared_prior = review.prior_review_sha256 is not None or review.prior_workflow_sha256 is not None
    supplied_prior = prior_review is not None or prior_workflow is not None
    if not supplied_prior:
        if declared_prior or review.adjudications:
            errors.append("Declared history/adjudications require both prior artifacts")
    elif prior_review is None or prior_workflow is None:
        errors.append("Supply both prior_review and prior_workflow")
    else:
        if (review.prior_review_sha256 != digest(prior_review)
                or review.prior_workflow_sha256 != digest(prior_workflow)):
            errors.append("Field review prior artifact digests do not match")
        prior_errors = _integrity(prior_review, prior_workflow, packet)
        errors.extend(f"Prior review: {error}" for error in prior_errors)
        if not prior_errors:
            errors.extend(_history_errors(review, workflow, prior_review, prior_workflow))
    if require_supported and any(item.verdict != "supported" for item in review.checks):
        errors.append("Positive field review requires every group supported")
    return errors


def _history_errors(review, workflow, prior_review, prior_workflow):
    old_data, new_data = (item.model_dump(mode="json") for item in (prior_workflow, workflow))
    current = {item.group_id: item for item in review.checks}
    required = set()
    for old in prior_review.checks:
        new = current.get(old.group_id)
        if old.verdict not in NEGATIVE or new is None or new.verdict != "supported":
            continue
        old_prefix = old.field_bindings[0].field_pointer.rsplit("/", 1)[0]
        new_prefix = new.field_bindings[0].field_pointer.rsplit("/", 1)[0]
        for pointer in old.affected_fields:
            old_value = _resolve(old_data, pointer)
            try:
                new_value = _resolve(new_data, new_prefix + pointer[len(old_prefix):])
            except (ValueError, KeyError, IndexError, TypeError):
                continue  # Removed or reorganized; no claim of semantic correction.
            if canonical_hash(old_value) == canonical_hash(new_value):
                required.add((old.group_id, pointer, canonical_hash(old_value)))
    adjudicated, errors = set(), []
    for item in review.adjudications:
        key = (item.prior_group_id, item.prior_field_binding.field_pointer,
               item.prior_field_binding.value_sha256)
        if key not in required or key in adjudicated:
            errors.append("Adjudication must identify one unchanged prior negative field exactly once")
        adjudicated.add(key)
    for group, pointer, _ in sorted(required - adjudicated):
        errors.append(f"{group}: unchanged prior negative field requires explicit adjudication: {pointer}")
    return errors


def field_review_outcome(review: WorkflowFieldReview, workflow: Workflow, packet: dict,
                         **history) -> str:
    """A positive result requires valid bindings and every group supported."""
    if errors := validate_field_review(review, workflow, packet, **history):
        raise ValueError("; ".join(errors))
    verdicts = {item.verdict for item in review.checks}
    return next((verdict for verdict in ("unsupported", "not_established", "unassessed")
                 if verdict in verdicts), "supported")
