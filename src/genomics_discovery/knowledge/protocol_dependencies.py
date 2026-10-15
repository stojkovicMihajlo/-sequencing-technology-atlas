"""Bounded review paths through recorded protocol connections.

Graph reachability identifies records to revisit after a caller-proposed change.
It does not identify experimentally affected modules or establish independence.
The audit records the BFS reference and the source-scoped protocol contract.
"""

from __future__ import annotations

import json
import re
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, Literal

DEFAULT_DEPENDENCY_RELATIONS = ("material", "information", "control", "ordering_requirement")
_ANNOTATION_FIELDS = {
    "unknowns", "warnings", "uncertainties", "limitations", "caveats", "verification",
    "normalization_review", "evidence_status", "boundary_status", "evidence_scope",
}


def _pointer(*parts: Any) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _navigation_trace(profile: Mapping, trace: dict, snapshot: str) -> dict:
    """Reversible navigation projection; omitted prose remains an exact field reference."""
    def reference(pointer: str) -> dict:
        return {"profile_id": profile["profile_id"], "snapshot_sha256": snapshot,
                "json_pointer": pointer}

    def field(value: Any, pointer: str, *, present: bool = True,
              expand_items: bool = True) -> dict:
        if not present:
            return {"state": "missing"}
        state = "null" if value is None else "empty" if value in ([], {}) else "recorded"
        result = {"state": state, "record_ref": reference(pointer)}
        if isinstance(value, (list, dict)):
            result["count"] = len(value)
        # Expose identifiers, affected fields and blocking/status flags without
        # summarizing the scientific prose in a gap or warning into a new claim.
        if isinstance(value, Mapping):
            result["recorded_flags"] = {
                key: deepcopy(item) for key, item in value.items()
                if key in {"gap_id", "affected_field", "blocking_for_declared_scope", "status",
                           "outcome", "code", "report_sha256", "verified_profile_sha256",
                           "report_path", "reviewer", "blocking", "severity"}
            }
        elif isinstance(value, list):
            if expand_items:
                result["items"] = [field(item, pointer + _pointer(index))
                                   for index, item in enumerate(value)]
            else:
                # Global lists repeat on every navigation page. Retain their exact
                # list reference and declared flags without enumerating each ref.
                result["recorded_blocking_item_count"] = sum(
                    isinstance(item, Mapping) and (
                        item.get("blocking_for_declared_scope") is True
                        or item.get("blocking") is True) for item in value)
        elif len(json.dumps(value, ensure_ascii=False).encode("utf-8")) <= 256:
            result["value"] = deepcopy(value)
        result["details_require_drilldown"] = (
            isinstance(value, (list, dict)) and bool(value)
        ) or (not isinstance(value, (list, dict)) and "value" not in result)
        return result

    def annotations(record: Any, pointer: str) -> list[dict]:
        result = []
        if isinstance(record, Mapping):
            for key, value in record.items():
                child = pointer + _pointer(key)
                if key in _ANNOTATION_FIELDS:
                    result.append({"field": key, **field(value, child)})
                elif key not in {"source_refs", "source_registry", "sources"}:
                    result.extend(annotations(value, child))
        elif isinstance(record, list):
            for index, value in enumerate(record):
                result.extend(annotations(value, pointer + _pointer(index)))
        return result

    def null_fields(record: Any, pointer: str) -> list[str]:
        if record is None:
            return [pointer]
        if isinstance(record, Mapping):
            return [path for key, value in record.items()
                    if key not in {"source_refs", "source_registry", "sources"}
                    for path in null_fields(value, pointer + _pointer(key))]
        if isinstance(record, list):
            return [path for index, value in enumerate(record)
                    for path in null_fields(value, pointer + _pointer(index))]
        return []

    module_field = "modules" if "modules" in profile else "components"
    module_pointers = {}
    for index, record in enumerate(profile[module_field]):
        module_id = record.get("module_id") if module_field == "modules" else (
            record.get("source_module_id") or record.get("module_id") or record.get("id")
        )
        module_pointers[module_id] = _pointer(module_field, index)

    def connection_pointer(ref: str) -> str:
        match = re.fullmatch(r"((?:topology|source_topology)\.)?"
                             r"(connections|edges|control_edges)\[(\d+)\]", ref)
        assert match is not None  # Generated solely by the validated _records path.
        return _pointer(*(match[1][:-1].split(".") if match[1] else ()),
                        match[2], int(match[3]))

    def edge_navigation(edge: dict) -> dict:
        pointer = connection_pointer(edge["connection_ref"])
        record = edge["record"]
        return {key: deepcopy(value) for key, value in edge.items() if key != "record"} | {
            "record_ref": reference(pointer),
            "condition": deepcopy(record.get("condition")),
            "condition_state": "missing" if "condition" not in record else (
                "null" if record["condition"] is None else "recorded"
            ),
            "annotations": annotations(record, pointer),
            "unresolved_field_pointers": null_fields(record, pointer),
        }

    result = {key: deepcopy(value) for key, value in trace.items() if key not in {
        "modules_requiring_recheck", "documented_requirements_to_recheck", "explanatory_paths",
        "profile_context", "unknowns", "unclassified_connections",
    }}
    result["view"] = "navigation"
    result["snapshot_sha256"] = snapshot
    result["profile_ref"] = reference("")
    result["modules_requiring_recheck"] = []
    for item in trace["modules_requiring_recheck"]:
        pointer = module_pointers[item["module_id"]]
        record = item["record"]
        result["modules_requiring_recheck"].append({
            **{key: deepcopy(value) for key, value in item.items() if key != "record"},
            "record_ref": reference(pointer),
            "label": deepcopy(record.get("label")),
            "annotations": annotations(record, pointer),
            "unresolved_field_pointers": null_fields(record, pointer),
            "state_fields": {key: field(record.get(key), pointer + _pointer(key),
                                         present=key in record)
                             for key in ("input_state", "output_state", "input_state_detail",
                                         "output_state_detail", "interfaces")},
        })
    result["documented_requirements_to_recheck"] = [
        edge_navigation(edge) for edge in trace["documented_requirements_to_recheck"]
    ]
    result["unclassified_connections"] = [
        edge_navigation(edge) for edge in trace["unclassified_connections"]
    ]
    # A predecessor forest represents the same selected shortest paths without
    # repeating every prefix. Direction and seed identity remain separate.
    result["witness_steps"] = []
    for proof in trace["explanatory_paths"]:
        last = proof["hops"][-1]
        result["witness_steps"].append({
            **{key: deepcopy(value) for key, value in proof.items() if key != "hops"},
            "predecessor_module_id": last["from_module_id"],
            "connection_ref": last["connection_ref"],
            "connection_record_ref": reference(connection_pointer(last["connection_ref"])),
            "relation": last["relation"],
            "recorded_source_module_id": last["recorded_source_module_id"],
            "recorded_target_module_id": last["recorded_target_module_id"],
        })
    result["witness_representation"] = (
        "Follow predecessor_module_id in witness_steps with the same direction until a "
        "changed seed. Connections carry the recorded conditions; branches are not evaluated."
    )
    result["profile_context"] = {
        key: (deepcopy(value) if key in {"method_id", "method_ids",
                                       "status", "scope"}
              else field(value, _pointer(key), expand_items=False))
        for key, value in trace["profile_context"].items()
    }
    result["unknowns"] = field(profile.get("unknowns"), "/unknowns",
                               present="unknowns" in profile, expand_items=False)
    result["profile_annotations"] = [
        {"field": key, **field(profile[key], _pointer(key), expand_items=False)}
        for key in sorted(_ANNOTATION_FIELDS - {"unknowns", "verification"}) if key in profile
    ]
    result["navigation_note"] = (
        "References are uninspected fields, not empty requirements or verified support. "
        "Field state describes JSON presence, not scientific completeness. Null field pointers "
        "are relative to profile_ref. "
        "Global lists expose a count and exact list reference; a zero recorded blocking "
        "count does not establish no blockers or complete review. "
        "Read relevant full module, gap/warning and evidence records before scientific "
        "assessment; get_protocol_record resolves a reference only in this graph snapshot."
    )
    return result


class _InvalidProfile(ValueError):
    pass


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _records(profile: Mapping[str, Any]) -> tuple[dict, dict, list, dict]:
    """Resolve local aliases and validate every recorded edge before traversing."""
    if "modules" in profile and "components" in profile:
        raise _InvalidProfile("profile has ambiguous modules and components representations")
    try:
        json.dumps(profile, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise _InvalidProfile("profile must contain finite JSON values") from exc
    raw = "modules" in profile
    field = "modules" if raw else "components"
    records = profile.get(field)
    if not _sequence(records):
        raise _InvalidProfile(f"{field} must be an array of complete module records")
    modules, aliases = {}, {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise _InvalidProfile(f"{field}[{index}] must be an object")
        module_id = record.get("module_id") if raw else (
            record.get("source_module_id") or record.get("module_id") or record.get("id")
        )
        if not _text(module_id) or module_id in modules:
            raise _InvalidProfile(f"{field}[{index}] has a missing or duplicate module ID")
        modules[module_id] = record
        for key in ("module_id", "source_module_id", "id"):
            if key not in record:
                continue
            alias = record[key]
            if not _text(alias):
                raise _InvalidProfile(f"{field}[{index}].{key} must be a nonblank string")
            if alias in aliases and aliases[alias] != module_id:
                raise _InvalidProfile(f"Ambiguous module alias: {alias}")
            aliases[alias] = module_id

    # Exported normalized connections are authoritative. Re-reading source_topology
    # as additional edges would duplicate them and undo explicit relation reviews.
    topology = profile.get("topology" if raw else "source_topology")
    if topology is not None and not isinstance(topology, Mapping):
        raise _InvalidProfile("topology must be an object or null")
    containers = []
    if not raw and "connections" in profile:
        containers.append(("connections", profile["connections"]))
    elif isinstance(topology, Mapping):
        for key in ("connections", "edges", "control_edges"):
            if key in topology:
                containers.append((f"{'topology' if raw else 'source_topology'}.{key}",
                                   topology[key]))
    unavailable = [path for path, values in containers if values is None]
    available = [path for path, values in containers if values is not None]
    edges = []
    for path, values in containers:
        if values is None:
            continue
        if not _sequence(values):
            raise _InvalidProfile(f"{path} must be an array or null")
        for index, record in enumerate(values):
            ref = f"{path}[{index}]"
            if not isinstance(record, Mapping):
                raise _InvalidProfile(f"{ref} must be an object")
            endpoints = []
            for keys in (("source", "from", "source_module_id"),
                         ("target", "to", "target_module_id")):
                values = [record[key] for key in keys if key in record]
                if not values or any(not _text(value) or value not in aliases
                                     for value in values):
                    raise _InvalidProfile(f"{ref} has a missing or unknown {keys[0]} endpoint")
                resolved = {aliases[value] for value in values}
                if len(resolved) != 1:
                    raise _InvalidProfile(f"{ref} has conflicting {keys[0]} endpoints")
                endpoints.append(resolved.pop())
            relations = [record[key] for key in ("relation", "kind", "type") if key in record]
            relation = relations[0] if relations else None
            if any(value is not None and not _text(value) for value in relations):
                raise _InvalidProfile(f"{ref}.relation must be a nonblank string or null")
            if any(value != relation for value in relations):
                raise _InvalidProfile(f"{ref} has conflicting relation/kind/type fields")
            edges.append({"connection_ref": ref, "source_module_id": endpoints[0],
                          "target_module_id": endpoints[1], "relation": relation,
                          "record": record})
    coverage = {
        "connection_records": ("partially_unavailable" if unavailable else
                               "available" if available else "unavailable"),
        "connection_fields": available,
        "unavailable_connection_fields": unavailable,
        "dependency_completeness": "unknown",
        "profile_architecture_coverage": (profile.get("profile_metadata") or {}).get(
            "architecture_coverage"
        ) if isinstance(profile.get("profile_metadata"), Mapping) else None,
        "unclassified_connection_count": sum(edge["relation"] is None for edge in edges),
        "recorded_relation_counts": dict(Counter(
            edge["relation"] if edge["relation"] is not None else "(unknown)" for edge in edges
        )),
    }
    return modules, aliases, edges, coverage


def trace_protocol_dependencies(
    profile: Mapping[str, Any],
    changed_module_ids: Sequence[str],
    *,
    relation_types: Sequence[str] = DEFAULT_DEPENDENCY_RELATIONS,
    max_depth: int = 8,
    direction: Literal["downstream", "upstream", "both"] = "downstream",
    max_modules: int = 100,
    max_edges: int = 500,
    view: Literal["full", "navigation"] = "full",
    snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    """Return candidate review modules and shortest recorded witness paths.

    IDs may be raw module IDs or unambiguous exported component IDs. ``both``
    unions separate upstream and downstream walks; paths never switch direction
    to reach a sibling. Conditional edges are traversed as review leads with their
    recorded conditions; the caller supplies no branch execution state.

    Module and edge bounds count distinct returned records, shared across walks.
    Depth bounds each witness path. A visited set bounds cycles; one shortest
    witness per reached module per direction avoids enumerating simple paths.
    Errors are returned without a partial or apparently complete trace.
    ``navigation`` projects records into exact JSON-pointer references. Its caller
    must bind the supplied profile to ``snapshot_sha256``; the graph query layer
    supplies and checks that immutable identity for drilldown.
    """
    def error(code: str, message: str) -> dict:
        return {"error": {"code": code, "message": message}}

    if not isinstance(profile, Mapping):
        return error("invalid_profile", "profile must be an object")
    if view not in ("full", "navigation"):
        return error("invalid_arguments", "view must be full or navigation")
    if view == "navigation" and (
        not _text(profile.get("profile_id")) or not isinstance(snapshot_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256) is None
    ):
        return error("invalid_arguments",
                     "Navigation requires profile_id and graph snapshot SHA256")
    for name, value, minimum, maximum in (
        ("max_depth", max_depth, 0, 64), ("max_modules", max_modules, 1, 1000),
        ("max_edges", max_edges, 1, 10000),
    ):
        if type(value) is not int or not minimum <= value <= maximum:
            return error("invalid_arguments", f"{name} must be an integer in {minimum}..{maximum}")
    if direction not in ("downstream", "upstream", "both"):
        return error("invalid_arguments", "direction must be downstream, upstream or both")
    if not _sequence(changed_module_ids) or not changed_module_ids or any(
        not _text(value) for value in changed_module_ids
    ):
        return error("invalid_arguments", "changed_module_ids needs nonblank module IDs")
    if not _sequence(relation_types) or any(not _text(value) for value in relation_types):
        return error("invalid_arguments", "relation_types must be an array of nonblank strings")
    if "next_in_summary" in relation_types:
        return error("invalid_arguments", "next_in_summary is presentation order, not a dependency")
    try:
        modules, aliases, edges, coverage = _records(profile)
    except _InvalidProfile as exc:
        return error("invalid_profile", str(exc))
    missing = [value for value in changed_module_ids if value not in aliases]
    if missing:
        return error("invalid_arguments", f"Unknown changed module IDs: {missing}")
    seeds = list(dict.fromkeys(aliases[value] for value in changed_module_ids))
    if len(seeds) > max_modules:
        return error("invalid_arguments", "Changed module count exceeds max_modules")
    selected = set(relation_types)
    adjacency = {name: {node: [] for node in modules} for name in ("upstream", "downstream")}
    for index, edge in enumerate(edges):
        if edge["relation"] in selected and edge["relation"] != "next_in_summary":
            adjacency["downstream"][edge["source_module_id"]].append(
                (edge["target_module_id"], index)
            )
            adjacency["upstream"][edge["target_module_id"]].append(
                (edge["source_module_id"], index)
            )

    reached = dict.fromkeys(seeds, 0)
    encountered, proofs, walks = {}, [], {}
    limits = set()
    directions = ("downstream", "upstream") if direction == "both" else (direction,)
    for walk in directions:
        # Each tree has its own depths: both-direction review must not mix edges.
        depths = dict.fromkeys(seeds, 0)
        parents, origins = {}, {seed: seed for seed in seeds}
        pending = deque(seeds)
        while pending:
            node = pending.popleft()
            for neighbor, edge_index in adjacency[walk][node]:
                if depths[node] == max_depth and neighbor not in depths:
                    limits.add("max_depth")
                    continue
                if neighbor not in reached and len(reached) == max_modules:
                    limits.add("max_modules")
                    continue
                if edge_index not in encountered:
                    if len(encountered) == max_edges:
                        limits.add("max_edges")
                        continue
                    encountered[edge_index] = edges[edge_index]
                if neighbor not in depths:
                    depth = depths[node] + 1
                    depths[neighbor] = depth
                    reached[neighbor] = min(reached.get(neighbor, depth), depth)
                    parents[neighbor] = (node, edge_index)
                    origins[neighbor] = origins[node]
                    pending.append(neighbor)
        walks[walk] = {"reached_module_ids": list(depths), "depth_by_module": depths}
        for node in depths:
            if node in seeds:
                continue
            hops, cursor = [], node
            while cursor in parents:
                parent, edge_index = parents[cursor]
                edge = edges[edge_index]
                hops.append({"connection_ref": edge["connection_ref"],
                             "from_module_id": parent, "to_module_id": cursor,
                             "recorded_source_module_id": edge["source_module_id"],
                             "recorded_target_module_id": edge["target_module_id"],
                             "relation": edge["relation"],
                             "condition": deepcopy(edge["record"].get("condition"))})
                cursor = parent
            proofs.append({"module_id": node, "changed_module_id": origins[node],
                           "direction": walk, "depth": depths[node],
                           "hops": list(reversed(hops)),
                           "reason": "Recorded path requires review; effect is unassessed."})

    requirements = [deepcopy(edge) for edge in encountered.values()]
    not_reached = [node for node in modules if node not in reached]
    annotations = profile.get("topology" if "modules" in profile else "source_topology") or {}
    unclassified = [edge for edge in edges if edge["relation"] is None]
    result = {
        "profile_id": profile.get("profile_id"),
        "changed_module_ids": seeds,
        "affected_module_ids": list(reached),
        "affected_meaning": "Recorded graph reachability identifies candidate records to recheck.",
        "modules_requiring_recheck": [
            {"module_id": node, "depth": depth, "changed": node in seeds,
             "record": deepcopy(modules[node])} for node, depth in reached.items()
        ],
        "documented_requirements_to_recheck": requirements,
        "explanatory_paths": proofs,
        "path_scope": (
            "One shortest witness per module and direction, from any changed seed; not all paths."
        ),
        "walks": walks,
        "not_reached_module_ids": not_reached[:max_modules],
        "not_reached_summary": {
            "count": len(not_reached), "ids_truncated": len(not_reached) > max_modules,
        },
        "not_reached_meaning": (
            "No returned path under these filters and bounds; independence is not established."
        ),
        "direction": direction,
        "relation_types": list(dict.fromkeys(relation_types)),
        "coverage": coverage,
        "unclassified_connections": deepcopy(unclassified[:max_edges]),
        "unclassified_connections_truncated": len(unclassified) > max_edges,
        "traversal": {
            "limits": {"max_depth": max_depth, "max_modules": max_modules, "max_edges": max_edges},
            "truncated": bool(limits), "limits_reached": sorted(limits),
            "complete_for_selected_recorded_connections": not limits,
            "returned_module_count": len(reached), "returned_connection_count": len(requirements),
            "returned_connection_counts_by_relation": dict(Counter(
                edge["relation"] for edge in requirements
            )),
        },
        "profile_context": {key: deepcopy(profile[key]) for key in (
            "profile_metadata", "method_id", "method_ids", "verification", "sources",
            "source_registry",
            "source_inspection_scopes", "prompt_provenance", "status", "scope"
        ) if key in profile},
        "unknowns": deepcopy(profile.get("unknowns")),
        "topology_annotations": {key: deepcopy(value) for key, value in annotations.items()
                                 if key not in ("connections", "edges", "control_edges")},
        "branch_condition_policy": (
            "Conditions are retained, not evaluated; each route is a review lead."
        ),
        "compatibility_verdict": "not_assessed",
        "coverage_note": (
            "Only recorded connections are traversed. Missing paths or empty connection lists "
            "do not establish independence or complete dependency coverage. Reachability does "
            "not establish "
            "experimental effects, invalidation, chemistry validity or compatibility."
        ),
    }
    return _navigation_trace(profile, result, snapshot_sha256) if view == "navigation" else result
