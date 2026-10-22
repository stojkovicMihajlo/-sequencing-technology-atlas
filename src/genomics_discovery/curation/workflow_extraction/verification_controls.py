"""Build source-bound local review controls; this is not a truth oracle.

The caller supplies independently checked labels and exact mutations. The host
binds those choices to existing field values and passages, hides labels/origins
from readers, and reuses the existing grounding diagnostic. Omission controls ask
an explicit local coverage question: deleting a true statement alone does not
make the remaining statements unsupported. These controls do not measure the
production reviewer's whole-graph sensitivity or experimental correctness.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from ..protocol_benchmark.grounding_diagnostic import Dataset, Labels, Result, score_result, validate_labels
from ..run_state import sha
from .field_review import _resolve
from .models import Workflow, digest, validate_evidence


COVERAGE_INSTRUCTIONS = """\nSome values have requirement, candidate_graph and coverage_assertion keys.
These are local coverage cases. The graph is a candidate representation, not a
source or a reference answer. Check its factual claims against the visible source
passages and whether its Operations and Flows explicitly represent the stated
requirement. A State name, an unconnected State, or general biological plausibility
does not substitute for a missing operation or handoff. Judge only this requirement,
not unshown parts of the full protocol. supported requires both faithful graph
claims and explicit coverage. Use not_established for an unmet coverage assertion
without a contradictory experimental claim; explain the missing operation or
handoff in unsupported_clause. Do not infer an expected answer from another case.
"""


def neighborhood(workflow: Workflow, operation_ids: list[str]) -> dict:
    """Project selected occurrences and their interfaces, without review opinions."""
    if not operation_ids or len(set(operation_ids)) != len(operation_ids):
        raise ValueError("Select nonempty unique operation IDs")
    data = workflow.model_dump(mode="json")
    operations = {item["id"]: item for item in data["operations"]}
    if set(operation_ids) - operations.keys():
        raise ValueError("Unknown neighborhood operation")
    selected = [operations[ident] for ident in operation_ids]
    state_ids = {ident for item in selected for ident in (*item["inputs"], *item["outputs"])}
    node_ids = set(operation_ids) | state_ids
    return {
        "operations": [{key: item[key] for key in ("id", "name", "action", "inputs", "outputs")}
                       for item in selected],
        "states": [{key: item[key] for key in ("id", "kind", "description")}
                   for item in data["states"] if item["id"] in state_ids],
        "flows": [{key: item[key] for key in ("id", "source", "target", "kind", "condition",
                                              "selection", "alternative_group") if key in item}
                  for item in data["flows"]
                  if item["source"] in node_ids and item["target"] in node_ids],
    }


def _delete_operation(graph: dict, operation_id: str) -> dict:
    changed = deepcopy(graph)
    if sum(item["id"] == operation_id for item in graph["operations"]) != 1:
        raise ValueError("Omitted operation must occur exactly once in neighborhood")
    changed["operations"] = [item for item in graph["operations"] if item["id"] != operation_id]
    changed["flows"] = [item for item in graph["flows"]
                        if operation_id not in (item["source"], item["target"])]
    # Keep material boundaries visible. Their presence is not an operation or
    # an invented bypass edge; the remaining graph claims are unchanged.
    return changed


def build_controls(workflow: Workflow, packet: dict, packet_path: Path, definitions: list[dict],
                   *, family: str, split: str) -> tuple[Dataset, Labels, dict]:
    """Build paired controls without inventing labels or editing historical data.

    This is a deliberately small host authoring helper, not a second runner.
    Native execution must replay packets with the existing verify_dataset gate.
    Source locators/quotes are validated here; semantic label accuracy is not.
    """
    packet_path = Path(packet_path).resolve()
    if not definitions:
        raise ValueError("At least one independently specified control is required")
    if errors := validate_evidence(workflow, packet):
        raise ValueError("; ".join(errors))
    if any(getattr(workflow, key) != packet[key] for key in ("method_id", "title", "scope", "variant")):
        raise ValueError("Workflow and source packet have different scope")
    if digest(json.loads(packet_path.read_text())) != digest(packet):
        raise ValueError("Supplied packet differs from packet_path")
    passage_map = {(source["source_id"], passage["passage_id"]): passage["text"]
                   for source in packet["sources"] for passage in source["passages"]}
    data = workflow.model_dump(mode="json")
    contexts, cases, labels, selection = [], [], [], []
    ids = set()
    for definition in definitions:
        ident = definition["id"]
        if ident in ids:
            raise ValueError("Duplicate control definition ID")
        ids.add(ident)
        refs = definition["passage_refs"]
        keys = [(ref["source_id"], ref["passage_id"]) for ref in refs]
        if not keys or len(keys) != len(set(keys)) or set(keys) - passage_map.keys():
            raise ValueError("Control requires distinct registered passage references")
        context_id = "ctx_" + digest([digest(workflow), ident])[:16]
        contexts.append({"id": context_id, "method_id": workflow.method_id, "family": family,
            "split": split, "source_packet_sha256": sha(packet_path),
            "source_packet_digest": digest(packet), "source_packet_path": str(packet_path),
            "passages": [{**ref, "text": passage_map[key]} for ref, key in zip(refs, keys)]})
        kind = definition["kind"]
        if kind == "field_support":
            pointer = definition["field_pointer"]
            original = _resolve(data, pointer)
            if digest(original) != definition["original_value_sha256"]:
                raise ValueError("Control original field value changed")
            changed = definition["changed_value"]
        elif kind == "local_coverage":
            graph = neighborhood(workflow, definition["operation_ids"])
            assertion = "The candidate graph explicitly represents every operation and material handoff in the requirement."
            original = {"requirement": definition["requirement"], "candidate_graph": graph,
                        "coverage_assertion": assertion}
            changed = {**original, "candidate_graph": _delete_operation(graph, definition["omit_operation_id"])}
            pointer = "/local_coverage"
        else:
            raise ValueError("Unknown control kind")
        if digest(changed) == digest(original):
            raise ValueError("Control mutation must change the candidate value")
        for origin, value in (("original", original), ("changed", changed)):
            expected = definition[origin + "_label"]
            case_id = "case_" + digest([digest(workflow), ident, origin])[:20]
            cases.append({"id": case_id, "context_id": context_id,
                          "field_pointer": pointer, "value": value})
            labels.append({"case_id": case_id, "outcome": expected["outcome"],
                           "phenomenon": definition["phenomenon"], "rationale": expected["rationale"],
                           "decisive_passages": expected["decisive_passages"]})
            selection.append({"case_id": case_id, "definition_id": ident, "origin": origin,
                              "kind": kind, "field_pointer": pointer, "value_sha256": digest(value)})
    # Opaque, deterministically mixed IDs carry no origin or desired judgment.
    cases.sort(key=lambda row: row["id"])
    dataset = Dataset.model_validate({"format_version": "protocol_grounding_cases/1.0",
        "annotation_status": "model_authored_diagnostic_candidate", "contexts": contexts, "cases": cases})
    label_set = Labels.model_validate({"format_version": "protocol_grounding_labels/1.0",
        "annotation_status": "model_authored_diagnostic_candidate", "cases": labels})
    validate_labels(label_set, dataset)
    return dataset, label_set, {"workflow_sha256": digest(workflow),
        "source_packet_sha256": sha(packet_path), "definitions_sha256": digest(definitions),
        "selection": selection, "boundary": "Source-reviewed model-authored controls, not expert gold; local field/coverage sensitivity only."}


def control_gate(dataset: Dataset, labels: Labels, result: Result | None,
                 *, native_complete: bool) -> dict:
    """A deterministic regression sentinel after independent transport validation.

    This does not certify the labels or change production admission. A successful
    result must retain all supported controls and explicitly reject all negative
    controls. Abstaining from everything cannot pass. Exact negative-class
    agreement remains separately reported, rather than conflated with detection.
    """
    report = score_result(dataset, labels, result)
    metrics = report["overall"]
    checks = {
        "native_complete": native_complete,
        "no_unavailable_items": metrics["failure_item_count"] == 0,
        "no_abstentions": metrics["unassessed_item_count"] == 0,
        "negative_controls_present": metrics["unsupported_acceptance"]["denominator"] > 0,
        "supported_controls_present": metrics["supported_retention"]["denominator"] > 0,
        "no_unsupported_acceptance": metrics["unsupported_acceptance"]["rate"] == 0,
        "all_supported_retained": metrics["supported_retention"]["rate"] == 1,
    }
    return {"passed": all(checks.values()), "checks": checks, "metrics": report,
            "boundary": "Only the supplied model-authored local controls; not whole-graph accuracy or production reviewer validation."}
