"""Mechanical bindings and complete accounting, never automated entailment."""

from __future__ import annotations

from collections import Counter

from genomics_discovery.curation.workflow_extraction.models import (
    Workflow, digest, target_ids, validate_evidence,
)
from genomics_discovery.curation.workflow_extraction.sources import validate_packet

from .models import BenchmarkAssessment, BenchmarkAssessmentV2, ProtocolReference, ReferenceReview


def _unique(values, label, errors):
    counts = Counter(values)
    if any(count != 1 for count in counts.values()):
        errors.append(f"Duplicate {label}")


def _packet_errors(packet):
    try:
        validate_packet(packet)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"Invalid source packet/original: {exc}"]
    return []


def _refs(items):
    return {(item.source_id, item.passage_id) for item in items}


def _inspected_evidence(items, inspected, label, errors):
    for item in items:
        if (item.source_id, item.passage_id) not in inspected:
            errors.append(f"{label}: evidence passage was not inspected")


def benchmark_target_ids(workflow: Workflow) -> set[str]:
    """Explicit graph targets plus authored module boundaries and retained gaps."""
    return (target_ids(workflow)
            | {f"module:{item.id}" for item in workflow.modules}
            | {f"gap:{item.id}" for item in workflow.unresolved})


def validate_reference(reference: ProtocolReference, packet: dict) -> list[str]:
    if errors := _packet_errors(packet):
        return errors
    if reference.source_packet_sha256 != digest(packet):
        errors.append("Reference source_packet_sha256 does not match")
    for field in ("method_id", "scope", "variant"):
        if getattr(reference, field) != packet[field]:
            errors.append(f"Reference {field} differs from frozen source scope")
    for field in ("obligations", "unknowns", "admissible_decompositions", "query_cases"):
        _unique((item.id for item in getattr(reference, field)), f"reference {field} ID", errors)
    obligations = {item.id for item in reference.obligations}
    for item in (*reference.admissible_decompositions, *reference.query_cases):
        _unique(item.obligation_ids, f"obligation IDs in {item.id}", errors)
        if set(item.obligation_ids) - obligations:
            errors.append(f"{item.id}: unknown obligation IDs")
    sources = {source["source_id"] for source in packet["sources"]}
    declared = [item.source_id for item in reference.source_coverage]
    _unique(declared, "source_coverage source ID", errors)
    if set(declared) != sources:
        errors.append("Reference source_coverage must account for every registered source exactly once")
    inspected = set()
    passage_refs = []
    for source in reference.source_coverage:
        _unique(source.inspected_passage_ids, f"inspected passage IDs for {source.source_id}", errors)
        for passage_id in source.inspected_passage_ids:
            inspected.add((source.source_id, passage_id))
            passage_refs.append({"source_id": source.source_id, "passage_id": passage_id})
    # source_coverage.source_id is source metadata, not a partial PassageRef.
    errors.extend(validate_evidence({"passages": passage_refs, "obligations": reference.obligations,
        "unknowns": reference.unknowns, "queries": reference.query_cases}, packet))
    for item in (*reference.obligations, *reference.query_cases):
        _inspected_evidence(item.evidence, inspected, item.id, errors)
    for unknown in reference.unknowns:
        _unique([(ref.source_id, ref.passage_id) for ref in unknown.inspected_passages],
                f"unknown {unknown.id} inspected passage", errors)
        if _refs(unknown.inspected_passages) - inspected:
            errors.append(f"Unknown {unknown.id}: passages outside declared inspected source scope")
    return errors


def validate_reference_review(review: ReferenceReview, reference: ProtocolReference,
                              packet: dict) -> list[str]:
    errors = validate_reference(reference, packet)
    if packet_errors := _packet_errors(packet):
        return list(dict.fromkeys([*errors, *packet_errors]))
    if review.reference_sha256 != digest(reference):
        errors.append("Review reference_sha256 does not match")
    if review.source_packet_sha256 != digest(packet):
        errors.append("Review source_packet_sha256 does not match")
    errors.extend(validate_evidence(review, packet))
    inspected = _refs(review.inspected_passages)
    _unique([(ref.source_id, ref.passage_id) for ref in review.inspected_passages],
            "review inspected passage", errors)
    for checks, originals, label in (
        (review.obligation_checks, reference.obligations, "obligation"),
        (review.unknown_checks, reference.unknowns, "unknown"),
        (review.decomposition_checks, reference.admissible_decompositions, "decomposition"),
        (review.query_checks, reference.query_cases, "query"),
    ):
        expected = {item.id: item for item in originals}
        ids = [check.target_id for check in checks]
        _unique(ids, f"{label} review check", errors)
        if set(ids) != set(expected):
            errors.append(f"Review must check each {label} exactly once")
        for check in checks:
            if not check.evidence and check.verdict != "uncertain":
                scoped_unknown = (label == "unknown" and check.verdict == "supported"
                    and check.target_id in expected
                    and _refs(expected[check.target_id].inspected_passages) <= inspected)
                if not scoped_unknown:
                    errors.append(f"{label} {check.target_id}: judged claim requires inspected evidence")
            _inspected_evidence(check.evidence, inspected, check.target_id, errors)
    _unique((item.id for item in review.missing_obligations), "missing obligation ID", errors)
    for item in review.missing_obligations:
        if item.id in {obligation.id for obligation in reference.obligations}:
            errors.append(f"Missing obligation ID already exists: {item.id}")
        _inspected_evidence(item.evidence, inspected, item.id, errors)
    return errors


def reference_review_outcome(review: ReferenceReview) -> str:
    """Call only after validation; supported remains a reference candidate."""
    verdicts = {check.verdict for group in (review.obligation_checks, review.unknown_checks,
        review.decomposition_checks, review.query_checks) for check in group}
    if "needs_revision" in verdicts or review.missing_obligations:
        return "needs_revision"
    if "uncertain" in verdicts:
        return "research_needed"
    return "supported_reference_candidate"


def validate_assessment(assessment: BenchmarkAssessment, reference: ProtocolReference,
                        workflow: Workflow, packet: dict) -> list[str]:
    errors = validate_reference(reference, packet)
    if packet_errors := _packet_errors(packet):
        return list(dict.fromkeys([*errors, *packet_errors]))
    for field, expected in (("reference_sha256", digest(reference)),
                             ("workflow_sha256", digest(workflow)),
                             ("source_packet_sha256", digest(packet))):
        if getattr(assessment, field) != expected:
            errors.append(f"Assessment {field} does not match")
    for field in ("method_id", "title", "scope", "variant"):
        if getattr(workflow, field) != packet[field]:
            errors.append(f"Workflow {field} differs from frozen source scope")
    errors.extend(validate_evidence(workflow, packet))
    errors.extend(validate_evidence(assessment, packet))
    expected = {item.id for item in reference.obligations}
    ids = [item.obligation_id for item in assessment.obligations]
    _unique(ids, "obligation assessment", errors)
    if set(ids) != expected:
        errors.append("Assessment must account for every reference obligation exactly once")
    _unique((audit.id for audit in assessment.factual_audits), "factual audit ID", errors)
    valid_targets = benchmark_target_ids(workflow)
    inspected = _refs(assessment.inspected_passages)
    _unique([(ref.source_id, ref.passage_id) for ref in assessment.inspected_passages],
            "assessment inspected passage", errors)
    for item in (*assessment.obligations, *assessment.factual_audits):
        label = getattr(item, "obligation_id", None) or item.id
        _unique(item.targets, f"witness target in {label}", errors)
        if set(item.targets) - valid_targets:
            errors.append(f"{label}: unknown workflow target IDs")
        _inspected_evidence(item.evidence, inspected, label, errors)
    if isinstance(assessment, BenchmarkAssessmentV2):
        unknowns = {item.id: item for item in reference.unknowns}
        ids = [item.unknown_id for item in assessment.unknown_checks]
        _unique(ids, "unknown preservation check", errors)
        if set(ids) != set(unknowns):
            errors.append("Assessment 1.1 must account for every reference unknown exactly once")
        for item in assessment.unknown_checks:
            _unique(item.targets, f"unknown witness target in {item.unknown_id}", errors)
            if set(item.targets) - valid_targets:
                errors.append(f"Unknown {item.unknown_id}: unknown workflow target IDs")
            _inspected_evidence(item.evidence, inspected, item.unknown_id, errors)
            unknown = unknowns.get(item.unknown_id)
            if (unknown is not None and item.outcome != "unassessed"
                    and _refs(unknown.inspected_passages) - inspected):
                errors.append(f"Unknown {item.unknown_id}: preservation judgment requires its inspected reference scope")
            # An inspected absence can be retained without fabricating a quote
            # saying something was not reported. No outcome asserts resolution;
            # reference_revision_needed instead supplies a source-backed objection.
    return errors
