"""Candidate-blind source obligations and digest-bound completeness review.

Integrity checks do not derive scientific requirements from operation counts or
certify semantic completeness. A fresh source reader authors the requirements;
the independent candidate reviewer supplies their source-backed dispositions.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Annotated, Literal

from pydantic import Field, model_serializer

from genomics_discovery.core.artifacts import StrictModel
from .models import (
    Evidence, PassageRef, Text, Workflow, WorkflowReview, digest, review_outcome,
    target_ids, validate_evidence, validate_review,
)

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
EvidenceSet = Annotated[tuple[Evidence, ...], Field(min_length=1)]


class SourceObligation(StrictModel):
    id: Text = Field(description="Stable unique identity within this source-obligation ledger.")
    requirement: Text = Field(description=
        "One independently applicable coverage predicate in the frozen scope, not one verb or "
        "graph node. Separate clauses with different applicability or conditions; source presence "
        "alone does not establish minimum necessary coverage.")
    applicability: Literal["required", "optional", "outside_scope"] = Field(description=
        "Source-grounded coverage necessity for the declared scope, distinct from the accuracy "
        "of any detail actually asserted. Included optional detail still needs faithful evidence.")
    # A required item may be conditional; otherwise required cannot be waived.
    condition: Text | None = Field(default=None, description=
        "Actual scope/route antecedent, recorded here rather than only in requirement prose. "
        "Null leaves this expectation unqualified; a candidate omission does not itself make "
        "a scope-required route unselected.")
    rationale: Text = Field(description=
        "Contextual scope justification. For required coverage, state the declared output or "
        "interface that would become unsupported or ambiguous if the predicate were omitted; "
        "do not merely repeat that the source reports it.")
    evidence: EvidenceSet = Field(description=
        "Contextual source evidence for the reported fact and its applicability. Literal quote "
        "matching establishes provenance, not scientific necessity or semantic completeness.")


class SourceScope(StrictModel):
    registered_source_id: Text
    disposition: Literal["inspected", "outside_scope", "access_limited"]
    inspected_passages: tuple[PassageRef, ...] = ()
    rationale: Text
    next_evidence: Text | None = None
    blocks_completeness: bool


class SourceLimit(StrictModel):
    document_or_fact: Text
    cause: Literal["document_missing", "rendition_unreadable", "not_reported_in_inspected_sources"]
    consequence: Text
    blocking: bool
    next_evidence: Text
    evidence: EvidenceSet


class SourceObligations(StrictModel):
    format_version: Literal["source_obligations/1.0"] = "source_obligations/1.0"
    source_packet_sha256: Digest
    method_id: Text
    scope: Text
    variant: Text
    source_scope: Annotated[tuple[SourceScope, ...], Field(min_length=1)]
    obligations: Annotated[tuple[SourceObligation, ...], Field(min_length=1)]
    source_limits: tuple[SourceLimit, ...] = ()


class SourceCoverageCheck(StrictModel):
    obligation_id: Text = Field(description="Exact identity of the frozen source obligation assessed.")
    disposition: Literal["satisfied", "partial", "missing", "uncertain", "not_applicable"] = Field(
        description="Assess actual coverage: missing/partial denotes a genuine candidate omission; "
        "uncertain can retain an unresolved source/scope dispute. A disputed unconditional "
        "required item stays selected and uncertain, never silently waived or marked satisfied.")
    selected: bool = Field(description=
        "Whether the requirement's route applies under the unchanged scope. Unconditional "
        "required items must remain selected, including when their necessity is disputed.")
    targets: tuple[Text, ...] = Field(default=(), description=
        "Actual workflow target IDs witnessing the assessed coverage. IDs or matching quotes "
        "alone do not establish that the predicate is represented.")
    rationale: Text = Field(description=
        "Independent contextual assessment of actual coverage, applicability and scope. "
        "Distinguish a candidate omission from uncertainty about the requirement itself.")
    evidence: EvidenceSet = Field(description=
        "Inspected contextual source witnesses for this coverage or scope judgment; not proof "
        "of acceptance merely because quotations and identifiers match.")


class SourceLimitAdjudication(StrictModel):
    """Independent scope correction; original source preparation stays frozen."""
    source_pointer: Text
    disposition: Literal["retain_nonblocking_detail", "outside_selected_scope",
                         "resolved_by_registered_evidence"]
    scope_rationale: Text
    evidence: EvidenceSet
    retained_gap_ids: tuple[Text, ...] = ()
    targets: tuple[Text, ...] = Field(default=(), description=
        "Actual workflow locations of recovered facts/interfaces; required for resolved_by_registered_evidence.")


class SourceCoverageReview(StrictModel):
    format_version: Literal["source_coverage_review/1.0"] = "source_coverage_review/1.0"
    source_packet_sha256: Digest
    obligations_sha256: Digest
    workflow_sha256: Digest
    workflow_review_sha256: Digest
    inspected_passages: Annotated[tuple[PassageRef, ...], Field(min_length=1)]
    checks: Annotated[tuple[SourceCoverageCheck, ...], Field(min_length=1)]
    source_limit_adjudications: tuple[SourceLimitAdjudication, ...] | None = None

    @model_serializer(mode="wrap")
    def preserve_legacy_fields(self, handler):
        data = handler(self)
        if self.source_limit_adjudications is None:
            data.pop("source_limit_adjudications", None)
        return data


def validate_source_obligations(obligations: SourceObligations, packet: dict) -> list[str]:
    """Check scope, source accounting and provenance, not scientific adequacy."""
    errors = validate_evidence(obligations, packet)
    if obligations.source_packet_sha256 != digest(packet):
        errors.append("Source obligations packet digest does not match")
    for key in ("method_id", "scope", "variant"):
        if getattr(obligations, key) != packet[key]:
            errors.append(f"Source obligations {key} differs from frozen scope")
    counts = Counter(item.id for item in obligations.obligations)
    if any(count != 1 for count in counts.values()):
        errors.append("Duplicate source obligation ID")
    sources = {source["source_id"] for source in packet["sources"]}
    scoped = Counter(item.registered_source_id for item in obligations.source_scope)
    if set(scoped) != sources or any(count != 1 for count in scoped.values()):
        errors.append("Source scope must account for every registered source exactly once")
    inspected = set()
    for entry in obligations.source_scope:
        if entry.disposition == "inspected" and not entry.inspected_passages:
            errors.append(f"Source {entry.registered_source_id}: inspected scope has no passages")
        if entry.disposition == "access_limited" and not entry.next_evidence:
            errors.append(f"Source {entry.registered_source_id}: access limit needs exact next evidence")
        if entry.disposition != "access_limited" and entry.blocks_completeness:
            errors.append(f"Source {entry.registered_source_id}: only access limits can block source access")
        for ref in entry.inspected_passages:
            if ref.source_id != entry.registered_source_id:
                errors.append(f"Source {entry.registered_source_id}: inspected passage belongs to another source")
            inspected.add((ref.source_id, ref.passage_id))
    for item in (*obligations.obligations, *obligations.source_limits):
        for evidence in item.evidence:
            if (evidence.source_id, evidence.passage_id) not in inspected:
                errors.append("Source obligation/limit evidence was not in inspected source scope")
    return errors


def validate_source_coverage_review(
    coverage: SourceCoverageReview, obligations: SourceObligations,
    workflow_review: WorkflowReview, workflow: Workflow, packet: dict,
) -> list[str]:
    errors = validate_source_obligations(obligations, packet)
    errors.extend(validate_review(workflow_review, workflow, packet))
    errors.extend(validate_evidence(coverage, packet))
    for key, expected in (
        ("source_packet_sha256", digest(packet)),
        ("obligations_sha256", digest(obligations)),
        ("workflow_sha256", digest(workflow)),
        ("workflow_review_sha256", digest(workflow_review)),
    ):
        if getattr(coverage, key) != expected:
            errors.append(f"Source coverage {key} does not match frozen binding")
    required = {item.id: item for item in obligations.obligations}
    counts = Counter(check.obligation_id for check in coverage.checks)
    if set(counts) != set(required) or any(count != 1 for count in counts.values()):
        errors.append("Source coverage must check every obligation exactly once")
    inspected = {(ref.source_id, ref.passage_id) for ref in coverage.inspected_passages}
    valid_targets = target_ids(workflow)
    for check in coverage.checks:
        item = required.get(check.obligation_id)
        if item is None:
            continue
        if len(check.targets) != len(set(check.targets)) or set(check.targets) - valid_targets:
            errors.append(f"Obligation {item.id}: duplicate or unknown workflow targets")
        if check.disposition == "satisfied" and (not check.selected or not check.targets):
            errors.append(f"Obligation {item.id}: satisfied needs selected actual workflow targets")
        if check.disposition == "not_applicable" and check.selected:
            errors.append(f"Obligation {item.id}: not_applicable cannot select a route")
        if not check.selected and check.disposition != "not_applicable":
            errors.append(f"Obligation {item.id}: unselected must be explicitly not_applicable")
        if item.applicability == "required" and not check.selected and item.condition is None:
            errors.append(f"Obligation {item.id}: unconditional required item cannot be excluded")
        if item.applicability == "outside_scope" and check.selected:
            errors.append(f"Obligation {item.id}: outside-scope item cannot be selected")
        for ev in check.evidence:
            if (ev.source_id, ev.passage_id) not in inspected:
                errors.append(f"Obligation {item.id}: evidence passage was not inspected in review")
    pointers = set()
    gaps = {gap.id: gap for gap in workflow.unresolved}
    if coverage.source_limit_adjudications:
        for key in ("method_id", "title", "scope", "variant"):
            if getattr(workflow, key) != packet[key]:
                errors.append(f"Source adjudication cannot change frozen workflow {key}")
    for item in coverage.source_limit_adjudications or ():
        pointer = item.source_pointer
        match = re.fullmatch(r"/(source_limits|source_scope)/(0|[1-9][0-9]*)", pointer)
        if match is None or int(match[2]) >= len(getattr(obligations, match[1])):
            errors.append(f"Source adjudication {pointer}: unknown source limit/scope pointer")
        else:
            original = getattr(obligations, match[1])[int(match[2])]
            if not (original.blocking if match[1] == "source_limits" else original.blocks_completeness):
                errors.append(f"Source adjudication {pointer}: original item does not block completeness")
        if pointer in pointers:
            errors.append(f"Source adjudication {pointer}: duplicate correction")
        pointers.add(pointer)
        if len(item.targets) != len(set(item.targets)) or set(item.targets) - valid_targets:
            errors.append(f"Source adjudication {pointer}: duplicate or unknown workflow targets")
        if item.disposition == "resolved_by_registered_evidence" and not item.targets:
            errors.append(f"Source adjudication {pointer}: resolved fact needs actual workflow targets")
        if len(item.retained_gap_ids) != len(set(item.retained_gap_ids)):
            errors.append(f"Source adjudication {pointer}: duplicate retained gap IDs")
        if item.disposition == "retain_nonblocking_detail" and not item.retained_gap_ids:
            errors.append(f"Source adjudication {pointer}: unknown detail needs retained workflow gaps")
        for gap_id in item.retained_gap_ids:
            if gap_id not in gaps or gaps[gap_id].blocking:
                errors.append(f"Source adjudication {pointer}: retained gap must exist and remain nonblocking: {gap_id}")
        for ev in item.evidence:
            if (ev.source_id, ev.passage_id) not in inspected:
                errors.append(f"Source adjudication {pointer}: evidence passage was not inspected in review")
    return errors


def source_coverage_outcome(
    coverage: SourceCoverageReview, obligations: SourceObligations,
    workflow_review: WorkflowReview, workflow: Workflow,
) -> str:
    """Host-derived outcome after separate integrity validation; never a truth oracle."""
    primary = review_outcome(workflow_review, workflow)
    if primary != "supported":
        return primary
    # Only the source veto is adjudicated. Required operations, ordinary review
    # findings and blocking workflow gaps retain their independent gates.
    adjudicated = {item.source_pointer for item in coverage.source_limit_adjudications or ()}
    blockers = {f"/source_scope/{i}" for i, entry in enumerate(obligations.source_scope) if entry.blocks_completeness}
    blockers |= {f"/source_limits/{i}" for i, limit in enumerate(obligations.source_limits) if limit.blocking}
    if blockers - adjudicated:
        return "research_needed"
    selected = [check for check in coverage.checks if check.selected]
    if any(check.disposition in {"missing", "partial"} for check in selected):
        return "needs_revision"
    if any(check.disposition == "uncertain" for check in selected):
        return "research_needed"
    return "supported"
