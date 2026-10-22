"""Bounded repairs must expose exact edits without mistaking array position for identity."""

from copy import deepcopy

from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.changes import diff_workflows, normalized_workflow
from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowDraft, digest
from test_workflow_extraction_assemble import EVIDENCE, draft


def workflow():
    return assemble_workflow(WorkflowDraft.model_validate(draft()))


def resolve(value, pointer):
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def test_record_array_reorder_has_no_content_or_boundary_edits_but_retains_distinct_pins():
    before = workflow()
    data = before.model_dump(mode="json")
    for key in ("states", "operations", "flows", "modules"):
        data[key].reverse()
    after = Workflow.model_validate(data)
    report = diff_workflows(before, after)
    assert report["before_workflow_sha256"] != report["after_workflow_sha256"]
    assert report["before_normalized_sha256"] == report["after_normalized_sha256"]
    assert not report["stable_record_content_changed"]
    assert all(not delta[kind] for delta in report["records"].values()
               for kind in ("added", "removed", "changed"))
    assert not report["boundary_inventory_delta"]["changed"]


def test_changed_action_and_boundary_description_report_only_changed_values():
    before = workflow()
    data = before.model_dump(mode="json")
    data["operations"][0]["action"] = "Qualified preparation action."
    data["states"][0]["description"] = "Native material with explicit applicability."
    after = Workflow.model_validate(data)
    report = diff_workflows(before, after)
    assert report["stable_record_content_changed"]
    assert [r["record_id"] for r in report["records"]["operations"]["changed"]] == ["prepare"]
    change = report["records"]["operations"]["changed"][0]["value_changes"]
    assert len(change) == 1 and change[0]["pointer"] == "/operations/prepare/action"
    assert change[0]["new_value"] == data["operations"][0]["action"]
    assert "Reported transformation." in change[0]["old_value"]
    assert "evidence" not in str(change)
    assert [r["record_id"] for r in report["boundary_inventory_delta"]["changed"]] == ["native"]
    for row in change:
        assert resolve(normalized_workflow(after), row["pointer"]) == row["new_value"]
        assert row["new_ref"]["value_sha256"] == digest(row["new_value"])


def test_added_and_removed_records_keep_compact_exact_references_and_do_not_guess_rename():
    before = workflow()
    data = before.model_dump(mode="json")
    data["states"].append({"id": "context", "kind": "data", "description": "Context only",
                           "evidence": EVIDENCE})
    data["modules"][0]["id"] = "renamed_module"
    after = Workflow.model_validate(data)
    report = diff_workflows(before, after)
    assert report["records"]["states"]["added"][0]["record_id"] == "context"
    added = report["records"]["states"]["added"][0]
    assert "evidence" not in str(added)
    assert digest(resolve(normalized_workflow(after), added["record_ref"]["pointer"])) == added[
        "record_ref"]["value_sha256"]
    assert report["records"]["modules"]["removed"][0]["record_id"] == "preparation"
    assert report["records"]["modules"]["added"][0]["record_id"] == "renamed_module"
    assert report["boundary_inventory_delta"]["after_counts"]["isolated"] == 1


def test_gap_and_resolution_keys_are_distinct_and_json_pointer_escaped():
    before_data = workflow().model_dump(mode="json")
    before_data["unresolved"] = [{"id": "gap/~1", "description": "Unresolved detail",
                                  "blocking": False, "next_evidence": "Inspect supplement."}]
    before_data["gap_resolutions"] = [{"reader_role": role, "gap_id": "G/1",
        "rationale": "Scoped resolution", "evidence": EVIDENCE}
        for role in ("chemistry", "information")]
    before = Workflow.model_validate(before_data)
    after_data = deepcopy(before_data)
    after_data["gap_resolutions"].reverse()
    after_data["gap_resolutions"][0]["rationale"] = "Qualified information resolution."
    after_data["unresolved"][0]["description"] = "Qualified unknown."
    after = Workflow.model_validate(after_data)
    report = diff_workflows(before, after)
    changed = report["records"]["gap_resolutions"]["changed"]
    assert len(changed) == 1 and changed[0]["record_id"] == "information:G/1"
    assert changed[0]["value_changes"][0]["pointer"] == "/gap_resolutions/information:G~11/rationale"
    gap = report["records"]["unresolved"]["changed"][0]["value_changes"][0]
    assert gap["pointer"] == "/unresolved/gap~1~01/description"
    assert resolve(normalized_workflow(after), gap["pointer"]) == "Qualified unknown."


def test_nested_evidence_append_is_an_exact_ref_without_unchanged_evidence_copy():
    before = workflow()
    data = before.model_dump(mode="json")
    added_evidence = {**EVIDENCE[0], "rationale": "An additional explicit qualification."}
    data["operations"][0]["evidence"].append(added_evidence)
    after = Workflow.model_validate(data)
    report = diff_workflows(before, after)
    changes = report["records"]["operations"]["changed"][0]["value_changes"]
    assert len(changes) == 1
    assert changes[0]["pointer"] == "/operations/prepare/evidence/1"
    assert changes[0]["change"] == "added" and "new_value" not in changes[0]
    assert changes[0]["new_ref"]["value_sha256"] == digest(added_evidence)
    assert before.model_dump(mode="json")["operations"][0]["evidence"] == EVIDENCE


def test_metadata_and_terminal_to_internal_boundary_change_are_visible():
    before = workflow()
    data = before.model_dump(mode="json")
    data["scope"] = "Qualified revised scope, not automatically accepted."
    data["states"].append({"id": "derived", "kind": "data", "description": "Derived data",
                           "evidence": EVIDENCE})
    data["operations"].append({"id": "analyze", "name": "Analyze", "action": "Analyze data.",
        "inputs": ["observations"], "outputs": ["derived"], "resources": [],
        "information_effect": "Derive values.", "evidence": EVIDENCE})
    data["modules"][-1]["operation_ids"].append("analyze")
    data["flows"].extend([
        {"id": "analyze_in", "source": "observations", "target": "analyze", "kind": "data_input",
         "basis": "Declared association", "evidence": EVIDENCE},
        {"id": "analyze_out", "source": "analyze", "target": "derived", "kind": "data_output",
         "basis": "Declared association", "evidence": EVIDENCE}])
    after = Workflow.model_validate(data)
    report = diff_workflows(before, after)
    assert report["metadata_changes"][0]["pointer"] == "/scope"
    delta = report["boundary_inventory_delta"]
    assert delta["removed"][0]["record_id"] == "observations"
    assert delta["added"][0]["record_id"] == "derived"
    assert delta["before_counts"] == delta["after_counts"]
