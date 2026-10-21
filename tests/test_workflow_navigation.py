"""Navigation must preserve scientific records while fetching evidence on demand."""

from copy import deepcopy
import json
import sys

import pytest
from test_workflow_extraction_bindings import case as conditional_case

from genomics_discovery.curation.evidence_views import inspect_pointer
from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.inspection import (
    expand_inspection, navigation_view, workflow_pointer,
)
from genomics_discovery.curation.workflow_extraction.models import WorkflowDraft, digest


@pytest.fixture
def workflow(conditional_case):
    raw = deepcopy(conditional_case[0])
    evidence = raw["operations"][0]["evidence"][0]
    inferred = {**evidence, "support": "inferred", "rationale": "A distinct contextual inference."}
    raw["operations"][0]["resources"] = [{
        "kind": "condition", "description": "Source-scoped reaction conditions remain readable.",
        "evidence": [evidence, inferred],
    }]
    raw["unresolved"] = [{"id": "recipe", "description": "A source-scoped recipe detail is unknown.",
                          "blocking": False, "next_evidence": "Inspect the original supplement."}]
    return assemble_workflow(WorkflowDraft.model_validate(raw), require_bindings=True)


def restore_evidence(value, workflow, found):
    if isinstance(value, dict):
        if set(value) == {"pointer", "count", "support_types", "passages"}:
            result = workflow_pointer(workflow, value["pointer"])
            assert result["workflow_sha256"] == digest(workflow)
            evidence = result["value"]
            assert len(evidence) == value["count"]
            assert sorted({item["support"] for item in evidence}) == value["support_types"]
            assert {(item["source_id"], item["passage_id"]) for item in evidence} == {
                (item["source_id"], item["passage_id"]) for item in value["passages"]}
            found.append(value["pointer"])
            return evidence
        return {key: restore_evidence(child, workflow, found) for key, child in value.items()}
    if isinstance(value, list):
        return [restore_evidence(child, workflow, found) for child in value]
    return value


def test_navigation_restores_exact_nested_workflow_and_retains_alternatives(workflow):
    original = workflow.model_dump(mode="json")
    view = navigation_view(workflow)
    pointers = []
    assert restore_evidence(view["record"], workflow, pointers) == original
    assert workflow.model_dump(mode="json") == original
    assert any("/resources/" in pointer for pointer in pointers)
    assert any("/input_bindings/" in pointer for pointer in pointers)
    assert any("/output_bindings/" in pointer for pointer in pointers)
    assert any(pointer.startswith("/states/") for pointer in pointers)
    assert any(pointer.startswith("/flows/") for pointer in pointers)
    inputs = view["record"]["operations"][2]["input_bindings"]
    assert [item["selection"] for item in inputs] == ["alternative", "alternative", "optional"]
    assert inputs[0]["alternative_group"] == inputs[1]["alternative_group"] == "library_route"
    assert all(item["condition"] for item in inputs)
    assert view["record"]["unresolved"] == original["unresolved"]
    resource = view["record"]["operations"][0]["resources"][0]
    assert resource["description"] == original["operations"][0]["resources"][0]["description"]
    assert resource["evidence"]["count"] == 2
    assert resource["evidence"]["support_types"] == ["inferred", "reported"]
    assert len(resource["evidence"]["passages"]) == 1


@pytest.mark.parametrize("pointer", ["/operations/0", "/operations/2", "/states/0", "/flows/0", ""])
def test_scoped_navigation_retains_absolute_evidence_pointers_and_exact_fields(workflow, pointer):
    view = navigation_view(workflow, pointer)
    pointers = []
    assert view["pointer"] == pointer and view["workflow_sha256"] == digest(workflow)
    assert restore_evidence(view["record"], workflow, pointers) == inspect_pointer(
        workflow.model_dump(mode="json"), pointer)
    assert pointers and all(item.startswith(pointer + "/") for item in pointers)
    if pointer == "/operations/0":
        assert "/operations/0/resources/0/evidence" in pointers
        assert "/operations/0/input_bindings/0/evidence" in pointers


def test_navigation_and_pointer_cli_reopen_exact_original_evidence(workflow, tmp_path, monkeypatch, capsys):
    from genomics_discovery.curation.workflow_extraction.__main__ import main

    path = tmp_path / "workflow.json"
    path.write_text(workflow.model_dump_json())
    before = path.read_bytes()
    monkeypatch.setattr(sys, "argv", ["atlas-extract", "inspect", str(path), "--navigation"])
    main()
    navigation = json.loads(capsys.readouterr().out)
    assert navigation == navigation_view(workflow)
    monkeypatch.setattr(sys, "argv", ["atlas-extract", "inspect", str(path), "--navigation",
                                      "--pointer", "/operations/0"])
    main()
    scoped = json.loads(capsys.readouterr().out)
    assert scoped["record"] == navigation["record"]["operations"][0]
    pointer = scoped["record"]["resources"][0]["evidence"]["pointer"]
    monkeypatch.setattr(sys, "argv", ["atlas-extract", "inspect", str(path), "--pointer", pointer])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["value"] == workflow.model_dump(mode="json")["operations"][0]["resources"][0]["evidence"]
    assert result["workflow_sha256"] == navigation["workflow_sha256"]
    monkeypatch.setattr(sys, "argv", ["atlas-extract", "inspect", str(path), "--navigation",
                                      "--pointer", pointer])
    main()
    assert json.loads(capsys.readouterr().out)["record"] == scoped["record"]["resources"][0]["evidence"]
    monkeypatch.setattr(sys, "argv", ["atlas-extract", "inspect", str(path)])
    main()
    assert expand_inspection(json.loads(capsys.readouterr().out)) == workflow
    assert path.read_bytes() == before


@pytest.mark.parametrize("pointer", ["#/operations", "/operations/99", "/operations/~2", "/record/operations"])
@pytest.mark.parametrize("navigation", [False, True])
def test_pointer_cli_rejects_invalid_or_wrong_envelope_paths(workflow, tmp_path, monkeypatch, capsys, pointer, navigation):
    from genomics_discovery.curation.workflow_extraction.__main__ import main

    path = tmp_path / "workflow.json"
    path.write_text(workflow.model_dump_json())
    monkeypatch.setattr(sys, "argv", ["atlas-extract", "inspect", str(path), "--pointer", pointer,
                                      *(["--navigation"] if navigation else [])])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "ValueError" in captured.err


def test_pointer_escape_resolution_remains_exact_and_independent():
    original = {"a/b": {"m~n": [{"value": "retained"}]}}
    result = inspect_pointer(original, "/a~1b/m~0n/0")
    assert result == {"value": "retained"}
    result["value"] = "modified"
    assert original["a/b"]["m~n"][0]["value"] == "retained"
