"""Claim-level admission gates; synthetic verdicts do not establish scientific truth."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from test_operation_registry import (
    admission_for, assignment, decision_for, registry as registry, review,
)
from test_workflow_transformations import cases as cases

from genomics_discovery.curation.operation_registry.admission import (
    AdmissionReview, CurrentVocabularyReview, VocabularyProposal,
    VocabularyReview, evaluate, load_admission_review,
)
from genomics_discovery.curation.operation_registry.definition_review import (
    DEFINITION_FIELDS, DefinitionCheck, validate_definition_review,
)
from genomics_discovery.curation.operation_registry.integrity import validate_snapshot
from genomics_discovery.curation.workflow_extraction.models import digest


@pytest.fixture
def proposed(cases, registry):
    candidate = assignment(cases, registry.snapshot())
    decision = decision_for(candidate)
    return decision, admission_for(decision, review(candidate, cases))


def altered_check(checked, field, **updates):
    return checked.model_copy(update={"definition_checks": tuple(
        item.model_copy(update=updates) if field in item.fields else item
        for item in checked.definition_checks
    )})


def apply_one(registry, cases, decision, checked):
    proposed = VocabularyProposal(decisions=(decision,))
    checked_batch = VocabularyReview(proposal_sha256=digest(proposed), reviews=(checked,))
    return registry.apply_batch(proposed, checked_batch,
        {decision.occurrence_id: cases[0][decision.occurrence_id]}, cases[1])


def test_complete_grouped_definition_review_can_admit(cases, registry, proposed):
    decision, checked = proposed
    # Grouping is permitted when the same source evidence genuinely supports the
    # selected facets; software only verifies the explicit coverage contract.
    grouped = DefinitionCheck(fields=DEFINITION_FIELDS, verdict="supported",
        rationale="Synthetic fixture's common evidence covers the declared definition",
        evidence=checked.definition_checks[0].evidence)
    checked = checked.model_copy(update={"definition_checks": (grouped,)})
    result = apply_one(registry, cases, decision, checked)
    assert result["outcomes"][0]["status"] == "admit"
    assert result["outcomes"][0]["definition_status"] == "supported_definition"
    assert len(registry.snapshot()["assignments"]) == 1


@pytest.mark.parametrize("fault", ["omitted", "duplicate", "duplicate_inside_group"])
def test_positive_overall_review_cannot_bypass_exact_field_coverage(cases, registry, proposed, fault):
    decision, checked = proposed
    checks = checked.definition_checks
    if fault == "omitted":
        checks = checks[:-1]
    elif fault == "duplicate":
        checks = (*checks, checks[0])
    else:
        checks = (checks[0].model_copy(update={"fields": ("substrate", "substrate")}), *checks[1:])
    checked = checked.model_copy(update={"definition_checks": checks})
    with pytest.raises(ValueError, match="each definition field exactly once"):
        apply_one(registry, cases, decision, checked)
    assert not registry.snapshot()["concepts"]


@pytest.mark.parametrize("verdict,expected", [
    ("unsupported", "needs_revision"), ("unresolved", "research_needed"),
])
def test_negative_field_blocks_admission_despite_positive_member_review(
    cases, registry, proposed, verdict, expected,
):
    decision, checked = proposed
    checked = altered_check(checked, "physical_mechanism", verdict=verdict,
        rationale="Fixture evidence does not establish the asserted mechanism")
    assert checked.vocabulary_verdict == "admit"
    assert checked.source_review.checks[0].verdict == "supported"
    result = apply_one(registry, cases, decision, checked)
    assert result["outcomes"][0]["status"] == expected
    snapshot = registry.snapshot()
    assert not snapshot["concepts"] and not snapshot["assignments"]
    assert snapshot["decisions"][digest(decision)]["outcome"]["definition_status"] == expected


def test_unsupported_membership_is_not_downgraded_to_research_needed(cases, proposed):
    decision, checked = proposed
    checked = altered_check(checked, "output", verdict="unresolved")
    checked = checked.model_copy(update={"source_review": review(decision.assignment, cases, "unsupported")})
    assert evaluate(decision, checked, *cases[:2])["status"] == "needs_revision"


def test_unresolved_claim_can_record_missing_support_without_citation_padding(cases, registry, proposed):
    decision, checked = proposed
    checked = altered_check(checked, "physical_mechanism", verdict="unresolved", evidence=(),
        rationale="The inspected source scope does not establish the claimed mechanism.")
    result = apply_one(registry, cases, decision, checked)
    assert result["outcomes"][0]["definition_status"] == "research_needed"
    assert result["outcomes"][0]["status"] == "research_needed"
    assert not registry.snapshot()["concepts"]


@pytest.mark.parametrize("fault", ["unjustified_not_asserted", "missing_evidence"])
def test_asserted_fields_cannot_escape_review(cases, proposed, fault):
    decision, checked = proposed
    update = {"evidence": ()}
    if fault == "unjustified_not_asserted":
        update["verdict"] = "not_asserted"
    checked = altered_check(checked, "output", **update)
    with pytest.raises(ValueError, match="absent definition claim|require scoped evidence"):
        evaluate(decision, checked, *cases[:2])


@pytest.mark.parametrize("fault", [None, "supported", "evidence_attached"])
def test_unknown_mechanism_is_explicitly_not_asserted(cases, registry, fault):
    candidate = assignment(cases, registry.snapshot(), relation="shared_purpose")
    definition = candidate.definition.model_copy(update={"physical_mechanism": None,
        "mechanism_unknown_reason": "The inspected fixture leaves the physical mechanism unknown"})
    candidate = candidate.model_copy(update={"definition": definition})
    decision = decision_for(candidate)
    checked = admission_for(decision, review(candidate, cases))
    if fault == "supported":
        checked = altered_check(checked, "physical_mechanism", verdict="supported",
            evidence=checked.source_review.checks[0].evidence)
    elif fault == "evidence_attached":
        checked = altered_check(checked, "physical_mechanism",
            evidence=checked.source_review.checks[0].evidence)
    if fault:
        with pytest.raises(ValueError, match="absent mechanism|without evidence"):
            evaluate(decision, checked, *cases[:2])
    else:
        result = evaluate(decision, checked, *cases[:2])
        assert result["status"] == "admit"
        assert result["definition_status"] == "supported_definition"


def test_definition_citation_requires_declared_source_inspection(cases, proposed):
    decision, checked = proposed
    member = checked.source_review.checks[0]
    # The exact quote is genuine, but its passage is omitted from the inspection
    # declaration. This tests only the definition gate, not observed tool access.
    source_review = checked.source_review.model_copy(update={"checks": (
        member.model_copy(update={"inspected_passages": member.inspected_passages[1:]}),)})
    with pytest.raises(ValueError, match="absent from declared source inspection"):
        validate_definition_review(decision.assignment, checked.definition_checks,
            source_review, cases[1][decision.occurrence_id])


def test_fabricated_definition_quote_cannot_authorize_admission(cases, proposed):
    decision, checked = proposed
    forged = checked.definition_checks[0].evidence[0].model_copy(update={
        "quote": "Invented chemistry statement absent from this source packet"})
    checked = altered_check(checked, "physical_mechanism", evidence=(forged,))
    with pytest.raises(ValueError, match="quote is not present"):
        evaluate(decision, checked, *cases[:2])


def test_absent_assignment_keeps_no_definition_verdicts(cases, proposed):
    decision, prior = proposed
    decision = decision.model_copy(update={"assignment": None})
    checked = admission_for(decision, None, "properties_only")
    assert evaluate(decision, checked, *cases[:2])["definition_status"] == "not_proposed"
    checked = checked.model_copy(update={"definition_checks": prior.definition_checks})
    with pytest.raises(ValueError, match="Absent assignment cannot have definition checks"):
        evaluate(decision, checked, *cases[:2])


def test_historical_review_roundtrips_unchanged_but_cannot_authorize_new_admission(cases, proposed):
    decision, checked = proposed
    old = checked.model_dump(mode="json", exclude={"format_version", "definition_checks"})
    historical = load_admission_review(old)
    assert type(historical) is AdmissionReview
    assert historical.model_dump(mode="json") == old
    assert digest(historical) == digest(old)
    batch = VocabularyReview(proposal_sha256=digest(VocabularyProposal(decisions=(decision,))),
        reviews=(historical,))
    assert digest(VocabularyReview.model_validate_json(batch.model_dump_json())) == digest(batch)
    with pytest.raises(ValidationError):
        CurrentVocabularyReview.model_validate(batch.model_dump(mode="json"))
    with pytest.raises(ValueError, match="new admission requires concept_admission_review/2.0"):
        evaluate(decision, historical, *cases[:2])


def test_v2_review_binds_the_actual_definition_text(cases, proposed):
    decision, checked = proposed
    changed = decision.assignment.definition.model_copy(update={"output": "A different product"})
    decision = decision.model_copy(update={"assignment": decision.assignment.model_copy(
        update={"definition": changed})})
    with pytest.raises(ValueError, match="exact decision"):
        evaluate(decision, checked, *cases[:2])


def test_changed_archived_facet_verdict_invalidates_review_hash(cases, registry, proposed):
    decision, checked = proposed
    apply_one(registry, cases, decision, checked)
    snapshot = deepcopy(registry.snapshot())
    row = snapshot["decisions"][digest(decision)]
    row["review"]["definition_checks"][0]["verdict"] = "unsupported"
    with pytest.raises(ValueError, match="admission decision/review binding"):
        validate_snapshot(snapshot)


@pytest.mark.parametrize("fault", ["definition_status", "overall_status"])
def test_archived_positive_outcome_cannot_contradict_negative_definition(
    cases, registry, proposed, fault,
):
    decision, checked = proposed
    checked = altered_check(checked, "operation", verdict="unsupported")
    apply_one(registry, cases, decision, checked)
    snapshot = deepcopy(registry.snapshot())
    row = snapshot["decisions"][digest(decision)]
    if fault == "definition_status":
        row["outcome"]["definition_status"] = "supported_definition"
    else:
        row["outcome"]["status"] = "admit"
    with pytest.raises(ValueError, match="definition outcome differs|unsupported definition claims"):
        validate_snapshot(snapshot)
