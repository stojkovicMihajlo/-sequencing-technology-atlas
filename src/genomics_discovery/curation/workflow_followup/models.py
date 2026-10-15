"""Small follow-up contract: one accounted decision per selected gap."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.workflow_extraction.models import Evidence, PassageRef, Text

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Action = Literal[
    "repair_record", "retrieve_source", "inspect_original", "retain_unknown", "outside_scope"
]


class EvidenceRequest(StrictModel):
    required_property: Text
    expected_document: Text
    query: Text


class OriginalInspection(StrictModel):
    source_id: Text
    page: Annotated[int, Field(strict=True, ge=1)]
    render_receipt: Text


class Issue(StrictModel):
    """One question within a possibly compound historical gap description."""

    id: Text
    question: Text
    targets: Annotated[tuple[Text, ...], Field(min_length=1)]
    action: Action
    rationale: Text
    consequence_for_scope: Text
    inspected_passages: Annotated[tuple[PassageRef, ...], Field(min_length=1)]
    inspected_originals: tuple[OriginalInspection, ...] = ()
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]
    proposed_correction: Text | None = None
    next_evidence: EvidenceRequest | None = None

    @model_validator(mode="after")
    def actionable(self):
        if self.action == "repair_record" and not self.proposed_correction:
            raise ValueError("A repair needs a source-backed proposed correction")
        if self.action != "repair_record" and self.proposed_correction is not None:
            raise ValueError("Only a repair proposes a correction")
        if self.action in {"retrieve_source", "inspect_original"} and not self.next_evidence:
            raise ValueError("Acquisition/inspection needs an exact property and document request")
        return self


class GapCheck(StrictModel):
    gap_id: Text
    issues: Annotated[tuple[Issue, ...], Field(min_length=1)]


class GapAudit(StrictModel):
    format_version: Literal["workflow_gap_audit/1.1"] = "workflow_gap_audit/1.1"
    workflow_sha256: Hash
    source_packet_sha256: Hash
    workflow_review_sha256: Hash
    checks: tuple[GapCheck, ...]
    notes: tuple[Text, ...] = ()
