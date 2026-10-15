"""Check exact accounting/provenance and produce an explicitly unapproved worklist."""

from collections import Counter
from pathlib import Path

from genomics_discovery.curation.source_originals import validate_inspection
from genomics_discovery.curation.technology_graph.queries import load_graph
from genomics_discovery.curation.workflow_extraction.models import (
    Workflow,
    WorkflowReview,
    digest,
    target_ids,
    validate_evidence,
    validate_review,
)
from genomics_discovery.curation.workflow_extraction.sources import load_packet

from .models import GapAudit


def load_inputs(graph_path: Path, workflow_id: str, selected_gap_ids=None):
    graph = load_graph(graph_path)
    record = graph["workflows"][workflow_id]
    workflow = Workflow.model_validate(record["workflow"])
    review = WorkflowReview.model_validate(record["review"])
    packet = load_packet(Path(record["packet_path"]))
    if (digest(workflow), digest(packet), digest(review)) != (
        record["workflow_sha256"], record["packet_sha256"], record["review_sha256"]
    ):
        raise ValueError("Parent workflow, source packet or review digest changed")
    errors = validate_evidence(workflow, packet) + validate_review(review, workflow, packet)
    if errors:
        raise ValueError("Invalid parent: " + "; ".join(errors))
    available = {g.id for g in workflow.unresolved}
    selected = sorted(available) if selected_gap_ids is None else list(selected_gap_ids)
    if len(selected) != len(set(selected)) or set(selected) - available:
        raise ValueError("Select unique, existing retained gap IDs")
    return graph, record, workflow, review, packet, selected


def validate_audit(audit: GapAudit, workflow, review, packet, selected_gap_ids, *,
                   originals=None, attempt=None):
    """Structural accounting and quote integrity, not semantic certification."""
    # A page locator and a text-passage locator deliberately have different
    # contracts. The legacy recursive quote checker interprets *any* source_id
    # dictionary as a passage, so feed it only the typed text references.
    errors = []
    for check in audit.checks:
        for issue in check.issues:
            errors.extend(f"Issue {issue.id}: {error}" for error in validate_evidence(
                {"evidence": issue.evidence, "inspected_passages": issue.inspected_passages},
                packet,
            ))
    if (audit.workflow_sha256, audit.source_packet_sha256, audit.workflow_review_sha256) != (
        digest(workflow), digest(packet), digest(review)
    ):
        errors.append("Audit must bind the exact workflow, packet and source review")
    expected = set(selected_gap_ids)
    if len(expected) != len(selected_gap_ids) or expected - {g.id for g in workflow.unresolved}:
        errors.append("Selected gaps must be unique and belong to this workflow")
    counts = Counter(c.gap_id for c in audit.checks)
    if counts.keys() != expected or any(n != 1 for n in counts.values()):
        errors.append("Audit must check every selected gap exactly once, with no extras")
    allowed = set(target_ids(workflow)) | {"workflow"}
    issue_ids = set()
    for check in audit.checks:
        for issue in check.issues:
            if issue.id in issue_ids:
                errors.append(f"Duplicate issue ID: {issue.id}")
            issue_ids.add(issue.id)
            if len(issue.targets) != len(set(issue.targets)) or set(issue.targets) - allowed:
                errors.append(f"Issue {issue.id}: unknown or duplicate workflow targets")
            inspected = [(r.source_id, r.passage_id) for r in issue.inspected_passages]
            if len(inspected) != len(set(inspected)):
                errors.append(f"Issue {issue.id}: duplicate inspected passage")
            if any((e.source_id, e.passage_id) not in inspected for e in issue.evidence):
                errors.append(f"Issue {issue.id}: evidence was not recorded as inspected")
            for inspection in issue.inspected_originals:
                try:
                    if originals is None or attempt is None:
                        raise ValueError("Original inspection needs frozen bindings and an attempt")
                    validate_inspection(inspection, originals, Path(attempt))
                except (ValueError, KeyError, OSError, TypeError) as exc:
                    errors.append(f"Issue {issue.id}: invalid original inspection: {exc}")
    return errors


def followup_queue(audit, workflow, review, packet, selected_gap_ids, **inspection_context):
    errors = validate_audit(audit, workflow, review, packet, selected_gap_ids, **inspection_context)
    if errors:
        raise ValueError("; ".join(errors))
    rows = []
    for check in audit.checks:
        for issue in check.issues:
            rows.append({
                "request_id": "followup:" + digest([digest(audit), check.gap_id, issue.id]),
                "gap_id": check.gap_id,
                **issue.model_dump(mode="json"),
                "status": "proposed_not_resolved",
            })
    return {
        "format_version": "workflow_followup_queue/1.0",
        "audit_sha256": digest(audit),
        "workflow_sha256": digest(workflow),
        "source_packet_sha256": digest(packet),
        "selected_gap_ids": list(selected_gap_ids),
        "unreviewed_gap_ids": sorted({g.id for g in workflow.unresolved} - set(selected_gap_ids)),
        "action_counts": dict(sorted(Counter(row["action"] for row in rows).items())),
        "requests": rows,
        "boundary": "An independent model's proposed follow-up, not scientific closure. "
                    "Unknowns apply to the explicitly inspected passages, not all literature. "
                    "Repairs require a new workflow and fresh source review; acquisition alone "
                    "does not resolve a gap. Parent records and graph edges are unchanged.",
    }
