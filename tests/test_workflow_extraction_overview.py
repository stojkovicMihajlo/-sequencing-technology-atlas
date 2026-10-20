"""Overview preserves routes and qualifications without repeating full scientific records."""

import json
from copy import deepcopy

import pytest

from test_workflow_extraction_bindings import case as conditional_case
from test_workflow_extraction_contract import candidate as candidate, review_for
from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowDraft, WorkflowReview, digest
from genomics_discovery.curation.workflow_extraction.overview import overview_text, workflow_overview


@pytest.fixture
def feedback(candidate):
    data = deepcopy(candidate)
    evidence = data["states"][0]["evidence"]
    data["states"] += [{"id": sid, "kind": "material", "description": description, "evidence": evidence}
                       for sid, description in (("retained", "RETAINED MAIN MATERIAL"),
                                                ("aliquot", "SEPARATE QC ALIQUOT"),
                                                ("disconnected", "DECLARED DISCONNECTED MATERIAL"))]
    original = data["operations"][1]
    split = {**deepcopy(original), "id": "split", "name": "Split the retained material and aliquot",
             "inputs": ["library"], "outputs": ["retained", "aliquot"]}
    original["inputs"] = ["aliquot"]
    data["operations"][2]["inputs"] = ["retained", "quantity"]
    data["operations"].insert(1, split)
    data["modules"][0]["operation_ids"].insert(1, "split")
    data["flows"] = [f for f in data["flows"] if f["id"] not in {"f2", "f4", "f6"}]
    for index, (source, target, kind) in enumerate((
            ("library", "split", "material_input"), ("split", "retained", "material_output"),
            ("split", "aliquot", "material_output"), ("aliquot", "quantify", "material_input"),
            ("retained", "sequence", "material_input"), ("quantity", "sequence", "data_input"))):
        data["flows"].append({"id": f"new{index}", "source": source, "target": target, "kind": kind,
                              "basis": "Exact declared relationship.", "evidence": evidence})
    data["flows"].append({"id": "adaptive", "source": "quantify", "target": "sequence", "kind": "control",
        "basis": "The reported monitoring signal governs the retained main route.",
        "condition": "Use the measured signal for the documented adjustment.", "evidence": evidence})
    data["unresolved"] = [{"id": "threshold", "description": "Exact numeric threshold is unresolved.",
        "blocking": False, "next_evidence": "Original decision-rule specification."},
        {"id": "unknown_route", "description": "A claimed route requires independent evidence.",
         "blocking": True, "next_evidence": "Source for the claimed association."}]
    return Workflow.model_validate(data)


def test_overview_preserves_aliquot_main_path_and_control(feedback):
    view = workflow_overview(feedback)
    by_id = {operation["id"]: operation for operation in view["operations"]}
    assert by_id["split"]["outputs"] == ["retained", "aliquot"]
    assert by_id["quantify"]["inputs"] == ["aliquot"]
    assert by_id["sequence"]["inputs"] == ["retained", "quantity"]
    assert "aliquot" not in by_id["sequence"]["inputs"]
    signal = next(f for f in view["flows"] if f["source"] == "quantity")
    assert signal["kind"] == "data_input" and signal["target"] == "sequence"
    control = next(f for f in view["flows"] if f["id"] == "adaptive")
    assert control["kind"] == "control" and control["condition"] == feedback.flows[-1].condition
    assert len(view["flows"]) == len(feedback.flows)
    assert set(view["states"]) == {state.id for state in feedback.states}
    assert {"sample", "reads", "disconnected"} <= view["states"].keys()


def test_alternative_and_optional_inputs_are_not_made_cumulative(conditional_case):
    raw, _, _ = conditional_case
    workflow = assemble_workflow(WorkflowDraft.model_validate(raw), require_bindings=True)
    view = workflow_overview(workflow)
    inputs = {f["source"]: f for f in view["flows"] if f["target"] == "measure"}
    for sid in ("standard", "low"):
        assert inputs[sid]["selection"] == "alternative"
        assert inputs[sid]["alternative_group"] == "library_route"
        assert inputs[sid]["condition"]
    assert inputs["control"]["selection"] == "optional"
    assert inputs["control"]["condition"] == "Control is available."
    assert all(f["association"] == "source_qualified" for f in inputs.values())
    text = overview_text(workflow)
    assert "selection=alternative; alternative_group=library_route" in text
    assert "selection=optional; condition=Control is available." in text


def test_descriptions_once_and_recipe_evidence_are_details(feedback):
    view, text = workflow_overview(feedback), overview_text(feedback)
    for state in feedback.states:
        assert text.count(state.description) == 1
    for operation in view["operations"]:
        assert not {"action", "resources", "information_effect", "evidence"} & operation.keys()
    assert '"evidence":' not in json.dumps(view)
    assert "atlas-extract query WORKFLOW OPERATION_ID" in text
    assert "execution schedule" in text
    assert "renewed review, acceptance" in text
    assert "selection=unspecified" in text
    assert all(f["selection"] is None for f in view["flows"])


def test_groups_gaps_and_pointers_are_exact_without_promoting(feedback):
    view = workflow_overview(feedback)
    assert view["workflow_sha256"] == digest(feedback)
    assert view["unresolved"] == [gap.model_dump(mode="json") for gap in feedback.unresolved]
    for index, group in enumerate(view["modules"]):
        assert {k: v for k, v in group.items() if k != "detail_pointer"} == feedback.modules[index].model_dump(mode="json")
        assert group["detail_pointer"] == f"/modules/{index}"
    for index, operation in enumerate(view["operations"]):
        assert operation["detail_pointer"] == f"/operations/{index}"
        assert operation["module_ids"] == [feedback.modules[0].id]
    assert list(view["states"]) == [state.id for state in feedback.states]
    assert [o["id"] for o in view["operations"]] == [o.id for o in feedback.operations]
    assert [f["id"] for f in view["flows"]] == [f.id for f in feedback.flows]
    text = overview_text(feedback)
    assert all(gap.description in text and gap.next_evidence in text for gap in feedback.unresolved)
    assert "blocking=False" in text and "blocking=True" in text
    assert feedback.modules[0].boundary_rationale not in text
    assert "accepted" not in view and "review_outcome" not in view


def test_projection_and_result_mutation_cannot_change_workflow(feedback):
    before = feedback.model_dump(mode="json")
    view = workflow_overview(feedback)
    overview_text(feedback)
    view["operations"][0]["inputs"].append("invented")
    view["states"]["library"]["description"] = "Changed output view"
    view["modules"][0]["operation_ids"].clear()
    view["unresolved"][0]["description"] = "Changed view gap"
    assert feedback.model_dump(mode="json") == before
    assert digest(feedback) == workflow_overview(feedback)["workflow_sha256"]


def test_gap_resolution_keeps_scope_rationale_with_exact_detail_pointer(feedback):
    data = feedback.model_dump(mode="json")
    data["gap_resolutions"] = [{"reader_role": "chemistry", "gap_id": "reader-boundary",
        "rationale": "This source describes only a named conversion boundary.",
        "evidence": data["states"][0]["evidence"]}]
    workflow = Workflow.model_validate(data)
    view = workflow_overview(workflow)
    assert view["gap_resolutions"] == [{"reader_role": "chemistry", "gap_id": "reader-boundary",
        "rationale": data["gap_resolutions"][0]["rationale"], "detail_pointer": "/gap_resolutions/0"}]
    assert data["gap_resolutions"][0]["rationale"] in overview_text(workflow)
    assert view["workflow_sha256"] != workflow_overview(feedback)["workflow_sha256"]


@pytest.mark.parametrize("projection", [workflow_overview, overview_text])
def test_review_for_another_workflow_is_rejected(feedback, projection):
    raw = review_for(feedback, {"sources": []})
    raw["workflow_sha256"] = "0" * 64
    review = WorkflowReview.model_validate(raw)
    with pytest.raises(ValueError, match="exact Workflow"):
        projection(feedback, review=review)


def test_review_omission_remains_visible_when_workflow_has_no_gaps(feedback):
    raw = feedback.model_dump(mode="json")
    raw["unresolved"] = []
    workflow = Workflow.model_validate(raw)
    review_data = review_for(workflow, {"sources": []})
    review_data["omissions"] = [{"description": "An essential library handoff requires evidence.",
        "action": "retrieve_evidence", "blocking": True, "evidence": []}]
    review = WorkflowReview.model_validate(review_data)
    view = workflow_overview(workflow, review=review)
    context = view["review_context"]
    assert not view["unresolved"]
    assert context["status"] == "archived_model_review"
    assert context["outcome"] == "research_needed"
    assert context["workflow_sha256"] == digest(workflow)
    assert context["review_sha256"] == digest(review)
    assert context["source_packet_sha256"] == review.source_packet_sha256
    assert context["omissions"] == review_data["omissions"]
    text = overview_text(workflow, review=review)
    assert "outcome: research_needed" in text
    assert review.omissions[0].description in text
    assert "action=retrieve_evidence; blocking=True" in text
    assert text.index("Review omission:") < text.index("Operations (State IDs")


def test_exact_adverse_checks_all_omissions_notes_are_retained(feedback):
    raw = review_for(feedback, {"sources": []})
    raw["checks"][0]["verdict"] = "unsupported"
    raw["checks"][1]["verdict"] = "unresolved"
    raw["omissions"] = [{"description": "Correct an unsupported asserted identity.",
        "action": "revise_record", "blocking": False, "evidence": raw["checks"][0]["evidence"]},
        {"description": "Optional execution qualification.", "action": "advisory", "blocking": False, "evidence": []}]
    raw["notes"] = ["Keep the review's stated scope qualification."]
    review = WorkflowReview.model_validate(raw)
    before = review.model_dump(mode="json")
    context = workflow_overview(feedback, review=review)["review_context"]
    assert context["outcome"] == "needs_revision"
    assert context["nonpositive_checks"] == raw["checks"][:2]
    assert context["omissions"] == raw["omissions"]
    assert context["notes"] == raw["notes"]
    text = overview_text(feedback, review=review)
    assert all(check["rationale"] in text and check["target_id"] in text for check in raw["checks"][:2])
    assert all(item["description"] in text for item in raw["omissions"])
    assert raw["notes"][0] in text
    assert "unique exact target coverage only" in text
    assert "does not revalidate source-packet integrity, evidence or source verdicts" in text
    context["omissions"][0]["evidence"].clear()
    context["notes"].clear()
    assert review.model_dump(mode="json") == before


def test_missing_review_is_explicitly_unreviewed(feedback):
    context = workflow_overview(feedback)["review_context"]
    assert context["status"] == "unreviewed_projection" and context["outcome"] == "not_assessed"
    assert context["review_sha256"] is None and context["source_packet_sha256"] is None
    assert "unreviewed projection" in overview_text(feedback)


@pytest.mark.parametrize("fault", ["empty", "missing", "duplicate", "foreign"])
def test_review_rule_cannot_turn_invalid_target_accounting_into_supported_context(feedback, fault):
    raw = feedback.model_dump(mode="json")
    raw["unresolved"] = []
    workflow = Workflow.model_validate(raw)
    review_data = review_for(workflow, {"sources": []})
    if fault == "empty":
        review_data["checks"] = []
    elif fault == "missing":
        review_data["checks"].pop()
    elif fault == "duplicate":
        review_data["checks"].append(deepcopy(review_data["checks"][0]))
    else:
        review_data["checks"][0]["target_id"] = "operation:foreign-workflow-operation"
    review = WorkflowReview.model_validate(review_data)
    for projection in (workflow_overview, overview_text):
        with pytest.raises(ValueError, match="unique exact target coverage"):
            projection(workflow, review=review)
