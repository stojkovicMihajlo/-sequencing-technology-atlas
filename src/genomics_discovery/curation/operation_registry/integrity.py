"""Check archived bindings without replaying scientific review or reopening papers."""

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipReview, TransformationOccurrence,
)

from .models import Assignment

FORMAT = "operation_concept_registry/1.0"


def validate_snapshot(snapshot: dict) -> None:
    if (snapshot.get("format_version") != FORMAT
        or not isinstance(snapshot.get("concepts"), dict)
        or not isinstance(snapshot.get("assignments"), dict)):
        raise ValueError("Unsupported concept registry")
    seen_concepts, seen_uses = set(), set()
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
        use = (cid, member.occurrence_sha256)
        if use in seen_uses:
            raise ValueError("Duplicate stored concept use")
        seen_uses.add(use)
        seen_concepts.add(cid)
    if seen_concepts != snapshot["concepts"].keys():
        raise ValueError("Stored concept has no reviewed assignment")
