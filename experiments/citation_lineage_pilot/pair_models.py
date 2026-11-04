"""Comparison of two unchanged diagram extractions, not a new extraction schema."""

from typing import Literal

from pydantic import Field, model_validator

from genomics_discovery.illumina.graph import operation_group
from genomics_discovery.illumina.relationships import HISTORICAL_RELATIONS
from models import Evidence, Record, Review, check_evidence, validate_review


class PairClaim(Record):
    id: str = Field(min_length=1, description="Local claim address, not a biological classification.")
    left_operation_ids: list[str] = Field(min_length=1, description="Exact global Operation IDs in the left graph; connected group, no invented internals.")
    right_operation_ids: list[str] = Field(min_length=1, description="Exact global Operation IDs in the right graph; connected group, counts need not match.")
    relation: Literal["shared_mechanism", "shared_purpose", "component_adoption", "method_development", "comparison", "no_established_relation"]
    direction: Literal["undirected", "left_to_right", "right_to_left"] = Field(description="Only explicit historical adoption/development is directed. Display order never establishes history.")
    shared_description: str = Field(min_length=1, description="Complete shared transformation or purpose, with substrate/output scope; not merely a reagent or label.")
    difference: str = Field(min_length=1, description="Contrast the two source-supported implementations, including consequential input/context/output/information differences. Do not fill unshown internals.")
    consequence: str = Field(min_length=1, description="Reported measurement consequence, limitation or gain; say not established if absent. Do not invent causal benefit.")
    rationale: str = Field(min_length=1, description="Explain why this relation type is warranted. History requires author transfer/adaptation evidence, not similarity or chronology.")
    left_evidence: list[Evidence] = Field(min_length=1, description="Exact passages from the registered left-method paper(s), supporting this side.")
    right_evidence: list[Evidence] = Field(min_length=1, description="Exact passages from the registered right-method paper(s), supporting this side.")
    unresolved: list[str] = Field(description="Precise remaining questions or scope qualifications, retained through review and export.")

    @model_validator(mode="after")
    def direction_matches_relation(self):
        historical = self.relation in HISTORICAL_RELATIONS
        if historical == (self.direction == "undirected"):
            raise ValueError("Only historical relations have a direction; historical relations require one")
        return self


class OperationCoverage(Record):
    operation_id: str = Field(description="An existing global Operation ID from either input graph.")
    claim_ids: list[str] = Field(description="Claims actually containing this operation, or empty if unmatched.")
    explanation: str = Field(min_length=1, description="What correspondence was found, or why none is established; do not force a match.")


class DiagramAdequacy(Record):
    record_id: str
    verdict: Literal["sufficient", "sufficient_with_paper_annotations", "needs_finer_extraction"]
    explanation: str = Field(min_length=1, description="Adequate for which comparison? If inadequate, identify the exact distinction that cannot be expressed with current operation groups.")


class PairAnalysis(Record):
    claims: list[PairClaim]
    operation_coverage: list[OperationCoverage]
    diagram_adequacy: list[DiagramAdequacy]
    summary: str


def validate_pair(document, inputs):
    graph, sources, sides = inputs["graph"], inputs["sources"], inputs["sides"]
    claims = {c.id: c for c in document.claims}
    if len(claims) != len(document.claims):
        raise ValueError("Duplicate claim ID")
    for c in document.claims:
        for side in ("left", "right"):
            binding = sides[side]
            operation_group(graph, binding["record_id"], getattr(c, side + "_operation_ids"))
            check_evidence(getattr(c, side + "_evidence"), sources, allowed=set(binding["paper_source_ids"]), scientific=True)
    expected = {n["id"] for n in graph["nodes"] if n["node_type"] == "operation"}
    actual = [x.operation_id for x in document.operation_coverage]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Account for each existing operation exactly once")
    for row in document.operation_coverage:
        expected_claims = {c.id for c in document.claims if row.operation_id in c.left_operation_ids + c.right_operation_ids}
        if len(row.claim_ids) != len(set(row.claim_ids)) or set(row.claim_ids) != expected_claims:
            raise ValueError("Coverage claim references disagree with actual membership")
    adequacy_ids = [x.record_id for x in document.diagram_adequacy]
    if len(adequacy_ids) != 2 or set(adequacy_ids) != {x["record_id"] for x in sides.values()}:
        raise ValueError("Assess adequacy of both original diagrams exactly once")


def export_pair(analysis, review, inputs, analysis_sha256):
    validate_pair(analysis, inputs)
    validate_review(review, analysis, analysis_sha256, inputs["sources"])
    decisions = {x.claim_id: x for x in review.findings}
    correspondences, history, withheld = [], [], []
    for claim in analysis.claims:
        finding = decisions[claim.id]
        reason = (finding.decision if finding.decision != "accept" else
                  "omitted_comparison" if review.omitted_candidates else
                  "no_established_relation" if claim.relation == "no_established_relation" else None)
        item = {"claim": claim.model_dump(), "review": finding.model_dump()}
        if reason:
            withheld.append({**item, "reason": reason})
            continue
        item["groups"] = {side: operation_group(inputs["graph"], inputs["sides"][side]["record_id"],
                          getattr(claim, side + "_operation_ids")) for side in ("left", "right")}
        item["status"] = "model_reviewed_pair_pilot"
        (history if claim.direction != "undirected" else correspondences).append(item)
    return {"format_version": "illumina_method_pair/1", "source_graph": inputs["graph"],
            "bindings": inputs["sides"], "correspondences": correspondences,
            "historical_links": history, "withheld": withheld,
            "diagram_adequacy": [x.model_dump() for x in analysis.diagram_adequacy],
            "analysis_sha256": analysis_sha256,
            "boundary": "Unchanged source workflows plus reviewed comparisons. Correspondences are NOT material/data flows, interchangeability guarantees or canonical atlas admission."}
