"""Specialist objections cannot disappear at the synthesis boundary.

Fixtures establish bookkeeping guarantees, not the scientific truth of reviews.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from genomics_discovery.ideation.models import IdeaReview, parse_candidate, review_targets
from genomics_discovery.ideation.review import (
    SPECIALISMS, SpecialistIssue, SpecialistPanel, SpecialistResolution,
    required_specialist_targets, specialist_issue_id, validate_specialist_panel,
    validate_specialist_resolutions,
)
from genomics_discovery.illumina.models import digest
from tests.ideation.test_models import candidate_data, review_data


def ideas():
    result = []
    for name in ("first", "second", "third"):
        data = candidate_data()
        data.update(id=name, format_version="proposed_assay/1.1", research={
            area: {"question": f"Investigate {area}.", "conclusion": "Unresolved fixture question.",
                "status": "unresolved", "targets": ["operation:prepare"],
                "remaining_uncertainty": "No scientific assessment was performed in this test."}
            for area in ("capability", "mechanism", "interfaces", "challenge")})
        result.append(parse_candidate(data))
    return result


def issue(severity="blocking"):
    return {"id": "handoff", "target": "operation:prepare", "severity": severity,
        "reason": "The fixture does not establish the required molecular interface.",
        "next_test": "Locate supporting evidence or explicitly qualify the required property.",
        "literature_refs": [{"source_id": "source_fixture", "content_sha256": "a"*64,
            "locator": "Fixture passage", "quote": "No scientific evidence is asserted by this fixture."}]}


def reports(candidates, *, issues=True):
    result = []
    for lens in SPECIALISMS:
        reviews = []
        for candidate in candidates:
            reviews.append({"candidate_id": candidate.id, "candidate_sha256": digest(candidate),
                "reviewed_targets": sorted(required_specialist_targets(candidate, lens) | {"operation:prepare"}),
                "issues": [issue()] if issues else [], "summary": "Scoped test assessment only."})
        result.append(SpecialistPanel(specialism=lens, reviews=reviews, summary="Separate design risks retained."))
    return result


def synthesis(candidates, panels):
    reviews, resolutions = [], []
    for candidate in candidates:
        data = review_data(candidate)
        data.update(issues=[], disposition="hold")
        for panel in panels:
            specialist = next(r for r in panel.reviews if r.candidate_id == candidate.id)
            for finding in specialist.issues:
                copied = {key: getattr(finding, key) for key in ("id", "target", "severity", "reason", "next_test")}
                copied["id"] = specialist_issue_id(panel.specialism, finding.id)
                data["issues"].append(copied)
                resolutions.append(SpecialistResolution(specialism=panel.specialism,
                    candidate_id=candidate.id, issue_id=finding.id, status="unresolved",
                    reason="The original dependency remains unresolved."))
        reviews.append(IdeaReview.model_validate(data))
    return SimpleNamespace(reviews=tuple(reviews), specialist_issue_resolutions=tuple(resolutions))


def test_three_candidates_receive_two_scoped_reviews_without_duplicate_full_ledgers():
    candidates = ideas()
    panels = reports(candidates)
    for panel in panels:
        validate_specialist_panel(panel, candidates, panel.specialism)
        for candidate, review in zip(candidates, panel.reviews):
            assert set(review.reviewed_targets) < review_targets(candidate)
            assert {"candidate", "change:change1"} <= set(review.reviewed_targets)
            assert len(review.issues[0].literature_refs) == 1
    assert "research:interfaces" in panels[0].reviews[0].reviewed_targets
    assert "research:challenge" in panels[1].reviews[0].reviewed_targets
    validate_specialist_resolutions(synthesis(candidates, panels), panels, candidates)


@pytest.mark.parametrize("fault", ["stale", "missing_candidate", "duplicate_candidate",
    "missing_change", "missing_research", "unknown_target", "issue_not_inspected", "wrong_lens"])
def test_specialist_cannot_skip_or_misbind_its_assignment(fault):
    candidates = ideas()
    report = reports(candidates)[0]
    data = report.model_dump(mode="json")
    if fault == "stale":
        data["reviews"][0]["candidate_sha256"] = "f"*64
    elif fault == "missing_candidate":
        data["reviews"].pop()
    elif fault == "duplicate_candidate":
        data["reviews"].append(deepcopy(data["reviews"][0]))
    elif fault == "missing_change":
        data["reviews"][0]["reviewed_targets"].remove("change:change1")
    elif fault == "missing_research":
        data["reviews"][0]["reviewed_targets"].remove("research:interfaces")
    elif fault == "unknown_target":
        data["reviews"][0]["reviewed_targets"].append("operation:invented")
    elif fault == "issue_not_inspected":
        data["reviews"][0]["issues"][0]["target"] = "operation:sequence"
    else:
        data["specialism"] = "measurement_benefit"
    with pytest.raises(ValueError):
        validate_specialist_panel(SpecialistPanel.model_validate(data), candidates, "chemistry_interfaces")


@pytest.mark.parametrize("field", ["reviewed_targets", "issues"])
def test_specialist_duplicate_targets_or_issue_ids_are_rejected(field):
    data = reports(ideas())[0].model_dump(mode="json")
    data["reviews"][0][field].append(deepcopy(data["reviews"][0][field][0]))
    with pytest.raises(ValidationError, match="unique"):
        SpecialistPanel.model_validate(data)


@pytest.mark.parametrize("fault", ["missing_lens", "duplicate_lens", "stale_synthesis",
    "missing_resolution", "duplicate_resolution", "unknown_resolution", "missing_issue",
    "weakened_issue", "changed_reason", "changed_test", "changed_target", "invented_origin"])
def test_synthesis_cannot_erase_or_launder_a_specialist_objection(fault):
    candidates = ideas()
    panels = reports(candidates)
    panel = synthesis(candidates, panels)
    resolutions = list(panel.specialist_issue_resolutions)
    reviews = list(panel.reviews)
    current = reviews[0]
    if fault == "missing_lens":
        panels = panels[:1]
    elif fault == "duplicate_lens":
        panels = [panels[0], panels[0]]
    elif fault == "stale_synthesis":
        reviews[0] = current.model_copy(update={"candidate_sha256": "f"*64})
    elif fault == "missing_resolution":
        resolutions.pop()
    elif fault == "duplicate_resolution":
        resolutions.append(resolutions[0])
    elif fault == "unknown_resolution":
        resolutions[0] = resolutions[0].model_copy(update={"issue_id": "invented"})
    elif fault == "missing_issue":
        reviews[0] = current.model_copy(update={"issues": current.issues[1:]})
    elif fault == "invented_origin":
        extra = current.issues[0].model_copy(update={"id": "specialist:chemistry_interfaces:invented"})
        reviews[0] = current.model_copy(update={"issues": (*current.issues, extra)})
    else:
        key, value = {"weakened_issue": ("severity", "minor"), "changed_reason": ("reason", "Dismissed without evidence."),
            "changed_test": ("next_test", "No check needed."), "changed_target": ("target", "candidate")}[fault]
        reviews[0] = current.model_copy(update={"issues": (current.issues[0].model_copy(update={key: value}), *current.issues[1:])})
    panel.reviews, panel.specialist_issue_resolutions = tuple(reviews), tuple(resolutions)
    with pytest.raises(ValueError):
        validate_specialist_resolutions(panel, panels, candidates)


def test_an_explicit_design_internal_rebuttal_is_permitted_but_not_certified_true():
    candidates = ideas()
    panels = reports(candidates)
    panel = synthesis(candidates, panels)
    old = panel.specialist_issue_resolutions[0]
    replacement = SpecialistResolution(**{**old.model_dump(), "status": "resolved",
        "reason": "The cited graph target explicitly removes the dependency alleged by the specialist.",
        "resolution_targets": ["operation:prepare"]})
    panel.specialist_issue_resolutions = (replacement, *panel.specialist_issue_resolutions[1:])
    first = panel.reviews[0]
    panel.reviews = (first.model_copy(update={"issues": first.issues[1:]}), *panel.reviews[1:])
    validate_specialist_resolutions(panel, panels, candidates)
    # An unresolved objection from the other lens remains blocking.
    assert panel.reviews[0].issues[0].severity == "blocking"
    panel.specialist_issue_resolutions = (replacement.model_copy(update={"resolution_targets": ("operation:invented",)}),
        *panel.specialist_issue_resolutions[1:])
    with pytest.raises(ValueError, match="unknown candidate target"):
        validate_specialist_resolutions(panel, panels, candidates)


def test_resolving_an_objection_without_evidence_or_a_design_target_is_rejected():
    with pytest.raises(ValidationError, match="cited evidence or exact candidate targets"):
        SpecialistResolution(specialism="chemistry_interfaces", candidate_id="first", issue_id="i1",
            status="resolved", reason="The synthesis reviewer prefers to proceed.")


def test_no_objections_requires_no_fabricated_resolution_ledger():
    candidates = ideas()
    panels = reports(candidates, issues=False)
    panel = synthesis(candidates, panels)
    assert panel.specialist_issue_resolutions == ()
    validate_specialist_resolutions(panel, panels, candidates)


def test_blocker_cannot_be_retained_even_if_object_constructed_outside_validation():
    candidates = ideas()
    panels = reports(candidates)
    panel = synthesis(candidates, panels)
    panel.reviews = (panel.reviews[0].model_copy(update={"disposition": "retain_for_testing"}), *panel.reviews[1:])
    with pytest.raises(ValueError, match="blocker"):
        validate_specialist_resolutions(panel, panels, candidates)


def test_same_local_issue_id_from_two_lenses_does_not_collide():
    candidates = ideas()
    panels = reports(candidates)
    panel = synthesis(candidates, panels)
    assert {x.id for x in panel.reviews[0].issues} == {
        "specialist:chemistry_interfaces:handoff", "specialist:measurement_benefit:handoff"}
    # The source-containing original objections remain unchanged and addressable.
    assert isinstance(panels[0].reviews[0].issues[0], SpecialistIssue)
    assert panels[0].reviews[0].issues[0].literature_refs[0].source_id == "source_fixture"
