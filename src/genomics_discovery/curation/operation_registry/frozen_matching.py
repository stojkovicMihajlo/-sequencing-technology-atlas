"""Read-only matching against explicit definitions; new labels remain proposals.

This pilot enumerates the small catalogue, separating membership judgment from
candidate retrieval. No function here writes the registry or infers compatibility.
"""

from types import SimpleNamespace
from typing import Annotated, Literal

from pydantic import Field

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.workflow_extraction.models import (
    EvidenceSet, PassageRef, Text, digest, validate_evidence,
)
from genomics_discovery.curation.workflow_extraction.transformations import (
    Hash, TransformationDefinition,
)
from .definition_review import DefinitionCheck, definition_outcome
from .store import revision


class CandidateAssessment(StrictModel):
    occurrence_id: Text
    concept_id: Text
    inspected_passages: Annotated[tuple[PassageRef, ...], Field(min_length=1)]
    criteria: tuple[DefinitionCheck, ...]


class ExtensionRequest(StrictModel):
    occurrence_id: Text
    definition: TransformationDefinition
    relation: Literal["shared_purpose", "shared_mechanism"]
    closest_concept_ids: tuple[Text, ...]
    distinction: Text
    properties_to_preserve: tuple[Text, ...]
    evidence: EvidenceSet


class MatchingReport(StrictModel):
    format_version: Literal["frozen_matching_report/1.0"] = "frozen_matching_report/1.0"
    plan_sha256: Hash
    assessments: tuple[CandidateAssessment, ...]
    extension_requests: tuple[ExtensionRequest, ...] = ()


def make_plan(snapshot, occurrences):
    """Stable identities; manifest order may change presentation, never membership."""
    return {
        "format_version": "frozen_matching_plan/1.0",
        "registry_revision": revision(snapshot),
        "catalogue_sha256": digest(snapshot),
        "selections": {key: digest(value) for key, value in sorted(occurrences.items())},
        "definitions": {key: digest(value) for key, value in sorted(snapshot["concepts"].items())},
        "pairs": [{"occurrence_id": oid, "concept_id": cid}
                  for oid in sorted(occurrences) for cid in sorted(snapshot["concepts"])],
        "candidate_policy": "exhaustive_small_catalogue",
        "write_policy": "read_only_matching_with_pending_extensions",
    }


def criterion_outcome(definition, checks):
    status = definition_outcome(SimpleNamespace(definition=definition), checks)
    return {"supported_definition": "member", "needs_revision": "non_member",
            "research_needed": "unknown"}[status]


def validate_report(report, plan, snapshot, occurrences, packets):
    if plan != make_plan(snapshot, occurrences) or report.plan_sha256 != digest(plan):
        raise ValueError("Matching plan, catalogue or selections changed")
    expected = {(p["occurrence_id"], p["concept_id"]) for p in plan["pairs"]}
    actual = [(r.occurrence_id, r.concept_id) for r in report.assessments]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Assess every host-supplied candidate pair exactly once")
    outcomes = []
    for row in report.assessments:
        packet = packets[row.occurrence_id]
        available = {(s["source_id"], p["passage_id"])
                     for s in packet["sources"] for p in s["passages"]}
        inspected = {(p.source_id, p.passage_id) for p in row.inspected_passages}
        cited = {(e.source_id, e.passage_id) for c in row.criteria for e in c.evidence}
        if inspected - available or cited - inspected:
            raise ValueError("Candidate evidence must refer to inspected registered passages")
        errors = validate_evidence(row, packet)
        if errors:
            raise ValueError("; ".join(errors))
        definition = TransformationDefinition.model_validate(
            snapshot["concepts"][row.concept_id]["definition"])
        outcomes.append({"occurrence_id": row.occurrence_id, "concept_id": row.concept_id,
                         "status": criterion_outcome(definition, row.criteria)})
    for request in report.extension_requests:
        if request.occurrence_id not in occurrences:
            raise ValueError("Extension refers to an unknown selection")
        if set(request.closest_concept_ids) - snapshot["concepts"].keys():
            raise ValueError("Extension refers to an unknown existing concept")
        if request.definition.id in snapshot["concepts"]:
            raise ValueError("An extension cannot overwrite an existing definition")
        if request.relation == "shared_mechanism" and request.definition.physical_mechanism is None:
            raise ValueError("A mechanism proposal needs an asserted mechanism")
        errors = validate_evidence(request, packets[request.occurrence_id])
        if errors:
            raise ValueError("; ".join(errors))
    return outcomes


def reconcile_reports(first, second, plan, snapshot, occurrences, packets):
    """Unanimity gates positive candidates; disagreements remain inspectable."""
    runs = [validate_report(r, plan, snapshot, occurrences, packets) for r in (first, second)]
    indexed = [{(r["occurrence_id"], r["concept_id"]): r["status"] for r in rows} for rows in runs]
    decisions = []
    for pair in plan["pairs"]:
        key = (pair["occurrence_id"], pair["concept_id"])
        a, b = indexed[0][key], indexed[1][key]
        status = a if a == b else "unknown" if "unknown" in (a, b) else "disagreement"
        decisions.append({**pair, "first": a, "second": b, "status": status})
    pending = [{"report_sha256": digest(report), "request_index": i,
                "status": "pending_curation", "request": req.model_dump(mode="json")}
               for report in (first, second) for i, req in enumerate(report.extension_requests)]
    return {"format_version": "frozen_matching_result/1.0", "plan_sha256": digest(plan),
            "report_sha256": [digest(first), digest(second)], "decisions": decisions,
            "pending_extensions": pending, "registry_mutated": False,
            "boundary": "Paired model judgments, not expert validation. Positive matches are staged "
                        "membership candidates; no canonical label or atlas edge is admitted."}
