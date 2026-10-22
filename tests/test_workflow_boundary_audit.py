"""Topology accounting must not mistake legitimate inputs/QC/discards for errors."""

from copy import deepcopy

import pytest

from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.boundaries import (
    BoundaryManifest, audit_boundaries, validate_boundary_manifest,
)
from genomics_discovery.curation.workflow_extraction.models import WorkflowDraft, digest
from test_workflow_extraction_assemble import EVIDENCE, draft


def workflow_with_endpoints():
    data = draft()
    data["states"].extend([
        {"id": sid, "kind": kind, "description": sid, "evidence": EVIDENCE}
        for sid, kind in (("reference", "data"), ("qc", "data"), ("discard", "material"))])
    data["operations"][1]["inputs"].append("reference")
    data["operations"][0]["outputs"].extend(["qc", "discard"])
    return assemble_workflow(WorkflowDraft.model_validate(data))


def manifest_for(workflow):
    return BoundaryManifest.model_validate({
        "workflow_sha256": digest(workflow),
        "dispositions": [{"state_id": item["state_id"],
                          "boundary_kind": item["boundary_kind"],
                          "disposition": "Explicit scoped boundary, requiring source review."}
                         for item in audit_boundaries(workflow)["boundaries"]]})


def test_legitimate_start_reference_product_qc_and_discard_are_reported_without_rejection():
    workflow = workflow_with_endpoints()
    report = audit_boundaries(workflow)
    assert report["counts"] == {"external_input": 2, "terminal_output": 3, "isolated": 0}
    assert report["annotation_coverage"] == "not_supplied"
    assert report["manifest_errors"] == []
    assert report["scientific_admission"] == "not_established_by_boundary_audit"
    assert {b["state_id"] for b in report["boundaries"]} == {
        "native", "reference", "observations", "qc", "discard"}
    assert validate_boundary_manifest(workflow, manifest_for(workflow)) == []
    assert audit_boundaries(workflow, manifest_for(workflow))["annotation_coverage"] == "complete"


def test_isolated_state_needs_explicit_annotation_but_is_not_auto_classified_as_false():
    data = draft()
    data["states"].append({"id": "orphan", "kind": "material", "description": "Unrouted",
                           "evidence": EVIDENCE})
    workflow = assemble_workflow(WorkflowDraft.model_validate(data))
    report = audit_boundaries(workflow)
    assert report["counts"]["isolated"] == 1
    assert report["manifest_errors"] == []
    item = next(b for b in report["boundaries"] if b["state_id"] == "orphan")
    assert item["interfaces"] == []
    assert validate_boundary_manifest(workflow, manifest_for(workflow)) == []


@pytest.mark.parametrize("change,expected", [
    ("stale", "workflow_sha256"), ("missing", "missing dispositions"),
    ("extra", "unknown/non-boundary"), ("wrong_kind", "expected external_input"),
])
def test_inexact_manifest_cannot_pass(change, expected):
    workflow = workflow_with_endpoints()
    data = manifest_for(workflow).model_dump(mode="json")
    if change == "stale":
        data["workflow_sha256"] = "0" * 64
    elif change == "missing":
        data["dispositions"].pop()
    elif change == "extra":
        data["dispositions"].append({"state_id": "processed", "boundary_kind": "isolated",
                                     "disposition": "Incorrect internal State entry."})
    else:
        next(d for d in data["dispositions"] if d["state_id"] == "native")[
            "boundary_kind"] = "terminal_output"
    manifest = BoundaryManifest.model_validate(data)
    errors = validate_boundary_manifest(workflow, manifest)
    assert any(expected in error for error in errors)
    assert audit_boundaries(workflow, manifest)["annotation_coverage"] == "invalid"


def test_duplicate_manifest_state_is_rejected():
    data = manifest_for(workflow_with_endpoints()).model_dump(mode="json")
    data["dispositions"].append(deepcopy(data["dispositions"][0]))
    with pytest.raises(ValueError, match="duplicate State"):
        BoundaryManifest.model_validate(data)


def test_conditions_and_selections_survive_boundary_report():
    data = draft()
    for operation in data["operations"]:
        for direction in ("input", "output"):
            operation[direction + "_bindings"] = [
                {"state_id": sid, "basis": "Reported association", "evidence": EVIDENCE,
                 "selection": "optional", "condition": "Only when this assay route applies."}
                for sid in operation[direction + "s"]]
    workflow = assemble_workflow(WorkflowDraft.model_validate(data), require_bindings=True)
    report = audit_boundaries(workflow)
    assert all(interface["selection"] == "optional" and interface["condition"] ==
               "Only when this assay route applies."
               for item in report["boundaries"] for interface in item["interfaces"])
    assert report["boundaries"][0]["interfaces"][0]["association"] == "source_qualified"


def test_inventory_is_stable_under_array_reordering_and_does_not_mutate_workflow():
    workflow = workflow_with_endpoints()
    before = workflow.model_dump(mode="json")
    reordered = deepcopy(before)
    reordered["states"].reverse()
    reordered["flows"].reverse()
    from genomics_discovery.curation.workflow_extraction.models import Workflow
    other = audit_boundaries(Workflow.model_validate(reordered))
    assert audit_boundaries(workflow)["boundaries"] == other["boundaries"]
    assert workflow.model_dump(mode="json") == before
