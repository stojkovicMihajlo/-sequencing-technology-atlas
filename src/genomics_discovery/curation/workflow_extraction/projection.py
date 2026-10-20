"""Queryable, source-bound candidate views; no production atlas writes."""

from __future__ import annotations

import copy
import json
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from urllib.parse import quote

from .models import (
    Workflow,
    WorkflowReview,
    digest,
    review_outcome,
    validate_evidence,
    validate_review,
)
from .sources import validate_packet


def _binding_view(flow) -> dict:
    """Keep edge support and selection distinct from state/operation evidence."""
    return {"flow_id": flow.id,
            "state_id": flow.source if flow.kind.endswith("_input") else flow.target,
            "direction": "input" if flow.kind.endswith("_input") else "output",
            "association": flow.association,
            "selection": flow.selection, "alternative_group": flow.alternative_group,
            "condition": flow.condition, "basis": flow.basis,
            "evidence": [item.model_dump(mode="json") for item in flow.evidence]}


def _input_groups(bindings: list[dict]) -> list[dict]:
    groups = {}
    for binding in bindings:
        alternative = binding["selection"] == "alternative"
        key = ("alternative", binding["alternative_group"]) if alternative else (
            "single", binding["flow_id"])
        group = groups.setdefault(key, {
            "selection": binding["selection"],
            "alternative_group": binding["alternative_group"],
            "rule": "exactly_one" if alternative else binding["selection"] or "unspecified",
            "state_ids": [], "flow_ids": [],
        })
        group["state_ids"].append(binding["state_id"])
        group["flow_ids"].append(binding["flow_id"])
    return list(groups.values())


def _relations(workflow: Workflow) -> list[dict]:
    operations = {op.id: op for op in workflow.operations}
    relations = []
    for flow in workflow.flows:
        if flow.kind in {"precedence", "control"}:
            relations.append({
                "source": flow.source, "target": flow.target,
                "flow_ids": [flow.id], "flow_types": [flow.kind],
                "state_id": None, "conditions": [flow.condition] if flow.condition else [],
                "route_bindings": [],
            })
        elif flow.kind in {"material_output", "data_output"}:
            for consumer in workflow.flows:
                if consumer.source == flow.target and consumer.target in operations:
                    relations.append({
                        "source": flow.source, "target": consumer.target,
                        "flow_ids": [flow.id, consumer.id],
                        "flow_types": [flow.kind, consumer.kind], "state_id": flow.target,
                        "conditions": [f.condition for f in (flow, consumer) if f.condition],
                        "route_bindings": [_binding_view(f) for f in (flow, consumer)],
                    })
    return relations


def query_workflow(workflow: Workflow, operation_id: str) -> dict:
    """Return declared interfaces and routed dependencies within one workflow."""
    operations = {op.id: op for op in workflow.operations}
    if operation_id not in operations:
        raise KeyError(f"Unknown operation: {operation_id}")
    operation = operations[operation_id]
    states = {state.id: state for state in workflow.states}
    relations = _relations(workflow)

    def neighbours(direction: str) -> list[dict]:
        own, other = ("target", "source") if direction == "predecessors" else ("source", "target")
        return [{
            "operation_id": edge[other], "name": operations[edge[other]].name,
            "flow_ids": edge["flow_ids"], "flow_types": edge["flow_types"],
            "state_id": edge["state_id"], "conditions": edge["conditions"],
            "route_bindings": edge["route_bindings"],
        } for edge in relations if edge[own] == operation_id]

    input_bindings = [_binding_view(flow) for flow in workflow.flows
                      if flow.target == operation_id and flow.kind.endswith("_input")]
    output_bindings = [_binding_view(flow) for flow in workflow.flows
                       if flow.source == operation_id and flow.kind.endswith("_output")]
    return {
        "method_id": workflow.method_id, "variant": workflow.variant, "scope": workflow.scope,
        "workflow_sha256": digest(workflow),
        "support_boundary": "Declared candidate interfaces. This query does not establish "
                            "fresh source review, biochemical compatibility or completeness.",
        "operation": operation.model_dump(mode="json"),
        "inputs": [states[sid].model_dump(mode="json") for sid in operation.inputs],
        "outputs": [states[sid].model_dump(mode="json") for sid in operation.outputs],
        "input_bindings": input_bindings, "output_bindings": output_bindings,
        "input_groups": _input_groups(input_bindings),
        "resources": [resource.model_dump(mode="json") for resource in operation.resources],
        "predecessors": neighbours("predecessors"), "consumers": neighbours("consumers"),
        "modules": [module.model_dump(mode="json") for module in workflow.modules
                    if operation_id in module.operation_ids],
        "flows": [flow.model_dump(mode="json") for flow in workflow.flows
                  if operation_id in (flow.source, flow.target)],
    }


def _ordered_operations(workflow: Workflow) -> list[str]:
    dependencies = {op.id: set() for op in workflow.operations}
    for edge in _relations(workflow):
        dependencies[edge["target"]].add(edge["source"])
    sorter = TopologicalSorter(dependencies)
    try:
        sorter.prepare()
    except CycleError as exc:
        raise ValueError("Workflow dependencies contain a cycle") from exc
    ordered = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        ordered.extend(ready)
        sorter.done(*ready)
    return ordered


def _references(value: dict | list, packet: dict) -> list[dict]:
    sources = {source["source_id"]: source for source in packet["sources"]}
    found = {}

    def collect(nested):
        if isinstance(nested, dict):
            if {"source_id", "passage_id", "quote", "support"} <= nested.keys():
                source = sources[nested["source_id"]]
                passage = next(p for p in source["passages"]
                               if p["passage_id"] == nested["passage_id"])
                found[digest(nested)] = {
                    **copy.deepcopy(nested), "locator": passage["locator"],
                    "source": {key: copy.deepcopy(value) for key, value in source.items()
                               if key != "passages"},
                }
            for child in nested.values():
                collect(child)
        elif isinstance(nested, list):
            for child in nested:
                collect(child)

    collect(value)
    return list(found.values())


def _evidence_line(evidence: list[dict]) -> str:
    return "; ".join(
        f"{e['source_id']}:{e['passage_id']} ({e['support']}): {e['quote']}"
        for e in evidence
    ) or "No direct passage citation supplied."


def _protocol_text(workflow: Workflow, review: WorkflowReview, *, diagnostic: bool = False) -> str:
    outcome = review_outcome(review, workflow)
    status = "diagnostic_" + outcome if diagnostic else "model_supported_within_declared_scope"
    lines = [workflow.title, f"Method: {workflow.method_id}; variant: {workflow.variant}",
             f"Scope: {workflow.scope}", f"Status: {status}.",
             f"Model/source review outcome: {outcome} within the declared scope.",
             "This artifact does not establish human acceptance or experimental validation.",
             "Module boundaries are curator interpretations.",
             "Step order displays the union of declared flow dependencies; equally ready "
             "steps use ID order. This is not an execution schedule.",
             "Optional and mutually exclusive inputs remain conditional alternatives, "
             "not cumulative execution requirements. Unspecified selection is not assumed required.", ""]
    for module in workflow.modules:
        lines.append(f"Module {module.id}: {module.name}; "
                     f"members: {', '.join(module.operation_ids)}")
        lines.append(f"  Boundary rationale: {module.boundary_rationale}")
    for index, operation_id in enumerate(_ordered_operations(workflow), 1):
        query = query_workflow(workflow, operation_id)
        op = query["operation"]
        lines.extend(["", f"Step {index} [{op['id']}]: {op['name']}"])
        for group in query["input_groups"]:
            if group["selection"] == "alternative":
                lines.append(f"  Input alternative group [{group['alternative_group']}]: "
                             f"exactly one of {', '.join(group['state_ids'])}")
        for state in query["inputs"]:
            lines.append(f"  Input [{state['id']}] ({state['kind']}): {state['description']}")
            lines.append(f"    Evidence: {_evidence_line(state['evidence'])}")
        for binding in query["input_bindings"]:
            lines.append(f"  Input binding [{binding['state_id']}]: "
                         f"association {binding['association'] or 'legacy_unspecified'}; "
                         f"selection {binding['selection'] or 'unspecified'}; "
                         f"group {binding['alternative_group']}; condition {binding['condition']}")
            lines.append(f"    Association basis: {binding['basis']}")
            lines.append(f"    Association evidence: {_evidence_line(binding['evidence'])}")
        lines.append(f"  Action: {op['action']}")
        for resource in query["resources"]:
            lines.append(f"  {resource['kind'].capitalize()}: {resource['description']}")
            lines.append(f"    Evidence: {_evidence_line(resource['evidence'])}")
        lines.append(f"  Information effect: {op['information_effect']}")
        lines.append(f"  Evidence: {_evidence_line(op['evidence'])}")
        for relation in query["predecessors"]:
            lines.append(f"  Predecessor [{relation['operation_id']}]: "
                         f"{', '.join(relation['flow_types'])}; flows {relation['flow_ids']}; "
                         f"conditions {relation['conditions']}")
        for state in query["outputs"]:
            consumers = [r["operation_id"] for r in query["consumers"]
                         if r["state_id"] == state["id"]]
            lines.append(f"  Output [{state['id']}] ({state['kind']}): {state['description']}"
                         f" -> next consumers: {', '.join(consumers) or 'none declared in scope'}")
            lines.append(f"    Evidence: {_evidence_line(state['evidence'])}")
        for relation in query["consumers"]:
            lines.append(f"  Next consumer [{relation['operation_id']}]: "
                         f"{', '.join(relation['flow_types'])}; flows {relation['flow_ids']}; "
                         f"conditions {relation['conditions']}")
        for flow in query["flows"]:
            lines.append(f"  Route [{flow['id']}]: {flow['source']} -> {flow['target']} "
                         f"({flow['kind']}); basis: {flow['basis']}; "
                         f"condition: {flow['condition']}; "
                         f"association: {flow.get('association', 'legacy_unspecified')}; "
                         f"selection: {flow.get('selection', 'unspecified')}; "
                         f"alternative group: {flow.get('alternative_group')}")
            lines.append(f"    Evidence: {_evidence_line(flow['evidence'])}")
    lines.extend(["", "Explicit gaps:"])
    lines.extend(f"  [{gap.id}] {gap.description}; blocking: {gap.blocking}; "
                 f"next evidence: {gap.next_evidence}" for gap in workflow.unresolved)
    lines.extend(f"  Review omission: {gap.description}; blocking: {gap.blocking}; "
                 f"action: {gap.action}; "
                 f"evidence: {_evidence_line([e.model_dump(mode='json') for e in gap.evidence])}"
                 for gap in review.omissions)
    if not workflow.unresolved and not review.omissions:
        lines.append("  None declared by the model/source review within this scope.")
    nonpositive = [check for check in review.checks if check.verdict != "supported"]
    if nonpositive:
        lines.extend(["", "Review targets requiring follow-up:"])
        for check in nonpositive:
            lines.append(f"  [{check.target_id}] {check.verdict}: {check.rationale}")
            lines.append("    Evidence: " + _evidence_line([
                evidence.model_dump(mode="json") for evidence in check.evidence]))
    if workflow.gap_resolutions:
        lines.extend(["", "Reader gap resolutions:"])
        for resolution in workflow.gap_resolutions:
            lines.append(f"  [{resolution.reader_role}:{resolution.gap_id}] {resolution.rationale}")
            lines.append("    Evidence: " + _evidence_line([
                evidence.model_dump(mode="json") for evidence in resolution.evidence]))
    return "\n".join(lines) + "\n"


def _export(workflow: Workflow, review: WorkflowReview, packet: dict, run: Path, *,
            diagnostic: bool) -> dict:
    validate_packet(packet)
    if any(getattr(workflow, field) != packet[field]
           for field in ("method_id", "title", "scope", "variant")):
        raise ValueError("Workflow metadata differs from registered source scope")
    errors = validate_evidence(workflow, packet) + validate_review(review, workflow, packet)
    if errors:
        raise ValueError("Workflow provenance/review validation failed: " + "; ".join(errors))
    outcome = review_outcome(review, workflow)
    if not diagnostic and outcome != "supported":
        raise ValueError("Only a supported model/source review can export a candidate")
    if diagnostic and outcome == "supported":
        raise ValueError("Diagnostic export requires a nonpositive model/source review")
    verdicts = {check.target_id: check.verdict for check in review.checks}
    original = workflow.model_dump(mode="json")
    namespace = ("workflow:" + quote(workflow.method_id, safe="") + ":"
                 + quote(workflow.variant, safe=""))
    ids = {item.id: f"{namespace}:{kind}:{quote(item.id, safe='')}"
           for kind, items in (("state", workflow.states), ("operation", workflow.operations),
                               ("module", workflow.modules)) for item in items}
    nodes = []
    for kind, items in (("state", workflow.states), ("operation", workflow.operations),
                        ("module", workflow.modules)):
        for item in items:
            raw = item.model_dump(mode="json")
            evidence_value = raw if kind != "module" else [op.model_dump(mode="json")
                for op in workflow.operations if op.id in item.operation_ids]
            nodes.append({"id": ids[item.id], "node_type": kind, "source_id": item.id,
                          "scope": workflow.scope, "original": raw,
                          "source_refs": _references(evidence_value, packet),
                          **({"boundary_status": "curator_interpretation",
                              "operation_ids": [ids[oid] for oid in item.operation_ids]}
                             if kind == "module" else {
                                 "review_verdict": verdicts[f"{kind}:{item.id}"]})})
    edges = [{"id": f"{namespace}:flow:{quote(flow.id, safe='')}",
              "source": ids[flow.source], "target": ids[flow.target], "kind": flow.kind,
              "scope": workflow.scope, "original": flow.model_dump(mode="json"),
              "association": flow.association, "selection": flow.selection,
              "alternative_group": flow.alternative_group, "condition": flow.condition,
              "review_verdict": verdicts[f"flow:{flow.id}"],
              "source_refs": _references(flow.model_dump(mode="json"), packet)}
             for flow in workflow.flows]
    memberships = [{"module": ids[module.id], "operation": ids[oid],
                    "basis": "curator_interpretation", "rationale": module.boundary_rationale}
                   for module in workflow.modules for oid in module.operation_ids]
    representation = "diagnostic" if diagnostic else "candidate"
    status = "diagnostic_" + outcome if diagnostic else "model_supported_within_declared_scope"
    graph = {"format_version": f"workflow_graph_{representation}/1.0", "id": namespace,
             "status": status, "review_outcome": outcome, "scope": workflow.scope,
             "workflow_sha256": digest(workflow), "source_packet_sha256": digest(packet),
             "workflow": original, "review": review.model_dump(mode="json"),
             "source_registry": copy.deepcopy(packet["sources"]), "nodes": nodes,
             "edges": edges, "module_memberships": memberships}
    text = _protocol_text(workflow, review, diagnostic=diagnostic)
    run = Path(run)
    run.mkdir(parents=True, exist_ok=True)
    (run / f"graph_{representation}.json").write_text(
        json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run / ("protocol_diagnostic.txt" if diagnostic else "protocol.txt")).write_text(
        text, encoding="utf-8")
    return graph


def export_candidate(workflow: Workflow, review: WorkflowReview, packet: dict, run: Path) -> dict:
    """Export a supported candidate without promoting atlas records."""
    return _export(workflow, review, packet, run, diagnostic=False)


def export_diagnostic(workflow: Workflow, review: WorkflowReview, packet: dict, run: Path) -> dict:
    """Export valid nonpositive review artifacts with their unresolved/unsupported verdicts."""
    return _export(workflow, review, packet, run, diagnostic=True)
