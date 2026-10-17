"""Reject contradictory admitted membership claims, without deciding scientific truth.

Occurrence IDs bind a parent workflow and selected operations. A changed workflow
therefore has a different identity; a different reviewer alone does not create a
different scientific selection. Definitions and relation types must match exactly.
No equivalence, exclusivity, transitivity or experimental compatibility is inferred.
"""

from genomics_discovery.curation.workflow_extraction.models import digest

from .admission import LabelDecision, load_admission_review


def _assertions(decision, review, origin):
    assignment = decision.assignment
    if assignment is None:
        return
    concept = digest({"definition": assignment.definition, "relation": assignment.relation})
    label = assignment.definition.id
    checks = {check.occurrence_id: check for check in review.source_review.checks} if (
        review.source_review is not None) else {}
    target = checks.get(decision.occurrence_id)
    if target is not None and target.verdict == "supported":
        yield (concept, decision.occurrence_id), "member", label, origin + " target"
    boundaries = {check.occurrence_id: check for check in review.boundary_checks}
    for example in decision.case.boundary_examples:
        checked = boundaries.get(example.occurrence_id)
        if (example.membership != "unknown" and checked is not None
                and checked.verdict == "supported"):
            yield (concept, example.occurrence_id), example.membership, label, origin + " boundary"


def _stored_assertions(snapshot):
    # Worker-facing catalogues intentionally omit prior reviews/assignments.
    # The host commit repeats this check against the complete registry.
    admitted = {row["admission_decision_sha256"] for row in snapshot.get("assignments", {}).values()
                if row.get("admission_decision_sha256")}
    for key in sorted(admitted):
        row = snapshot["decisions"][key]
        if row["outcome"]["status"] != "admit":
            continue
        yield from _assertions(LabelDecision.model_validate(row["decision"]),
                               load_admission_review(row["review"]),
                               "stored decision " + key)


def _check(assertions):
    seen = {}
    for key, membership, label, origin in assertions:
        if key in seen and seen[key][0] != membership:
            previous, previous_origin = seen[key]
            raise ValueError(
                f"Conflicting supported membership assertions for concept {label}, "
                f"occurrence {key[1]}: {previous} ({previous_origin}) versus "
                f"{membership} ({origin}); reconcile the claims before admission")
        seen[key] = membership, origin


def check_snapshot_consistency(snapshot):
    """Check an otherwise validated archive's actually admitted assertions."""
    _check(_stored_assertions(snapshot))


def check_batch_consistency(proposal, review, snapshot, outcomes):
    """Check evaluated decisions against each other and admitted archive claims.

    Call after ordinary binding, evidence and outcome validation. Provisional,
    properties-only and source-negative decisions do not assert accepted facts.
    """
    if (len(outcomes) != len(proposal.decisions)
            or any(outcome["occurrence_id"] != decision.occurrence_id
                   for decision, outcome in zip(proposal.decisions, outcomes))):
        raise ValueError("Consistency check needs one ordered outcome per decision")
    checks = {check.decision_sha256: check for check in review.reviews}

    def assertions():
        yield from _stored_assertions(snapshot)
        for decision, outcome in zip(proposal.decisions, outcomes):
            if outcome["status"] == "admit":
                key = digest(decision)
                yield from _assertions(decision, checks[key], "proposed decision " + key)

    _check(assertions())
