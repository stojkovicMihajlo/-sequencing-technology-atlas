"""Generate mechanical I/O edges from explicit state references, never list order."""

from urllib.parse import quote

from .models import Flow, Workflow, WorkflowDraft


def assemble_workflow(draft: WorkflowDraft) -> Workflow:
    """Formalize declared I/O; source review must still establish its correctness.

    An operation explicitly naming a state as its input/output asserts that route.
    The helper renders that assertion as a typed edge. It does not infer a shared
    state from similar descriptions or establish biochemical compatibility.
    """
    states = {state.id: state for state in draft.states}
    flows = list(draft.ordering_flows)
    for operation in draft.operations:
        for direction, state_ids in (("input", operation.inputs), ("output", operation.outputs)):
            for state_id in state_ids:
                if state_id not in states:
                    raise ValueError(f"Operation {operation.id}: unknown state {state_id}")
                source, target = ((state_id, operation.id) if direction == "input"
                                  else (operation.id, state_id))
                flows.append(Flow(
                    id=f"io:{direction}:{quote(operation.id, safe='')}:{quote(state_id, safe='')}",
                    source=source, target=target, kind=f"{states[state_id].kind}_{direction}",
                    basis=f"Explicit {direction} state declared by operation {operation.id}; "
                          "mechanically rendered from its I/O, not inferred from array order.",
                    evidence=operation.evidence,
                ))
    data = draft.model_dump(mode="json")
    data.pop("ordering_flows")
    data["flows"] = [flow.model_dump(mode="json") for flow in flows]
    return Workflow.model_validate(data)
