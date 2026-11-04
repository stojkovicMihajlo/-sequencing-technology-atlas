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
        "meaning": "The later work identifies a particular earlier assay implementation as a starting point and documents its revision into the later assay. The claimed inheritance is an assay strategy or arrangement, not merely use of an isolated contribution.",
        "required": (
            "Identify both assay implementations and the later source passage that establishes development from the earlier one. An explicit procedural attribution can establish this; the exact word 'developed' is not required.",
            "Describe the retained experimental strategy or connected arrangement specifically, then the supported earlier-to-later changes. Check the earlier description in the cited work; do not reconstruct it from the later paper alone.",
            "Name the affected portion of the workflow. Several coordinated replacements are allowed; there is no required percentage of shared operations or identical biological application.",
        ),
        "exclusions": (
            "A shared assay family, name, reagent, goal, publication date, background citation or performance comparison does not establish development.",
            "Developing a method does not imply improved performance, inheritance of every component, a unique parent, or the earliest invention.",
        ),
    },
    "component_adoption": {
        "layer": "history",
        "meaning": "The later work attributes use or adaptation of an identifiable experimental contribution to the cited earlier work, which independently demonstrates that contribution at the claimed scope. Only that contribution is inherited.",
        "required": (
            "Name the adopted contribution and the demonstrated property or procedure being used: for example a reagent design, oligonucleotide feature, capture mechanism, preparation or analysis procedure. A component can be smaller than an Operation or span several Operations; it is not defined by size or a fixed primitive vocabulary.",
            "Locate the later source's attribution and actual use, and verify the corresponding earlier demonstration. Mere availability of an enzyme or a similar earlier experiment establishes at most a precedent.",
            "Describe the destination and function in the later workflow, what was retained, and any supported adaptation or changed context. Unchanged adoption is valid; do not invent a design change or a benefit.",
        ),
        "exclusions": (
            "Do not expand adoption of a component to its enclosing Operation or entire earlier assay. Shared purpose, identical names and structural similarity are insufficient.",
            "A demonstrated reagent class does not establish identity of every commercial construct, substrate compatibility, or transfer of its reported performance. Keep the claim at the supported scope.",
        ),
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

HISTORY_DECISION_RULES = """
HISTORICAL DECISION RULES
Direction is earlier contribution -> later implementation, justified by attribution
and source identity, never by chronology alone. First identify what the citation
actually attributes. If an earlier assay is the documented starting point and the
retained arrangement and revisions are established, use method_development. If
only a particular contribution is demonstrably adopted, use component_adoption.
If neither is supported, use a justified nonhistorical relation or withhold it.
Do not select component_adoption just because method_development is uncertain:
adoption needs its own evidence. Do not publish both types for the same assertion;
separate claims between the same methods are allowed for distinct contributions.

Use the narrowest supported claim. 'As described in reference X' can support
adoption of the procedure it modifies; it does not license transfer of everything
in X. An introductory citation, 'unlike X', and outperforming X do not by themselves
establish inheritance. Replacing all of a compared process is a description of
change, not proof of descent from that comparator.

A historical assertion is supported by the later attribution AND the earlier
demonstration. Read both at their available evidence levels. Missing decisive
support means needs_evidence or a narrower independently supported claim, never
an invented parent. Absence of a history edge means not established in this
evidence, not proof of independent invention. Record uncertainty about versions
or priority without converting journal date into proof of influence.

History type and technical change category are separate. Preserve the actual
before/after description in natural language. Do not force changes into an enzyme,
chemistry, routing or other taxonomy now; later clustering must retain these
descriptions, sources and original relationship types.
"""


def relation_instructions(kinds):
    """The same definitions feed both citation following and paired comparison."""
    kinds = tuple(kinds)
    lines = ["RELATION DEFINITIONS"]
    for kind in kinds:
        rule = RELATIONS[kind]
        lines.append(f"{kind} [{rule['layer']}]: {rule['meaning']}")
        lines.extend("Required: " + text for text in rule.get("required", ()))
        lines.extend("Does not establish: " + text for text in rule.get("exclusions", ()))
    if HISTORICAL_RELATIONS.intersection(kinds):
        lines.append(HISTORY_DECISION_RULES)
    return "\n".join(lines)


CHANGE_INSTRUCTIONS = """
RECORDING A CHANGE
Start from the existing Illumina operation groups. Use the primary papers to
locate the specific contribution, not to expand every group into a full recipe.
Begin with the later paper's stated methodological contributions and relevant
Methods/figure/supplement descriptions. Account for each source-described change
within the relationship's scope; do not stop after finding one convenient adopted
reagent. An important change without a supported historical donor belongs in the
source-backed method summary, not an invented ancestry edge.

Write a short retained-arrangement description, then separate numbered entries
for the distinct changes in the existing comparison field (seed_design_change in
citation records; difference in pair records). Each entry must let a reader answer:
what feature/process is compared; what the specified earlier implementation did;
what the later implementation does instead; and what material, molecular feature,
placement, ordering or interpretation makes that difference meaningful. Point to
the affected existing workflow records and source support. Explain which next
operation or measurement depends on the difference when the sources establish it.
There is no fixed number of entries or mandatory set of changed assay stages.

Keep interventions tested together together. If a buffer and temperature changed
in the same comparison, describe the joint change and do not assign separate causal
gains without separate evidence. Record reported consequences in the existing
gain/consequence field, identifying which change or combined workflow they apply
to, the comparator and tested context. Missing consequence is not an invitation
to invent one. A retained feature must be named concretely, not 'same backbone'.

DETAIL STOPPING RULE: retain a detail when removing it would obscure (1) what was
actually changed, (2) which material/product reaches the next operation, (3) what
feature carries the measured information, or (4) the scope of the demonstrated
result. Thus location, tag carrier, end-group, reagent identity or a changed
condition can be essential. Routine volumes/washes/catalogue numbers are not
required unless consequential to this comparison. If the source lacks a decisive
detail, state that limitation; neither invent it nor demand unrelated recipe data.

For a component inside a combined operation, explicitly limit the assertion to
that component. Multiple changes or contributors are allowed; do not invent one
parent or a linear tree. A method outside the catalogue may remain external.
An adopted component is not necessarily the method's main innovation. Explain the
distinguishing assay architecture in the analysis summary with source support;
do not substitute a convenient minor adoption for that explanation, or force a
historical parent when the distinguishing change is supported but ancestry is not.

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


CHANGE_REVIEW_INSTRUCTIONS = """
CHANGE COVERAGE REVIEW
Read the papers' stated contributions and relevant Methods/figures before using
the proposal as a checklist. Identify the necessary changes for the specified
relationship, then compare them with the submitted record. This is a coverage
check as well as a correctness check; agreeing that each included sentence is
plausible is insufficient. No fixed number of changes or universal assay-stage
list is expected. For development inspect the retained/revised assay arrangement;
for adoption inspect the named contribution, destination and any adaptation.

For every distinct change check the earlier/later descriptions, material and
carrier identity, compartment/order where consequential, source support and the
attribution of the reported consequence. Reject 'improved recovery' as a complete
description when the sources establish a specific changed procedure. Do not split
a jointly tested intervention into separately proven causal effects. Record an
omitted necessary change in omitted_candidates with its source ID, passage and
why it matters. Use revise for a correctable omission within an existing claim,
needs_evidence when necessary evidence is unavailable, and reject for a false
relationship. Genuine gaps cannot be fixed by demanding more confident wording.
Check that the same contribution was not counted as two different historical
claims. Scientific similarity alone does not repair missing historical attribution.

SCOPED BEFORE-TO-AFTER CHECK
For each accepted historical claim, use the existing finding.rationale to explain
the reconstruction at that claim's scope: starting with the evidenced earlier
arrangement, what is retained, what changes (or is adopted unchanged), what
material/information state results, and how that result serves the next operation
or measurement. Identify the source support for the necessary connections. Do not
merely repeat the author's comparison or state that it is plausible. If a necessary
connection requires an unstated assumption, request correction or evidence rather
than silently completing it from general knowledge. A source-backed earlier
description is sufficient; do not require nonexistent predecessor graph IDs.

This is conceptual reconstruction, not execution of a complete wet-lab recipe.
A component-adoption edge need only reconstruct the adopted contribution and its
destination, not the entire later assay. A method-development edge must account
for the retained arrangement and the necessary coordinated changes it claims.
Keep a novel proposed transfer separate: a reviewed historical implementation is
evidence for what worked there, not a certificate that a new combination works.
"""


REVISION_INSTRUCTIONS = """
REVISION TASK
Read previous_review and the previous analysis, then inspect the original source
for every requested correction and omission. Revise the affected descriptions,
relationship types or scope; preserve supported facts and source qualifications.
In summary account for each requested correction: corrected with source support,
narrowed/withdrawn, or still unresolved with the missing evidence named. If the
reviewer was mistaken, explain why from the source; do not copy its assertions
uncritically. Do not remove an unresolved item merely to obtain acceptance, invent
an unavailable recipe, or rewrite unrelated source workflow graphs. Return a
complete revised analysis under the same schema. A separate fresh review judges
the revised artifact; your revision is not approval. The host bounds the cycle.
"""


REVISION_REVIEW_INSTRUCTIONS = """
REVIEWING A REVISED ANALYSIS
Read previous_review and previous_analysis as an issue record, not as scientific
authority. Independently check the revised analysis against the original sources.
In the existing review summary, account for each earlier requested correction,
omission and blocking uncertainty: resolved with source support, narrowed or
withdrawn, still unresolved, or a mistaken criticism with source-based reasoning.
Check removed as well as retained claims. Deleting an uncertainty or claim does
not establish that the underlying question was resolved; retain an important
unanswered methodological lead in omitted_candidates. If necessary sources are
outside the registered bundle, say which source is needed; do not accept confident
wording as a substitute. Remain a fresh reviewer; previous approval is not proof.
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
        "relation_definition_status": "Current retrieval guidance; not the original review contract and not a re-review of this archived claim.",
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
