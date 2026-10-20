"""Source-bound comparison annotations beside an unchanged Workflow.

The prescribed questions are interfaces, not a taxonomy of biological answers.
Keep assertions separate when their support, applicability or context differs;
never collapse a collection's members into one asserted molecular identity.
Quote and hash checks establish provenance, not scientific entailment or a match.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Iterator, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.workflow_extraction.models import (
    Evidence,
    Text,
    Workflow,
    digest,
    validate_evidence,
)
from genomics_discovery.curation.workflow_extraction.sources import load_packet

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
STATE_QUESTIONS = {
    "entity": "What material or data entities are present, at what unit of description?",
    "form_or_association": "What physical form, compartment or association is established?",
    "identity_or_information": "What molecular identity or information is retained or represented?",
    "lineage": "Which provenance, attribution or parent-to-product relationships are established?",
    "context": "Which conditions, scope and limitations qualify these state assertions?",
}
OPERATION_QUESTIONS = {
    "transformation": "What changes between the exact input and output states?",
    "purpose": "What function does this operation serve in this workflow context?",
    "mechanism": "By what supported physical or computational process does it act?",
    "information_effect": "What information is retained, created, changed or lost?",
    "context": "Which conditions, dependencies and scope qualify this operation?",
}
_KNOWN = {"reported", "inferred"}


class ProfileAssertion(StrictModel):
    id: Text
    status: Literal["reported", "inferred", "unknown", "not_assessed"]
    applicability: Literal["applicable", "not_applicable"] = "applicable"
    value: Text | None = None
    value_kind: Literal["value", "absent"] | None = None
    rationale: Text
    evidence: tuple[Evidence, ...] = ()
    unknown_reason: Literal[
        "documentation_absent", "not_reported", "conflicting_evidence", "other"
    ] | None = None

    @model_validator(mode="after")
    def explicit_knowledge(self) -> ProfileAssertion:
        if self.status in _KNOWN:
            if not self.evidence:
                raise ValueError("A reported or inferred assertion requires source evidence")
            if self.status == "reported" and any(e.support != "reported" for e in self.evidence):
                raise ValueError("A reported assertion requires reported evidence only")
            if self.status == "inferred" and not any(e.support == "inferred" for e in self.evidence):
                raise ValueError("An inferred assertion requires explicit inferred evidence")
            if self.applicability == "applicable":
                if self.value_kind is None or (self.value_kind == "value") != (self.value is not None):
                    raise ValueError("Supported values and supported absence must be explicit")
            elif self.value is not None or self.value_kind is not None:
                raise ValueError("Not-applicable assertions explain applicability, not a value")
        else:
            if self.applicability != "applicable":
                raise ValueError("Not-applicable is a supported assertion, not an unknown")
            if self.value is not None or self.value_kind is not None:
                raise ValueError("Unknown and not-assessed assertions cannot contain a value")
            if self.status == "not_assessed" and self.evidence:
                raise ValueError("Not-assessed assertions cannot contain evidence")
        if (self.status == "unknown") != (self.unknown_reason is not None):
            raise ValueError("Only unknown assertions require an unknown_reason")
        return self


Assertions = Annotated[tuple[ProfileAssertion, ...], Field(min_length=1)]


class StateProfile(StrictModel):
    target_id: Text
    entity: Assertions = Field(description=STATE_QUESTIONS["entity"])
    form_or_association: Assertions = Field(description=STATE_QUESTIONS["form_or_association"])
    identity_or_information: Assertions = Field(description=STATE_QUESTIONS["identity_or_information"])
    lineage: Assertions = Field(description=STATE_QUESTIONS["lineage"])
    context: Assertions = Field(description=STATE_QUESTIONS["context"])


class OperationProfile(StrictModel):
    target_id: Text
    transformation: Assertions = Field(description=OPERATION_QUESTIONS["transformation"])
    purpose: Assertions = Field(description=OPERATION_QUESTIONS["purpose"])
    mechanism: Assertions = Field(description=OPERATION_QUESTIONS["mechanism"])
    information_effect: Assertions = Field(description=OPERATION_QUESTIONS["information_effect"])
    context: Assertions = Field(description=OPERATION_QUESTIONS["context"])


class ComparisonProfile(StrictModel):
    format_version: Literal["workflow_comparison_profile/1.0"] = "workflow_comparison_profile/1.0"
    workflow_sha256: Hash
    source_packet_sha256: Hash
    states: tuple[StateProfile, ...] = ()
    operations: tuple[OperationProfile, ...] = ()

    @model_validator(mode="after")
    def unique_scoped_records(self) -> ComparisonProfile:
        records = (*self.states, *self.operations)
        if not records:
            raise ValueError("A comparison profile requires at least one target")
        if len({record.target_id for record in records}) != len(records):
            raise ValueError("Comparison profile targets must be unique")
        ids = [assertion.id for _, _, assertion in iter_profile_assertions(self)]
        if len(ids) != len(set(ids)):
            raise ValueError("Comparison profile assertion IDs must be unique")
        return self


def iter_profile_assertions(
    profile: ComparisonProfile,
) -> Iterator[tuple[StateProfile | OperationProfile, str, ProfileAssertion]]:
    for records, questions in ((profile.states, STATE_QUESTIONS),
                               (profile.operations, OPERATION_QUESTIONS)):
        for record in records:
            for field in questions:
                for assertion in getattr(record, field):
                    yield record, field, assertion


def comparison_profile_schema() -> dict:
    """Closed record shapes with open scientific answers and source Evidence."""
    return ComparisonProfile.model_json_schema()


def validate_profile(profile: ComparisonProfile, workflow: Workflow, packet: dict) -> list[str]:
    """Check exact bindings and quotes; load_packet separately checks original bytes."""
    errors = []
    if profile.workflow_sha256 != digest(workflow):
        errors.append("Profile workflow_sha256 does not match the workflow")
    if profile.source_packet_sha256 != digest(packet):
        errors.append("Profile source_packet_sha256 does not match the source packet")
    for field in ("method_id", "title", "scope", "variant"):
        if packet.get(field) != getattr(workflow, field):
            errors.append(f"Workflow {field} differs from its source packet")
    for records, kind, objects in ((profile.states, "state", workflow.states),
                                   (profile.operations, "operation", workflow.operations)):
        known = {item.id for item in objects}
        errors.extend(f"Profile contains unknown {kind} target: {record.target_id}"
                      for record in records if record.target_id not in known)
    errors.extend(validate_evidence(workflow, packet))
    errors.extend(validate_evidence(profile, packet))
    return errors


def validate_profile_selection(profile: ComparisonProfile, occurrence) -> list[str]:
    """An opted-in cut needs every selected operation and retained state annotated."""
    errors = []
    for field in ("workflow_sha256", "source_packet_sha256"):
        if getattr(profile, field) != getattr(occurrence, field):
            errors.append(f"Profile {field} does not match the selected occurrence")
    for kind, selected, records in (
        ("operation", occurrence.operations, profile.operations),
        ("state", (*occurrence.inputs, *occurrence.outputs, *occurrence.internal_states), profile.states),
    ):
        missing = {item.id for item in selected} - {record.target_id for record in records}
        if missing:
            errors.append(f"Profile missing selected {kind} targets: {sorted(missing)}")
    return errors


def load_comparison_profile(profile_path: Path, workflow_path: Path,
                            packet_path: Path) -> ComparisonProfile:
    """Fail closed on stale original source bytes, packet, workflow or sidecar."""
    profile = ComparisonProfile.model_validate_json(Path(profile_path).read_bytes())
    workflow = Workflow.model_validate_json(Path(workflow_path).read_bytes())
    packet = load_packet(Path(packet_path))
    if errors := validate_profile(profile, workflow, packet):
        raise ValueError("; ".join(errors))
    return profile


def comparison_profile_view(profile: ComparisonProfile, workflow: Workflow, packet: dict) -> dict:
    """Keep original graph references beside annotations without copying the graph."""
    if errors := validate_profile(profile, workflow, packet):
        raise ValueError("; ".join(errors))
    targets = {}
    for state in workflow.states:
        if any(record.target_id == state.id for record in profile.states):
            targets[f"state:{state.id}"] = {
                "kind": state.kind, "source_context_description": state.description,
            }
    for operation in workflow.operations:
        if any(record.target_id == operation.id for record in profile.operations):
            targets[f"operation:{operation.id}"] = {
                "source_context_name": operation.name,
                "input_target_ids": [f"state:{sid}" for sid in operation.inputs],
                "output_target_ids": [f"state:{sid}" for sid in operation.outputs],
            }
    return {"profile": profile.model_dump(mode="json"), "target_context": targets,
            "boundary": "Source context names and graph references are not profile assertions; "
                        "annotations do not establish equivalence or compatibility."}


def profile_retrieval_documents(profile: ComparisonProfile, workflow: Workflow,
                                packet: dict) -> list[dict]:
    """Retrieve supported, applicable assertions only; results are never relations."""
    view = comparison_profile_view(profile, workflow, packet)
    documents = []
    for record, field, assertion in iter_profile_assertions(profile):
        if assertion.status not in _KNOWN or assertion.applicability != "applicable":
            continue
        kind = "state" if isinstance(record, StateProfile) else "operation"
        typed_target = f"{kind}:{record.target_id}"
        context = view["target_context"][typed_target]
        label = context.get("source_context_name", context.get("source_context_description"))
        value = assertion.value if assertion.value_kind == "value" else f"ABSENT: {assertion.rationale}"
        documents.append({
            "target_id": record.target_id, "target_type": kind,
            "field": field, "assertion_id": assertion.id,
            "status": assertion.status, "value_kind": assertion.value_kind,
            "text": f"workflow {workflow.method_id}\nsource context {typed_target}: {label}\n"
                    f"{field} ({assertion.status}, {assertion.value_kind}): {value}",
            "source_context": context,
            "evidence": [e.model_dump(mode="json") for e in assertion.evidence],
        })
    return documents


def profile_retrieval_text(profile: ComparisonProfile, workflow: Workflow, packet: dict) -> str:
    return "\n\n".join(document["text"] for document in
                        profile_retrieval_documents(profile, workflow, packet))
