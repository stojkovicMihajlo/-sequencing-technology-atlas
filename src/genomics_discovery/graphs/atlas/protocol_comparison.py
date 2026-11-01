"""Recorded protocol differences conditional on explicit, tentative correspondences.

This is a literal inspection aid, not an alignment solver or a chemical validator.
It compares directed, typed multiedges and never derives edges from display order.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

_MISSING = object()
_IDENTITY = {"id", "module_id", "source_module_id"}
_PROJECTION = {
    "label",
    "description",
    "input_state_detail",
    "output_state_detail",
    "state_scope",
    "evidence_status",
    "boundary_status",
}
_UNORDERED = {"function_tags", "source_refs"}
_PROVENANCE_FIELDS = {
    "source_refs",
    "evidence_scope",
    "evidence_status",
    "verification",
    "prompt_provenance",
    "source_metadata",
    "source_sha256",
    "provenance",
    "basis",
}
# Defaults injected by the inspected component_chains._profile display projection.
# A difference in these defaults is kept as a representation observation, not
# inferred equivalence or a recorded scientific absence.
_LEGACY_DEFAULTS = {
    "purpose": None,
    "boundary_rationale": None,
    "steps": [],
    "materials": [],
    "equipment": [],
    "parameters": [],
    "interfaces": [],
    "dependencies_and_interfaces": None,
    "information_effect": None,
    "input_state": None,
    "output_state": None,
    "function_tags": [],
    "information_preserved": [],
    "evidence_scope": None,
    "unknowns": [],
}
_UNKNOWN = {
    "unknown",
    "unreported",
    "not_reported",
    "not specified",
    "not_specified",
    "uninspected",
    "not_inspected",
    "unresolved",
    "ambiguous",
    "uncertain",
}
_EDGE_IDENTITY = {
    "id",
    "source",
    "target",
    "from",
    "to",
    "source_module_id",
    "target_module_id",
    "relation",
    "kind",
    "type",
    "source_edge",
    "normalization_review",
}


def _json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _path(base: str, field: str | int) -> str:
    return base + "/" + str(field).replace("~", "~0").replace("/", "~1")


def _local_state(value: Any) -> str:
    if value is _MISSING:
        return "missing"
    if value is None or (isinstance(value, str) and not value.strip()):
        return "unknown"
    if isinstance(value, str) and value.strip().lower() in _UNKNOWN:
        return "unknown"
    if isinstance(value, dict):
        if not value:
            return "unknown"
        if any(
            isinstance(value.get(k), str) and value[k].strip().lower() in _UNKNOWN
            for k in ("status", "state", "evidence_status")
        ):
            return "unknown"
        if value.get("ambiguous") is True or value.get("unresolved") is True:
            return "unknown"
    return "recorded"


def _state(value: Any) -> str:
    state = _local_state(value)
    if state == "recorded" and isinstance(value, (dict, list)):
        children = value.values() if isinstance(value, dict) else value
        if any(_state(child) != "recorded" for child in children):
            return "partially_unknown"
    return state


def _value_record(value: Any, path: str, refs: Any = None) -> dict:
    result = {"path": path, "state": _state(value)}
    if value is not _MISSING:
        result["value"] = deepcopy(value)
    if refs is not None:
        result["source_refs"] = deepcopy(refs)
    return result


@dataclass
class _Profile:
    record: dict
    prefix: str
    modules: dict[str, dict]
    module_paths: dict[str, str]
    aliases: dict[str, str]
    exported: bool
    edges: list[dict]
    topology_state: str
    context: dict


def _records(value: Any, label: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be a list of objects")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _endpoint(edge: dict, keys: tuple[str, ...], aliases: dict, label: str) -> str:
    values = [edge[key] for key in keys if key in edge and edge[key] is not None]
    resolved = []
    for value in values:
        _string(value, label)
        if value not in aliases:
            raise ValueError(f"unknown connection endpoint at {label}: {value}")
        resolved.append(aliases[value])
    if not resolved:
        raise ValueError(f"unknown connection endpoint at {label}: missing endpoint")
    if len(set(resolved)) != 1:
        raise ValueError(f"Conflicting connection endpoint aliases at {label}")
    return resolved[0]


def _normalize(profile: dict, side: str) -> _Profile:
    if not isinstance(profile, dict):
        raise ValueError(f"{side} profile must be an object")
    try:
        _json_key(profile)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{side} profile must contain finite JSON values") from exc
    prefix, outer = "", {}
    if "profiles" in profile:
        profiles = _records(profile["profiles"], f"{side}.profiles")
        if len(profiles) != 1:
            raise ValueError(f"{side} extraction must contain exactly one profile")
        outer = {k: deepcopy(v) for k, v in profile.items() if k != "profiles"}
        profile, prefix = profiles[0], "/profiles/0"
    if "modules" in profile and "components" in profile:
        raise ValueError(f"{side} profile has ambiguous modules and components representations")
    exported = "components" in profile
    field = "components" if exported else "modules"
    records = _records(profile.get(field), f"{side}.{field}")
    modules, paths, aliases = {}, {}, {}
    for i, record in enumerate(records):
        mid = (
            record.get("source_module_id", record.get("module_id", record.get("id")))
            if exported
            else record.get("module_id")
        )
        mid = _string(mid, f"{side}.{field}[{i}] module ID")
        if mid in modules:
            raise ValueError(f"{side} has duplicate module ID: {mid}")
        modules[mid], paths[mid] = record, _path(_path(prefix, field), i)
        if exported and "source_module" in record:
            source_module = record["source_module"]
            if not isinstance(source_module, dict):
                raise ValueError(f"{side} source_module must be an object at {paths[mid]}")
            if source_module.get("module_id") != mid:
                raise ValueError(f"{side} source_module ID differs from component at {paths[mid]}")
        for alias in (
            mid,
            record.get("id"),
            record.get("module_id"),
            record.get("source_module_id"),
        ):
            if alias is None:
                continue
            alias = _string(alias, f"{side} component alias")
            if alias in aliases and aliases[alias] != mid:
                raise ValueError(f"{side} has ambiguous component/module alias: {alias}")
            aliases[alias] = mid
    topology = profile.get("topology", _MISSING)
    if exported:
        edge_lists = [("connections", profile.get("connections", _MISSING))]
    elif topology is _MISSING or topology is None:
        edge_lists = []
    elif not isinstance(topology, dict):
        raise ValueError(f"{side}.topology must be an object or null")
    else:
        edge_lists = [
            (name, topology[name])
            for name in ("edges", "connections", "control_edges")
            if name in topology
        ]
    state = (
        "recorded"
        if edge_lists and all(v is not None and v is not _MISSING for _, v in edge_lists)
        else "unknown"
    )
    edges = []
    for name, items in edge_lists:
        if items is _MISSING or items is None:
            continue
        for i, edge in enumerate(_records(items, f"{side}.{name}")):
            path = _path(_path(prefix if exported else _path(prefix, "topology"), name), i)
            source = _endpoint(
                edge, ("source_module_id", "source", "from"), aliases, f"{side}{path}/source"
            )
            target = _endpoint(
                edge, ("target_module_id", "target", "to"), aliases, f"{side}{path}/target"
            )
            # An export may retain full edge detail explicitly. Otherwise preserve
            # its own attributes and make no guesses about omitted raw attributes.
            detail = edge.get("source_edge", edge)
            if not isinstance(detail, dict):
                raise ValueError(f"{side} source_edge must be an object at {path}")
            if detail is not edge:
                for keys, endpoint in (
                    (("source_module_id", "source", "from"), source),
                    (("target_module_id", "target", "to"), target),
                ):
                    if any(k in detail for k in keys):
                        detail_endpoint = _endpoint(
                            detail, keys, aliases, f"{side}{path}/source_edge"
                        )
                        if detail_endpoint != endpoint:
                            raise ValueError(
                                f"source_edge endpoint differs from connection at {path}"
                            )
            attrs = {k: v for k, v in detail.items() if k not in _EDGE_IDENTITY}
            projection_attrs = {}
            attr_paths = {
                k: _path(_path(path, "source_edge") if detail is not edge else path, k)
                for k in attrs
            }
            if detail is not edge:
                for k, v in edge.items():
                    if k not in _EDGE_IDENTITY and (v is not None or k in attrs):
                        if k == "status" and v == "source_scoped_curator_topology":
                            continue
                        if k not in attrs and "normalization_review" not in edge:
                            projection_attrs[k] = _value_record(v, _path(path, k))
                            continue
                        attrs[k] = v
                        attr_paths[k] = _path(path, k)
            elif exported and attrs.get("status") == "source_scoped_curator_topology":
                attrs.pop("status")
            # Projection inserts a null condition even when the source did not
            # specify one; the null is still available in the preserved record.
            if (
                exported
                and attrs.get("condition", _MISSING) is None
                and (detail is edge or "condition" not in detail)
            ):
                attrs.pop("condition", None)
            relations = [edge[k] for k in ("relation", "kind", "type") if k in edge]
            if any(value is not None and not isinstance(value, str) for value in relations):
                raise ValueError(f"{side} connection relation must be a string or null at {path}")
            if len(set(relations)) > 1:
                raise ValueError(f"Conflicting connection relation aliases at {side}{path}")
            relation = edge.get("relation", edge.get("kind", edge.get("type")))
            if relation is not None and not isinstance(relation, str):
                raise ValueError(f"{side} connection relation must be a string or null at {path}")
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "path": path,
                    "attributes": attrs,
                    "attribute_paths": attr_paths,
                    "projection_attributes": projection_attrs,
                    "record": edge,
                }
            )
    context = {k: deepcopy(v) for k, v in profile.items() if k not in {"modules", "components"}}
    if outer:
        context["extraction_context"] = outer
    return _Profile(profile, prefix, modules, paths, aliases, exported, edges, state, context)


def _matches(value: Any, left: _Profile, right: _Profile) -> list[dict]:
    if value is None:
        value = []
    try:
        _json_key(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("module_matches must contain finite JSON values") from exc
    if isinstance(value, dict):
        value = [{"left_module_ids": [a], "right_module_ids": [b]} for a, b in value.items()]
    groups = _records(value, "module_matches")
    used = {"left": set(), "right": set()}
    result = []
    for i, group in enumerate(groups):
        normalized = deepcopy(group)
        for side, profile in (("left", left), ("right", right)):
            ids = group.get(side + "_module_ids")
            if not isinstance(ids, list) or not ids:
                raise ValueError(f"module_matches[{i}].{side}_module_ids must be a nonempty list")
            resolved = []
            for mid in ids:
                _string(mid, "matched module ID")
                if mid not in profile.aliases:
                    raise ValueError(f"Unknown {side} matched module ID: {mid}")
                mid = profile.aliases[mid]
                if mid in used[side]:
                    raise ValueError(f"Duplicate {side} module correspondence: {mid}")
                used[side].add(mid)
                resolved.append(mid)
            normalized[side + "_module_ids"] = resolved
        normalized["alignment_basis"] = "caller_supplied_tentative"
        normalized["comparison_supported"] = (
            len(normalized["left_module_ids"]) == len(normalized["right_module_ids"]) == 1
        )
        result.append(normalized)
    return result


def _module_values(profile: _Profile, mid: str) -> tuple[dict, dict]:
    module, base = profile.modules[mid], profile.module_paths[mid]
    if profile.exported and "source_module" in module:
        base, module = _path(base, "source_module"), module["source_module"]
        values = {k: v for k, v in module.items() if k not in _IDENTITY}
        return values, {k: _path(base, k) for k in values}
    ignored = _IDENTITY | (
        _PROJECTION | {"input_state", "output_state"} if profile.exported else set()
    )
    values = {k: v for k, v in module.items() if k not in ignored}
    paths = {k: _path(base, k) for k in values}
    if profile.exported:
        values["operation_description"] = module.get("description", module.get("label", _MISSING))
        paths["operation_description"] = _path(
            base, "description" if "description" in module else "label"
        )
        for field in ("input_state", "output_state"):
            values[field] = module.get(field + "_detail", _MISSING)
            paths[field] = _path(base, field + "_detail")
    return values, paths


def _equivalent(left: Any, right: Any, field: str) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    if field in _UNORDERED and isinstance(left, list) and isinstance(right, list):
        return Counter(map(_json_key, left)) == Counter(map(_json_key, right))
    return _json_key(left) == _json_key(right)


def _field_differences(left: Any, right: Any, left_path: str, right_path: str, field: str):
    if _equivalent(left, right, field):
        return
    # Object keys are explicit field names. Lists remain complete values: no
    # matching of list members or implicit scientific correspondence is inferred.
    if (
        isinstance(left, dict)
        and isinstance(right, dict)
        and _local_state(left) == _local_state(right) == "recorded"
    ):
        for key in sorted(left.keys() | right.keys()):
            yield from _field_differences(
                left.get(key, _MISSING),
                right.get(key, _MISSING),
                _path(left_path, key),
                _path(right_path, key),
                key,
            )
    else:
        yield left, right, left_path, right_path


def _legacy_default(profile: _Profile, mid: str, field: str, value: Any) -> bool:
    return (
        profile.exported
        and "source_module" not in profile.modules[mid]
        and field in _LEGACY_DEFAULTS
        and value is not _MISSING
        and _equivalent(value, _LEGACY_DEFAULTS[field], field)
    )


def _scientific_module(profile: _Profile, mid: str) -> dict:
    record = profile.modules[mid]
    return record.get("source_module", record) if profile.exported else record


def _module_field_path(profile: _Profile, mid: str, field: str) -> str:
    base = profile.module_paths[mid]
    if profile.exported and "source_module" in profile.modules[mid]:
        base = _path(base, "source_module")
    elif profile.exported and field in {"input_state", "output_state"}:
        field += "_detail"
    return _path(base, field)


def _attributes_key(attributes: dict) -> str:
    return _json_key(
        {
            key: sorted(value, key=_json_key)
            if key in _UNORDERED and isinstance(value, list)
            else value
            for key, value in attributes.items()
        }
    )


def _edge_record(edge: dict) -> dict:
    return {
        k: deepcopy(v)
        for k, v in edge.items()
        if k not in {"attributes", "attribute_paths", "projection_attributes"}
    }


def _module_context(profile: _Profile, mid: str) -> dict:
    module = _scientific_module(profile, mid)
    fields, paths = _module_values(profile, mid)
    return {
        "module_id": mid,
        "path": profile.module_paths[mid],
        "source_refs": deepcopy(module.get("source_refs", [])),
        "unknowns": deepcopy(module.get("unknowns", [])),
        "unknown_fields": {
            key: _value_record(value, paths[key])
            for key, value in fields.items()
            if _state(value) != "recorded"
        },
    }


def protocol_comparison_navigation(
    comparison: dict, left_profile: dict, right_profile: dict, *, snapshot_sha256: str
) -> dict:
    """Reference large exact values without changing the literal comparison distinctions.

    This is a projection of this module's comparison output over indexed profiles,
    not a generic scientific summary. The graph supplies the immutable snapshot.
    A normalization-derived value is never mislabeled as an exact source value.
    """
    profiles = {"left": left_profile, "right": right_profile}

    def reference(side, path):
        return {"profile_id": profiles[side]["profile_id"],
                "snapshot_sha256": snapshot_sha256, "json_pointer": path}

    def original(side, path):
        value = profiles[side]
        try:
            for part in path.split("/")[1:] if path else ():
                key = part.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, TypeError, ValueError):
            return _MISSING
        return value

    def exact(side, path, value):
        source = original(side, path)
        return source is not _MISSING and _json_key(source) == _json_key(value)

    def descriptor(side, path):
        value = original(side, path)
        if value is _MISSING:
            return {"state": "missing", "path": path}
        result = {"path": path, "state": _state(value), "record_ref": reference(side, path),
                  "presence": "null" if value is None else "empty" if value in ([], {})
                  else "recorded"}
        if isinstance(value, (dict, list)):
            result["count"] = len(value)
        if isinstance(value, dict):
            result["recorded_flags"] = {key: deepcopy(value[key]) for key in (
                "outcome", "status", "report_sha256", "verified_profile_sha256",
                "blocking_for_declared_scope", "affected_field", "gap_id",
            ) if key in value}
        if not isinstance(value, (list, dict)):
            result["value"] = deepcopy(value)
        return result

    def scientific_base(side, path):
        parts = path.split("/")
        if len(parts) >= 3 and parts[1] in {"modules", "components"}:
            base = "/" + "/".join(parts[1:3])
            if isinstance(original(side, base), dict) and "source_module" in original(side, base):
                base += "/source_module"
            return base
        return None

    def transform(value, side=None):
        if isinstance(value, list):
            return [transform(item, side) for item in value]
        if not isinstance(value, dict):
            return deepcopy(value)
        side = value.get("side", side)
        if side in profiles and {"path", "record", "source", "target", "relation"} <= value.keys():
            result = {key: deepcopy(item) for key, item in value.items() if key != "record"}
            if not exact(side, value["path"], value["record"]):
                raise ValueError("Comparison connection reference differs from original record")
            result["record_ref"] = reference(side, value["path"])
            result["condition"] = deepcopy(value["record"].get("condition"))
            result["condition_presence"] = "missing" if "condition" not in value["record"] else (
                "null" if value["record"]["condition"] is None else "recorded"
            )
            result["annotations"] = {key: descriptor(side, _path(value["path"], key))
                                     for key in ("unknowns", "warnings", "normalization_review")}
            if "source_edge" in value["record"]:
                result["source_edge_ref"] = reference(side, _path(value["path"], "source_edge"))
                result["source_edge_annotations"] = {
                    key: descriptor(side, _path(_path(value["path"], "source_edge"), key))
                    for key in ("unknowns", "warnings")
                    if key in value["record"]["source_edge"]
                }
                source_condition = value["record"]["source_edge"].get("condition", _MISSING)
                if source_condition is not _MISSING and not _equivalent(
                    source_condition, value["record"].get("condition", _MISSING), "condition"
                ):
                    result["source_condition"] = deepcopy(source_condition)
            return result
        if side in profiles and "path" in value:
            result = deepcopy(value)
            base = scientific_base(side, value["path"])
            if "value" in value and not exact(side, value["path"], value["value"]):
                result["normalization_note"] = (
                    "Compared value differs from the source pointer; retained in full."
                )
                if base:
                    result["source_record_ref"] = reference(side, base)
            if "value" in value and (
                isinstance(value["value"], (dict, list))
                or len(_json_key(value["value"]).encode("utf-8")) > 256
            ):
                if exact(side, value["path"], value["value"]):
                    result.pop("value")
                    result["value_ref"] = reference(side, value["path"])
                    result["value_bytes"] = len(_json_key(value["value"]).encode("utf-8"))
            for key in ("source_refs", "unknowns"):
                if key in value and base and exact(side, _path(base, key), value[key]):
                    result[key] = descriptor(side, _path(base, key))
            if "module_id" in value and original(side, value["path"]) is not _MISSING:
                result["record_ref"] = reference(side, value["path"])
            return {key: item if key in {"value", "source_refs", "unknowns"}
                    else transform(item, side) for key, item in result.items()}
        return {key: transform(item, key if key in profiles else
                               "left" if key.startswith("left_connection") else
                               "right" if key.startswith("right_connection") else side)
                for key, item in value.items()}

    result = {key: deepcopy(value) for key, value in comparison.items()
              if key not in {"profiles", "differences", "compared_module_context",
                             "excluded_presentation_connections"}}
    result["view"] = "navigation"
    result["snapshot_sha256"] = snapshot_sha256
    result["profiles"] = {
        side: {"profile_id": profile["profile_id"], "profile_ref": reference(side, ""),
               "profile_metadata": descriptor(side, "/profile_metadata"),
               "unknowns": descriptor(side, "/unknowns"),
               "warnings": descriptor(side, "/warnings"),
               "verification": descriptor(side, "/verification")}
        for side, profile in profiles.items()
    }
    result["compared_module_context"] = transform(comparison["compared_module_context"])
    result["excluded_presentation_connections"] = transform(
        comparison["excluded_presentation_connections"]
    )
    result["records"] = [{"record_type": "difference", **transform(row)}
                         for row in comparison["differences"]]
    result["navigation_note"] = (
        "All literal difference distinctions are retained. Summary counts describe all "
        "generated differences; pagination describes this page. Large exact values and "
        "source/context details require drilldown; references are uninspected fields, not "
        "scientific equivalence or support. Missing fields have no fabricated exact reference."
    )
    return result


def compare_protocol_graphs(
    left_profile: dict,
    right_profile: dict,
    *,
    module_matches: dict[str, str] | list[dict] | None = None,
    max_differences: int | None = 200,
) -> dict:
    """Compare recorded fields and typed directed connections without inferring alignment.

    Inputs are single extraction profiles or exported component profiles. A bundle
    containing exactly one profile is also accepted to preserve its source context.
    Matches are a mapping ``{left_id: right_id}`` or groups with ``left_module_ids``
    and ``right_module_ids`` lists. Split/merge groups are preserved but detailed
    comparison is unsupported. Any caller status is metadata; alignment is always
    labeled tentative. JSON pointers address the original input records.

    ``max_differences=None`` returns all complete difference records for callers
    providing their own bounded transport. The default bounds record count, not
    bytes. Missing/null/explicit unknown fields cannot establish scientific absence.
    """
    if max_differences is not None and (type(max_differences) is not int or max_differences < 1):
        raise ValueError("max_differences must be a positive integer or None")
    left, right = _normalize(left_profile, "left"), _normalize(right_profile, "right")
    groups = _matches(module_matches, left, right)
    differences, counts = [], Counter()

    def add(kind: str, **detail: Any) -> None:
        counts[kind] += 1
        if max_differences is None or len(differences) < max_differences:
            differences.append({"kind": kind, **deepcopy(detail)})

    paired, compared_modules = {}, []
    grouped = {"left": set(), "right": set()}
    for group in groups:
        a, b = group["left_module_ids"], group["right_module_ids"]
        grouped["left"].update(a)
        grouped["right"].update(b)
        if not group["comparison_supported"]:
            add(
                "split_merge_comparison_unsupported",
                match=group,
                reason="No action/interface correspondence within this group was supplied.",
            )
            continue
        aid, bid = a[0], b[0]
        paired[aid] = bid
        compared_modules.append(
            {"left": _module_context(left, aid), "right": _module_context(right, bid)}
        )
        av, ap = _module_values(left, aid)
        bv, bp = _module_values(right, bid)
        for field in sorted(av.keys() | bv.keys()):
            old, new = av.get(field, _MISSING), bv.get(field, _MISSING)
            representation = (old is _MISSING and _legacy_default(right, bid, field, new)) or (
                new is _MISSING and _legacy_default(left, aid, field, old)
            )
            for old_value, new_value, old_path, new_path in _field_differences(
                old,
                new,
                ap.get(field, _module_field_path(left, aid, field)),
                bp.get(field, _module_field_path(right, bid, field)),
                field,
            ):
                uncertain = _state(old_value) != "recorded" or _state(new_value) != "recorded"
                if representation:
                    kind = "module_representation_difference"
                    interpretation = (
                        "A legacy export supplies a schema default "
                        "where source content is missing. "
                        "No scientific absence or equivalence is established."
                    )
                elif field in _PROVENANCE_FIELDS:
                    kind = "module_provenance_changed"
                    interpretation = (
                        "Source/evidence metadata differs; this is not an action change."
                    )
                else:
                    kind = "module_field_uncertain" if uncertain else "module_field_changed"
                    interpretation = (
                        "Recorded values differ; functional equivalence is not assessed."
                    )
                add(
                    kind,
                    field=field,
                    uncertain=uncertain,
                    left_module_id=aid,
                    right_module_id=bid,
                    left=_value_record(
                        old_value, old_path, _scientific_module(left, aid).get("source_refs")
                    ),
                    right=_value_record(
                        new_value, new_path, _scientific_module(right, bid).get("source_refs")
                    ),
                    interpretation=interpretation,
                )
    for side, profile in (("left", left), ("right", right)):
        for mid in sorted(profile.modules.keys() - grouped[side]):
            values, paths = _module_values(profile, mid)
            add(
                "unaligned_module",
                side=side,
                module_id=mid,
                path=profile.module_paths[mid],
                operation=_value_record(
                    values.get("operation_description", _MISSING),
                    paths.get(
                        "operation_description",
                        _path(profile.module_paths[mid], "operation_description"),
                    ),
                ),
                source_refs=_scientific_module(profile, mid).get("source_refs", []),
                unknowns=_scientific_module(profile, mid).get("unknowns", []),
                interpretation="No correspondence was supplied; inspect the original module.",
            )

    inverse = {v: k for k, v in paired.items()}
    buckets = {"left": defaultdict(list), "right": defaultdict(list)}
    excluded = {"left": [], "right": []}
    unknown_relation_endpoints = set()
    for side, profile, mapping in (("left", left, paired), ("right", right, inverse)):
        for edge in profile.edges:
            if edge["relation"] == "next_in_summary":
                excluded[side].append(_edge_record(edge))
                continue
            if edge["source"] not in mapping or edge["target"] not in mapping:
                add(
                    "unaligned_connection",
                    side=side,
                    connection=_edge_record(edge),
                    interpretation="At least one endpoint lacks a supported one-to-one alignment.",
                )
                continue
            if _state(edge["relation"]) != "recorded":
                add("connection_relation_uncertain", side=side, connection=_edge_record(edge))
                unknown_relation_endpoints.add(
                    (edge["source"], edge["target"])
                    if side == "left"
                    else (mapping[edge["source"]], mapping[edge["target"]])
                )
                continue
            source = edge["source"] if side == "left" else mapping[edge["source"]]
            target = edge["target"] if side == "left" else mapping[edge["target"]]
            buckets[side][(source, target, edge["relation"])].append(edge)
    topology_known = left.topology_state == right.topology_state == "recorded"
    if not topology_known:
        add(
            "topology_comparison_uncertain",
            left_state=left.topology_state,
            right_state=right.topology_state,
            reason="Missing or null connection collections are not observed absence.",
        )
    else:
        for key in sorted(buckets["left"].keys() | buckets["right"].keys()):
            a, b = buckets["left"][key], buckets["right"][key]
            source, target, relation = key
            detail = {
                "relation": relation,
                "left_endpoints": {"source": source, "target": target},
                "right_endpoints": {"source": paired[source], "target": paired[target]},
            }
            if (source, target) in unknown_relation_endpoints:
                continue
            if len(a) != len(b):
                add(
                    "connection_count_changed",
                    **detail,
                    left={"count": len(a), "connections": list(map(_edge_record, a))},
                    right={"count": len(b), "connections": list(map(_edge_record, b))},
                    interpretation=(
                        "Counts of recorded connections differ, conditional on alignment."
                    ),
                )
            projection_fields = {field for edge in a + b for field in edge["projection_attributes"]}
            for field in sorted(projection_fields):
                left_annotations = [
                    edge["projection_attributes"][field]
                    for edge in a
                    if field in edge["projection_attributes"]
                ]
                right_annotations = [
                    edge["projection_attributes"][field]
                    for edge in b
                    if field in edge["projection_attributes"]
                ]
                if Counter(_json_key(item["value"]) for item in left_annotations) == Counter(
                    _json_key(item["value"]) for item in right_annotations
                ):
                    continue
                add(
                    "connection_representation_difference",
                    **detail,
                    field=field,
                    left={"projection_annotations": left_annotations},
                    right={"projection_annotations": right_annotations},
                    interpretation=(
                        "Normalized projection annotations are absent from retained "
                        "source edges; no source attribute equivalence is inferred."
                    ),
                )
            # Cancel identical attribute multisets without collapsing parallel edges.
            remaining = list(b)
            residual_a = []
            for edge in a:
                index = next(
                    (
                        i
                        for i, other in enumerate(remaining)
                        if _attributes_key(edge["attributes"])
                        == _attributes_key(other["attributes"])
                    ),
                    None,
                )
                if index is None:
                    residual_a.append(edge)
                else:
                    remaining.pop(index)
            if len(residual_a) == len(remaining) == 1:
                old, new = residual_a[0], remaining[0]
                for field in sorted(old["attributes"].keys() | new["attributes"].keys()):
                    av, bv = (
                        old["attributes"].get(field, _MISSING),
                        new["attributes"].get(field, _MISSING),
                    )
                    if _equivalent(av, bv, field):
                        continue
                    for avalue, bvalue, apath, bpath in _field_differences(
                        av,
                        bv,
                        old["attribute_paths"].get(field, _path(old["path"], field)),
                        new["attribute_paths"].get(field, _path(new["path"], field)),
                        field,
                    ):
                        uncertain = _state(avalue) != "recorded" or _state(bvalue) != "recorded"
                        if field in _PROVENANCE_FIELDS:
                            kind = "connection_provenance_changed"
                        else:
                            kind = (
                                "connection_attributes_uncertain"
                                if uncertain
                                else "connection_attributes_changed"
                            )
                        add(
                            kind,
                            **detail,
                            field=field,
                            uncertain=uncertain,
                            left=_value_record(avalue, apath),
                            right=_value_record(bvalue, bpath),
                            left_connection=_edge_record(old),
                            right_connection=_edge_record(new),
                        )
            elif residual_a and remaining:
                add(
                    "parallel_connection_attributes_unaligned",
                    **detail,
                    left_connections=list(map(_edge_record, residual_a)),
                    right_connections=list(map(_edge_record, remaining)),
                    reason="Parallel edge attribute correspondence is ambiguous.",
                )
    total = sum(counts.values())
    unaligned = {
        side: sorted(profile.modules.keys() - grouped[side])
        for side, profile in (("left", left), ("right", right))
    }
    return {
        "format_version": "protocol_graph_comparison/1.0",
        "alignment": {
            "status": "unaligned"
            if not groups
            else "partial"
            if any(unaligned.values()) or any(not g["comparison_supported"] for g in groups)
            else "supplied_complete",
            "basis": "caller_supplied_tentative",
            "matches": deepcopy(groups),
            "one_to_one_count": len(paired),
            "unaligned_module_ids": unaligned,
        },
        "profiles": {"left": deepcopy(left.context), "right": deepcopy(right.context)},
        "compared_module_context": compared_modules,
        "differences": differences,
        "summary": {
            "difference_counts": dict(sorted(counts.items())),
            "total_differences": total,
            "returned_differences": len(differences),
            "truncated": len(differences) < total,
            "max_differences": max_differences,
        },
        "excluded_presentation_connections": excluded,
        "limitations": [
            "Correspondences are supplied and tentative; names and IDs never infer alignment.",
            "Recorded JSON values are compared literally; "
            "semantic or unit equivalence is not inferred.",
            "Missing, null and explicitly uncertain values "
            "preserve uncertainty, not scientific absence.",
            "Only direct recorded connections are compared; "
            "display order does not imply dependencies.",
            "Unaligned modules and split/merge groups do not establish new or removed technology.",
            "No novelty score, historical edge, chemical validity, "
            "feasibility or performance is inferred.",
            "Export projections may omit source attributes; "
            "full retained source metadata remains context.",
            "Provenance and known projection defaults are distinguished from action/interface "
            "differences; their recorded values do not establish scientific equivalence.",
            "The record count bound is not a byte bound; "
            "individual source records remain complete.",
        ],
    }
