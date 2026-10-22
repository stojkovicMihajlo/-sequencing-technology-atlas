"""Deterministic counterfactual authoring tests, not live reviewer performance."""

from copy import deepcopy
import json

import pytest

from test_workflow_extraction_runtime import source_case
from test_workflow_verification_controls import definitions
from genomics_discovery.curation.workflow_extraction.models import Workflow, digest, validate_evidence
from genomics_discovery.curation.workflow_extraction.workflow_mutation_controls import seed_workflow_controls
from genomics_discovery.curation.run_state import sha
from scripts.benchmark.prepare_full_workflow_control import main


def test_full_candidate_is_structurally_valid_with_a_false_claim_and_missing_operation(source_case):
    packet, _, raw = source_case
    workflow = Workflow.model_validate(raw)
    specs = definitions(workflow, packet)
    # Use an operation other than the one modified by the first definition.
    specs[1].update(operation_ids=["seq_b", "merge"], omit_operation_id="seq_b")
    before = deepcopy(packet), workflow.model_dump(mode="json"), deepcopy(specs)
    candidate, receipt = seed_workflow_controls(workflow, packet, specs, ["field", "coverage"])
    assert validate_evidence(candidate, packet) == []
    assert "seq_b" not in {item.id for item in candidate.operations}
    assert all("seq_b" not in (flow.source, flow.target) for flow in candidate.flows)
    assert candidate.states == workflow.states
    assert any("Discard all products." in op.action for op in candidate.operations)
    assert before == (packet, workflow.model_dump(mode="json"), specs)
    assert receipt["new_edges"] == receipt["material_states_deleted"] == []
    assert receipt["operation_deletions"][0]["omitted_operation_id"] == "seq_b"
    assert receipt["field_mutations"][0]["target_id"] == "operation:wash"
    # seq_b precedes wash in original array; the receipt follows stable record ID.
    assert receipt["field_mutations"][0]["original_field_pointer"] == "/operations/1/action"
    assert receipt["field_mutations"][0]["candidate_field_pointer"] == "/operations/0/action"
    assert receipt["candidate_workflow_sha256"] == digest(candidate)


def test_empty_module_removed_without_changing_surviving_memberships(source_case):
    packet, _, raw = source_case
    workflow = Workflow.model_validate(raw)
    specs = definitions(workflow, packet)
    candidate, receipt = seed_workflow_controls(workflow, packet, specs, ["coverage"])
    assert "preparation" not in {module.id for module in candidate.modules}
    assert candidate.modules == tuple(m for m in workflow.modules if m.id != "preparation")
    assert receipt["operation_deletions"][0]["module_membership_changes"] == [{
        "module_id": "preparation", "original_members": ["wash"], "candidate_members": []}]


@pytest.mark.parametrize("fault", ["stale", "duplicate_selection", "unknown_selection", "overlap",
                                  "mutate_deleted", "duplicate_definitions", "scope", "bad_quote"])
def test_invalid_mutation_plan_rejected(source_case, fault):
    packet, _, raw = source_case
    workflow = Workflow.model_validate(raw)
    specs = definitions(workflow, packet)
    chosen = ["field"]
    if fault == "stale": specs[0]["original_value_sha256"] = "0" * 64
    elif fault == "duplicate_selection": chosen = ["field", "field"]
    elif fault == "unknown_selection": chosen = ["unknown"]
    elif fault == "overlap":
        another = deepcopy(specs[0]); another["id"] = "other"; specs.append(another)
        chosen = ["field", "other"]
    elif fault == "mutate_deleted": chosen = ["field", "coverage"]
    elif fault == "duplicate_definitions": specs.append(deepcopy(specs[0]))
    elif fault == "scope": packet["scope"] = "Wrong scope"
    elif fault == "bad_quote": packet["sources"][0]["passages"][0]["text"] = "Unrelated text."
    with pytest.raises(ValueError):
        seed_workflow_controls(workflow, packet, specs, chosen)


@pytest.mark.parametrize("bad_pin", [False, True])
def test_cli_pins_inputs_and_keeps_expected_errors_out_of_candidate(source_case, tmp_path, bad_pin):
    packet, packet_path, raw = source_case
    workflow = Workflow.model_validate(raw)
    specs = definitions(workflow, packet)
    specs[1].update(operation_ids=["seq_b", "merge"], omit_operation_id="seq_b")
    workflow_path, definitions_path = tmp_path / "workflow.json", tmp_path / "definitions.json"
    workflow_path.write_text(json.dumps(raw))
    definitions_path.write_text(json.dumps({"controls": specs}))
    output = tmp_path / "prepared"
    argv = ["--workflow", str(workflow_path), "--packet", str(packet_path),
            "--definitions", str(definitions_path), "--output", str(output),
            "--expected-workflow-sha256", "0" * 64 if bad_pin else sha(workflow_path),
            "--expected-packet-sha256", sha(packet_path),
            "--expected-definitions-sha256", sha(definitions_path),
            "--control", "field", "--control", "coverage"]
    if bad_pin:
        with pytest.raises(ValueError, match="Frozen workflow bytes differ"):
            main(argv)
        assert not output.exists()
        return
    main(argv)
    text = (output / "candidate/workflow.json").read_text()
    Workflow.model_validate_json(text)
    for secret in ("expected_source_judgment", "selected_control_ids", "field_mutations"):
        assert secret not in text
    receipt = json.loads((output / "host/expected_controls.json").read_text())
    assert receipt["candidate_raw_sha256"] == sha(output / "candidate/workflow.json")
    with pytest.raises(FileExistsError):
        main(argv)
