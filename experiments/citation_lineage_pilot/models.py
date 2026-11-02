"""Contracts for a one-hop, paper-guided lineage pilot, not canonical admission."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Record):
    source_id: str
    quote: str = Field(min_length=12)


class Nomination(Record):
    reference_id: str
    contribution: str
    reason_to_follow: str
    seed_evidence: list[Evidence] = Field(min_length=1)


class Nominations(Record):
    references: list[Nomination]
    selection_rationale: str
    unresolved_searches: list[str]


Relation = Literal[
    "method_development", "component_adoption", "mechanistic_precedent",
    "comparison", "background", "no_established_relation",
]


class Claim(Record):
    id: str
    reference_id: str
    contributor: str
    contributor_kind: Literal["assay", "experimental_component", "general_method", "other"]
    relation: Relation
    seed_operation_ids: list[str]
    earlier_demonstration: str
    seed_design_change: str
    limitation_addressed: str
    reported_gain: str
    tradeoffs_and_transfer_limits: str
    seed_evidence: list[Evidence] = Field(min_length=1)
    predecessor_evidence: list[Evidence]
    catalogue_candidates: list[str]
    unresolved: list[str]


class Analysis(Record):
    seed_method: str
    claims: list[Claim]
    reference_dispositions: dict[str, str]
    summary: str


class UncertaintyAssessment(Record):
    issue_index: int = Field(ge=0)
    blocks_relation: bool
    rationale: str = Field(min_length=12)


class Finding(Record):
    claim_id: str
    decision: Literal["accept", "revise", "needs_evidence", "reject"]
    rationale: str
    evidence: list[Evidence] = Field(min_length=1)
    unresolved_assessments: list[UncertaintyAssessment]


class Review(Record):
    analysis_sha256: str
    findings: list[Finding]
    omitted_candidates: list[str]
    summary: str


def check_evidence(items, sources, *, allowed=None):
    for item in items:
        if item.source_id not in sources or (allowed is not None and item.source_id not in allowed):
            raise ValueError(f"Unregistered evidence source: {item.source_id}")
        # Exact text support, not semantic entailment. No fuzzy quote repair.
        if item.quote not in sources[item.source_id]["text"]:
            raise ValueError(f"Quote absent from {item.source_id}: {item.quote[:90]}")


def validate_nominations(document, sources, bibliography):
    ids = [x.reference_id for x in document.references]
    if len(ids) != len(set(ids)) or set(ids) - bibliography.keys():
        raise ValueError("Duplicate or unknown nominated reference")
    for item in document.references:
        check_evidence(item.seed_evidence, sources, allowed={"seed"})


def validate_analysis(document, sources, nominated, operation_ids, catalogue_ids):
    ids = [x.id for x in document.claims]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate claim ID")
    if set(document.reference_dispositions) != set(nominated):
        raise ValueError("Account for every nominated reference, including rejected candidates")
    for claim in document.claims:
        if claim.reference_id not in nominated:
            raise ValueError("Claim uses an unnominated reference")
        if set(claim.seed_operation_ids) - set(operation_ids):
            raise ValueError("Claim invents a seed operation")
        if set(claim.catalogue_candidates) - set(catalogue_ids):
            raise ValueError("Claim invents an Illumina entry")
        check_evidence(claim.seed_evidence, sources, allowed={"seed"})
        check_evidence(claim.predecessor_evidence, sources, allowed={claim.reference_id})


def validate_review(document, analysis, analysis_sha256, sources):
    if document.analysis_sha256 != analysis_sha256:
        raise ValueError("Review is bound to a different analysis")
    expected = {x.id for x in analysis.claims}
    actual = [x.claim_id for x in document.findings]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Review must cover every claim exactly once")
    claims = {x.id: x for x in analysis.claims}
    for finding in document.findings:
        check_evidence(finding.evidence, sources)
        indices = [x.issue_index for x in finding.unresolved_assessments]
        expected_indices = set(range(len(claims[finding.claim_id].unresolved)))
        if len(indices) != len(set(indices)) or set(indices) != expected_indices:
            raise ValueError("Review must assess every unresolved issue exactly once")
        if finding.decision == "accept" and any(x.blocks_relation for x in finding.unresolved_assessments):
            raise ValueError("Accepted relationship cannot have a blocking unresolved issue")


def graph_overlay(analysis, review, sources, analysis_sha256, bindings):
    """Export only independently accepted, two-sided claims; never edit the atlas."""
    validate_review(review, analysis, analysis_sha256, sources)
    decisions = {x.claim_id: x for x in review.findings}
    nodes = [{"id": "seed", "kind": "assay", "name": analysis.seed_method,
              "bindings": bindings}]
    links, withheld = [], []
    for claim in analysis.claims:
        finding = decisions[claim.id]
        reason = None
        if finding.decision != "accept":
            reason = finding.decision
        elif review.omitted_candidates:
            reason = "review_found_omissions"
        elif any(x.blocks_relation for x in finding.unresolved_assessments):
            reason = "blocking_unresolved_issue"
        elif not claim.predecessor_evidence:
            reason = "no_independent_predecessor_evidence"
        elif claim.relation in {"background", "no_established_relation"}:
            reason = "no_methodological_link"
        if reason:
            withheld.append({"claim": claim.model_dump(), "reason": reason,
                             "review": finding.model_dump()})
            continue
        contributor_id = "contributor:" + claim.id
        nodes.append({"id": contributor_id, "kind": claim.contributor_kind,
                      "name": claim.contributor, "reference_id": claim.reference_id,
                      "catalogue_candidates": claim.catalogue_candidates,
                      "identity_status": "scoped_to_cited_contribution; catalogue_matches_are_candidates"})
        links.append({"id": claim.id, "source": contributor_id, "target": "seed",
                      "kind": claim.relation,
                      "historical_development_edge": claim.relation == "method_development",
                      "status": "model_reviewed_pilot_overlay",
                      "claim": claim.model_dump(), "review": finding.model_dump()})
    return {"format_version": "citation_lineage_pilot/2", "nodes": nodes, "links": links,
            "withheld": withheld, "analysis_sha256": analysis_sha256,
            "boundary": "One-hop source-guided pilot, not a complete lineage or canonical atlas admission."
                        " Shared evidence, comparisons and mechanistic precedents are not descent."
                        " Referenced methods have not received full workflow extraction."}
