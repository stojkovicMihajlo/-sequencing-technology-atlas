"""Task-directed vocabulary decisions; source support alone cannot admit a label."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.workflow_extraction.models import Text, EvidenceSet, digest, validate_evidence
from genomics_discovery.curation.workflow_extraction.transformations import (
    Hash, MemberCheck, MembershipReview, review_membership,
)
from .models import Assignment
from .definition_review import DefinitionCheck, validate_definition_review


class BoundaryExample(StrictModel):
    occurrence_id: Text
    membership: Literal["member", "non_member", "unknown"]
    reason: Text
    evidence: EvidenceSet


class RevisionRequest(StrictModel):
    concept_ids: Annotated[tuple[Text, ...], Field(min_length=1)]
    action: Literal["broaden", "split", "merge", "clarify"]
    reason: Text


class AdmissionCase(StrictModel):
    question: Text
    existing_concept_ids: tuple[Text, ...]
    existing_plus_filters: Text
    filters_sufficient: bool
    lost_distinction_if_merged: Text
    properties_to_preserve: tuple[Text, ...]
    boundary_examples: tuple[BoundaryExample, ...]
    revision_requests: tuple[RevisionRequest, ...] = ()


class LabelDecision(StrictModel):
    occurrence_id: Text
    assignment: Assignment | None
    case: AdmissionCase
    explanation: Text


class AdmissionReview(StrictModel):
    decision_sha256: Hash
    source_review: MembershipReview | None
    vocabulary_verdict: Literal["admit", "properties_only", "provisional"]
    question_useful: bool
    boundary_clear: bool
    context_properties_separated: bool
    existing_plus_filters_sufficient: bool
    rationale: Text
    boundary_checks: tuple[MemberCheck, ...] = ()


class AdmissionReviewV2(AdmissionReview):
    format_version: Literal["concept_admission_review/2.0"]
    definition_checks: tuple[DefinitionCheck, ...]


def load_admission_review(value):
    """Read historical reviews unchanged; only V2 can authorize a new admission."""
    return TypeAdapter(AdmissionReviewV2 | AdmissionReview).validate_python(value)


class VocabularyProposal(StrictModel):
    decisions: Annotated[tuple[LabelDecision, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def distinct_targets(self):
        if len({d.occurrence_id for d in self.decisions}) != len(self.decisions):
            raise ValueError("Exactly one vocabulary decision per supplied selection")
        return self


class VocabularyReview(StrictModel):
    proposal_sha256: Hash
    reviews: tuple[AdmissionReviewV2 | AdmissionReview, ...]


class CurrentVocabularyReview(VocabularyReview):
    reviews: tuple[AdmissionReviewV2, ...]


def validate_decision(decision, snapshot, occurrences, packets, *, allow_stale_new=False):
    from .store import check_proposal
    if decision.occurrence_id not in occurrences:
        raise ValueError("Unknown decision occurrence")
    case, proposal = decision.case, decision.assignment
    referenced = set(case.existing_concept_ids)
    referenced.update(cid for req in case.revision_requests for cid in req.concept_ids)
    if referenced - snapshot["concepts"].keys():
        raise ValueError("Admission case references an unknown existing concept")
    example_ids = [e.occurrence_id for e in case.boundary_examples]
    if len(set(example_ids)) != len(example_ids) or set(example_ids) - occurrences.keys():
        raise ValueError("Boundary examples must be distinct supplied selections")
    for example in case.boundary_examples:
        errors = validate_evidence(example, packets[example.occurrence_id])
        if errors:
            raise ValueError("; ".join(errors))
    if proposal is None:
        return []
    if proposal.members[0].occurrence_id != decision.occurrence_id:
        raise ValueError("Assignment changed the decision target")
    if not any(e.occurrence_id == decision.occurrence_id and e.membership == "member"
               for e in case.boundary_examples):
        raise ValueError("Assignment must include its target as a positive boundary example")
    return check_proposal(snapshot, proposal, occurrences, packets, allow_stale_reuse=True,
                          allow_stale_new=allow_stale_new)


def evaluate(decision, review, occurrences, packets):
    """Derive an outcome; no reviewer-supplied overall approval bypasses either gate."""
    if review.decision_sha256 != digest(decision):
        raise ValueError("Admission review does not bind the exact decision")
    if not isinstance(review, AdmissionReviewV2):
        raise ValueError("A new admission requires concept_admission_review/2.0; historical reviews remain readable")
    proposal = decision.assignment
    extra_examples = {e.occurrence_id: e for e in decision.case.boundary_examples
                      if e.occurrence_id != decision.occurrence_id}
    extra_checks = {c.occurrence_id: c for c in review.boundary_checks}
    if len(extra_checks) != len(review.boundary_checks) or extra_checks.keys() != extra_examples.keys():
        raise ValueError("Every additional boundary example needs its own source check")
    for ident, checked in extra_checks.items():
        errors = validate_evidence(checked, packets[ident])
        cited = {(e.source_id, e.passage_id) for e in checked.evidence}
        inspected = {(p.source_id, p.passage_id) for p in checked.inspected_passages}
        proposed = {(e.source_id, e.passage_id) for e in extra_examples[ident].evidence}
        if errors or cited - inspected or not cited & proposed:
            raise ValueError("Invalid boundary-example source coverage: " + "; ".join(errors))
    source_status = "not_proposed"
    if proposal is None:
        if review.source_review is not None or review.vocabulary_verdict == "admit":
            raise ValueError("Cannot admit an absent assignment")
    else:
        if review.source_review is None:
            raise ValueError("Proposed assignment needs a source review")
        source_status = review_membership(proposal, review.source_review,
                                          occurrences, packets)["status"]
    definition_status = validate_definition_review(
        proposal, review.definition_checks, review.source_review, packets[decision.occurrence_id])
    if review.vocabulary_verdict == "admit":
        if not (review.question_useful and review.boundary_clear
                and review.context_properties_separated):
            raise ValueError("Admission contradicts its utility checks")
        if proposal.decision == "new" and (
            decision.case.filters_sufficient or review.existing_plus_filters_sufficient
        ):
            raise ValueError("A sufficient property-filter alternative cannot justify a new concept")
    outcome = review.vocabulary_verdict
    if outcome == "admit" and any(c.verdict != "supported" for c in review.boundary_checks):
        outcome = "provisional"
    if proposal is not None and source_status != "supported_membership_candidate":
        outcome = source_status
    if definition_status == "needs_revision" or (
        definition_status == "research_needed" and outcome != "needs_revision"
    ):
        outcome = definition_status
    return {"status": outcome, "source_status": source_status,
            "definition_status": definition_status,
            "decision_sha256": digest(decision), "review_sha256": digest(review)}


def validate_batch(proposal, review, snapshot, occurrences, packets, *, allow_stale_new=False):
    if set(occurrences) != {d.occurrence_id for d in proposal.decisions}:
        raise ValueError("Batch must account for every supplied selection exactly once")
    if review.proposal_sha256 != digest(proposal):
        raise ValueError("Vocabulary review does not bind the exact proposal")
    checks = {r.decision_sha256: r for r in review.reviews}
    if len(checks) != len(review.reviews) or set(checks) != {digest(d) for d in proposal.decisions}:
        raise ValueError("Exactly one admission review per decision is required")
    outcomes = []
    for decision in proposal.decisions:
        lookups = validate_decision(decision, snapshot, occurrences, packets, allow_stale_new=allow_stale_new)
        outcomes.append({**evaluate(decision, checks[digest(decision)], occurrences, packets),
                         "lookup_results": lookups, "occurrence_id": decision.occurrence_id})
    from .consistency import check_batch_consistency
    check_batch_consistency(proposal, review, snapshot, outcomes)
    return outcomes
