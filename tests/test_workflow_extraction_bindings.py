"""Association provenance and conditional inputs are claims, not graph conveniences."""

import hashlib
import json
from copy import deepcopy

import pytest

from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.authoring import (
    authoring_schema,
    authoring_view,
    expand_authoring,
)
from genomics_discovery.curation.workflow_extraction.inspection import (
    expand_inspection,
    inspection_view,
)
from genomics_discovery.curation.workflow_extraction.models import (
    Flow,
    Workflow,
    WorkflowDraft,
    WorkflowReview,
    digest,
    target_ids,
    validate_evidence,
    validate_io_bindings,
    validate_review,
)
from genomics_discovery.curation.workflow_extraction.projection import (
    export_candidate,
    export_diagnostic,
    query_workflow,
)
from genomics_discovery.curation.workflow_extraction.sources import build_packet


@pytest.fixture
def case(tmp_path):
    text = (
        "Prepare standard library from cells.\n\n"
        "Prepare low library from cells.\n\n"
        "Use exactly one of standard library and low library for measurement. "
        "If annotation control is available, include it in measurement. "
        "Measurement produces paired reads."
    )
    original = tmp_path / "source.txt"
    original.write_text(text)
    packet = build_packet(method_id="conditional", title="Conditional measurement",
                          scope="Isolated cells to paired reads", variant="original",
                          sources=[{"source_id": "paper", "title": "Synthetic method",
                                    "role": "primary_paper", "url": "https://example.org/source",
                                    "path": str(original)}])

    def evidence(quote, *, inferred=False):
        passage = next(p for p in packet["sources"][0]["passages"] if quote in p["text"])
        return {"source_id": "paper", "passage_id": passage["passage_id"], "quote": quote,
                "support": "inferred" if inferred else "reported",
                "rationale": "Authored contextual association inference." if inferred
                else "The source identifies this exact association."}

    standard = evidence("Prepare standard library from cells.")
    low = evidence("Prepare low library from cells.")
    route = evidence("Use exactly one of standard library and low library for measurement.")
    optional = evidence("If annotation control is available, include it in measurement.")
    reads = evidence("Measurement produces paired reads.")

    def binding(sid, citation, **kwargs):
        return {"state_id": sid, "basis": f"Authored association with {sid}.",
                "evidence": [citation], **kwargs}

    data = {**{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
            "states": [{"id": sid, "kind": kind, "description": sid, "evidence": [citation]}
                       for sid, kind, citation in (
                           ("cells", "material", standard), ("standard", "material", standard),
                           ("low", "material", low), ("control", "data", optional),
                           ("reads", "data", reads))],
            "operations": [
                {"id": ident, "name": ident, "action": citation["quote"],
                 "inputs": ["cells"], "outputs": [product], "resources": [],
                 "information_effect": "Retain native specimen identity.",
                 "evidence": [citation], "input_bindings": [binding("cells", citation)],
                 "output_bindings": [binding(product, citation)]}
                for ident, product, citation in (("prepare_standard", "standard", standard),
                                                 ("prepare_low", "low", low))],
            "modules": [{"id": "main", "name": "Main", "operation_ids": [
                "prepare_standard", "prepare_low", "measure"], "boundary_rationale": "Scope."}],
            "unresolved": []}
    data["operations"].append({
        "id": "measure", "name": "Measure one library", "action": "Measure selected library.",
        "inputs": ["standard", "low", "control"], "outputs": ["reads"], "resources": [],
        "information_effect": "One library produces paired observations.", "evidence": [reads],
        "input_bindings": [
            binding("standard", route, selection="alternative", alternative_group="library_route",
                    condition="The standard route was selected."),
            binding("low", route, selection="alternative", alternative_group="library_route",
                    condition="The low-input route was selected."),
            binding("control", optional, selection="optional", condition="Control is available.")],
        "output_bindings": [binding("reads", reads)]})
    return data, packet, evidence


def review_for(workflow, packet, *, unqualified_verdict="supported"):
    evidence = [e.model_dump(mode="json") for state in workflow.states for e in state.evidence]
    unqualified = {"flow:" + flow.id for flow in workflow.flows
                   if flow.association == "unqualified"}
    return WorkflowReview.model_validate({
        "workflow_sha256": digest(workflow), "source_packet_sha256": digest(packet),
        "inspected_passages": [{"source_id": source, "passage_id": passage}
                               for source, passage in sorted({(e["source_id"], e["passage_id"])
                                                               for e in evidence})],
        "checks": [{"target_id": target, "verdict": unqualified_verdict
                    if target in unqualified else "supported", "rationale": "Source comparison.",
                    "evidence": evidence} for target in sorted(target_ids(workflow))],
        "omissions": [], "notes": []})


def test_legacy_draft_association_is_unqualified_not_reported_or_scientifically_inferred(case):
    data, packet, _ = case
    for op in data["operations"]:
        op.pop("input_bindings")
        op.pop("output_bindings")
    draft = WorkflowDraft.model_validate(data)
    workflow = assemble_workflow(draft)
    assert all(flow.association == "unqualified" and not flow.evidence
               and flow.selection is None for flow in workflow.flows)
    assert workflow.operations[0].evidence[0].support == "reported"
    assert "input_bindings" not in workflow.operations[0].model_dump()
    assert validate_io_bindings(draft) == []
    assert validate_io_bindings(draft, require_bindings=True)
    assert validate_io_bindings(workflow, require_bindings=True)
    with pytest.raises(ValueError, match="explicit input_bindings required"):
        assemble_workflow(draft, require_bindings=True)
    assert validate_evidence(workflow, packet) == []
    errors = validate_review(review_for(workflow, packet), workflow, packet)
    assert any("unqualified I/O must be qualified" in error for error in errors)


def test_unqualified_routes_can_be_exported_only_as_nonpositive_diagnostics(case, tmp_path):
    data, packet, _ = case
    for op in data["operations"]:
        op.pop("input_bindings")
        op.pop("output_bindings")
    workflow = assemble_workflow(WorkflowDraft.model_validate(data))
    review = review_for(workflow, packet, unqualified_verdict="unresolved")
    assert validate_review(review, workflow, packet) == []
    graph = export_diagnostic(workflow, review, packet, tmp_path / "diagnostic")
    assert all(edge["association"] == "unqualified" and not edge["source_refs"]
               for edge in graph["edges"])
    assert all(group["rule"] == "unspecified"
               for group in query_workflow(workflow, "measure")["input_groups"])
    with pytest.raises(ValueError, match="Only a supported"):
        export_candidate(workflow, review, packet, tmp_path / "candidate")


def test_exact_binding_support_is_not_operation_support_and_inference_remains_authored(case):
    data, packet, evidence = case
    data["operations"][2]["input_bindings"][0]["evidence"] = [evidence(
        "Use exactly one of standard library and low library for measurement.", inferred=True)]
    draft = WorkflowDraft.model_validate(data)
    workflow = assemble_workflow(draft, require_bindings=True)
    flow = next(f for f in workflow.flows if f.source == "standard" and f.target == "measure")
    assert flow.association == "source_qualified"
    assert flow.evidence == draft.operations[2].input_bindings[0].evidence
    assert flow.evidence != workflow.operations[2].evidence
    assert flow.evidence[0].support == "inferred"
    assert flow.evidence[0].rationale == "Authored contextual association inference."
    assert workflow.operations[2].evidence[0].support == "reported"
    assert validate_io_bindings(workflow, require_bindings=True) == []
    assert validate_evidence(workflow, packet) == []


def test_exclusive_and_optional_inputs_survive_authoring_query_and_export(case, tmp_path):
    data, packet, _ = case
    draft = WorkflowDraft.model_validate(data)
    expanded = expand_authoring(authoring_view(draft))
    assert expanded == draft
    schema = authoring_schema("draft")
    assert schema["$defs"]["IOBinding"]["properties"]["evidence_refs"]["minItems"] == 1
    workflow = assemble_workflow(expanded, require_bindings=True)
    assert expand_authoring(authoring_view(workflow)) == workflow
    assert expand_inspection(inspection_view(workflow)) == workflow
    query = query_workflow(workflow, "measure")
    alternatives = next(g for g in query["input_groups"] if g["selection"] == "alternative")
    assert alternatives["rule"] == "exactly_one"
    assert alternatives["state_ids"] == ["standard", "low"]
    assert next(g for g in query["input_groups"] if g["state_ids"] == ["control"])[
        "rule"] == "optional"
    assert {binding["selection"] for relation in query["predecessors"]
            for binding in relation["route_bindings"] if binding["direction"] == "input"} == {
        "alternative"}
    review = review_for(workflow, packet)
    assert validate_review(review, workflow, packet) == []
    graph = export_candidate(workflow, review, packet, tmp_path / "export")
    edges = [edge for edge in graph["edges"] if edge["original"]["target"] == "measure"]
    assert sum(e["selection"] == "alternative" for e in edges) == 2
    assert next(e for e in edges if e["original"]["source"] == "control")[
        "condition"] == "Control is available."
    assert all(edge["source_refs"] for edge in edges)
    protocol = (tmp_path / "export/protocol.txt").read_text()
    assert "exactly one of standard, low" in protocol
    assert "selection optional" in protocol
    assert "not an execution schedule" in protocol


@pytest.mark.parametrize("mutation,message", [
    ("missing_state", "cover the declared states exactly once"),
    ("duplicate_state", "cover the declared states exactly once"),
    ("extra_state", "cover the declared states exactly once"),
    ("empty_evidence", "at least 1"),
    ("optional_no_condition", "Optional I/O requires a condition"),
    ("alternative_no_condition", "alternative_group and condition"),
    ("alternative_no_group", "alternative_group and condition"),
    ("group_on_required", "only valid for alternative"),
    ("singleton_group", "at least two members"),
])
def test_invalid_per_io_claims_and_selection_groups_are_rejected(case, mutation, message):
    data, _, _ = case
    bindings = data["operations"][2]["input_bindings"]
    if mutation == "missing_state":
        bindings.pop()
    elif mutation == "duplicate_state":
        bindings.append(deepcopy(bindings[0]))
    elif mutation == "extra_state":
        bindings.append({**deepcopy(bindings[2]), "state_id": "absent"})
    elif mutation == "empty_evidence":
        bindings[0]["evidence"] = []
    elif mutation == "optional_no_condition":
        bindings[2]["condition"] = None
    elif mutation == "alternative_no_condition":
        bindings[0]["condition"] = None
    elif mutation == "alternative_no_group":
        bindings[0]["alternative_group"] = None
    elif mutation == "group_on_required":
        bindings[2].update(selection="required", alternative_group="invalid")
    else:
        bindings[0]["alternative_group"] = "singleton"
    with pytest.raises(ValueError, match=message):
        WorkflowDraft.model_validate(data)


@pytest.mark.parametrize("field,value", [("condition", "Different guard."),
                                         ("basis", "Different association claim.")])
def test_full_workflow_cannot_disagree_with_retained_binding(case, field, value):
    data, _, _ = case
    workflow = assemble_workflow(WorkflowDraft.model_validate(data))
    raw = workflow.model_dump(mode="json")
    flow = next(f for f in raw["flows"] if f["source"] == "standard" and f["target"] == "measure")
    flow[field] = value
    with pytest.raises(ValueError, match="differs from its typed flow"):
        Workflow.model_validate(raw)


def test_flow_qualification_cannot_be_bypassed_or_used_for_ordering(case):
    data, _, _ = case
    raw = assemble_workflow(WorkflowDraft.model_validate(data)).flows[0].model_dump(mode="json")
    with pytest.raises(ValueError, match="requires an explicit selection"):
        Flow.model_validate({**raw, "selection": None})
    with pytest.raises(ValueError, match="source_qualified association"):
        Flow.model_validate({**raw, "association": None})
    with pytest.raises(ValueError, match="Unqualified I/O"):
        Flow.model_validate({**raw, "association": "unqualified"})
    with pytest.raises(ValueError, match="requires an explicit selection"):
        Flow.model_validate({**raw, "kind": "precedence"})


def test_accepted_legacy_workflow_fields_digest_review_and_roundtrips_are_unchanged(case, tmp_path):
    data, packet, _ = case
    raw = assemble_workflow(WorkflowDraft.model_validate(data)).model_dump(mode="json")
    for operation in raw["operations"]:
        operation.pop("input_bindings")
        operation.pop("output_bindings")
    for flow in raw["flows"]:
        for field in ("association", "selection", "alternative_group"):
            flow.pop(field, None)
    serialized = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                            allow_nan=False)
    historical_digest = hashlib.sha256(serialized.encode()).hexdigest()
    workflow = Workflow.model_validate_json(serialized)
    assert workflow.model_dump(mode="json") == raw
    assert digest(workflow) == historical_digest
    assert expand_authoring(authoring_view(workflow)).model_dump(mode="json") == raw
    assert digest(expand_inspection(inspection_view(workflow))) == historical_digest
    review = review_for(workflow, packet)
    assert validate_review(review, workflow, packet) == []
    graph = export_candidate(workflow, review, packet, tmp_path / "legacy")
    assert graph["workflow"] == raw
    assert graph["workflow_sha256"] == historical_digest
    assert Workflow.model_validate(graph["workflow"]) == workflow
    # No new semantics are retroactively guessed from legacy route prose.
    assert all(group["rule"] == "unspecified"
               for group in query_workflow(workflow, "measure")["input_groups"])
