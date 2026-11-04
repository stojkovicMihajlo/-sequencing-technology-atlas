"""Contracts for a one-hop, paper-guided lineage pilot, not canonical admission."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from genomics_discovery.illumina.relationships import RELATIONS, HISTORICAL_RELATIONS


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


class CurrentNominations(Nominations):
    format_version: Literal["citation_nomination/2"] = "citation_nomination/2"
    excluded_references: dict[str, str] = Field(description="Every bibliography reference not nominated, mapped to its source-context reason for not following. Do not silently omit a reference; uncertain methodological leads should be nominated.")


Relation = Literal[
    "method_development", "component_adoption", "mechanistic_precedent",
    "comparison", "background", "no_established_relation",
]


class Claim(Record):
    id: str
    reference_id: str
    contributor: str
    contributor_kind: Literal["assay", "experimental_component", "general_method", "other"]
    relation: Relation = Field(description="Apply the shared RELATION DEFINITIONS. Only method_development and component_adoption assert historical transfer; the type is not a technical-change category.")
    seed_operation_ids: list[str]
    earlier_demonstration: str = Field(description="Describe what the cited implementation demonstrably did or contained at the claimed scope, supported by predecessor_evidence. A title or general topic is not a demonstration.")
    seed_design_change: str = Field(description="Natural-language comparison: identify the feature being compared, earlier versus later arrangement, retained elements and affected workflow portion. For component adoption name the contribution and its destination; unchanged use is valid. For method development specify the retained assay strategy and revisions. Do not substitute vague 'same backbone' wording or invent unsupported changes.")
    limitation_addressed: str = Field(description="The reported limitation or reason for this change; state not established when absent. Keep author rationale distinct from measured consequences.")
    reported_gain: str = Field(description="Reported consequence with metric, comparator and tested context. Say not established when absent. A history edge does not require a gain; whole-assay gains must not be assigned to one component without evidence.")
    tradeoffs_and_transfer_limits: str = Field(description="Supported limitations and relevant context/interface differences. Distinguish documented trade-offs from unknown transferability; absence of a reported trade-off does not establish none exist.")
    seed_evidence: list[Evidence] = Field(min_length=1)
    predecessor_evidence: list[Evidence]
    catalogue_candidates: list[str]
    unresolved: list[str]


class Analysis(Record):
    seed_method: str
    claims: list[Claim]
    reference_dispositions: dict[str, str]
    summary: str


class CurrentClaim(Claim):
    citation_context: str = Field(description="Copy one complete context verbatim from bibliography.json for this exact reference_id. Empty only if none is available; an empty context cannot publish a connection. This locates the citation, not proof of inheritance.")
    claim_scope: Literal["seed_workflow", "supporting_experiment", "unlocalized"] = Field(description="seed_workflow means the claim concerns the extracted assay route and names its affected operation IDs. supporting_experiment means a separate validation/comparator experiment, not an inherited part of that route. unlocalized means no supported workflow location. Non-workflow claims cannot publish evolutionary edges.")


class CurrentAnalysis(Analysis):
    format_version: Literal["citation_analysis/3"] = "citation_analysis/3"
    claims: list[CurrentClaim]


def parse_analysis(data):
    """Read archived contracts as archived; never relabel them as new checks."""
    schema = CurrentAnalysis if data.get("format_version") == "citation_analysis/3" else Analysis
    return schema.model_validate(data)


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


def check_evidence(items, sources, *, allowed=None, scientific=False):
    for item in items:
        if item.source_id not in sources or (allowed is not None and item.source_id not in allowed):
            raise ValueError(f"Unregistered evidence source: {item.source_id}")
        # Exact text support, not semantic entailment. No fuzzy quote repair.
        if item.quote not in sources[item.source_id]["text"]:
            raise ValueError(f"Quote absent from {item.source_id}: {item.quote[:90]}")
        if scientific:
            source = sources[item.source_id]
            if source.get("content_level") not in {"fulltext", "abstract"}:
                raise ValueError("Scientific relationship needs primary content, not metadata/catalogue")
            normalize = lambda text: " ".join(text.casefold().split()).strip(". ")
            if source.get("title") and normalize(item.quote) == normalize(source["title"]):
                raise ValueError("An article title cannot support a scientific relationship")


def validate_nominations(document, sources, bibliography):
    ids = [x.reference_id for x in document.references]
    if len(ids) != len(set(ids)) or set(ids) - bibliography.keys():
        raise ValueError("Duplicate or unknown nominated reference")
    for item in document.references:
        check_evidence(item.seed_evidence, sources, allowed={"seed"})
    if isinstance(document, CurrentNominations):
        excluded = document.excluded_references
        if set(ids) & set(excluded) or (set(ids) | set(excluded)) != set(bibliography):
            raise ValueError("Account for every bibliography reference exactly once: nominated or excluded")
        if any(not reason.strip() for reason in excluded.values()):
            raise ValueError("Excluded reference needs a context-specific reason")


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
        if isinstance(claim, CurrentClaim):
            check_citation_binding(claim, sources)


def check_citation_binding(claim, sources):
    refs = json.loads(sources["bibliography"]["text"])
    if claim.reference_id not in refs:
        raise ValueError("Claim reference is absent from the seed bibliography")
    if claim.citation_context and claim.citation_context not in refs[claim.reference_id]["contexts"]:
        raise ValueError("Citation context must come from this exact reference, not another citation or paragraph")
    if claim.claim_scope == "seed_workflow" and not claim.seed_operation_ids:
        raise ValueError("A seed-workflow claim must locate at least one actual operation")
    if claim.claim_scope != "seed_workflow" and claim.seed_operation_ids:
        raise ValueError("An outside/unlocalized claim cannot borrow seed-workflow operation IDs")
    # These reject contradictory/empty declarations, not scientifically false prose.
    # Source entailment and the retained-versus-adopted distinction need review.
    if claim.relation == "method_development" and claim.contributor_kind != "assay":
        raise ValueError("Method development must identify an earlier assay, not just a component")
    if claim.relation in HISTORICAL_RELATIONS:
        for field in ("contributor", "earlier_demonstration", "seed_design_change"):
            if not getattr(claim, field).strip():
                raise ValueError(f"Historical claim needs a nonblank {field} description")


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
        claim = claims[finding.claim_id]
        if (isinstance(claim, CurrentClaim) and finding.decision == "accept"
                and claim.relation not in {"background", "no_established_relation"}):
            cited = {x.source_id for x in finding.evidence}
            if not {"seed", claim.reference_id} <= cited:
                raise ValueError("Accepted new relationship review must cite seed and exact predecessor evidence")
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
        if isinstance(claim, CurrentClaim):
            check_citation_binding(claim, sources)
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
        elif isinstance(claim, CurrentClaim) and not claim.citation_context:
            reason = "no_located_citation_context"
        elif (isinstance(claim, CurrentClaim) and
              RELATIONS[claim.relation]["layer"] == "history" and claim.claim_scope != "seed_workflow"):
            reason = "not_part_of_seed_workflow"
        if reason:
            withheld.append({"claim": claim.model_dump(), "reason": reason,
                             "review": finding.model_dump()})
            continue
        check_evidence(claim.seed_evidence, sources, allowed={"seed"}, scientific=True)
        check_evidence(claim.predecessor_evidence, sources, allowed={claim.reference_id}, scientific=True)
        contributor_id = "contributor:" + claim.id
        nodes.append({"id": contributor_id, "kind": claim.contributor_kind,
                      "name": claim.contributor, "reference_id": claim.reference_id,
                      "catalogue_candidates": claim.catalogue_candidates,
                      "identity_status": "scoped_to_cited_contribution; catalogue_matches_are_candidates"})
        links.append({"id": claim.id, "source": contributor_id, "target": "seed",
                      "kind": claim.relation,
                      "historical_development_edge": claim.relation == "method_development",
                      "relationship_layer": RELATIONS[claim.relation]["layer"],
                      "status": "model_reviewed_pilot_overlay",
                      "claim": claim.model_dump(), "review": finding.model_dump()})
    return {"format_version": "citation_lineage_pilot/3" if isinstance(analysis, CurrentAnalysis) else "citation_lineage_pilot/2", "nodes": nodes, "links": links,
            "withheld": withheld, "analysis_sha256": analysis_sha256,
            "boundary": "One-hop source-guided pilot, not a complete lineage or canonical atlas admission."
                        " Shared evidence, comparisons and mechanistic precedents are not descent."
                        " Referenced methods have not received full workflow extraction."}
