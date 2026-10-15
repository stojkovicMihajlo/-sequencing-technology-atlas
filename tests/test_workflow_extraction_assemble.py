"""Mechanical formalization preserves explicitly declared interfaces and nothing more."""

import copy

import pytest

from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.models import WorkflowDraft

EVIDENCE = [{"source_id": "paper", "passage_id": "p1", "quote": "Reported transformation.",
             "support": "reported", "rationale": "Source description."}]


def draft():
    return {"method_id": "example", "title": "Protocol", "scope": "General procedure",
            "variant": "version1", "states": [
                {"id": s, "kind": k, "description": s, "evidence": EVIDENCE}
                for s, k in [("native", "material"), ("processed", "material"),
                             ("observations", "data")]],
            "operations": [
                {"id": i, "name": i, "action": "Reported transformation.", "inputs": inputs,
                 "outputs": outputs, "resources": [], "information_effect": "Preserve identity.",
                 "evidence": EVIDENCE}
                for i, inputs, outputs in [("prepare", ["native"], ["processed"]),
                                          ("measure", ["processed"], ["observations"])]],
            "modules": [{"id": "preparation", "name": "Preparation", "operation_ids": ["prepare"],
                         "boundary_rationale": "Material change."},
                        {"id": "readout", "name": "Readout", "operation_ids": ["measure"],
                         "boundary_rationale": "Material to data."}], "unresolved": []}


def test_edges_follow_explicit_state_references_even_when_operations_are_reversed():
    original = draft()
    shuffled = copy.deepcopy(original)
    shuffled["operations"].reverse()
    a = assemble_workflow(WorkflowDraft.model_validate(original))
    b = assemble_workflow(WorkflowDraft.model_validate(shuffled))
    assert {flow.model_dump_json() for flow in a.flows} == {
        flow.model_dump_json() for flow in b.flows}
    assert {(f.source, f.target, f.kind) for f in a.flows} == {
        ("native", "prepare", "material_input"), ("prepare", "processed", "material_output"),
        ("processed", "measure", "material_input"), ("measure", "observations", "data_output")}
    assert not any(f.kind == "precedence" for f in a.flows)


def test_unknown_consumed_state_and_implicit_self_reuse_are_rejected():
    data = draft()
    data["operations"][1]["inputs"] = ["invented_state"]
    with pytest.raises(ValueError, match="unknown state"):
        assemble_workflow(WorkflowDraft.model_validate(data))
    data = draft()
    data["operations"][0]["outputs"] = ["native"]
    with pytest.raises(ValueError, match="cycles"):
        assemble_workflow(WorkflowDraft.model_validate(data))


def test_reported_precedence_survives_without_inventing_additional_io():
    data = draft()
    data["ordering_flows"] = [{"id": "reported_before", "source": "prepare", "target": "measure",
                               "kind": "precedence", "basis": "Reported before readout.",
                               "evidence": EVIDENCE}]
    workflow = assemble_workflow(WorkflowDraft.model_validate(data))
    assert len(workflow.flows) == 5
    assert sum(f.kind == "precedence" for f in workflow.flows) == 1
