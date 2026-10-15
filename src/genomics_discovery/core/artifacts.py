"""Small scientific placeholders and content-addressed artifact envelopes."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceRef(StrictModel):
    source_id: str
    document: str
    locator: str | None = None
    excerpt: str | None = None


class EvidenceClaim(StrictModel):
    claim: str
    status: Literal["candidate", "reviewed", "contradicted", "unresolved"] = "candidate"
    source_refs: tuple[SourceRef, ...] = ()
    review_note: str | None = None
    checked_by: str | None = None
    check_method: str | None = None

    @model_validator(mode="after")
    def checked_support_has_located_source(self) -> EvidenceClaim:
        if self.status == "reviewed":
            if not self.source_refs or any(
                not ref.locator or not ref.excerpt for ref in self.source_refs
            ):
                raise ValueError("Reviewed evidence requires a source locator and excerpt")
            if not self.checked_by or not self.check_method or not self.review_note:
                raise ValueError("Reviewed evidence requires a recorded reviewer and check")
        return self

    @classmethod
    def reviewed(
        cls,
        claim: str,
        *,
        source_refs: tuple[SourceRef, ...],
        review_note: str,
        checked_by: str,
        check_method: str,
    ) -> EvidenceClaim:
        if not source_refs or any(not ref.locator or not ref.excerpt for ref in source_refs):
            raise ValueError("Reviewed evidence requires a source locator and excerpt")
        return cls(
            claim=claim,
            status="reviewed",
            source_refs=source_refs,
            review_note=review_note,
            checked_by=checked_by,
            check_method=check_method,
        )


class ResearchContext(StrictModel):
    question: str
    entry_mode: Literal["biology_led", "technology_led", "mixed"] = "mixed"
    constraints: tuple[str, ...] = ()
    lab_capabilities: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()


class ConditionalRisk(StrictModel):
    """A scoped hazard to the biological claim, never a universal ban on an operation."""

    condition: str = Field(min_length=1)
    affected_requirement: str = Field(min_length=1)
    possible_failure: str = Field(min_length=1)
    evidence_status: Literal[
        "reported_in_context", "source_supported_inference", "untested", "unknown"
    ]
    source_refs: tuple[SourceRef, ...] = ()
    next_check: str = Field(min_length=1)

    @model_validator(mode="after")
    def supported_risk_has_located_source(self) -> ConditionalRisk:
        if self.evidence_status in {"reported_in_context", "source_supported_inference"}:
            if not self.source_refs or any(
                not ref.locator or not ref.excerpt for ref in self.source_refs
            ):
                raise ValueError("Supported conditional risks require located source excerpts")
        return self


class OpportunityBrief(StrictModel):
    title: str
    capability_gap: str
    native_specimen_and_state: str | None = None
    specimen_and_state: str | None = None
    target_property: str | None = None
    original_unit: str | None = None
    measurement_invariants: tuple[str, ...] = ()
    conditional_risks: tuple[ConditionalRisk, ...] = ()
    tradeoffs_to_measure: tuple[str, ...] = ()
    user_constraints: tuple[str, ...] = ()
    advance_type: (
        Literal[
            "new_distinction",
            "same_unit_linkage",
            "performance_frontier",
            "specimen_access",
            "readout_or_platform",
            "unresolved",
        ]
        | None
    ) = None
    frontier_claim_status: (
        Literal[
            "directly_reported",
            "benchmark_supported",
            "source_supported_inference",
            "untested",
            "unknown",
        ]
        | None
    ) = None
    nearest_direct_signal: str | None = None
    nearest_biological_inference: str | None = None
    information_path_issue: str | None = None
    comparison_envelope: str | None = None
    critical_uncertainty: str | None = None
    next_evidence_check: str | None = None
    plausible_lever_classes: tuple[str, ...] = ()
    measurement_cards_artifact_id: str | None = None
    affected_use: str | None = None
    current_approach: str | None = None
    observed_bottleneck: str | None = None
    desired_measurement: str | None = None
    baseline_comparison: str | None = None
    success_readout: str | None = None
    lab_capabilities: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()
    proposed_direction: str | None = None
    opportunity_family: str | None = None
    physical_change: str | None = None
    new_observable: str | None = None
    starting_workflow: str | None = None
    intervention_point: str | None = None
    critical_compatibility: str | None = None
    first_feasibility_test: str | None = None
    feasibility_route: Literal[
        "existing_workflow_hypothesis",
        "facility_or_custom_dependency",
        "new_platform_hypothesis",
        "undetermined",
    ] = "undetermined"
    feasibility_status: Literal[
        "unreviewed", "needs_platform_review", "needs_equipment_check", "experiment_ready"
    ] = "unreviewed"
    equipment_access: Literal["unknown", "confirmed", "shared_access", "collaborator"] = "unknown"
    kill_condition: str | None = None
    instrument_change: Literal["no_new_instrument_hypothesized", "possible", "unknown"] = "unknown"
    opening_basis: (
        Literal[
            "single_method_limitation",
            "benchmark_tradeoff",
            "cross_method_transfer",
            "specimen_constraint",
            "unexplained_observation",
        ]
        | None
    ) = None
    evidence_rationale: str | None = None
    cross_paper_reasoning: str | None = None
    prior_art_status: Literal[
        "not_searched", "direct_overlap", "bounded_search_no_direct_match", "insufficient_evidence"
    ] = "not_searched"
    prior_art_query: str | None = None
    closest_overlap: str | None = None
    remaining_difference: str | None = None
    search_limitations: tuple[str, ...] = ()
    search_queries: tuple[str, ...] = ()
    search_paper_ids: tuple[str, ...] = ()


class RequirementResponse(StrictModel):
    """How a proposed design handles one requirement; this is not verification."""

    requirement: str = Field(min_length=1)
    status: Literal["proposed_preservation", "sacrificed", "unresolved"]
    route: str = Field(min_length=1)
    decisive_check: str = Field(min_length=1)


class ConditionalRiskResponse(StrictModel):
    """A proposed design's response to a numbered risk in the opportunity brief."""

    risk_index: int = Field(ge=0)
    condition_status: Literal["occurs", "avoided", "unknown"]
    handling_status: Literal[
        "not_applicable", "mitigation_hypothesis", "accepted_tradeoff", "unresolved"
    ]
    route: str = Field(min_length=1)
    decisive_check: str = Field(min_length=1)

    @model_validator(mode="after")
    def coherent_condition_and_handling(self) -> ConditionalRiskResponse:
        if (self.condition_status == "avoided") != (self.handling_status == "not_applicable"):
            raise ValueError("Avoided conditions must have not_applicable handling")
        return self


class AssayDesign(StrictModel):
    title: str
    physical_intervention: str
    readout_path: str
    information_recorded: str | None = None
    retention_mechanism: str | None = None
    existing_workflow: str | None = None
    changed_steps: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    required_equipment: tuple[str, ...] = ()
    instrumentation_requirement: Literal[
        "standard_equipment", "additional_equipment", "unknown"
    ] = "unknown"
    baseline_comparison: str | None = None
    discriminating_test: str | None = None
    positive_control: str | None = None
    negative_control: str | None = None
    failure_modes: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    requirement_responses: tuple[RequirementResponse, ...] = ()
    risk_responses: tuple[ConditionalRiskResponse, ...] = ()
    source_refs: tuple[SourceRef, ...] = ()


class VerificationCheck(StrictModel):
    question: str
    status: Literal["supported", "contradicted", "unresolved"]
    rationale: str
    source_refs: tuple[SourceRef, ...] = ()
    checked_by: str | None = None

    @model_validator(mode="after")
    def supported_check_has_source(self) -> VerificationCheck:
        if self.status == "supported" and (
            not self.checked_by
            or not self.source_refs
            or any(not ref.locator or not ref.excerpt for ref in self.source_refs)
        ):
            raise ValueError("Supported check requires reviewer and located source excerpt")
        return self


class VerificationDossier(StrictModel):
    design_artifact_id: str
    checks: tuple[VerificationCheck, ...] = ()
    recommendation: Literal["review", "revise", "abstain"]
    unresolved: tuple[str, ...] = ()


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class ArtifactEnvelope(StrictModel):
    artifact_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_name: str
    schema_version: int = 1
    payload: dict[str, Any]
    payload_sha256: str
    envelope_sha256: str
    parent_ids: tuple[str, ...] = ()
    producer: str
    run_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def wrap(
        cls,
        value: BaseModel,
        *,
        producer: str,
        run_id: str | None = None,
        parent_ids: tuple[str, ...] = (),
        schema_version: int = 1,
    ) -> ArtifactEnvelope:
        payload = value.model_dump(mode="json")
        digest = hashlib.sha256(_canonical(payload)).hexdigest()
        artifact = cls(
            schema_name=type(value).__name__,
            schema_version=schema_version,
            payload=payload,
            payload_sha256=digest,
            parent_ids=parent_ids,
            producer=producer,
            run_id=run_id,
            envelope_sha256="",
        )
        envelope_hash = hashlib.sha256(
            _canonical(artifact.model_dump(mode="json", exclude={"envelope_sha256"}))
        ).hexdigest()
        return artifact.model_copy(update={"envelope_sha256": envelope_hash})

    def verify(self) -> bool:
        payload_valid = hashlib.sha256(_canonical(self.payload)).hexdigest() == self.payload_sha256
        envelope_valid = (
            hashlib.sha256(
                _canonical(self.model_dump(mode="json", exclude={"envelope_sha256"}))
            ).hexdigest()
            == self.envelope_sha256
        )
        return payload_valid and envelope_valid

    def decode(self, schema: type[BaseModel]) -> BaseModel:
        if not self.verify():
            raise ValueError(f"Artifact {self.artifact_id} failed content hash check")
        if self.schema_name != schema.__name__:
            raise ValueError(f"Expected {schema.__name__}, got {self.schema_name}")
        return schema.model_validate(self.payload)
