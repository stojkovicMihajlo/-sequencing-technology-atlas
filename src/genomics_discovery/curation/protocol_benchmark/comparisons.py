"""Bound cross-method benchmark judgments, never production registry admission.

The host checks custody, quotes and complete review accounting. It does not infer
entailment, semantic equivalence, compatibility or historical development. Session
IDs record declared independent runs; they do not prove reviewer independence.
"""

from __future__ import annotations

from collections import Counter
from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.workflow_extraction.models import (
    Evidence, PassageRef, Text, Workflow, digest, validate_evidence,
)

from .models import Hash, IDs, ProtocolReference
from .validation import benchmark_target_ids, validate_reference

Side = Literal["left", "right"]
ClaimType = Literal["shared_purpose", "shared_mechanism", "documented_development",
                    "interface_compatibility"]
CLAIM_TYPES = ("shared_purpose", "shared_mechanism", "documented_development",
               "interface_compatibility")
CHECK_FIELDS = ("text", "scope", "judgment", "evidence", "preserved_differences",
                "counterexamples", "unknowns")


class ComparisonTask(StrictModel):
    comparison_id: Text
    left_case_id: Text
    right_case_id: Text
    question: Text
    why_included: Text
    split: Literal["development", "future_tuning_holdout"]
    allowed_claim_types: Annotated[tuple[ClaimType, ...], Field(min_length=1)]
    annotation_policy: Text


class SidePassageRef(PassageRef):
    side: Side


class SideEvidence(Evidence):
    side: Side


class ComparedUnit(StrictModel):
    """A declared connected unit, without prescribing its operation count."""

    description: Text
    inputs: Text
    transformation: Text
    outputs: Text
    information: Text
    context: Text
    connectedness_rationale: Text
    selected_obligation_ids: IDs
    selected_protocol_targets: tuple[Text, ...] = ()
    workflow_sha256: Hash | None = None
    evidence: Annotated[tuple[SideEvidence, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def bind_optional_graph_targets(self):
        if self.selected_protocol_targets and self.workflow_sha256 is None:
            raise ValueError("Selected protocol targets require an exact workflow_sha256")
        return self


class ComparisonSide(StrictModel):
    case_id: Text
    method_id: Text
    reference_sha256: Hash
    source_packet_sha256: Hash
    unit: ComparedUnit


class ComparisonClaim(StrictModel):
    id: Text
    claim_type: ClaimType
    text: Text
    scope: Text
    judgment: Literal["supported", "contradicted", "not_established"]
    rationale: Text
    evidence: tuple[SideEvidence, ...]
    preserved_differences: tuple[Text, ...]
    counterexamples: tuple[Text, ...]
    unknowns: tuple[Text, ...]

    @model_validator(mode="after")
    def source_based_decisions(self):
        if self.judgment != "not_established" and {ev.side for ev in self.evidence} != {"left", "right"}:
            raise ValueError("Supported/contradicted comparisons require evidence on both sides")
        if self.judgment == "contradicted" and not self.counterexamples:
            raise ValueError("Contradicted requires an explicit source-grounded counterexample")
        return self


class ComparisonReference(StrictModel):
    format_version: Literal["protocol_comparison_reference/1.0"] = "protocol_comparison_reference/1.0"
    comparison_id: Text
    task_sha256: Hash
    annotation_session_id: Text
    left: ComparisonSide
    right: ComparisonSide
    inspected_passages: Annotated[tuple[SidePassageRef, ...], Field(min_length=2)]
    claims: tuple[ComparisonClaim, ...]
    no_claims_reason: Text | None = None
    annotation_status: Literal["model_authored_comparison_candidate"] = "model_authored_comparison_candidate"

    @model_validator(mode="after")
    def no_forced_relation(self):
        if not self.claims and self.no_claims_reason is None:
            raise ValueError("An empty comparison requires no_claims_reason")
        if self.claims and self.no_claims_reason is not None:
            raise ValueError("no_claims_reason applies only to an empty comparison")
        return self


class ComparisonUnitCheck(StrictModel):
    side: Side
    verdict: Literal["supported", "needs_revision", "uncertain"]
    rationale: Text
    evidence: tuple[SideEvidence, ...]


class ComparisonClaimCheck(StrictModel):
    claim_id: Text
    verdict: Literal["supported", "needs_revision", "uncertain"]
    checked_fields: tuple[Literal["text", "scope", "judgment", "evidence",
                                  "preserved_differences", "counterexamples", "unknowns"], ...]
    rationale: Text
    evidence: tuple[SideEvidence, ...]


class ComparisonReview(StrictModel):
    format_version: Literal["protocol_comparison_review/1.0"] = "protocol_comparison_review/1.0"
    comparison_sha256: Hash
    task_sha256: Hash
    left_reference_sha256: Hash
    right_reference_sha256: Hash
    left_source_packet_sha256: Hash
    right_source_packet_sha256: Hash
    review_session_id: Text
    inspected_passages: Annotated[tuple[SidePassageRef, ...], Field(min_length=2)]
    unit_checks: tuple[ComparisonUnitCheck, ...]
    claim_checks: tuple[ComparisonClaimCheck, ...]
    no_claims_check: Literal["supported", "needs_revision", "uncertain"] | None = None
    no_claims_rationale: Text | None = None
    notes: tuple[Text, ...] = ()


def comparison_schema(kind: Literal["reference", "review", "task"] = "reference") -> dict:
    return {"reference": ComparisonReference, "review": ComparisonReview,
            "task": ComparisonTask}[kind].model_json_schema()


def _unique(values, label, errors):
    if any(count != 1 for count in Counter(values).values()):
        errors.append(f"Duplicate {label}")


def _passages(items):
    return {(item.side, item.source_id, item.passage_id) for item in items}


def _side_integrity(items, left_packet, right_packet, label, errors):
    # Local source/passage IDs may be identical across packets. Never validate
    # the combined document with either packet or merge the passage namespaces.
    for side, packet in (("left", left_packet), ("right", right_packet)):
        subset = [item.model_dump(mode="json", exclude={"side"})
                  for item in items if item.side == side]
        errors.extend(f"{label} {side}: {error}"
                      for error in validate_evidence({"items": subset}, packet))


def _inspected(evidence, inspected, label, errors):
    if _passages(evidence) - inspected:
        errors.append(f"{label}: evidence outside declared inspected passages")


def _selected_graph(unit, reference, packet, workflow, side, errors):
    if unit.workflow_sha256 is None:
        return
    if workflow is None:
        errors.append(f"{side}: bound workflow must be supplied")
        return
    if unit.workflow_sha256 != digest(workflow):
        errors.append(f"{side}: workflow_sha256 does not match")
    for field in ("method_id", "scope", "variant"):
        if getattr(workflow, field) != getattr(reference, field):
            errors.append(f"{side}: workflow {field} differs from reference")
    errors.extend(f"{side} workflow: {error}" for error in validate_evidence(workflow, packet))
    if set(unit.selected_protocol_targets) - benchmark_target_ids(workflow):
        errors.append(f"{side}: unknown selected protocol targets")
    # Connectivity/meaning is a declared source judgment reviewed below. Target
    # presence or graph reachability never establishes a semantic relation.


def validate_comparison_reference(comparison: ComparisonReference,
        left_reference: ProtocolReference, right_reference: ProtocolReference,
        left_packet: dict, right_packet: dict, *, task: ComparisonTask | dict,
        left_workflow: Workflow | None = None, right_workflow: Workflow | None = None) -> list[str]:
    """Validate exact bindings and quotation integrity, not scientific entailment."""
    task = task if isinstance(task, ComparisonTask) else ComparisonTask.model_validate(task)
    errors = []
    for side, reference, packet in (("left", left_reference, left_packet),
                                     ("right", right_reference, right_packet)):
        errors.extend(f"{side}: {error}" for error in validate_reference(reference, packet))
    if comparison.task_sha256 != digest(task):
        errors.append("Comparison task_sha256 does not match")
    if comparison.comparison_id != task.comparison_id:
        errors.append("Comparison comparison_id differs from task")
    if task.left_case_id == task.right_case_id:
        errors.append("Comparison task must name two different cases")
    _unique(task.allowed_claim_types, "allowed claim type", errors)
    for side, reference, packet, workflow in (
        ("left", left_reference, left_packet, left_workflow),
        ("right", right_reference, right_packet, right_workflow),
    ):
        bound = getattr(comparison, side)
        expected = {"case_id": reference.case_id, "method_id": reference.method_id,
                    "reference_sha256": digest(reference), "source_packet_sha256": digest(packet)}
        for field, value in expected.items():
            if getattr(bound, field) != value:
                errors.append(f"{side}: {field} does not match")
        if bound.case_id != getattr(task, f"{side}_case_id"):
            errors.append(f"{side}: case_id differs from predeclared task")
        _unique(bound.unit.selected_obligation_ids, f"{side} selected obligation", errors)
        if set(bound.unit.selected_obligation_ids) - {item.id for item in reference.obligations}:
            errors.append(f"{side}: unknown selected obligation IDs")
        _unique(bound.unit.selected_protocol_targets, f"{side} selected protocol target", errors)
        if {ev.side for ev in bound.unit.evidence} != {side}:
            errors.append(f"{side}: unit evidence must be qualified to its own side")
        _selected_graph(bound.unit, reference, packet, workflow, side, errors)
    inspected = _passages(comparison.inspected_passages)
    _unique([(r.side, r.source_id, r.passage_id) for r in comparison.inspected_passages],
            "comparison inspected passage", errors)
    if {item.side for item in comparison.inspected_passages} != {"left", "right"}:
        errors.append("Comparison must declare inspected passages on both sides")
    _side_integrity(comparison.inspected_passages, left_packet, right_packet, "Inspected", errors)
    evidence = [*comparison.left.unit.evidence, *comparison.right.unit.evidence]
    _unique((claim.id for claim in comparison.claims), "comparison claim ID", errors)
    for claim in comparison.claims:
        if claim.claim_type not in task.allowed_claim_types:
            errors.append(f"{claim.id}: claim type not allowed by task")
        evidence.extend(claim.evidence)
    _side_integrity(evidence, left_packet, right_packet, "Evidence", errors)
    _inspected(evidence, inspected, "Comparison", errors)
    return errors


def validate_comparison_review(review: ComparisonReview, comparison: ComparisonReference,
        left_reference: ProtocolReference, right_reference: ProtocolReference,
        left_packet: dict, right_packet: dict, *, task: ComparisonTask | dict,
        left_workflow: Workflow | None = None, right_workflow: Workflow | None = None) -> list[str]:
    errors = validate_comparison_reference(comparison, left_reference, right_reference,
        left_packet, right_packet, task=task, left_workflow=left_workflow, right_workflow=right_workflow)
    for field, expected in (("comparison_sha256", digest(comparison)),
        ("task_sha256", comparison.task_sha256),
        ("left_reference_sha256", digest(left_reference)),
        ("right_reference_sha256", digest(right_reference)),
        ("left_source_packet_sha256", digest(left_packet)),
        ("right_source_packet_sha256", digest(right_packet))):
        if getattr(review, field) != expected:
            errors.append(f"Review {field} does not match")
    if review.review_session_id == comparison.annotation_session_id:
        errors.append("Comparison author and reviewer session IDs must differ")
    inspected = _passages(review.inspected_passages)
    _unique([(r.side, r.source_id, r.passage_id) for r in review.inspected_passages],
            "review inspected passage", errors)
    if {item.side for item in review.inspected_passages} != {"left", "right"}:
        errors.append("Review must inspect both sides")
    _side_integrity(review.inspected_passages, left_packet, right_packet, "Review inspected", errors)
    _unique((check.side for check in review.unit_checks), "unit review", errors)
    if {check.side for check in review.unit_checks} != {"left", "right"}:
        errors.append("Review must check each compared unit exactly once")
    evidence = []
    for check in review.unit_checks:
        if ((check.verdict != "uncertain" and not check.evidence)
                or any(ev.side != check.side for ev in check.evidence)):
            errors.append(f"{check.side}: unit review requires evidence from its own side")
        evidence.extend(check.evidence)
    expected = {claim.id: claim for claim in comparison.claims}
    _unique((check.claim_id for check in review.claim_checks), "claim review", errors)
    if {check.claim_id for check in review.claim_checks} != set(expected):
        errors.append("Review must check every comparison claim exactly once")
    for check in review.claim_checks:
        _unique(check.checked_fields, f"checked field in {check.claim_id}", errors)
        if set(check.checked_fields) != set(CHECK_FIELDS):
            errors.append(f"{check.claim_id}: review must check every claim field")
        claim = expected.get(check.claim_id)
        if check.verdict == "supported" and claim:
            if claim.judgment == "not_established":
                if _passages(comparison.inspected_passages) - inspected:
                    errors.append(f"{claim.id}: uncertain conclusion requires the declared inspected scope")
            elif {ev.side for ev in check.evidence} != {"left", "right"}:
                errors.append(f"{claim.id}: decided claim review requires evidence on both sides")
        if check.verdict == "needs_revision" and not check.evidence:
            errors.append(f"{check.claim_id}: revision judgment requires source evidence")
        evidence.extend(check.evidence)
    if comparison.claims:
        if review.no_claims_check is not None or review.no_claims_rationale is not None:
            errors.append("no_claims_check applies only to an empty comparison")
    elif review.no_claims_check is None or review.no_claims_rationale is None:
        errors.append("An empty comparison requires an explicit no_claims_check and rationale")
    elif review.no_claims_check == "supported" and _passages(comparison.inspected_passages) - inspected:
        errors.append("An empty comparison review requires the declared inspected scope")
    _side_integrity(evidence, left_packet, right_packet, "Review evidence", errors)
    _inspected(evidence, inspected, "Review", errors)
    _inspected([*comparison.left.unit.evidence, *comparison.right.unit.evidence,
                *(ev for claim in comparison.claims for ev in claim.evidence)],
               inspected, "Reviewed comparison", errors)
    return errors


def comparison_review_outcome(review: ComparisonReview) -> str:
    """Use only after full validation."""
    verdicts = {check.verdict for check in (*review.unit_checks, *review.claim_checks)}
    if review.no_claims_check:
        verdicts.add(review.no_claims_check)
    if "needs_revision" in verdicts:
        return "needs_revision"
    if "uncertain" in verdicts:
        return "research_needed"
    return "supported_comparison_candidate"


def score_comparison(review: ComparisonReview, comparison: ComparisonReference,
        left_reference: ProtocolReference, right_reference: ProtocolReference,
        left_packet: dict, right_packet: dict, *, task: ComparisonTask | dict,
        left_workflow: Workflow | None = None, right_workflow: Workflow | None = None) -> dict:
    if errors := validate_comparison_review(review, comparison, left_reference, right_reference,
        left_packet, right_packet, task=task, left_workflow=left_workflow, right_workflow=right_workflow):
        raise ValueError("; ".join(errors))
    units_supported = all(check.verdict == "supported" for check in review.unit_checks)
    checked = {check.claim_id for check in review.claim_checks if check.verdict == "supported"}
    reviewed = [claim for claim in comparison.claims if units_supported and claim.id in checked]
    counts = Counter(claim.judgment for claim in reviewed)
    return {"format_version": "protocol_comparison_scores/1.0",
        "comparison_sha256": digest(comparison), "review_sha256": digest(review),
        "task_sha256": comparison.task_sha256, "reference_status": comparison.annotation_status,
        "review_outcome": comparison_review_outcome(review),
        "authored_claim_count": len(comparison.claims), "reviewed_claim_count": len(reviewed),
        "reviewed_claim_ids": [claim.id for claim in reviewed],
        "unreviewed_claim_ids": [claim.id for claim in comparison.claims if claim not in reviewed],
        "counts": {key: counts[key] for key in ("supported", "contradicted", "not_established")},
        "by_claim_type": {kind: dict(Counter(claim.judgment for claim in reviewed
                                            if claim.claim_type == kind)) for kind in CLAIM_TYPES},
        "boundary": "Counts describe only supplied independently reviewed claim scope, not scientific "
                    "accuracy, precision or recall. Not established is not false. Quote integrity and "
                    "declared session separation do not establish entailment or reviewer independence. "
                    "No other relation type, registry admission or graph merge follows from a claim."}


def export_benchmark_relations(review: ComparisonReview, comparison: ComparisonReference,
        left_reference: ProtocolReference, right_reference: ProtocolReference,
        left_packet: dict, right_packet: dict, **kwargs) -> dict:
    score = score_comparison(review, comparison, left_reference, right_reference,
                             left_packet, right_packet, **kwargs)
    reviewed = set(score["reviewed_claim_ids"])
    return {"format_version": "protocol_benchmark_relations/1.0",
        "comparison_id": comparison.comparison_id,
        "comparison_sha256": score["comparison_sha256"], "review_sha256": score["review_sha256"],
        "task_sha256": comparison.task_sha256,
        "left": comparison.left.model_dump(mode="json"),
        "right": comparison.right.model_dump(mode="json"),
        "relations": [claim.model_dump(mode="json") for claim in comparison.claims
                      if claim.id in reviewed and claim.judgment == "supported"],
        "unexported_claims": [{"claim_id": claim.id, "judgment": claim.judgment,
                                "independently_reviewed": claim.id in reviewed}
                               for claim in comparison.claims
                               if claim.id not in reviewed or claim.judgment != "supported"],
        "review_outcome": score["review_outcome"],
        "boundary": "Benchmark artifact only. Relations mean exactly their authored text and scope; "
                    "they do not establish implicit direction, other claim types, production registry "
                    "admission, compatibility, shared mechanism or historical development."}
