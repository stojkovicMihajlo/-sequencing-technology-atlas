"""Check that exploratory unit inspection cannot invent protocol dependencies."""

import json

import pytest

from playground.compare_workflow_units import compare, digest, unit_boundary


def profile(connections):
    return {"profile_id": "example", "modules": [
        {"module_id": mid, "input_state": {"description": "recorded input"},
         "output_state": {"description": "recorded output"},
         "unknowns": [{"detail": "original execution remains unestablished"}]}
        for mid in ["a", "b", "c", "d"]],
        "topology": {"default_order": ["a", "b", "c", "d"],
                     "connections": connections}}


def test_display_neighbours_do_not_form_a_unit_or_establish_independence():
    result = unit_boundary(profile([]), ["a", "b"])
    assert result["recorded_weakly_connected"] is False
    assert result["internal_connections"] == []
    assert result["connection_coverage"]["dependency_completeness"] == "unknown"
    assert result["states"][0]["unknowns"]


def test_cut_preserves_external_control_parallel_flows_and_conditions():
    data = profile([
        {"source": "c", "target": "a", "relation": "control", "condition": "if QC supports use"},
        {"source": "a", "target": "b", "relation": "material", "condition": "selected branch"},
        {"source": "a", "target": "b", "relation": "information"},
        {"source": "b", "target": "d", "relation": "material"},
        {"source": "a", "target": "d", "relation": None},
    ])
    data["modules"][0]["input_requirements"] = [
        {"property": "end_state", "required_value": "recorded"}
    ]
    result = unit_boundary(data, ["a", "b"])
    assert result["recorded_weakly_connected"] is True
    assert len(result["internal_connections"]) == 2
    assert result["incoming_connections"][0]["record"]["condition"] == "if QC supports use"
    assert result["outgoing_connections"][0]["target_module_id"] == "d"
    assert result["unclassified_or_nonflow_connections"][0]["relation"] is None
    assert result["material_entry_modules"] == ["a"]
    assert result["material_exit_modules"] == ["b"]
    assert result["states"][0]["input_requirements"] == data["modules"][0]["input_requirements"]
    assert result["states"][0]["record_ref"]["json_pointer"] == "/modules/0"


@pytest.mark.parametrize("selection", [[], ["a", "a"], ["missing"]])
def test_bad_selection_is_rejected(selection):
    with pytest.raises(ValueError, match="selection"):
        unit_boundary(profile([]), selection)


def test_changed_source_blocks_replay_before_any_comparison(tmp_path):
    source = tmp_path / "primary.txt"
    source.write_text("original primary passage")
    document = tmp_path / "extraction.json"
    document.write_text(json.dumps({"profiles": [profile([])]}))
    manifest = {"scope": "test", "protocols": [{
        "method_id": "method", "profile_id": "example", "extraction_path": str(document),
        "extraction_sha256": digest(document), "review_at_selection": {"status": "unreviewed"},
        "primary_source": {"source_metadata": {"sha256": {str(source): digest(source)}}},
    }]}
    source.write_text("changed passage")
    with pytest.raises(ValueError, match="Changed primary source"):
        compare(manifest, {"units": [], "comparisons": []})
