"""Cheap contextual retrieval beyond recorded historical pairs.

An inverted index shortlists operation seeds, not reusable primitives or edges.
The existing source-bound comparison/review workflow decides their meaning.
"""

import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowReview, digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from genomics_discovery.curation.workflow_extraction.transformations import cut_transformation

_STOP = set("a an and are as at be been by for from has in into is it of on or that the "
            "their then these this to using was were which with without".split())
FIELDS = ("transformation", "inputs", "outputs", "information")


def _tokens(text):
    return {t for t in re.findall(r"\w+", text.casefold()) if t not in _STOP and len(t) > 1}


def _fields(operations, inputs, outputs):
    return {
        "transformation": _tokens(" ".join(op["name"] + " " + op["action"]
                                           for op in operations)),
        "inputs": _tokens(" ".join(s["description"] for s in inputs)),
        "outputs": _tokens(" ".join(s["description"] for s in outputs)),
        "information": _tokens(" ".join(op["information_effect"] for op in operations)),
    }


def cut(graph, workflow_id, operation_ids):
    row = graph["workflows"][workflow_id]
    return cut_transformation(
        Workflow.model_validate(row["workflow"]), load_packet(Path(row["packet_path"])),
        operation_ids, review=WorkflowReview.model_validate(row["review"])
    )


def _context(row, operation):
    workflow = row["workflow"]
    states = {s["id"]: s for s in workflow["states"]}
    inputs = [states[s] for s in operation["inputs"]]
    outputs = [states[s] for s in operation["outputs"]]
    return inputs, outputs


def operation_candidates(graph, workflow_id, operation_ids, *, limit=10, per_method=2):
    """Rank seeds using separate input/transformation/output/information descriptions.

    At least two fields and two distinct shared tokens are needed, including a
    transformation or information field. This is a declared retrieval heuristic,
    not a biochemical criterion; synonyms and cross-readout analogies can be missed.
    No N-by-N model calls, ontology merge or scientific edge is performed.
    """
    if type(limit) is not int or limit < 1 or type(per_method) is not int or per_method < 1:
        raise ValueError("Candidate limits must be positive integers")
    selected = cut(graph, workflow_id, operation_ids)
    query = _fields([o.model_dump(mode="json") for o in selected.operations],
                    [s.model_dump(mode="json") for s in selected.inputs],
                    [s.model_dump(mode="json") for s in selected.outputs])
    own_method = graph["workflows"][workflow_id]["atlas_method_id"]
    records, inverted = {}, defaultdict(set)
    for wid, row in graph["workflows"].items():
        if row["atlas_method_id"] == own_method:
            continue
        for operation in row["workflow"]["operations"]:
            inputs, outputs = _context(row, operation)
            fields = _fields([operation], inputs, outputs)
            key = (wid, operation["id"])
            records[key] = (row, operation, inputs, outputs, fields)
            for field, tokens in fields.items():
                for token in tokens:
                    inverted[(field, token)].add(key)
    possible = set()
    for field, tokens in query.items():
        for token in tokens:
            possible.update(inverted[(field, token)])
    ranked = []
    for key in possible:
        row, operation, inputs, outputs, fields = records[key]
        matched = {field: sorted(tokens & fields[field]) for field, tokens in query.items()}
        nonempty = {field for field, tokens in matched.items() if tokens}
        distinct = {token for tokens in matched.values() for token in tokens}
        if (len(nonempty) < 2 or len(distinct) < 2
                or not nonempty & {"transformation", "information"}):
            continue
        # Equal field contribution; IDF downweights corpus-common words. Scores are
        # local ranking aids only and must not be compared across graph revisions.
        score = sum(
            sum(math.log(1 + len(records) / len(inverted[(field, t)])) for t in matched[field])
            / math.sqrt(max(1, len(query[field])) * max(1, len(fields[field])))
            for field in FIELDS
        )
        wid, oid = key
        neighbor_states = set(operation["inputs"] + operation["outputs"])
        neighbors = [{"id": op["id"], "name": op["name"]}
                     for op in row["workflow"]["operations"] if op["id"] != oid
                     and neighbor_states & set(op["inputs"] + op["outputs"])]
        ranked.append({
            "request_id": "candidate:" + digest([graph["revision_id"], selected.id, wid, oid]),
            "workflow_id": wid, "method_id": row["atlas_method_id"],
            "operation_ids": [oid], "variant": row["workflow"]["variant"],
            "review_status": row["review_status"], "score": round(score, 6),
            "matched_fields": matched, "operation": operation,
            "inputs": inputs, "outputs": outputs, "neighbor_operations": neighbors,
            "scope": row["workflow"]["scope"],
            "workflow_gap_ids": [gap["id"] for gap in row["workflow"]["unresolved"]],
            "blocking_gap_ids": [gap["id"] for gap in row["workflow"]["unresolved"]
                                 if gap["blocking"]],
            "status": "retrieval_only_requires_source_comparison",
        })
    ranked.sort(key=lambda r: (-r["score"], r["method_id"], r["workflow_id"], r["operation_ids"]))
    results, counts = [], Counter()
    for row in ranked:
        if counts[row["method_id"]] < per_method:
            results.append(row)
            counts[row["method_id"]] += 1
    return {
        "graph_revision_id": graph["revision_id"], "source_workflow_id": workflow_id,
        "source_operation_ids": [o.id for o in selected.operations],
        "source_cut_sha256": digest(selected),
        "query_fields": {k: sorted(v) for k, v in query.items()},
        "indexed_operations": len(records), "retrieved_before_filter": len(possible),
        "qualified_candidates": len(ranked), "total_after_method_limit": len(results),
        "candidates": results[:limit], "truncated": len(results) > limit,
        "boundary": "Heuristic retrieval of contextual operation seeds, not mechanism "
                    "equivalence, inheritance, biochemical compatibility or novelty. "
                    "A shared word alone cannot qualify. A non-hit is not a negative result. "
                    "Inspect neighboring operations and expand the connected comparison cut "
                    "when needed. Inspect the workflow for full scope and gap descriptions. "
                    "Preserve each method's sources, conditions and unknowns; "
                    "use fresh source comparison and independent review before adding edges.",
    }
