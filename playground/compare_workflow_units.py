"""Replay a source-bound exploratory comparison without changing atlas records.

Selections and scientific interpretations live in the comparison spec. This
script checks their record bindings, exposes whole-unit boundaries, and reuses
the existing literal graph comparator. It does not discover or approve matches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from genomics_discovery.knowledge.protocol_comparison import compare_protocol_graphs
from genomics_discovery.knowledge.protocol_dependencies import _records

FLOW_RELATIONS = {"material", "information", "control", "ordering_requirement"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def unit_boundary(profile: dict, module_ids: list[str]) -> dict:
    """Inspect raw extraction units; display order and missing edges never imply flow."""
    if "modules" not in profile:
        raise ValueError("Unit inspection requires the raw source extraction profile")
    modules, _, edges, coverage = _records(profile)
    selected = set(module_ids)
    if not selected or len(selected) != len(module_ids) or selected - modules.keys():
        raise ValueError("Unit selection contains empty, duplicate or unknown module IDs")
    internal, incoming, outgoing, unclassified = [], [], [], []
    neighbours = {mid: set() for mid in selected}
    for edge in edges:
        a, b = edge["source_module_id"], edge["target_module_id"]
        if a not in selected and b not in selected:
            continue
        if edge["relation"] not in FLOW_RELATIONS:
            unclassified.append(edge)
            continue
        if a in selected and b in selected:
            internal.append(edge)
            neighbours[a].add(b)
            neighbours[b].add(a)
        elif b in selected:
            incoming.append(edge)
        else:
            outgoing.append(edge)
    reached, pending = set(), [module_ids[0]]
    while pending:
        mid = pending.pop()
        if mid not in reached:
            reached.add(mid)
            pending.extend(neighbours[mid] - reached)
    material = [e for e in internal if e["relation"] == "material"]
    material_sources = {e["source_module_id"] for e in material}
    material_targets = {e["target_module_id"] for e in material}
    pointers = {m["module_id"]: f"/modules/{i}" for i, m in enumerate(profile["modules"])}
    return {
        "module_ids": module_ids,
        "recorded_weakly_connected": reached == selected,
        "connectivity_is_not_execution_or_compatibility": True,
        "internal_connections": internal,
        "incoming_connections": incoming,
        "outgoing_connections": outgoing,
        "unclassified_or_nonflow_connections": unclassified,
        "material_entry_modules": sorted(selected - material_targets),
        "material_exit_modules": sorted(selected - material_sources),
        "states": [{"module_id": mid, "operation": modules[mid].get("operation_description"),
                    "record_ref": {"profile_id": profile["profile_id"],
                                   "json_pointer": pointers[mid]},
                    "fields_present": sorted(modules[mid]),
                    "input": modules[mid].get("input_state"),
                    "output": modules[mid].get("output_state"),
                    "input_requirements": modules[mid].get("input_requirements"),
                    "interfaces": modules[mid].get("interfaces"),
                    "dependencies_and_interfaces": modules[mid].get("dependencies_and_interfaces"),
                    "information_effect": modules[mid].get("information_effect"),
                    "unknowns": modules[mid].get("unknowns", []),
                    "source_refs": modules[mid].get("source_refs", [])}
                   for mid in module_ids],
        "connection_coverage": coverage,
            "limitation": "Boundaries are recorded cuts, not complete experimental interfaces. "
                      "External reagents, conditions and unrecorded dependencies remain relevant. "
                      "Read complete source modules through record_ref for steps and other fields.",
    }


def compare(manifest: dict, spec: dict) -> dict:
    profiles, inventory, bindings = {}, [], []
    for item in manifest["protocols"]:
        path = Path(item["extraction_path"])
        if digest(path) != item["extraction_sha256"]:
            raise ValueError(f"Changed extraction: {path}")
        document = read(path)
        profile = document["profiles"][0]
        if profile["profile_id"] != item["profile_id"]:
            raise ValueError("Profile identity differs from frozen manifest")
        mid = item["method_id"]
        if mid in profiles:
            raise ValueError("Duplicate corpus method")
        profiles[mid] = profile
        source = item["primary_source"]["source_metadata"]
        hashes = source["sha256"]
        if not isinstance(hashes, dict):
            raise ValueError("Comparison requires a prepared source hash map")
        for file, expected in hashes.items():
            if digest(Path(file)) != expected:
                raise ValueError(f"Changed primary source: {file}")
            bindings.append({"path": file, "sha256": expected})
        modules, _, edges, coverage = _records(profile)
        inventory.append({"method_id": mid, "profile_id": profile["profile_id"],
                          "module_count": len(modules), "connection_count": len(edges),
                          "review_at_selection": item["review_at_selection"],
                          "connection_coverage": coverage,
                          "modules": [{"module_id": key,
                                       "operation": m.get("operation_description"),
                                       "input": m.get("input_state"),
                                       "output": m.get("output_state")}
                                      for key, m in modules.items()]})
    units = {}
    for selection in spec["units"]:
        uid = selection["unit_id"]
        if uid in units:
            raise ValueError("Duplicate unit identity")
        source = next(item for item in manifest["protocols"]
                      if item["method_id"] == selection["method_id"])
        units[uid] = {**selection,
                      "source_record": {"path": source["extraction_path"],
                                        "sha256": source["extraction_sha256"],
                                        "json_pointer": "/profiles/0"},
                      "boundary": unit_boundary(profiles[selection["method_id"]],
                                                selection["module_ids"])}
    results = []
    for case in spec["comparisons"]:
        left, right = units[case["left_unit_id"]], units[case["right_unit_id"]]
        literal = compare_protocol_graphs(
            profiles[left["method_id"]], profiles[right["method_id"]],
            module_matches=[{"left_module_ids": left["module_ids"],
                             "right_module_ids": right["module_ids"],
                             "reason": case["selection_reason"]}],
            max_differences=50,
        )
        results.append({**case, "literal_inspection": literal,
                        "interpretation_authority": "Human-readable analyst hypothesis in spec; "
                        "literal comparison does not validate scientific correspondence."})
    return {"format_version": "empirical_workflow_comparison/1.0",
            "scope": manifest["scope"], "inventory": inventory,
            "source_file_bindings": bindings, "units": list(units.values()),
            "comparisons": results,
            "summary": {"methods": len(inventory),
                        "modules": sum(x["module_count"] for x in inventory),
                        "units": len(units), "comparisons": len(results),
                        "connected_units": sum(u["boundary"]["recorded_weakly_connected"]
                                               for u in units.values()),
                        "selection_granularity": dict(Counter(
                            str(len(u["module_ids"])) for u in units.values()))},
            "limits": ["Purposeful exploratory sample, not a generalization benchmark.",
                       "Draft extraction comparisons do not establish full protocol acceptance.",
                       "Source-bound interpretations require reading the evidence ledger.",
                       "No ancestry, novelty, performance or transfer compatibility is inferred."]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = compare(read(args.manifest), read(args.spec))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result["summary"]))


if __name__ == "__main__":
    main()
