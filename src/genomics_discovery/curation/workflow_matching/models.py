"""Small comparison contract; descriptions stay open while evidence bindings are explicit."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.workflow_extraction.models import Evidence, Text

Dimension = Literal["states", "transformation", "information", "dependencies", "scope_limits"]
QUESTIONS = {
    "states": "What enters and leaves, including retained fractions and attribution unit?",
    "transformation": "What physically or computationally changes; what mechanism is established?",
    "information": "What information survives, appears or is lost, on which carrier?",
    "dependencies": "Which conditions, ordering, branches and context enable the handoff?",
    "scope_limits": "What function, scope and limitations are supported; what is unestablished?",
}
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Nonempty = Annotated[tuple[Text, ...], Field(min_length=1)]


class Facet(StrictModel):
    id: Text
    dimension: Dimension
    description: Text
    status: Literal["reported", "inferred", "not_characterized"]
    targets: Nonempty
    evidence: tuple[Evidence, ...]
    search_terms: tuple[Text, ...] = ()
    follow_up: Text | None = None

    @model_validator(mode="after")
    def scoped_support(self):
        if self.status != "not_characterized" and not self.evidence:
            raise ValueError("A positive facet requires source evidence")
        if self.status == "reported" and any(e.support != "reported" for e in self.evidence):
            raise ValueError("A reported facet cannot hide inferred evidence")
        if self.status == "not_characterized" and (not self.follow_up or self.search_terms):
            raise ValueError(
                "An uncharacterized property needs follow-up, not positive index terms"
            )
        return self


class Profile(StrictModel):
    occurrence_id: Text
    occurrence_sha256: Hash
    facets: tuple[Facet, ...]

    @model_validator(mode="after")
    def question_coverage(self):
        if {f.dimension for f in self.facets} != QUESTIONS.keys():
            raise ValueError("Each profile must address every fundamental question")
        if len({f.id for f in self.facets}) != len(self.facets):
            raise ValueError("Profile assertion IDs must be unique")
        return self


class ProfileBatch(StrictModel):
    profiles: tuple[Profile, ...]


class SearchRequest(StrictModel):
    query: Text
    dimension: Dimension | None = None
    limit: Annotated[int, Field(gt=0)] = 10


class Selection(StrictModel):
    parent_id: Text
    operation_ids: Nonempty

    @model_validator(mode="after")
    def unique_operations(self):
        if len(self.operation_ids) != len(set(self.operation_ids)):
            raise ValueError("Operation selections must be unique")
        return self


class Case(StrictModel):
    id: Text
    left_parent: Text
    right_parent: Text
    question: Text
    relation: Literal["shared_purpose", "shared_mechanism"]


class ComparisonClaim(StrictModel):
    dimension: Dimension
    kind: Literal["correspondence", "difference", "not_established"]
    description: Text
    left_targets: Nonempty
    right_targets: Nonempty
    left_evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]
    right_evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


class DesignQuestion(StrictModel):
    question: Text
    based_on_claims: Annotated[tuple[int, ...], Field(min_length=1)]
    needed_evidence: Text


class Alignment(StrictModel):
    case_id: Text
    left: Selection
    right: Selection
    disposition: Literal[
        "candidate_correspondence", "no_supported_correspondence", "research_needed"
    ]
    rationale: Text
    claims: Annotated[tuple[ComparisonClaim, ...], Field(min_length=1)]
    design_questions: tuple[DesignQuestion, ...] = ()

    @model_validator(mode="after")
    def explicit_comparison(self):
        if self.disposition == "candidate_correspondence" and not any(
            c.kind == "correspondence" for c in self.claims
        ):
            raise ValueError("A positive alignment requires an explicit correspondence")
        for question in self.design_questions:
            if any(i < 0 or i >= len(self.claims) for i in question.based_on_claims):
                raise ValueError("Design question cites a nonexistent comparison claim")
        return self


class AlignmentBatch(StrictModel):
    alignments: tuple[Alignment, ...]


class Citation(StrictModel):
    parent_id: Text
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


class Inspection(StrictModel):
    parent_id: Text
    source_id: Text
    passage_id: Text


class ReviewCheck(StrictModel):
    target_id: Text
    verdict: Literal["supported", "unsupported", "unresolved"]
    rationale: Text
    citations: Annotated[tuple[Citation, ...], Field(min_length=1)]
    inspected: Annotated[tuple[Inspection, ...], Field(min_length=1)]


class MatchingReview(StrictModel):
    profiles_sha256: Hash
    alignments_sha256: Hash
    checks: tuple[ReviewCheck, ...]
