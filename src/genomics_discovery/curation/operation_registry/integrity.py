"""Check archived bindings without replaying scientific review or reopening papers."""

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipReview, TransformationOccurrence,
)

from .models import Assignment
from .admission import AdmissionReview, LabelDecision

FORMAT = "operation_concept_registry/1.0"


def validate_snapshot(snapshot: dict) -> None:
    if (snapshot.get("format_version") != FORMAT
        or not isinstance(snapshot.get("concepts"), dict)
        or not isinstance(snapshot.get("assignments"), dict)):
        raise ValueError("Unsupported concept registry")
    seen_concepts, seen_uses = set(), {}
    for key, row in snapshot.get("decisions", {}).items():
        decision = LabelDecision.model_validate(row["decision"])
        check = AdmissionReview.model_validate(row["review"])
        occurrence = TransformationOccurrence.model_validate(row["occurrence"])
        if (key != digest(decision) or check.decision_sha256 != key
            or digest(check) != row["review_sha256"]
            or decision.occurrence_id != occurrence.id
            or digest(occurrence) != row["occurrence_sha256"]):
            raise ValueError("Stored admission decision/review binding differs")
    for key, row in snapshot["assignments"].items():
        proposal = Assignment.model_validate(row["proposal"])
        occurrence = TransformationOccurrence.model_validate(row["occurrence"])
        review = MembershipReview.model_validate(row["review"])
        cid = proposal.definition.id
        if row["concept_id"] != cid or snapshot["concepts"].get(cid) != proposal.concept():
            raise ValueError("Stored concept differs from its reviewed assignment")
        member = proposal.members[0]
        if (key != digest(proposal) or row["occurrence_sha256"] != digest(occurrence)
            or member.occurrence_sha256 != digest(occurrence)
            or member.occurrence_id != occurrence.id):
            raise ValueError("Stored assignment/occurrence binding differs")
        if (row["review_sha256"] != digest(review) or review.candidate_sha256 != key
            or len(review.checks) != 1 or review.checks[0].occurrence_id != occurrence.id
            or review.checks[0].verdict != "supported"):
            raise ValueError("Stored assignment lacks its bound positive review")
        if decision_key := row.get("admission_decision_sha256"):
            admission = snapshot.get("decisions", {}).get(decision_key)
            if admission is None:
                raise ValueError("Stored assignment lacks its admission record")
            decision = LabelDecision.model_validate(admission["decision"])
            check = AdmissionReview.model_validate(admission["review"])
            if (decision.assignment != proposal or check.source_review != review
                or check.vocabulary_verdict != "admit" or admission["outcome"]["status"] != "admit"
                or not (check.question_useful and check.boundary_clear and check.context_properties_separated)
                or (proposal.decision == "new" and (
                    decision.case.filters_sufficient or check.existing_plus_filters_sufficient))):
                raise ValueError("Stored assignment lacks a consistent admission approval")
        use = (cid, member.occurrence_sha256)
        for predecessor_id in row.get("supersedes_assignment_ids", []):
            predecessor = snapshot["assignments"].get(predecessor_id)
            if (predecessor is None or predecessor_id == key
                or predecessor.get("admission_decision_sha256")
                or predecessor["concept_id"] != cid
                or predecessor["occurrence_sha256"] != member.occurrence_sha256):
                raise ValueError("Invalid legacy assignment supersession")
        seen_uses.setdefault(use, []).append(key)
        seen_concepts.add(cid)
    for keys in seen_uses.values():
        if len(keys) < 2:
            continue
        admitted = [k for k in keys if snapshot["assignments"][k].get("admission_decision_sha256")]
        if (len(admitted) != 1 or set(snapshot["assignments"][admitted[0]].get("supersedes_assignment_ids", []))
            != set(keys) - set(admitted)):
            raise ValueError("Duplicate stored concept use without explicit supersession")
    if seen_concepts != snapshot["concepts"].keys():
        raise ValueError("Stored concept has no reviewed assignment")
