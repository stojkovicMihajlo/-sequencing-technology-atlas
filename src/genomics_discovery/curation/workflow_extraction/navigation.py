"""Fail-closed structural paths: prose guards are never evaluated as route predicates."""

from collections import defaultdict, deque

from .models import Workflow, digest


def query_path(workflow: Workflow, source_id: str, target_id: str) -> dict:
    """Find directed graph paths without silently treating conditional edges as active.

    A path is not a complete protocol: other required inputs may lie outside it.
    Even an unconditional path does not prove source support or compatibility.
    This API deliberately cannot accept a local group choice as proof that guards
    at different Operations describe one globally consistent assay execution.
    """
    known = {item.id for item in (*workflow.states, *workflow.operations)}
    for node_id in (source_id, target_id):
        if node_id not in known:
            raise KeyError(f"Unknown Workflow State/Operation: {node_id}")
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for flow in sorted(workflow.flows, key=lambda item: item.id):
        outgoing[flow.source].append(flow)
        incoming[flow.target].append(flow)

    def guarded(flow):
        return flow.condition is not None or flow.selection in {"optional", "alternative"}

    def path(allow_guards):
        queue, previous = deque([source_id]), {source_id: None}
        while queue:
            node = queue.popleft()
            if node == target_id:
                result = []
                while previous[node] is not None:
                    flow = previous[node]
                    result.append(flow.id)
                    node = flow.source
                return list(reversed(result)), set(previous)
            for flow in outgoing[node]:
                if (allow_guards or not guarded(flow)) and flow.target not in previous:
                    previous[flow.target] = flow
                    queue.append(flow.target)
        return None, set(previous)

    unconditional, _ = path(False)
    declared, _ = path(True)
    # Discover all nodes that can reach target; do not evaluate any guard prose.
    reverse_reachable, queue = {target_id}, deque([target_id])
    while queue:
        for flow in incoming[queue.popleft()]:
            if flow.source not in reverse_reachable:
                reverse_reachable.add(flow.source)
                queue.append(flow.source)
    # BFS above stops on reaching target, so compute full forward reachability separately.
    forward_reachable, queue = {source_id}, deque([source_id])
    while queue:
        for flow in outgoing[queue.popleft()]:
            if flow.target not in forward_reachable:
                forward_reachable.add(flow.target)
                queue.append(flow.target)
    blocked = [{
        "flow_id": flow.id, "source": flow.source, "target": flow.target,
        "kind": flow.kind, "condition": flow.condition, "selection": flow.selection,
        "alternative_group": flow.alternative_group,
    } for flow in sorted(workflow.flows, key=lambda item: item.id)
        if guarded(flow) and flow.source in forward_reachable
        and flow.target in reverse_reachable]
    return {
        "workflow_sha256": digest(workflow), "source_id": source_id, "target_id": target_id,
        "status": ("unconditional_structural_path" if unconditional is not None else
                   "needs_route_selection" if declared is not None else "no_declared_path"),
        "path_flow_ids": unconditional,
        "conditional_candidate_path_flow_ids": (declared if unconditional is None else None),
        "blocked_conditional_edges": blocked,
        "boundary": "Structural navigation only, not an execution schedule or a scientific "
                    "validity judgment. Conditional candidate paths may mix incompatible routes. "
                    "No natural-language guard was evaluated; no globally consistent route "
                    "or complete set of required inputs was established.",
    }
