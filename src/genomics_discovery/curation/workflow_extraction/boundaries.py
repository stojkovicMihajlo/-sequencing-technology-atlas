"""Deterministic interface inventory, not biochemical or source-entailment review.

A State with no producer can be a legitimate specimen, reagent, design or reference.
A State with no consumer can be a product, QC observation, reserve or discard. Neither
is automatically an extraction error. An optional pinned manifest requires the
author to account explicitly for every such boundary, including isolated States.
"""

from collections import defaultdict
from typing import Annotated, Literal

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel

from .models import Text, Workflow, digest

BoundaryKind = Literal["external_input", "terminal_output", "isolated"]
BOUNDARY = (
    "Graph topology and exact annotation coverage only. Dispositions are authored claims; "
    "this audit does not verify source support, completeness, compatibility or execution readiness."
)


class BoundaryDisposition(StrictModel):
    state_id: Text
    boundary_kind: BoundaryKind
    disposition: Text = Field(description=
        "Explain why this interface is a legitimate start, endpoint or isolated record in "
        "the declared scope, or explicitly describe the unresolved boundary problem. "
        "Do not infer consumption, discard, reuse or scientific support from topology.")


class BoundaryManifest(StrictModel):
    workflow_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    dispositions: tuple[BoundaryDisposition, ...]

    @model_validator(mode="after")
    def unique_states(self):
        ids = [item.state_id for item in self.dispositions]
        if len(ids) != len(set(ids)):
            raise ValueError("Boundary manifest contains duplicate State dispositions")
        return self


def _inventory(workflow: Workflow) -> list[dict]:
    incoming, outgoing = defaultdict(list), defaultdict(list)
    for flow in workflow.flows:
        if flow.kind.endswith("_output"):
            incoming[flow.target].append(flow)
        elif flow.kind.endswith("_input"):
            outgoing[flow.source].append(flow)
    boundaries = []
    for state in sorted(workflow.states, key=lambda item: item.id):
        producers, consumers = incoming[state.id], outgoing[state.id]
        if producers and consumers:
            continue
        kind = ("external_input" if consumers else "terminal_output" if producers
                else "isolated")
        boundaries.append({
            "state_id": state.id, "boundary_kind": kind, "state_kind": state.kind,
            "description": state.description,
            "producer_operations": sorted(flow.source for flow in producers),
            "consumer_operations": sorted(flow.target for flow in consumers),
            "interfaces": [{
                "flow_id": flow.id, "kind": flow.kind,
                "association": flow.association, "selection": flow.selection,
                "alternative_group": flow.alternative_group, "condition": flow.condition,
            } for flow in sorted((*producers, *consumers), key=lambda item: item.id)],
        })
    return boundaries


def validate_boundary_manifest(workflow: Workflow, manifest: BoundaryManifest) -> list[str]:
    """Check an exact pinned annotation inventory; do not interpret its prose."""
    errors = []
    if manifest.workflow_sha256 != digest(workflow):
        errors.append("Boundary manifest workflow_sha256 does not match the Workflow")
    actual = {item["state_id"]: item["boundary_kind"] for item in _inventory(workflow)}
    declared = {item.state_id: item.boundary_kind for item in manifest.dispositions}
    if missing := actual.keys() - declared.keys():
        errors.append(f"Boundary manifest missing dispositions: {sorted(missing)}")
    if extra := declared.keys() - actual.keys():
        errors.append(f"Boundary manifest contains unknown/non-boundary States: {sorted(extra)}")
    for sid in sorted(actual.keys() & declared.keys()):
        if actual[sid] != declared[sid]:
            errors.append(f"Boundary manifest State {sid}: expected {actual[sid]}, "
                          f"received {declared[sid]}")
    return errors


def audit_boundaries(workflow: Workflow, manifest: BoundaryManifest | None = None) -> dict:
    """Produce a stable report without mutating a Workflow or admitting it to an atlas.

    ``annotation_coverage`` describes only manifest coverage. Even a complete
    manifest may explicitly retain unresolved problems and is no support verdict.
    """
    inventory = _inventory(workflow)
    errors = validate_boundary_manifest(workflow, manifest) if manifest is not None else []
    dispositions = ({item.state_id: item.model_dump(mode="json")
                     for item in manifest.dispositions} if manifest is not None else {})
    return {
        "format_version": "workflow_boundary_audit/1.0",
        "workflow_sha256": digest(workflow), "boundary": BOUNDARY,
        "annotation_coverage": ("not_supplied" if manifest is None else
                                "invalid" if errors else "complete"),
        "scientific_admission": "not_established_by_boundary_audit",
        "manifest_errors": errors,
        "counts": {kind: sum(item["boundary_kind"] == kind for item in inventory)
                   for kind in ("external_input", "terminal_output", "isolated")},
        "boundaries": [{**item, "disposition": dispositions.get(item["state_id"])}
                       for item in inventory],
    }
