"""Field guidance reaches authoring without changing stored scientific records.

These are schema/delivery tests, not evidence that a model obeys the guidance.
"""

import json
from pathlib import Path

import pytest

from genomics_discovery.curation.workflow_extraction.authoring import (
    authoring_schema,
    authoring_view,
    expand_authoring,
)
from genomics_discovery.curation.workflow_extraction.models import (
    Evidence,
    Finding,
    Flow,
    Gap,
    GapResolution,
    IOBinding,
    Module,
    Operation,
    PassageRef,
    ReadingReport,
    Resource,
    State,
    Workflow,
    WorkflowDraft,
    digest,
)


EXTRACTION_RECORDS = (
    PassageRef, Evidence, Finding, Gap, GapResolution, ReadingReport,
    Resource, State, IOBinding, Operation, Flow, Module, Workflow, WorkflowDraft,
)


@pytest.mark.parametrize("model", EXTRACTION_RECORDS, ids=lambda model: model.__name__)
def test_every_extraction_field_has_semantic_guidance_in_generated_schema(model):
    properties = model.model_json_schema()["properties"]
    assert properties.keys() == model.model_fields.keys()
    for name, field in model.model_fields.items():
        assert field.description and field.description.strip(), f"{model.__name__}.{name}"
        assert properties[name]["description"] == field.description


def test_draft_and_workflow_share_field_meanings():
    common = Workflow.model_fields.keys() & WorkflowDraft.model_fields.keys()
    for name in common:
        assert WorkflowDraft.model_fields[name].description == Workflow.model_fields[name].description


@pytest.mark.parametrize("kind, model", [("draft", WorkflowDraft), ("workflow", Workflow)])
def test_compact_authoring_preserves_all_field_guidance(kind, model):
    original = model.model_json_schema()
    compact = authoring_schema(kind)
    for name, record in {"record": original, **original["$defs"]}.items():
        authored = compact["properties"]["record"] if name == "record" else compact["$defs"][name]
        for field, definition in record["properties"].items():
            authored_field = "evidence_refs" if field == "evidence" else field
            description = authored["properties"][authored_field]["description"]
            if field == "evidence":
                assert description.startswith(definition["description"])
                assert "evidence_registry" in description
            else:
                assert description == definition["description"]


def test_existing_workflow_serialization_and_digest_are_unchanged():
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "examples/chipmentation/workflow.json").read_text())
    workflow = Workflow.model_validate(raw)
    assert workflow.model_dump(mode="json") == raw
    assert digest(workflow) == "451cd0f252e5e036012865c6bcd52b7c41b8a2aba179b608fa832d3a22e5fe83"
    assert expand_authoring(authoring_view(workflow)).model_dump(mode="json") == raw


def test_guidance_does_not_make_legacy_binding_defaults_into_assertions():
    root = Path(__file__).resolve().parents[1]
    workflow = Workflow.model_validate_json((root / "examples/chipmentation/workflow.json").read_bytes())
    operation = workflow.operations[0].model_dump(mode="json")
    operation.pop("input_bindings", None)
    operation.pop("output_bindings", None)
    assert Operation.model_validate(operation).model_dump(mode="json") == operation
    flow = workflow.flows[0].model_dump(mode="json")
    for key in ("association", "selection", "alternative_group"):
        flow.pop(key, None)
    assert Flow.model_validate(flow).model_dump(mode="json") == flow
