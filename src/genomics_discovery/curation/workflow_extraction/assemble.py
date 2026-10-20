"""Render declared I/O without manufacturing scientific association evidence."""

from urllib.parse import quote

from .models import Flow, Workflow, WorkflowDraft, validate_io_bindings


def assemble_workflow(draft: WorkflowDraft, *, require_bindings: bool = False) -> Workflow:
    """Formalize declared I/O; source review must still establish its correctness.

    Explicit bindings carry their own authored evidence and route conditions.
    Legacy bare references yield unqualified structural edges, not reported or
    scientifically inferred transfers. Existing saved workflows are not rewritten.
    """
    if errors := validate_io_bindings(draft, require_bindings=require_bindings):
        raise ValueError("; ".join(errors))
    states = {state.id: state for state in draft.states}
    flows = list(draft.ordering_flows)
    for operation in draft.operations:
        for direction, state_ids in (("input", operation.inputs), ("output", operation.outputs)):
            bindings = getattr(operation, direction + "_bindings")
            by_state = {binding.state_id: binding for binding in bindings or ()}
            for state_id in state_ids:
                if state_id not in states:
                    raise ValueError(f"Operation {operation.id}: unknown state {state_id}")
                source, target = ((state_id, operation.id) if direction == "input"
                                  else (operation.id, state_id))
                binding = by_state.get(state_id)
                qualification = (dict(
                    basis=binding.basis, condition=binding.condition,
                    evidence=binding.evidence, association="source_qualified",
                    selection=binding.selection, alternative_group=binding.alternative_group,
                ) if binding else dict(
                    basis=f"Explicit {direction} reference declared by operation {operation.id}; "
                          "structural association only, without per-I/O source qualification.",
                    evidence=(), association="unqualified",
                ))
                flows.append(Flow(
                    id=f"io:{direction}:{quote(operation.id, safe='')}:{quote(state_id, safe='')}",
                    source=source, target=target, kind=f"{states[state_id].kind}_{direction}",
                    **qualification,
                ))
    data = draft.model_dump(mode="json")
    data.pop("ordering_flows")
    data["flows"] = [flow.model_dump(mode="json") for flow in flows]
    return Workflow.model_validate(data)
