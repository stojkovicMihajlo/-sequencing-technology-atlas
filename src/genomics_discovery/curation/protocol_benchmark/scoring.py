"""Counts of supplied assessments, with partial-reference and abstention boundaries."""

from collections import Counter

from genomics_discovery.curation.workflow_extraction.models import digest

from .models import BenchmarkAssessmentV2
from .validation import benchmark_target_ids, validate_assessment


def unknown_preservation(assessment, reference):
    """Use after validation; unknown retention is independent of known obligations."""
    denominator = len(reference.unknowns)
    if not isinstance(assessment, BenchmarkAssessmentV2):
        return {"status": "not_assessed", "denominator": denominator, "counts": {},
                "strict_retained_fraction": None, "not_fully_retained_ids": None,
                "boundary": "Legacy assessment 1.0 does not assess graph preservation of reference unknowns."}
    counts = Counter(item.outcome for item in assessment.unknown_checks)
    not_retained = [item.unknown_id for item in assessment.unknown_checks if item.outcome != "retained"]
    return {"status": "not_applicable" if not denominator else "retained" if not not_retained else "incomplete",
            "denominator": denominator,
            "counts": {key: counts[key] for key in
                ("retained", "partial", "missing", "reference_revision_needed", "unassessed")},
            "strict_retained_fraction": counts["retained"] / denominator if denominator else None,
            "not_fully_retained_ids": not_retained,
            "boundary": "Bound reviewer judgments about uncertainty preservation only; source quotation integrity and target presence do not prove entailment. Reference revisions require new adjudication, never an implicit resolved fact."}


def score_assessment(assessment, reference, workflow, packet):
    """Aggregate reviewed judgments; never infer semantic matches from graph paths."""
    if errors := validate_assessment(assessment, reference, workflow, packet):
        raise ValueError("; ".join(errors))
    by_id = {item.obligation_id: item for item in assessment.obligations}
    counts = Counter(item.outcome for item in assessment.obligations)
    denominator = len(reference.obligations)
    critical = [item.id for item in reference.obligations
                if item.criticality == "critical" and by_id[item.id].outcome != "covered"]
    audits = Counter(item.verdict for item in assessment.factual_audits)
    total_audits = len(assessment.factual_audits)
    undecided = audits["uncertain"] + audits["unassessed"]
    # Abstentions are never true positives. A partial reference never supplies
    # an automatic negative judgment for an unmatched emitted claim or graph node.
    referenced = {target for item in assessment.obligations for target in item.targets}
    audited = {target for item in assessment.factual_audits for target in item.targets}
    per_kind = {}
    for obligation in reference.obligations:
        counter = per_kind.setdefault(obligation.kind, Counter())
        counter[by_id[obligation.id].outcome] += 1
    return {
        "format_version": "protocol_benchmark_scores/1.1" if isinstance(assessment, BenchmarkAssessmentV2)
                          else "protocol_benchmark_scores/1.0",
        "reference_sha256": digest(reference), "workflow_sha256": digest(workflow),
        "source_packet_sha256": digest(packet), "assessment_sha256": digest(assessment),
        "reference_status": reference.annotation_status,
        "obligation_coverage": {
            "denominator": denominator,
            "counts": {key: counts[key] for key in ("covered", "missing", "partial", "unassessed")},
            "strict_covered_fraction": counts["covered"] / denominator,
            "critical_not_covered": critical,
            "by_kind": {kind: dict(counter) for kind, counter in sorted(per_kind.items())},
            "boundary": "Coverage of these source-supported reference obligations only. "
                        "Partial and unassessed items receive no full-coverage credit.",
        },
        "factual_audit": {
            "scope": assessment.factual_audit_scope, "denominator": total_audits,
            "counts": {key: audits[key] for key in
                       ("supported", "unsupported", "uncertain", "unassessed", "abstained")},
            "verified_supported_fraction": audits["supported"] / total_audits if total_audits else None,
            "possible_supported_fraction": ((audits["supported"] + undecided) / total_audits
                                           if total_audits else None),
            "supported_claim_precision": None,
            "boundary": "Audit rows and completeness are evaluator declarations, not automatic "
                        "clause enumeration. Uncertain/unassessed claims are not false; abstentions "
                        "earn no support credit. Whole-graph precision is always withheld; these fractions describe declared audit rows only.",
        },
        "unknown_preservation": unknown_preservation(assessment, reference),
        "unmatched_targets": {
            "target_ids": sorted(benchmark_target_ids(workflow) - referenced),
            "unaudited_target_ids": sorted(benchmark_target_ids(workflow) - audited),
            "status": "unjudged",
            "boundary": "Absent reference matches do not imply false output. A target-level audit "
                        "does not certify every clause in that target.",
        },
        "boundary": "Mechanical accounting of bound evaluator judgments; evidence integrity is "
                    "separate from entailment. No graph reachability, wording match, module count "
                    "or unmatched output is automatically scored as scientific correctness/error. "
                    "Cross-method relation evaluation is separate.",
    }
