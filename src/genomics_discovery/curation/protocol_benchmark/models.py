"""Closed benchmark envelopes with open scientific content and explicit judgments.

These are source-first model-authored reference candidates, not expert gold labels.
Obligations do not prescribe node counts, names or a unique graph decomposition.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.workflow_extraction.models import (
    Evidence, EvidenceSet, PassageRef, Text, WorkflowReview,
)

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
IDs = Annotated[tuple[Text, ...], Field(min_length=1)]


class ReferenceObligation(StrictModel):
    id: Text
    kind: Text
    text: Text
    criticality: Literal["required", "critical"]
    why_needed: Text
    evidence: EvidenceSet


class ReferenceUnknown(StrictModel):
    id: Text
    question: Text
    consequence: Text
    inspected_passages: Annotated[tuple[PassageRef, ...], Field(min_length=1)]
    next_evidence: Text


class AdmissibleDecomposition(StrictModel):
    id: Text
    description: Text
    obligation_ids: IDs


class QueryCase(StrictModel):
    id: Text
    question: Text
    answer: Text
    obligation_ids: IDs
    evidence: EvidenceSet


class SourceCoverage(StrictModel):
    source_id: Text
    inspected_passage_ids: tuple[Text, ...]
    role_in_assay: Text
    notes: Text


class ProtocolReference(StrictModel):
    format_version: Literal["protocol_reference/1.0"] = "protocol_reference/1.0"
    case_id: Text
    method_id: Text
    source_packet_sha256: Hash
    scope: Text
    variant: Text
    measurement: Text
    obligations: Annotated[tuple[ReferenceObligation, ...], Field(min_length=1)]
    unknowns: tuple[ReferenceUnknown, ...]
    admissible_decompositions: Annotated[tuple[AdmissibleDecomposition, ...], Field(min_length=1)]
    query_cases: tuple[QueryCase, ...]
    source_coverage: Annotated[tuple[SourceCoverage, ...], Field(min_length=1)]
    annotation_status: Literal["model_authored_reference_candidate"] = (
        "model_authored_reference_candidate"
    )


class ReferenceCheck(StrictModel):
    target_id: Text
    verdict: Literal["supported", "needs_revision", "uncertain"]
    rationale: Text
    evidence: tuple[Evidence, ...] = ()


class ReferenceReview(StrictModel):
    format_version: Literal["protocol_reference_review/1.0"] = "protocol_reference_review/1.0"
    reference_sha256: Hash
    source_packet_sha256: Hash
    inspected_passages: Annotated[tuple[PassageRef, ...], Field(min_length=1)]
    obligation_checks: tuple[ReferenceCheck, ...]
    unknown_checks: tuple[ReferenceCheck, ...]
    decomposition_checks: tuple[ReferenceCheck, ...]
    query_checks: tuple[ReferenceCheck, ...]
    missing_obligations: tuple[ReferenceObligation, ...]
    notes: tuple[Text, ...] = ()


class ObligationAssessment(StrictModel):
    obligation_id: Text
    outcome: Literal["covered", "missing", "partial", "unassessed"]
    targets: tuple[Text, ...] = ()
    rationale: Text
    evidence: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def meaningful_witness(self):
        if self.outcome in {"covered", "partial"} and (not self.targets or not self.evidence):
            raise ValueError("Covered/partial obligations require targets and source evidence")
        return self


class FactualAudit(StrictModel):
    id: Text
    claim: Text
    targets: IDs
    verdict: Literal["supported", "unsupported", "uncertain", "unassessed", "abstained"]
    rationale: Text
    evidence: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def judged_support(self):
        if self.verdict in {"supported", "unsupported"} and not self.evidence:
            raise ValueError("A supported/unsupported factual judgment requires source evidence")
        return self


class BenchmarkAssessment(StrictModel):
    format_version: Literal["protocol_benchmark_assessment/1.0"] = "protocol_benchmark_assessment/1.0"
    reference_sha256: Hash
    workflow_sha256: Hash
    source_packet_sha256: Hash
    inspected_passages: tuple[PassageRef, ...]
    obligations: tuple[ObligationAssessment, ...]
    factual_audits: tuple[FactualAudit, ...]
    factual_audit_scope: Literal["partial", "complete_declared"] = "partial"


class GraphReviewAssessment(StrictModel):
    """A fresh graph source review and a separate reference-coverage judgment."""

    workflow_review: WorkflowReview
    assessment: BenchmarkAssessment


class UnknownPreservationCheck(StrictModel):
    """A judgment of retained uncertainty, never a new resolved scientific fact."""

    unknown_id: Text
    outcome: Literal["retained", "partial", "missing", "reference_revision_needed", "unassessed"]
    targets: tuple[Text, ...] = ()
    rationale: Text
    evidence: tuple[Evidence, ...] = ()

    @model_validator(mode="after")
    def meaningful_judgment(self):
        if self.outcome in {"retained", "partial"} and not self.targets:
            raise ValueError("Retained/partial unknowns require qualified graph witnesses")
        if self.outcome == "reference_revision_needed" and not self.evidence:
            raise ValueError("Reference revision requires source evidence, not an unsupported resolution")
        return self


class BenchmarkAssessmentV2(BenchmarkAssessment):
    """Assessment 1.1 adds exhaustive uncertainty accounting; 1.0 is unchanged."""

    format_version: Literal["protocol_benchmark_assessment/1.1"] = Field(...)
    unknown_checks: tuple[UnknownPreservationCheck, ...]


class GraphReviewAssessmentV2(StrictModel):
    workflow_review: WorkflowReview
    assessment: BenchmarkAssessmentV2


def parse_assessment(value) -> BenchmarkAssessment | BenchmarkAssessmentV2:
    """Dispatch archived versions without upgrading or changing legacy bytes."""
    if isinstance(value, (BenchmarkAssessment, BenchmarkAssessmentV2)):
        return value
    value = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
    if not isinstance(value, dict):
        raise ValueError("Benchmark assessment must be a JSON object")
    version = value.get("format_version", "protocol_benchmark_assessment/1.0")
    model = {"protocol_benchmark_assessment/1.0": BenchmarkAssessment,
             "protocol_benchmark_assessment/1.1": BenchmarkAssessmentV2}.get(version)
    if model is None:
        raise ValueError(f"Unsupported benchmark assessment version: {version}")
    return model.model_validate(value)


def parse_graph_review(value) -> GraphReviewAssessment | GraphReviewAssessmentV2:
    if isinstance(value, (GraphReviewAssessment, GraphReviewAssessmentV2)):
        return value
    value = json.loads(value) if isinstance(value, (str, bytes, bytearray)) else value
    if not isinstance(value, dict):
        raise ValueError("Graph review assessment must be a JSON object")
    assessment = parse_assessment(value["assessment"])
    model = GraphReviewAssessmentV2 if isinstance(assessment, BenchmarkAssessmentV2) else GraphReviewAssessment
    return model.model_validate(value)
