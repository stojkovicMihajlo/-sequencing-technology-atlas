"""Admission controls are evaluated separately from source-support correctness."""

import json
import sqlite3
from copy import deepcopy

import pytest
from test_workflow_transformations import cases as cases
from test_operation_registry import (registry as registry, assignment, review, decision_for,
                                     admission_for, commit)
from genomics_discovery.curation.operation_registry.admission import (
    VocabularyProposal, VocabularyReview, RevisionRequest,
)
from genomics_discovery.curation.operation_registry.store import Registry, catalogue, graph_overlay
from genomics_discovery.curation.workflow_extraction.models import digest


def batch(cases, snapshot, indices=(0,), verdict="admit", filters_sufficient=False):
    decisions, reviews = [], []
    for index in indices:
        proposal = assignment(cases, snapshot, index=index)
        decision = decision_for(proposal)
        decision = decision.model_copy(update={"case": decision.case.model_copy(update={
            "filters_sufficient": filters_sufficient})})
        decisions.append(decision)
        reviews.append(admission_for(decision, review(proposal, cases), verdict).model_copy(update={
            "existing_plus_filters_sufficient": filters_sufficient}))
    proposed = VocabularyProposal(decisions=tuple(decisions))
    checked = VocabularyReview(proposal_sha256=digest(proposed), reviews=tuple(reviews))
    return proposed, checked


def apply(registry, cases, proposed, checked):
    selected = {d.occurrence_id: cases[0][d.occurrence_id] for d in proposed.decisions}
    return registry.apply_batch(proposed, checked, selected, cases[1])


def test_source_support_alone_cannot_create_label(cases, registry):
    p = assignment(cases, registry.snapshot())
    with pytest.raises(ValueError, match="Source review alone"):
        registry.apply(p, review(p, cases), *cases[:2])
    assert not registry.snapshot()["concepts"]


def test_supported_but_redundant_keeps_record_without_creating_label(cases, registry):
    p, r = batch(cases, registry.snapshot(), verdict="properties_only", filters_sufficient=True)
    outcome = apply(registry, cases, p, r)
    assert outcome["outcomes"][0]["source_status"] == "supported_membership_candidate"
    snap = registry.snapshot()
    assert not snap["concepts"] and not snap["assignments"]
    retained = next(iter(snap["decisions"].values()))
    assert retained["occurrence"] == cases[0][p.decisions[0].occurrence_id].model_dump(mode="json")
    assert retained["outcome"]["status"] == "properties_only"


@pytest.mark.parametrize("fault", ["filter_alternative", "question_useful", "boundary_clear",
                                  "context_properties_separated", "stale_decision"])
def test_positive_verdict_cannot_bypass_admission_fields(cases, registry, fault):
    p, r = batch(cases, registry.snapshot(), filters_sufficient=fault == "filter_alternative")
    if fault == "stale_decision":
        changed = p.decisions[0].model_copy(update={"explanation": "Changed after review"})
        p = VocabularyProposal(decisions=(changed,))
        r = r.model_copy(update={"proposal_sha256": digest(p)})
    elif fault != "filter_alternative":
        r = r.model_copy(update={"reviews": (r.reviews[0].model_copy(update={fault: False}),)})
    with pytest.raises(ValueError):
        apply(registry, cases, p, r)
    assert not registry.snapshot()["concepts"]


def test_batch_admits_same_definition_for_two_examples_without_sequential_bias(cases, registry, tmp_path):
    p, r = batch(cases, registry.snapshot(), indices=(0, 1))
    apply(registry, cases, p, r)
    other = Registry(tmp_path / "reversed.sqlite"); other.initialize()
    reversed_p = VocabularyProposal(decisions=tuple(reversed(p.decisions)))
    reversed_r = VocabularyReview(proposal_sha256=digest(reversed_p), reviews=tuple(reversed(r.reviews)))
    apply(other, cases, reversed_p, reversed_r)
    first, second = registry.snapshot(), other.snapshot()
    assert first["concepts"] == second["concepts"]
    assert set(first["assignments"]) == set(second["assignments"])
    assert len(first["concepts"]) == 1 and len(first["assignments"]) == 2
    assert all(row["commit_base_revision"] == p.decisions[0].assignment.registry_revision
               for row in first["assignments"].values())


def test_conflicting_batch_definitions_roll_back_everything(cases, registry):
    p, r = batch(cases, registry.snapshot(), indices=(0, 1))
    d = p.decisions[1]
    a = d.assignment.model_copy(update={"definition": d.assignment.definition.model_copy(update={
        "output": "Different scientifically consequential output in this synthetic fixture"})})
    d = d.model_copy(update={"assignment": a})
    p = VocabularyProposal(decisions=(p.decisions[0], d))
    r = VocabularyReview(proposal_sha256=digest(p), reviews=(r.reviews[0], admission_for(d, review(a, cases))))
    with pytest.raises(ValueError, match="Conflicting definitions"):
        apply(registry, cases, p, r)
    assert not registry.snapshot()["concepts"] and "decisions" not in registry.snapshot()


def test_provisional_singleton_is_retained_without_recurrence_rule(cases, registry):
    p, r = batch(cases, registry.snapshot(), verdict="provisional")
    apply(registry, cases, p, r)
    assert len(registry.snapshot()["decisions"]) == 1
    assert not registry.snapshot()["concepts"]


def test_legacy_entries_are_unassessed_and_can_be_explicitly_reviewed(cases, registry):
    first = assignment(cases, registry.snapshot())
    commit(registry, first, review(first, cases), *cases[:2])
    legacy = registry.snapshot()
    legacy.pop("decisions")
    old = legacy["assignments"][digest(first)]
    old.pop("admission_decision_sha256"); old.pop("supersedes_assignment_ids")
    with sqlite3.connect(registry.path) as db:
        db.execute("UPDATE registry SET payload=? WHERE id=1", (json.dumps(legacy),))
    assert catalogue(registry.snapshot())["admission_status"][first.definition.id] == "legacy_unassessed"
    assert not graph_overlay(registry.snapshot())["edges"]
    reused = assignment(cases, registry.snapshot(), decision="reuse")
    commit(registry, reused, review(reused, cases), *cases[:2])
    after = registry.snapshot()
    assert after["assignments"][digest(first)] == old
    assert after["assignments"][digest(reused)]["supersedes_assignment_ids"] == [digest(first)]
    assert len(graph_overlay(after)["edges"]) == 1
    # Serialization order must not determine historical supersession validity.
    after["assignments"] = dict(reversed(list(after["assignments"].items())))
    assert len(graph_overlay(after)["edges"]) == 1


def test_revision_request_is_archived_without_mutating_definition(cases, registry):
    first = assignment(cases, registry.snapshot())
    commit(registry, first, review(first, cases), *cases[:2])
    old = deepcopy(registry.snapshot()["concepts"])
    p = assignment(cases, registry.snapshot(), index=1, decision="reuse")
    d = decision_for(p)
    d = d.model_copy(update={"case": d.case.model_copy(update={"revision_requests": (
        RevisionRequest(concept_ids=(first.definition.id,), action="broaden", reason="Fixture review needed"),)})})
    checked = admission_for(d, review(p, cases), "provisional")
    proposed = VocabularyProposal(decisions=(d,))
    apply(registry, cases, proposed, VocabularyReview(proposal_sha256=digest(proposed), reviews=(checked,)))
    assert registry.snapshot()["concepts"] == old
    assert registry.snapshot()["decisions"][digest(d)]["decision"]["case"]["revision_requests"]


def test_changed_archived_admission_review_is_rejected(cases, registry):
    p, r = batch(cases, registry.snapshot()); apply(registry, cases, p, r)
    snap = registry.snapshot()
    next(iter(snap["decisions"].values()))["review"]["boundary_clear"] = False
    with sqlite3.connect(registry.path) as db:
        db.execute("UPDATE registry SET payload=? WHERE id=1", (json.dumps(snap),))
    with pytest.raises(ValueError, match="admission decision/review binding"):
        registry.snapshot()


def test_boundary_example_requires_its_own_review_and_cannot_force_admission(cases, registry):
    from genomics_discovery.curation.operation_registry.admission import BoundaryExample, validate_batch
    first = assignment(cases, registry.snapshot())
    second = assignment(cases, registry.snapshot(), index=1)
    d = decision_for(first)
    d = d.model_copy(update={'case': d.case.model_copy(update={'boundary_examples': (
        *d.case.boundary_examples, BoundaryExample(occurrence_id=second.members[0].occurrence_id,
            membership='unknown', reason='Fixture: unresolved applicability', evidence=second.members[0].evidence))})})
    other = decision_for(second).model_copy(update={'assignment':None})
    proposal = VocabularyProposal(decisions=(d, other))
    first_check = admission_for(d, review(first, cases))
    other_check = admission_for(other, None, 'provisional')
    checked = VocabularyReview(proposal_sha256=digest(proposal), reviews=(first_check, other_check))
    with pytest.raises(ValueError, match='own source check'):
        validate_batch(proposal, checked, registry.snapshot(), *cases[:2])
    boundary = review(second, cases, 'unresolved').checks[0]
    first_check = first_check.model_copy(update={'boundary_checks': (boundary,)})
    checked = VocabularyReview(proposal_sha256=digest(proposal), reviews=(first_check, other_check))
    outcomes = apply(registry, cases, proposal, checked)['outcomes']
    assert outcomes[0]['status'] == 'provisional'
    assert not registry.snapshot()['concepts']


def test_forged_boundary_quote_is_not_accepted_as_usefulness_evidence(cases, registry):
    p, r = batch(cases, registry.snapshot())
    d = p.decisions[0]
    example = d.case.boundary_examples[0]
    forged = example.evidence[0].model_copy(update={'quote':'Fabricated text absent from the primary source'})
    example = example.model_copy(update={'evidence':(forged,)})
    d = d.model_copy(update={'case':d.case.model_copy(update={'boundary_examples':(example,)})})
    p = VocabularyProposal(decisions=(d,))
    r = VocabularyReview(proposal_sha256=digest(p),reviews=(admission_for(d,review(d.assignment,cases)),))
    with pytest.raises(ValueError,match='quote is not present'):
        apply(registry,cases,p,r)
