import copy

import pytest

from experiments.transcriptomics_revision_20261104.planning import (
    PlanReview, WorkPlan, admit, digest, subset, validate_plan,
)


def fixture():
    rows = [{"record_id": x, "registered_sources": {"card:" + x: {}},
             "source_registry_path": "/scratch/" + x + "/source_registry.json"} for x in ("web", "poster")]
    census = {"rows": rows, "candidate_identity_pairs": [{"left": "web", "right": "poster"}]}
    def evidence(x): return {"record_id": x, "source_id": "card:" + x,
                            "locator": "diagram", "basis": "image", "support": "depicted",
                            "finding": "Synthetic test source decision"}
    plan = {"census_sha256": digest(census),
            "dispositions": [{"record_id": x, "decision": "extract", "reason": "In scope",
                              "evidence": [evidence(x)]} for x in ("web", "poster")],
            "identity_decisions": [{"left": "web", "right": "poster", "decision": "same_scoped_method",
                                    "reason": "Synthetic same implementation", "evidence": [evidence("web"), evidence("poster")]}],
            "work_units": [{"unit_id": "one_method", "method_name": "Example", "implementation_scope": "Original implementation",
                            "distinction_from_other_units": "Only unit",
                            "bindings": [{"record_id": x, "role": role, "selected_scope": "Original implementation",
                                          "evidence": [evidence(x)]} for x, role in (("web", "primary_depiction"), ("poster", "additional_depiction"))]}]}
    return census, plan, evidence


def test_duplicate_depictions_produce_one_unit_and_require_complete_fresh_review():
    census, raw, ev = fixture(); plan = WorkPlan.model_validate(raw)
    targets = validate_plan(census, plan)
    review = PlanReview.model_validate({"plan_sha256": digest(plan.model_dump(mode="json")),
        "assessments": [{"target": t, "decision": "accept", "reason": "Checked synthetic fixture", "evidence": [ev("web"), ev("poster")]} for t in targets]})
    assert len(admit(census, plan, review)["units"]) == 1
    bad = review.model_dump(mode="json"); bad["assessments"].pop()
    with pytest.raises(ValueError, match="every source"):
        admit(census, plan, PlanReview.model_validate(bad))
    bad = review.model_dump(mode="json"); bad["plan_sha256"] = "changed"
    with pytest.raises(ValueError, match="exact proposed"):
        admit(census, plan, PlanReview.model_validate(bad))
    bad = review.model_dump(mode="json")
    for a in bad["assessments"]:
        if a["target"] == "unit:one_method": a["evidence"] = [ev("web")]
    with pytest.raises(ValueError, match="affected source occurrence"):
        admit(census, plan, PlanReview.model_validate(bad))


def test_same_method_cannot_schedule_two_source_jobs():
    census, raw, _ = fixture()
    second = copy.deepcopy(raw["work_units"][0]); second["unit_id"] = "second"; second["implementation_scope"] = "Reworded duplicate"
    second["bindings"] = [second["bindings"][1]]; second["bindings"][0]["role"] = "primary_depiction"
    raw["work_units"][0]["bindings"] = raw["work_units"][0]["bindings"][:1]
    raw["work_units"].append(second)
    with pytest.raises(ValueError, match="share one work unit"):
        validate_plan(census, WorkPlan.model_validate(raw))


@pytest.mark.parametrize("mutation,match", [
    (lambda p: p["dispositions"].pop(), "every source occurrence"),
    (lambda p: p["identity_decisions"].clear(), "Every nominated"),
    (lambda p: p["identity_decisions"][0].update(decision="unresolved"), "Unresolved potential duplicates"),
    (lambda p: p["identity_decisions"][0].update(decision="distinct_methods"), "Distinct methods"),
    (lambda p: p["identity_decisions"][0]["evidence"].pop(), "both source occurrences"),
    (lambda p: p["dispositions"][0]["evidence"][0].update(source_id="invented"), "unregistered source"),
])
def test_negative_planning_controls(mutation, match):
    census, raw, _ = fixture(); mutation(raw)
    with pytest.raises(ValueError, match=match): validate_plan(census, WorkPlan.model_validate(raw))


def test_bounded_pilot_includes_duplicate_candidates():
    census, _, _ = fixture()
    bounded = subset(census, ["web"])
    assert {r["record_id"] for r in bounded["rows"]} == {"web", "poster"}
    assert bounded["parent_census_sha256"] == digest(census)
