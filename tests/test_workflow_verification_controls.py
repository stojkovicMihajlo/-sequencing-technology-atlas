"""Offline control construction and scoring; no model or native execution."""

from copy import deepcopy
import json

import pytest

from test_workflow_extraction_runtime import source_case
from genomics_discovery.curation.protocol_benchmark.grounding_diagnostic import (
    Result, public_input, score_result,
)
from genomics_discovery.curation.workflow_extraction.models import Workflow, digest
from genomics_discovery.curation.workflow_extraction.verification_controls import build_controls, control_gate


def definitions(workflow, packet):
    ref = {"source_id": packet["sources"][0]["source_id"],
           "passage_id": packet["sources"][0]["passages"][0]["passage_id"]}
    def label(outcome):
        return {"outcome": outcome, "rationale": "Fixture label, not a scientific judgment.",
                "decisive_passages": [ref]}
    return [{"id": "field", "kind": "field_support", "field_pointer": "/operations/1/action",
             "original_value_sha256": digest(workflow.operations[1].action),
             "changed_value": workflow.operations[1].action + " Discard all products.",
             "passage_refs": [ref], "original_label": label("supported"),
             "changed_label": label("not_established"), "phenomenon": "added_fate"},
            {"id": "coverage", "kind": "local_coverage", "operation_ids": ["wash", "seq_a"],
             "requirement": "Wash nuclei then sequence aliquot A.", "omit_operation_id": "wash",
             "passage_refs": [ref], "original_label": label("supported"),
             "changed_label": label("not_established"), "phenomenon": "local_omission"}]


def build(source_case, specs=None):
    packet, path, raw = source_case
    workflow = Workflow.model_validate(raw)
    return build_controls(workflow, packet, path, specs or definitions(workflow, packet),
                          family="HOST_ONLY_FAMILY", split="diagnostic")


def test_paired_controls_preserve_inputs_and_hide_origin_labels(source_case):
    before = deepcopy(source_case)
    dataset, labels, selection = build(source_case)
    assert len(dataset.cases) == len(labels.cases) == 4
    assert len(dataset.contexts) == 2
    assert source_case == before
    visible = json.dumps(public_input(dataset))
    for hidden in ("original_label", "changed_label", "HOST_ONLY_FAMILY", "decisive_passages",
                   str(source_case[1]), '"origin":', '"definition_id":'):
        assert hidden not in visible
    assert all(row["value_sha256"] == digest(next(c.value for c in dataset.cases
               if c.id == row["case_id"])) for row in selection["selection"])
    assert build(source_case)[0] == dataset


def test_omission_removes_operation_and_incident_flows_without_inventing_bypass(source_case):
    dataset, _, selection = build(source_case)
    rows = {row["origin"]: next(c.value for c in dataset.cases if c.id == row["case_id"])
            for row in selection["selection"] if row["kind"] == "local_coverage"}
    original, changed = (rows[key]["candidate_graph"] for key in ("original", "changed"))
    assert len(original["operations"]) == 2 and len(changed["operations"]) == 1
    assert changed["states"] == original["states"]
    assert changed["flows"] == [flow for flow in original["flows"]
                                if "wash" not in (flow["source"], flow["target"])]
    assert all("evidence" not in record for record in original["operations"])


@pytest.mark.parametrize("fault", ["stale_field", "unchanged", "missing_passage", "missing_operation",
                                  "duplicate_control", "scope", "packet_bytes"])
def test_changed_or_malformed_control_inputs_fail_closed(source_case, fault):
    packet, path, raw = source_case
    workflow = Workflow.model_validate(raw)
    specs = definitions(workflow, packet)
    if fault == "stale_field": specs[0]["original_value_sha256"] = "0" * 64
    elif fault == "unchanged": specs[0]["changed_value"] = workflow.operations[1].action
    elif fault == "missing_passage": specs[0]["passage_refs"][0]["passage_id"] = "absent"
    elif fault == "missing_operation": specs[1]["omit_operation_id"] = "absent"
    elif fault == "duplicate_control": specs.append(deepcopy(specs[0]))
    elif fault == "scope": packet["scope"] = "Different protocol scope"
    elif fault == "packet_bytes": path.write_text("{}")
    with pytest.raises(ValueError):
        build_controls(workflow, packet, path, specs, family="fixture", split="fixture")


def test_all_supported_judge_fails_both_addition_and_omission_controls(source_case):
    dataset, labels, _ = build(source_case)
    refs = [{"source_id": dataset.contexts[0].passages[0].source_id,
             "passage_id": dataset.contexts[0].passages[0].passage_id}]
    result = Result.model_validate({"format_version": "protocol_grounding_result/1.0",
        "results": [{"case_id": case.id, "value_sha256": digest(case.value),
                     "outcome": "supported", "rationale": "Synthetic unconditional approval.",
                     "inspected_passages": refs} for case in dataset.cases]})
    score = score_result(dataset, labels, result)
    assert score["overall"]["unsupported_acceptance"] == {"numerator": 2, "denominator": 2, "rate": 1.0}
    assert score["by_phenomenon"]["local_omission"]["unsupported_acceptance"]["numerator"] == 1
    assert score["overall"]["supported_retention"]["rate"] == 1.0
    assert control_gate(dataset, labels, result, native_complete=True)["passed"] is False


@pytest.mark.parametrize("outcome,native,passed", [(None, True, True),
    ("unassessed", True, False), ("not_established", True, False), (None, False, False)])
def test_sentinel_rejects_abstain_all_reject_all_and_unvalidated_transport(source_case, outcome, native, passed):
    dataset, labels, _ = build(source_case)
    expected = {row.case_id: row.outcome for row in labels.cases}
    contexts = {context.id: context for context in dataset.contexts}
    result = Result.model_validate({"format_version": "protocol_grounding_result/1.0",
        "results": [{"case_id": case.id, "value_sha256": digest(case.value),
                     "outcome": outcome or expected[case.id], "rationale": "Fixture judgment.",
                     "inspected_passages": [{"source_id": passage.source_id,
                                             "passage_id": passage.passage_id}
                                            for passage in contexts[case.context_id].passages]}
                    for case in dataset.cases]})
    assert control_gate(dataset, labels, result, native_complete=native)["passed"] is passed
