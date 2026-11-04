"""Shared scientific relation meanings and read-only access to reviewed pair pilots.

This module does not infer history, merge method identities, or admit new claims.
The CLI requires the caller's recorded graph hash. It returns the claim together
with its exact anchors, review, qualifications and workflow boundary context.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from genomics_discovery.illumina.graph import operation_group


RELATIONS = {
    "method_development": {
        "layer": "history",
        "meaning": "The later implementation explicitly develops/adapts the earlier assay architecture. Identify the retained architecture and the actual changes; a shared reagent, citation, comparator or prior date is insufficient.",
    },
    "component_adoption": {
        "layer": "history",
        "meaning": "The later implementation explicitly adopts/adapts a specified earlier component or procedure, independently supported in the earlier work. Name the component and its destination; do not expand this to inheritance of its enclosing operation or whole assay.",
    },
    "mechanistic_precedent": {
        "layer": "precedent",
        "meaning": "The earlier work demonstrates a specified relevant experimental property or mechanism. Direct adoption in the later assay is not established. State substrate/context/implementation differences; this is not ancestry or proven transferability.",
    },
    "comparison": {
        "layer": "comparison",
        "meaning": "A paper explicitly compares the implementations or their results. Identify the comparator and conditions. A comparison, or reported advantage, does not establish adoption or method ancestry.",
    },
    "shared_mechanism": {
        "layer": "correspondence",
        "meaning": "Both implementations support the specified physical transformation at the stated scope. Record input, output and consequential context differences. The same enzyme or operation name alone is insufficient; no interchangeability or ancestry is asserted.",
    },
    "shared_purpose": {
        "layer": "correspondence",
        "meaning": "The scoped operations serve a comparable evidenced function; the same mechanism need not be established. Preserve material, readout and context differences. This is not chemical equivalence or ancestry.",
    },
    "background": {"layer": "excluded", "meaning": "Context only; retain the citation disposition but do not publish a methodological connection."},
    "no_established_relation": {"layer": "excluded", "meaning": "The considered relation is unsupported; retain the disposition without forcing an edge."},
}
HISTORICAL_RELATIONS = frozenset(k for k, v in RELATIONS.items() if v["layer"] == "history")


def relation_instructions(kinds):
    """The same definitions feed both citation following and paired comparison."""
    return "RELATION DEFINITIONS\n" + "\n".join(f"{k} [{RELATIONS[k]['layer']}]: {RELATIONS[k]['meaning']}" for k in kinds)


CHANGE_INSTRUCTIONS = """
RECORDING A CHANGE
Start from the existing Illumina operation groups. Use the primary papers to
locate the specific contribution, not to expand every group into a full recipe.
Name the retained component/transformation, what differs before versus after,
and its input/output, compartment or information-carrier consequence when relevant.
For a component inside a combined operation, explicitly limit the assertion to
that component. Multiple changes or contributors are allowed; do not invent one
parent or a linear tree. A method outside the catalogue may remain external.

Separate a documented difference from an experimentally demonstrated benefit.
For any claimed gain, record the metric, comparator and tested context in the
gain/consequence field. If these are not established, say so; do not infer a gain
from the method's name, publication date or the presence of a new operation.
Describe compatibility only to the extent demonstrated; a component's existence
does not establish that it works with a different substrate or downstream stage.

ASSAY-SPECIFIC INTERPRETATION
For the claimed change, make clear which of the following actually changes and
which is retained or unknown. These are comparison questions, not mandatory
protocol stages, and not every item needs a change in every claim:
- Biological quantity and unit: what is inferred about molecules, cells, locations
  or time windows? An omics category alone is not the measurement definition.
- Physical selection/recording: what interaction or transformation makes that
  quantity observable, and what molecules actually reach the instrument? Do not
  confuse an RNA target with a DNA reporter or assume all readouts use barcodes.
- Attribution: what links the readout to the original molecule, cell, location or
  contact after handling/pooling? Name the physical carrier when established.
- Interfaces: which substrate/end-group, attachment, compartment or retained
  feature must the next stage accept? Conditions/equipment matter when they
  determine this interface; routine recipe expansion is not the objective.
- Measurement consequences: what specificity, recovery, bias, resolution or
  quantitative interpretation changes in the reported setting? Keep measured
  results separate from author rationale and an untested mechanistic explanation.

For historical claims, inspect the later citation context AND the earlier
demonstration. Use unresolved to record missing version/date or priority evidence.
Journal publication order alone may miss a preprint or earlier protocol. Do not
claim earliest invention from the earliest retrieved reference. Explicit author
attribution supports a scoped transfer, not necessarily a complete history.

DETAIL ADEQUACY IS CLAIM-SPECIFIC
Keep the diagram level when a source-backed annotation can locate the change
unambiguously. Request finer extraction only when different unrepresented
operations, branches or material/information states would change this claim.
State precisely which distinction is missing and which source could resolve it.
A gap nonblocking for the depicted route may block a UMI, compatibility, novelty
or substitution claim. Do not inherit the workflow's supported verdict as a
blanket permission to make those claims. Do not fill unrelated routine recipes.
"""


def _checked_items(graph):
    if graph.get("format_version") != "illumina_method_pair/1":
        raise ValueError("Expected a reviewed paired-workflow overlay")
    for binding in graph["bindings"].values():
        card = graph["source_graph"]["cards"][binding["record_id"]]
        if card["artifact_pins"]["extraction.json"] != binding["extraction_sha256"]:
            raise ValueError("Workflow binding disagrees with its original extraction")
    items, seen = [], set()
    for section in ("historical_links", "correspondences"):
        for item in graph[section]:
            claim, review = item["claim"], item["review"]
            ident, kind = claim["id"], claim["relation"]
            if ident in seen or kind not in RELATIONS or RELATIONS[kind]["layer"] == "excluded":
                raise ValueError("Duplicate claim or unknown/unpublishable relation")
            seen.add(ident)
            historical = kind in HISTORICAL_RELATIONS
            if historical != (section == "historical_links"):
                raise ValueError("Relation is in the wrong graph layer")
            directions = {"left_to_right", "right_to_left"} if historical else {"undirected"}
            if claim["direction"] not in directions:
                raise ValueError("Direction disagrees with relation meaning")
            if (review["claim_id"] != ident or review["decision"] != "accept" or
                    item.get("status") != "model_reviewed_pair_pilot"):
                raise ValueError("Missing matching accepted pilot review")
            issues = review["unresolved_assessments"]
            if (sorted(i["issue_index"] for i in issues) != list(range(len(claim["unresolved"]))) or
                    any(i["blocks_relation"] for i in issues)):
                raise ValueError("Missing qualification assessment or blocking issue")
            for side in ("left", "right"):
                if not claim[side + "_evidence"]:
                    raise ValueError("Missing one side of the evidence")
                allowed = set(graph["bindings"][side]["paper_source_ids"])
                if any(e["source_id"] not in allowed or not e["quote"].strip()
                       for e in claim[side + "_evidence"]):
                    raise ValueError("Evidence has a wrong-side source or empty quote")
                binding = graph["bindings"][side]
                operation_group(graph["source_graph"], binding["record_id"], claim[side + "_operation_ids"])
            items.append(item)
    return items


def load_reviewed_pair(path: Path, expected_sha256: str):
    """The expected hash must come from the stored run/receipt, not a model guess."""
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Relationship artifact differs from its recorded hash")
    graph = json.loads(data)
    _checked_items(graph)
    return graph


def relationship_neighbors(graph, record_id, *, layer="history"):
    if layer not in {"history", "precedent", "comparison", "correspondence", "all"}:
        raise ValueError("Unknown relationship layer")
    if record_id not in {s["record_id"] for s in graph["bindings"].values()}:
        raise ValueError("Record is not bound to this reviewed overlay")
    rows = []
    for item in _checked_items(graph):
        c = item["claim"]
        rule = RELATIONS[c["relation"]]
        if layer != "all" and rule["layer"] != layer:
            continue
        left, right = (graph["bindings"][s]["record_id"] for s in ("left", "right"))
        source, target = ((left, right) if c["direction"] == "left_to_right" else
                          (right, left) if c["direction"] == "right_to_left" else (None, None))
        rows.append({"claim_id": c["id"], "relation": c["relation"], "layer": rule["layer"],
                     "source": source, "target": target, "participants": [left, right],
                     "scope": c["shared_description"], "status": item["status"]})
    return {"record_id": record_id, "layer": layer, "relationships": rows,
            "boundary": "Only explicit reviewed claims from this pair; not an exhaustive method neighborhood or canonical identity merge."}


def relationship_packet(graph, claim_id):
    item = next((x for x in _checked_items(graph) if x["claim"]["id"] == claim_id), None)
    if item is None:
        raise KeyError("No admitted claim with this exact ID")
    claim = item["claim"]
    groups = {}
    for side in ("left", "right"):
        group = operation_group(graph["source_graph"], graph["bindings"][side]["record_id"],
                                claim[side + "_operation_ids"])
        provenance = group.pop("provenance")
        referenced = set()

        def collect(value):
            if isinstance(value, dict):
                if "source_id" in value:
                    referenced.add(value["source_id"])
                for nested in value.values():
                    collect(nested)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)

        collect(group)
        referenced.update(e["source_id"].removeprefix(side + ":") for e in claim[side + "_evidence"])
        fields = {"kind", "content_level", "path", "sha256", "pmid", "title", "url"}
        group["provenance"] = {sid: {k: copy.deepcopy(v) for k, v in source.items() if k in fields}
                               for sid, source in provenance.items() if sid in referenced}
        group["provenance_projection"] = (
            "Only sources referenced in this context are indexed here. Full custody records remain "
            "at full_source_registry_artifact; evidence and selected workflow records are unchanged."
        )
        groups[side] = group
    return {
        "format_version": "illumina_relationship_context/1",
        "claim": copy.deepcopy(claim), "review": copy.deepcopy(item["review"]),
        "relation_definition": copy.deepcopy(RELATIONS[claim["relation"]]),
        "bindings": copy.deepcopy(graph["bindings"]),
        "groups": groups,
        "diagram_adequacy": copy.deepcopy(graph["diagram_adequacy"]),
        "analysis_sha256": graph["analysis_sha256"],
        "status": item["status"],
        "boundary": "Read-only retrieval of a previously reviewed pilot claim. Source quotes were checked in that run; "
                    "this query does not reread the papers. Component scope must not expand to its enclosing operation. "
                    "Shared mechanism is not interchangeability. New design transfers remain hypotheses. "
                    "Dates/priority and canonical method identity are not newly verified here.",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--graph", type=Path, required=True)
    p.add_argument("--sha256", required=True)
    choice = p.add_mutually_exclusive_group(required=True)
    choice.add_argument("--record-id")
    choice.add_argument("--claim-id")
    p.add_argument("--layer", default="history", choices=["history", "precedent", "comparison", "correspondence", "all"])
    args = p.parse_args()
    g = load_reviewed_pair(args.graph, args.sha256)
    result = relationship_packet(g, args.claim_id) if args.claim_id else relationship_neighbors(g, args.record_id, layer=args.layer)
    print(json.dumps(result, ensure_ascii=False, indent=2))
