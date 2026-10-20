"""Operation-centered display of exact declared topology, without scientific rewriting.

This is a bound projection, not a lossless workflow encoding or acceptance check.
Descriptions appear once in the State interface registry. Recipes and evidence
remain in the original Workflow and the existing per-operation query view.
"""

from __future__ import annotations

import json

from .models import Workflow, WorkflowReview, digest, review_outcome, target_ids

BOUNDARY = (
    "Display of the bound Workflow, not renewed review, acceptance or execution readiness. "
    "No State, Operation or Flow records are merged or filtered. Listed operation order is stored order, not an "
    "execution schedule. Input/output lists are references: Flow selection, alternatives "
    "and conditions govern applicability; unspecified selection is not assumed required. "
    "No declared connection does not establish physical absence. Action text, resources, "
    "information effects and evidence are available in the exact workflow via detail pointers "
    "and operation queries; their omission here is not a claim that those details are absent."
)
FLOW_FIELDS = ("id", "source", "target", "kind", "basis", "condition", "association",
               "selection", "alternative_group")


def workflow_overview(workflow: Workflow, *, review: WorkflowReview | None = None) -> dict:
    """Keep every State, Operation and typed Flow; omit repeated recipe/provenance text."""
    workflow_hash = digest(workflow)
    if review is not None and review.workflow_sha256 != workflow_hash:
        raise ValueError("Overview review does not bind this exact Workflow")
    if review is not None:
        checked = [check.target_id for check in review.checks]
        if len(checked) != len(set(checked)) or set(checked) != target_ids(workflow):
            raise ValueError("Overview review requires unique exact target coverage")
    review_context = {
        "status": "archived_model_review" if review is not None else "unreviewed_projection",
        "workflow_sha256": workflow_hash,
        "review_sha256": digest(review) if review is not None else None,
        "source_packet_sha256": review.source_packet_sha256 if review is not None else None,
        "outcome": review_outcome(review, workflow) if review is not None else "not_assessed",
        "nonpositive_checks": [check.model_dump(mode="json") for check in review.checks
                               if check.verdict != "supported"] if review is not None else [],
        "omissions": [item.model_dump(mode="json") for item in review.omissions] if review is not None else [],
        "notes": list(review.notes) if review is not None else [],
        "boundary": ("Existing bound model-review context only. The outcome uses the existing review rule; "
                     "this projection verifies the workflow hash and unique exact target coverage only. "
                     "It does not revalidate source-packet integrity, evidence or source verdicts without "
                     "the packet. No fresh adjudication "
                     "or acceptance is established.") if review is not None else
                    "No WorkflowReview supplied: unreviewed projection, not a supported or accepted result.",
    }
    memberships = {operation.id: [] for operation in workflow.operations}
    for module in workflow.modules:
        for operation_id in module.operation_ids:
            memberships[operation_id].append(module.id)
    return {
        "format_version": "workflow_overview/1.0",
        "workflow_sha256": workflow_hash,
        "review_context": review_context,
        **{field: getattr(workflow, field) for field in ("method_id", "title", "scope", "variant")},
        "states": {state.id: {"kind": state.kind, "description": state.description,
                                   "detail_pointer": f"/states/{index}"}
                       for index, state in enumerate(workflow.states)},
        "operations": [{"id": operation.id, "name": operation.name,
                        "module_ids": memberships[operation.id],
                        "inputs": list(operation.inputs), "outputs": list(operation.outputs),
                        "detail_pointer": f"/operations/{index}"}
                       for index, operation in enumerate(workflow.operations)],
        "flows": [{**{field: getattr(flow, field) for field in FLOW_FIELDS},
                   "detail_pointer": f"/flows/{index}"}
                  for index, flow in enumerate(workflow.flows)],
        "modules": [{**module.model_dump(mode="json"), "detail_pointer": f"/modules/{index}"}
                   for index, module in enumerate(workflow.modules)],
        "unresolved": [gap.model_dump(mode="json") for gap in workflow.unresolved],
        "gap_resolutions": [{"reader_role": item.reader_role, "gap_id": item.gap_id,
                             "rationale": item.rationale,
                             "detail_pointer": f"/gap_resolutions/{index}"}
                            for index, item in enumerate(workflow.gap_resolutions)],
        "details": {"operation_query": ["atlas-extract", "query", "WORKFLOW", "OPERATION_ID"],
                    "pointer_basis": "JSON Pointers into the Workflow with the bound canonical hash.",
                    "modules": "Exact existing Module memberships, available as optional display groups."},
        "boundary": BOUNDARY,
    }


def overview_text(workflow: Workflow, *, review: WorkflowReview | None = None) -> str:
    """Render operation/interface references once; keep all route qualifiers and gaps visible."""
    view = workflow_overview(workflow, review=review)
    context = view["review_context"]
    lines = [view["title"], f"Method: {view['method_id']}; variant: {view['variant']}",
             f"Scope: {view['scope']}", f"Workflow SHA256: {view['workflow_sha256']}",
             view["boundary"], "", f"Review context: {context['status']}; outcome: {context['outcome']}.",
             context["boundary"]]
    if review is not None:
        lines += [f"Review SHA256: {context['review_sha256']}",
                  f"Review-declared source packet SHA256: {context['source_packet_sha256']}"]
    for check in context["nonpositive_checks"]:
        lines += [f"Review follow-up [{check['target_id']}]: {check['verdict']}; {check['rationale']}",
                  "  Evidence: " + json.dumps(check["evidence"], ensure_ascii=False)]
    for omission in context["omissions"]:
        lines += [f"Review omission: {omission['description']}; action={omission['action']}; "
                  f"blocking={omission['blocking']}",
                  "  Evidence: " + json.dumps(omission["evidence"], ensure_ascii=False)]
    for note in context["notes"]:
        lines.append("Review note: " + note)
    lines += ["", "Operations (State IDs; selection is specified by the Flows):"]
    for operation in view["operations"]:
        lines += [f"[{operation['id']}] {operation['name']}",
                  "  Inputs: " + ", ".join(operation["inputs"]),
                  "  Outputs: " + ", ".join(operation["outputs"])]
    lines += ["", "States (each material/data description appears once):"]
    for state_id, state in view["states"].items():
        lines.append(f"[{state_id}] ({state['kind']}) {state['description']}")
    lines += ["", "Flows (exact declared relationships):"]
    for flow in view["flows"]:
        qualifiers = []
        if flow["kind"] not in {"precedence", "control"}:
            qualifiers += ["association=" + (flow["association"] or "unspecified"),
                           "selection=" + (flow["selection"] or "unspecified")]
        if flow["alternative_group"] is not None:
            qualifiers.append("alternative_group=" + flow["alternative_group"])
        if flow["condition"] is not None:
            qualifiers.append("condition=" + flow["condition"])
        suffix = "; " + "; ".join(qualifiers) if qualifiers else ""
        lines += [f"[{flow['id']}] {flow['source']} -> {flow['target']} ({flow['kind']}){suffix}",
                  "  Basis: " + flow["basis"]]
    lines += ["", "Unresolved qualifications:"]
    if not view["unresolved"]:
        lines.append("No unresolved Gap records are declared; this is not a completeness judgment.")
    for gap in view["unresolved"]:
        lines += [f"[{gap['id']}] {gap['description']} (blocking={gap['blocking']})",
                  "  Next evidence: " + gap["next_evidence"]]
    if view["gap_resolutions"]:
        lines += ["", "Recorded gap resolutions (evidence remains in the bound Workflow):"]
        for resolution in view["gap_resolutions"]:
            lines.append(f"[{resolution['reader_role']}:{resolution['gap_id']}] {resolution['rationale']}")
    lines += ["", "Details: atlas-extract query WORKFLOW OPERATION_ID",
              "Use this exact Workflow hash; JSON detail pointers resolve in that record. "
              "Existing Module groups are available in the JSON overview; they do not add transformations."]
    return "\n".join(lines) + "\n"
