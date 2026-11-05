"""Bounded specialist critique and explicit disposition of every objection.

These contracts check candidate identity, coverage and issue preservation. They
do not prove that a specialist's objection or a synthesis rebuttal is correct.
The caller validates exact atlas/source references and runs independent sessions.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.illumina.models import Text, digest
from .models import (
    AtlasReference, CandidateIdea, Hash, LiteratureReference, ReviewIssue,
    review_targets, validate_review_binding,
)

Specialism = Literal["chemistry_interfaces", "measurement_benefit"]
SPECIALISMS = ("chemistry_interfaces", "measurement_benefit")
_RESEARCH_AREAS = {
    "chemistry_interfaces": ("mechanism", "interfaces"),
    "measurement_benefit": ("capability", "challenge"),
}


class SpecialistIssue(ReviewIssue):
    atlas_refs: tuple[AtlasReference, ...] = ()
    literature_refs: tuple[LiteratureReference, ...] = ()


class SpecialistCandidateReview(StrictModel):
    candidate_id: Text
    candidate_sha256: Hash
    reviewed_targets: Annotated[tuple[Text, ...], Field(min_length=1)] = Field(
        description="Valid targets actually inspected through this specialist lens. Include candidate, every change:<id>, and the lens-specific research targets when present; add decision-critical States/Operations/Flows/properties.")
    issues: tuple[SpecialistIssue, ...]
    summary: Text = Field(description="What this lens establishes, objections, limits and unchecked dependencies. No novelty/readiness certification.")

    @model_validator(mode="after")
    def unique_records(self):
        if len(set(self.reviewed_targets)) != len(self.reviewed_targets):
            raise ValueError("Specialist inspected targets must be unique")
        if len({issue.id for issue in self.issues}) != len(self.issues):
            raise ValueError("Specialist issue IDs must be unique within each candidate")
        return self


class SpecialistPanel(StrictModel):
    format_version: Literal["ideation_specialist_review/1"] = "ideation_specialist_review/1"
    specialism: Specialism
    reviews: Annotated[tuple[SpecialistCandidateReview, ...], Field(min_length=1)]
    summary: Text = Field(description="Comparison of design-specific risks through this lens, without merging designs or deciding final readiness.")


class SpecialistResolution(StrictModel):
    specialism: Specialism
    candidate_id: Text
    issue_id: Text = Field(description="Exact local issue ID from the named specialist's review of this candidate.")
    status: Literal["resolved", "unresolved"]
    reason: Text
    resolution_targets: tuple[Text, ...] = Field(default=(), description=
        "Exact current candidate targets supporting a design-internal rebuttal; a target's existence does not prove the rebuttal.")
    atlas_refs: tuple[AtlasReference, ...] = ()
    literature_refs: tuple[LiteratureReference, ...] = ()

    @model_validator(mode="after")
    def explicit_resolution_basis(self):
        if len(set(self.resolution_targets)) != len(self.resolution_targets):
            raise ValueError("Specialist resolution targets must be unique")
        if self.status == "resolved" and not (
            self.resolution_targets or self.atlas_refs or self.literature_refs
        ):
            raise ValueError("Resolving a specialist objection needs cited evidence or exact candidate targets")
        return self


def specialist_issue_id(specialism: Specialism, issue_id: str) -> str:
    if specialism not in SPECIALISMS:
        raise ValueError("Unknown specialist lens")
    return f"specialist:{specialism}:{issue_id}"


def required_specialist_targets(idea: CandidateIdea, specialism: Specialism) -> set[str]:
    if specialism not in SPECIALISMS:
        raise ValueError("Unknown specialist lens")
    available = review_targets(idea)
    return {"candidate", *{"change:" + item.id for item in idea.changes}} | {
        "research:" + area for area in _RESEARCH_AREAS[specialism]
        if "research:" + area in available
    }


def _candidates_by_id(candidates):
    candidates = tuple(candidates)
    result = {idea.id: idea for idea in candidates}
    if not result or len(result) != len(candidates):
        raise ValueError("Specialist review requires nonempty, distinct candidate IDs")
    return result


def validate_specialist_panel(report, candidates, expected_specialism):
    if report.specialism != expected_specialism or expected_specialism not in SPECIALISMS:
        raise ValueError("Specialist report does not match its assigned lens")
    ideas = _candidates_by_id(candidates)
    ids = [item.candidate_id for item in report.reviews]
    if len(ids) != len(set(ids)) or set(ids) != set(ideas):
        raise ValueError("Specialist must review every exact candidate once")
    for review in report.reviews:
        idea = ideas[review.candidate_id]
        if review.candidate_sha256 != digest(idea):
            raise ValueError("Specialist review is not bound to the exact candidate")
        available = review_targets(idea)
        actual = set(review.reviewed_targets)
        if len(actual) != len(review.reviewed_targets) or actual - available:
            raise ValueError("Specialist inspected targets must be unique valid candidate targets")
        if not required_specialist_targets(idea, expected_specialism) <= actual:
            raise ValueError("Specialist must inspect candidate, every change and its assigned research areas")
        if len({issue.id for issue in review.issues}) != len(review.issues):
            raise ValueError("Specialist issue IDs must be unique within each candidate")
        if any(issue.target not in actual for issue in review.issues):
            raise ValueError("Specialist issue must identify an inspected candidate target")


def validate_specialist_resolutions(synthesis_panel, specialist_panels, candidates):
    """Require exact accounting and preserve every unresolved original objection.

    Called for initial synthesis against the same candidates the specialists saw.
    Revised candidates need the existing final-review previous-issue ledger;
    original specialist reports are not fresh reviews of changed graphs.
    """
    ideas = _candidates_by_id(candidates)
    panels = tuple(specialist_panels)
    lenses = [panel.specialism for panel in panels]
    if len(lenses) != len(set(lenses)) or set(lenses) != set(SPECIALISMS):
        raise ValueError("Synthesis requires exactly one report from each specialist lens")
    expected = {}
    for panel in panels:
        validate_specialist_panel(panel, ideas.values(), panel.specialism)
        for review in panel.reviews:
            for issue in review.issues:
                expected[(panel.specialism, review.candidate_id, issue.id)] = issue
    reviews = {review.candidate_id: review for review in synthesis_panel.reviews}
    if len(reviews) != len(synthesis_panel.reviews) or set(reviews) != set(ideas):
        raise ValueError("Synthesis must review every exact candidate once")
    for candidate_id, review in reviews.items():
        validate_review_binding(review, ideas[candidate_id])
    resolutions = tuple(getattr(synthesis_panel, "specialist_issue_resolutions", ()))
    keys = [(row.specialism, row.candidate_id, row.issue_id) for row in resolutions]
    if len(keys) != len(set(keys)) or set(keys) != set(expected):
        raise ValueError("Synthesis must account for every specialist objection exactly once")
    for row in resolutions:
        issue = expected[(row.specialism, row.candidate_id, row.issue_id)]
        if set(row.resolution_targets) - review_targets(ideas[row.candidate_id]):
            raise ValueError("Specialist resolution references an unknown candidate target")
        current_id = specialist_issue_id(row.specialism, row.issue_id)
        current = next((item for item in reviews[row.candidate_id].issues if item.id == current_id), None)
        if row.status == "unresolved":
            if current is None:
                raise ValueError("Unresolved specialist objection must remain visible in synthesis issues")
            if any(getattr(current, key) != getattr(issue, key)
                   for key in ("target", "severity", "reason", "next_test")):
                raise ValueError("Unresolved specialist objection cannot be weakened or rewritten by synthesis")
            if issue.severity == "blocking" and reviews[row.candidate_id].disposition == "retain_for_testing":
                raise ValueError("An unresolved specialist blocker cannot be retained for testing")
        elif current is not None:
            raise ValueError("A resolved specialist objection cannot also remain as the same open issue")
    known = {(candidate_id, specialist_issue_id(lens, ident))
             for lens, candidate_id, ident in expected}
    for candidate_id, review in reviews.items():
        for issue in review.issues:
            if issue.id.startswith("specialist:") and (candidate_id, issue.id) not in known:
                raise ValueError("Synthesis cannot invent a specialist-origin issue")


SPECIALIST_PROMPT = """ROLE: INDEPENDENT ASSAY SPECIALIST
Use the assigned specialism, read the original request, every candidate_file and
the proposal-specific literature report. Review all candidates through your lens
without rewriting them or substituting a stronger design on their behalf.

For chemistry_interfaces, inspect the experimental property needed from each
decision-critical primitive, its demonstrated substrate/context, and whether
one operation's material and information outputs meet the next one's requirements.
Challenge unsupported chemistry, missing attachment/selectivity mechanisms,
incompatible handoffs, and loss of identity during processing or pooling.
For measurement_benefit, trace the desired biological quantity to the instrument's
direct observation and required inference. Challenge confounded interpretations,
unsupported gains, unmatched comparators, and whether a decisive experiment could
distinguish the proposed improvement. Useful improvement need not create a wholly
absent capability. Literature findings are evidence inputs, not certificates.

Inspect the decision-critical graph paths, not only the prose summary. Account
for candidate, every change:<id> and your lens's research targets: mechanism and
interfaces for chemistry_interfaces; capability and challenge for
measurement_benefit. Include additional exact State/property/Operation/Flow
targets you actually inspect. You need not repeat a complete graph-target ledger;
the synthesis reviewer owns that full review. Copy supplied candidate_bindings.

Create stable local issue IDs with exact affected targets, severity, reason,
next_test and supporting atlas_refs/literature_refs. References may be empty for
a clearly explained candidate-internal inconsistency or missing-evidence issue;
do not fabricate evidence for an absence claim. Give the concrete source/design
basis and what could resolve the objection. A fully specified contextual transfer
with experimental precedent and a decisive test is not a new unsupported primitive
property merely because that particular assay has not been tested. Identify
blocking objections when an essential mechanism/identity path is undefined,
critical chemistry lacks precedent, or demonstrated incompatibility prevents the
claimed measurement. Preserve uncertainty without treating every untested transfer
as fatal. Do not decide final novelty or readiness; synthesis combines this critique
with the literature report and the other specialist, without erasing objections.

Return SpecialistPanel with your assigned specialism, one exact-bound review for
each candidate and a concise cross-design summary. Do not invent a discussion,
force a merged design, mutate input files, or start additional agents.
"""
