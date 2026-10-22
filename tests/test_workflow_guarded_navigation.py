"""Shared operations must not silently make mutually exclusive routes interchangeable."""

import pytest

from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.models import WorkflowDraft
from genomics_discovery.curation.workflow_extraction.navigation import query_path
from test_workflow_extraction_assemble import EVIDENCE, draft


def test_unconditional_path_is_structural_and_has_no_conditional_edges():
    workflow = assemble_workflow(WorkflowDraft.model_validate(draft()))
    result = query_path(workflow, "native", "observations")
    assert result["status"] == "unconditional_structural_path"
    assert len(result["path_flow_ids"]) == 4
    assert result["conditional_candidate_path_flow_ids"] is None
    assert result["blocked_conditional_edges"] == []
    assert "complete set of required inputs" in result["boundary"]


def test_shared_operation_crossed_alternatives_require_route_selection_without_removal():
    data = draft()
    data["states"].extend([
        {"id": sid, "kind": kind, "description": sid, "evidence": EVIDENCE}
        for sid, kind in (("other_library", "material"), ("other_reads", "data"))])
    operation = data["operations"][1]
    operation["inputs"].append("other_library")
    operation["outputs"].append("other_reads")
    for direction, group in (("input", "library_origin"), ("output", "read_origin")):
        operation[direction + "_bindings"] = [
            {"state_id": sid, "basis": "Reported alternative", "evidence": EVIDENCE,
             "selection": "alternative", "alternative_group": group,
             "condition": f"The {'first' if index == 0 else 'other'} route was selected."}
            for index, sid in enumerate(operation[direction + "s"])]
    workflow = assemble_workflow(WorkflowDraft.model_validate(data))
    before = workflow.model_dump(mode="json")
    result = query_path(workflow, "processed", "other_reads")
    assert result["status"] == "needs_route_selection"
    assert result["path_flow_ids"] is None
    assert len(result["conditional_candidate_path_flow_ids"]) == 2
    assert {edge["flow_id"] for edge in result["blocked_conditional_edges"]} == {
        "io:input:measure:processed", "io:output:measure:other_reads"}
    assert "may mix incompatible routes" in result["boundary"]
    assert workflow.model_dump(mode="json") == before


@pytest.mark.parametrize("selection", ["required", "optional"])
def test_prose_condition_blocks_even_a_required_binding(selection):
    data = draft()
    data["operations"][0]["input_bindings"] = [
        {"state_id": "native", "basis": "Conditional association", "evidence": EVIDENCE,
         "selection": selection, "condition": "Selected assay context applies."}]
    workflow = assemble_workflow(WorkflowDraft.model_validate(data))
    result = query_path(workflow, "native", "observations")
    assert result["status"] == "needs_route_selection"
    assert result["blocked_conditional_edges"][0]["condition"] == "Selected assay context applies."


def test_reverse_direction_has_no_path_and_unknown_id_is_not_an_empty_result():
    workflow = assemble_workflow(WorkflowDraft.model_validate(draft()))
    result = query_path(workflow, "observations", "native")
    assert result["status"] == "no_declared_path"
    assert result["path_flow_ids"] is None
    assert result["conditional_candidate_path_flow_ids"] is None
    assert result["blocked_conditional_edges"] == []
    with pytest.raises(KeyError, match="Unknown Workflow"):
        query_path(workflow, "invented", "native")
