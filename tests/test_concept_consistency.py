"""Synthetic contradictions test admission consistency, not scientific accuracy."""

from copy import deepcopy

import pytest

from test_workflow_transformations import cases as cases
from test_concept_admission import batch
from test_operation_registry import registry as registry, review, admission_for, assignment, decision_for

from genomics_discovery.curation.operation_registry.admission import (
    BoundaryExample, VocabularyProposal, VocabularyReview, evaluate,
)
from genomics_discovery.curation.operation_registry.consistency import (
    check_batch_consistency, check_snapshot_consistency,
)
from genomics_discovery.curation.operation_registry.store import empty_snapshot
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import cut_transformation


def with_boundary(proposal, checked, cases, *, membership="non_member",
                  boundary_verdict="supported", vocabulary_verdict="admit"):
    first, second = proposal.decisions
    example = BoundaryExample(occurrence_id=second.occurrence_id,
        membership=membership, reason="Synthetic classification assertion",
        evidence=second.assignment.members[0].evidence)
    first = first.model_copy(update={"case": first.case.model_copy(update={
        "boundary_examples": (*first.case.boundary_examples, example)})})
    boundary = review(second.assignment, cases, boundary_verdict).checks[0]
    first_check = admission_for(first, review(first.assignment, cases), vocabulary_verdict)
    first_check = first_check.model_copy(update={"boundary_checks": (boundary,)})
    proposal = VocabularyProposal(decisions=(first, second))
    checked = VocabularyReview(proposal_sha256=digest(proposal),
                               reviews=(first_check, checked.reviews[1]))
    return proposal, checked


def outcomes(proposal, checked, cases):
    by_decision = {item.decision_sha256: item for item in checked.reviews}
    return [{**evaluate(decision, by_decision[digest(decision)], *cases[:2]),
             "occurrence_id": decision.occurrence_id} for decision in proposal.decisions]


def test_same_concept_cannot_admit_an_explicitly_excluded_selection(cases):
    snapshot = empty_snapshot()
    proposed, checked = with_boundary(*batch(cases, snapshot, indices=(0, 1)), cases)
    assessed = outcomes(proposed, checked, cases)
    assert [row["status"] for row in assessed] == ["admit", "admit"]
    with pytest.raises(ValueError, match="Conflicting supported membership assertions"):
        check_batch_consistency(proposed, checked, snapshot, assessed)
    assert not snapshot["concepts"] and not snapshot["assignments"]


def test_host_commit_rejects_contradictory_batch_atomically(cases, registry):
    before = registry.snapshot()
    proposed, checked = with_boundary(*batch(cases, before, indices=(0, 1)), cases)
    with pytest.raises(ValueError, match="Conflicting supported membership assertions"):
        registry.apply_batch(proposed, checked, *cases[:2])
    assert registry.snapshot() == before


@pytest.mark.parametrize("membership", ["member", "unknown"])
def test_agreement_or_unknown_is_not_a_contradiction(cases, membership):
    snapshot = empty_snapshot()
    proposed, checked = with_boundary(*batch(cases, snapshot, indices=(0, 1)), cases,
                                      membership=membership)
    check_batch_consistency(proposed, checked, snapshot, outcomes(proposed, checked, cases))


@pytest.mark.parametrize("boundary_verdict", ["unsupported", "unresolved"])
def test_unaccepted_boundary_claim_does_not_assert_nonmembership(cases, boundary_verdict):
    snapshot = empty_snapshot()
    proposed, checked = with_boundary(*batch(cases, snapshot, indices=(0, 1)), cases,
                                      boundary_verdict=boundary_verdict)
    assessed = outcomes(proposed, checked, cases)
    assert assessed[0]["status"] == "provisional"
    check_batch_consistency(proposed, checked, snapshot, assessed)


@pytest.mark.parametrize("verdict", ["properties_only", "provisional"])
def test_unadmitted_decision_does_not_assert_accepted_boundary(cases, verdict):
    snapshot = empty_snapshot()
    proposed, checked = with_boundary(*batch(cases, snapshot, indices=(0, 1)), cases,
                                      vocabulary_verdict=verdict)
    check_batch_consistency(proposed, checked, snapshot, outcomes(proposed, checked, cases))


@pytest.mark.parametrize("field", ["definition", "relation"])
def test_distinct_definitions_or_relation_types_are_not_equated(cases, field):
    snapshot = empty_snapshot()
    proposed, checked = batch(cases, snapshot, indices=(0, 1))
    second = proposed.decisions[1]
    assignment = second.assignment
    if field == "definition":
        assignment = assignment.model_copy(update={"definition": assignment.definition.model_copy(
            update={"id": "different-definition", "output": "A different synthetic endpoint"})})
    else:
        assignment = assignment.model_copy(update={"relation": "shared_purpose"})
    second = second.model_copy(update={"assignment": assignment})
    proposed = VocabularyProposal(decisions=(proposed.decisions[0], second))
    checked = VocabularyReview(proposal_sha256=digest(proposed), reviews=(checked.reviews[0],
        admission_for(second, review(assignment, cases))))
    proposed, checked = with_boundary(proposed, checked, cases)
    check_batch_consistency(proposed, checked, snapshot, outcomes(proposed, checked, cases))


def test_prior_admitted_target_cannot_be_excluded_by_later_admission(cases, registry):
    previous, previous_review = batch(cases, registry.snapshot(), indices=(1,))
    ident = previous.decisions[0].occurrence_id
    registry.apply_batch(previous, previous_review, {ident: cases[0][ident]}, cases[1])
    snapshot = registry.snapshot()
    proposed, checked = batch(cases, snapshot, indices=(0, 1))
    decisions = tuple(decision.model_copy(update={"assignment": decision.assignment.model_copy(
        update={"decision": "reuse"})}) for decision in proposed.decisions)
    proposed = VocabularyProposal(decisions=decisions)
    checked = VocabularyReview(proposal_sha256=digest(proposed), reviews=tuple(
        admission_for(decision, review(decision.assignment, cases)) for decision in decisions))
    proposed, checked = with_boundary(proposed, checked, cases)
    # Only the new first decision is to be committed; the historical second
    # selection still serves as evidence for its boundary assertion.
    proposed = VocabularyProposal(decisions=(proposed.decisions[0],))
    checked = VocabularyReview(proposal_sha256=digest(proposed), reviews=(checked.reviews[0],))
    with pytest.raises(ValueError, match="Conflicting supported membership assertions"):
        check_batch_consistency(proposed, checked, snapshot, outcomes(proposed, checked, cases))


def test_snapshot_check_rejects_conflict_even_with_consistent_artifact_hashes(cases, registry):
    proposed, checked = batch(cases, registry.snapshot(), indices=(0, 1))
    registry.apply_batch(proposed, checked, *cases[:2])
    snapshot = deepcopy(registry.snapshot())
    changed, changed_review = with_boundary(proposed, checked, cases)
    original_key, changed_key = digest(proposed.decisions[0]), digest(changed.decisions[0])
    archived = snapshot["decisions"].pop(original_key)
    archived.update(decision=changed.decisions[0].model_dump(mode="json"),
                    review=changed_review.reviews[0].model_dump(mode="json"),
                    review_sha256=digest(changed_review.reviews[0]))
    snapshot["decisions"][changed_key] = archived
    for row in snapshot["assignments"].values():
        if row["admission_decision_sha256"] == original_key:
            row["admission_decision_sha256"] = changed_key
    with pytest.raises(ValueError, match="Conflicting supported membership assertions"):
        check_snapshot_consistency(snapshot)
    # The public graph projection must also refuse it, not only this helper.
    from genomics_discovery.curation.operation_registry.store import graph_overlay
    with pytest.raises(ValueError, match="Conflicting supported membership assertions"):
        graph_overlay(snapshot)


def third_selection(cases):
    occurrences, packets, workflows = cases
    packet = packets[next(ident for ident, item in occurrences.items() if item.method_id == "b")]
    cut = cut_transformation(workflows[1], packet, ["insert"])
    return ({**occurrences, cut.id: cut}, {**packets, cut.id: packet}, workflows)


def test_conflicting_supported_boundaries_rejected_without_target_admission(cases):
    cases = third_selection(cases)
    snapshot = empty_snapshot()
    proposed, checked = batch(cases, snapshot, indices=(0, 1))
    third = assignment(cases, snapshot, index=2)
    boundary_check = review(third, cases).checks[0]
    decisions, reviews = [], []
    for decision, classification in zip(proposed.decisions, ("member", "non_member")):
        example = BoundaryExample(occurrence_id=third.members[0].occurrence_id,
            membership=classification, reason="Synthetic boundary disagreement",
            evidence=third.members[0].evidence)
        decision = decision.model_copy(update={"case": decision.case.model_copy(update={
            "boundary_examples": (*decision.case.boundary_examples, example)})})
        decisions.append(decision)
        reviews.append(admission_for(decision, review(decision.assignment, cases)).model_copy(
            update={"boundary_checks": (boundary_check,)}))
    undecided = decision_for(third).model_copy(update={"assignment": None})
    decisions.append(undecided)
    reviews.append(admission_for(undecided, None, "provisional"))
    proposed = VocabularyProposal(decisions=tuple(decisions))
    checked = VocabularyReview(proposal_sha256=digest(proposed), reviews=tuple(reviews))
    with pytest.raises(ValueError, match="Conflicting supported membership assertions"):
        check_batch_consistency(proposed, checked, snapshot, outcomes(proposed, checked, cases))


def test_different_operation_selection_is_not_a_contradiction(cases):
    cases = third_selection(cases)
    snapshot = empty_snapshot()
    proposed, checked = with_boundary(*batch(cases, snapshot, indices=(0, 1)), cases)
    distinct = assignment(cases, snapshot, index=2)
    second = decision_for(distinct)
    proposed = VocabularyProposal(decisions=(proposed.decisions[0], second))
    checked = VocabularyReview(proposal_sha256=digest(proposed), reviews=(checked.reviews[0],
        admission_for(second, review(distinct, cases))))
    # The excluded two-operation cut and admitted one-operation cut are distinct.
    check_batch_consistency(proposed, checked, snapshot, outcomes(proposed, checked, cases))


def test_consistency_refuses_missing_outcome(cases):
    proposed, checked = batch(cases, empty_snapshot(), indices=(0, 1))
    with pytest.raises(ValueError, match="one ordered outcome"):
        check_batch_consistency(proposed, checked, empty_snapshot(),
                                outcomes(proposed, checked, cases)[:1])
