"""Graph-to-protocol inputs must retain routes without leaking source passages."""

import json

from test_workflow_extraction_runtime import source_case

from genomics_discovery.curation.protocol_benchmark.__main__ import main
from genomics_discovery.curation.workflow_extraction.inspection import navigation_view
from genomics_discovery.curation.workflow_extraction.models import Workflow


def test_graph_input_retains_science_without_evidence_text(source_case, tmp_path, capsys):
    workflow = Workflow.model_validate(source_case[2])
    source = tmp_path / "workflow.json"
    source.write_text(workflow.model_dump_json())
    original = source.read_bytes()
    target = tmp_path / "graph_input.json"
    assert main(["graph-input", str(source), "--output", str(target)]) == 0
    assert json.loads(capsys.readouterr().out)["prepared"] is True
    view = json.loads(target.read_text())
    assert view == navigation_view(workflow)
    assert source.read_bytes() == original
    assert view["record"]["flows"][6]["condition"] == "Aliquot A is available"
    assert view["record"]["operations"][1]["resources"][1]["description"] == "4 C"
    assert {flow["kind"] for flow in view["record"]["flows"]} >= {
        "material_input", "data_input", "control", "precedence"}
    assert "source_registry" not in view and "review" not in view
    assert "quote" not in view["record"]["operations"][0]["evidence"]
    assert "rationale" not in view["record"]["operations"][0]["evidence"]
    assert view["record"]["operations"][0]["evidence"]["support_types"] == ["reported"]


def test_graph_input_refuses_existing_output(source_case, tmp_path, capsys):
    source = tmp_path / "workflow.json"
    source.write_text(json.dumps(source_case[2]))
    target = tmp_path / "graph_input.json"
    target.write_text("preserved")
    assert main(["graph-input", str(source), "--output", str(target)]) == 1
    assert json.loads(capsys.readouterr().out)["prepared"] is False
    assert target.read_text() == "preserved"


def test_graph_input_rejects_full_source_bearing_export(source_case, tmp_path, capsys):
    source = tmp_path / "graph_candidate.json"
    source.write_text(json.dumps({"workflow": source_case[2],
                                 "source_registry": source_case[0]["sources"],
                                 "review": {"reference_answers": "not a graph input"}}))
    target = tmp_path / "graph_input.json"
    assert main(["graph-input", str(source), "--output", str(target)]) == 1
    assert json.loads(capsys.readouterr().out)["prepared"] is False
    assert not target.exists()


def test_graph_input_rejects_dangling_route(source_case, tmp_path, capsys):
    data = source_case[2]
    data["flows"][0]["target"] = "missing_operation"
    source = tmp_path / "workflow.json"
    source.write_text(json.dumps(data))
    target = tmp_path / "graph_input.json"
    assert main(["graph-input", str(source), "--output", str(target)]) == 1
    assert json.loads(capsys.readouterr().out)["prepared"] is False
    assert not target.exists()
