"""Offline contract checks; synthetic evidence is not an extraction-quality evaluation."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from genomics_discovery.curation.workflow_extraction.inspection import (
    expand_inspection,
    inspection_view,
)
from genomics_discovery.curation.workflow_extraction.models import (
    Evidence,
    Finding,
    Flow,
    GapResolution,
    Omission,
    Operation,
    ReadingReport,
    Resource,
    State,
    Workflow,
    WorkflowDraft,
    WorkflowReview,
    digest,
    review_outcome,
    target_ids,
    validate_evidence,
    validate_reader_gaps,
    validate_review,
)


def evidence(passage="p1", quote="Quantify an aliquot before sequencing."):
    return {"source_id": "paper", "passage_id": passage, "quote": quote,
            "support": "reported", "rationale": "The cited passage describes this step."}


@pytest.fixture
def packet():
    return {"sources": [{"source_id": "paper", "passages": [
        {"passage_id": "p1", "text": "Prepare a library.\nQuantify an aliquot before\n"
         "sequencing. Sequence the unamplified library.", "locator": "Methods, paragraph 1"},
        {"passage_id": "p2", "text": "Amplified analytical material is only used for "
         "quantification.", "locator": "Methods, paragraph 2"},
    ]}]}


@pytest.fixture
def candidate():
    states = [{"id": ident, "kind": kind, "description": description,
               "evidence": [evidence()]} for ident, kind, description in (
        ("sample", "material", "Starting specimen"),
        ("library", "material", "Unamplified production library"),
        ("quantity", "data", "Library concentration estimate"),
        ("reads", "data", "Sequence reads"),
    )]
    operations = [{"id": ident, "name": name, "action": name,
                   "inputs": inputs, "outputs": outputs, "resources": [],
                   "information_effect": "Change described in the declared scope.",
                   "evidence": [evidence()]} for ident, name, inputs, outputs in (
        ("prepare", "Prepare library", ["sample"], ["library"]),
        ("quantify", "Quantify analytical aliquot", ["library"], ["quantity"]),
        ("sequence", "Sequence production library", ["library"], ["reads"]),
    )]
    flows = [{"id": f"f{index}", "source": source, "target": target, "kind": kind,
              "basis": "Reported route within this variant.", "condition": None,
              "evidence": [evidence()]} for index, (source, target, kind) in enumerate((
        ("sample", "prepare", "material_input"),
        ("prepare", "library", "material_output"),
        ("library", "quantify", "material_input"),
        ("quantify", "quantity", "data_output"),
        ("library", "sequence", "material_input"),
        ("sequence", "reads", "data_output"),
        ("quantify", "sequence", "precedence"),
    ))]
    return {"method_id": "synthetic-method", "title": "Source-scoped workflow",
            "scope": "General published library preparation and readout protocol",
            "variant": "Unamplified library", "states": states, "operations": operations,
            "flows": flows, "modules": [{"id": "module", "name": "Library and readout",
                "operation_ids": ["prepare", "quantify", "sequence"],
                "boundary_rationale": "Interpretive grouping for this declared slice."}],
            "unresolved": []}


def review_for(workflow, packet, verdict="supported"):
    return {"workflow_sha256": digest(workflow),
            "source_packet_sha256": digest(packet),
            "inspected_passages": [{"source_id": "paper", "passage_id": "p1"}],
            "checks": [{"target_id": target, "verdict": verdict,
                        "rationale": "Independent source comparison.", "evidence": [evidence()]}
                       for target in sorted(target_ids(workflow))],
            "omissions": [], "notes": []}


def extra_flow(candidate, source, target, kind, condition=None):
    candidate["flows"].append({"id": "extra", "source": source, "target": target,
                               "kind": kind, "basis": "Explicit additional relation.",
                               "condition": condition, "evidence": [evidence()]})


def test_valid_branch_is_explicit_and_independent_of_array_order(candidate, packet):
    workflow = Workflow.model_validate(candidate)
    assert validate_evidence(workflow, packet) == []
    for key in ("states", "operations", "flows", "modules"):
        candidate[key].reverse()
    reordered = Workflow.model_validate(candidate)
    assert target_ids(reordered) == target_ids(workflow)
    assert len(target_ids(workflow)) == 14
    assert "module:module" not in target_ids(workflow)


def test_inspection_deduplicates_evidence_without_losing_any_claim(candidate):
    workflow = Workflow.model_validate(candidate)
    view = inspection_view(workflow)
    assert len(view["evidence_registry"]) == 1
    assert view["candidate"]["operations"][0]["evidence_refs"] == ["e0001"]
    assert expand_inspection(view).model_dump() == workflow.model_dump()
    assert view["workflow_sha256"] == digest(workflow)


def test_inspection_keeps_support_and_rationale_differences(candidate):
    candidate["states"][0]["evidence"][0]["support"] = "inferred"
    candidate["states"][0]["evidence"][0]["rationale"] = "Contextual routing inference."
    workflow = Workflow.model_validate(candidate)
    view = inspection_view(workflow)
    assert len(view["evidence_registry"]) == 2
    assert expand_inspection(view) == workflow


@pytest.mark.parametrize("change", ["quote", "routing", "gap", "missing", "extra"])
def test_inspection_refuses_changed_or_incomplete_records(candidate, change):
    view = inspection_view(Workflow.model_validate(candidate))
    if change == "quote":
        view["evidence_registry"]["e0001"]["quote"] = "Unreported replacement."
    elif change == "routing":
        view["candidate"]["flows"][0]["basis"] = "Different causal assertion."
    elif change == "gap":
        view["candidate"]["unresolved"] = [{
            "id": "new-gap", "description": "Changed uncertainty", "blocking": True,
            "next_evidence": "Investigate source.",
        }]
    elif change == "missing":
        del view["evidence_registry"]["e0001"]
    elif change == "extra":
        view["evidence_registry"]["unused"] = evidence()
    with pytest.raises(ValueError):
        expand_inspection(view)


def test_models_are_frozen_and_forbid_authored_acceptance(candidate, packet):
    workflow = Workflow.model_validate(candidate)
    with pytest.raises(ValidationError, match="frozen"):
        workflow.title = "Replacement"
    candidate["accepted"] = True
    with pytest.raises(ValidationError, match="Extra inputs"):
        Workflow.model_validate(candidate)
    review = review_for(workflow, packet)
    review["outcome"] = "supported"
    with pytest.raises(ValidationError, match="Extra inputs"):
        WorkflowReview.model_validate(review)


@pytest.mark.parametrize("quote", ["", " \n ", "x" * 601])
def test_quote_is_nonempty_and_bounded(quote):
    with pytest.raises(ValidationError):
        Evidence.model_validate(evidence(quote=quote))


@pytest.mark.parametrize("model,claim", [
    (Finding, {"id": "finding", "description": "Transformation"}),
    (State, {"id": "state", "kind": "material", "description": "Library"}),
    (Resource, {"kind": "condition", "description": "Incubation temperature"}),
    (Operation, {"id": "operation", "name": "Prepare", "action": "Prepare", "inputs": [],
                 "outputs": [], "resources": [], "information_effect": "None in this slice"}),
    (Flow, {"id": "flow", "source": "a", "target": "b", "kind": "precedence",
            "basis": "Reported chronology"}),
])
def test_every_declared_claim_requires_evidence(model, claim):
    with pytest.raises(ValidationError, match="at least 1"):
        model.model_validate({**claim, "evidence": []})


def test_reader_report_requires_findings():
    with pytest.raises(ValidationError, match="at least 1"):
        ReadingReport.model_validate({"role": "chemistry", "inspected_passages": [],
                                      "findings": [], "unresolved": []})


@pytest.mark.parametrize("mutation,message", [
    ("wrong_direction", "state/operation endpoints"),
    ("wrong_kind", "data state"),
    ("missing_flow", "exactly match typed flows"),
    ("extra_input", "exactly match typed flows"),
    ("duplicate_input", "duplicate input/output"),
    ("duplicate_route", "exactly match typed flows"),
    ("duplicate_id", "IDs must be unique"),
    ("missing_module", "at least 1"),
    ("two_modules", "exactly one module"),
    ("unknown_member", "unknown operation"),
    ("duplicate_member", "duplicate operation membership"),
])
def test_invalid_graph_contract(candidate, mutation, message):
    if mutation == "wrong_direction":
        candidate["flows"][0].update(source="prepare", target="sample")
    elif mutation == "wrong_kind":
        candidate["flows"][2]["kind"] = "data_input"
    elif mutation == "missing_flow":
        candidate["flows"].pop(0)
    elif mutation == "extra_input":
        candidate["operations"][0]["inputs"].append("quantity")
    elif mutation == "duplicate_input":
        candidate["operations"][0]["inputs"].append("sample")
    elif mutation == "duplicate_route":
        extra_flow(candidate, "sample", "prepare", "material_input")
    elif mutation == "duplicate_id":
        candidate["modules"][0]["id"] = "sample"
    elif mutation == "missing_module":
        candidate["modules"] = []
    elif mutation == "two_modules":
        candidate["modules"].append({**candidate["modules"][0], "id": "second-module"})
    elif mutation == "unknown_member":
        candidate["modules"][0]["operation_ids"].append("absent")
    elif mutation == "duplicate_member":
        candidate["modules"][0]["operation_ids"].append("prepare")
    with pytest.raises(ValidationError, match=message):
        Workflow.model_validate(candidate)


def test_multiple_producers_require_an_explicit_merge(candidate):
    candidate["operations"][1]["outputs"].append("library")
    extra_flow(candidate, "quantify", "library", "material_output")
    with pytest.raises(ValidationError, match="explicit merge operation"):
        Workflow.model_validate(candidate)


def test_an_explicit_merge_preserves_unique_state_producers(candidate):
    for ident in ("second-library", "merged-library"):
        candidate["states"].append({"id": ident, "kind": "material", "description": ident,
                                    "evidence": [evidence()]})
    for ident, inputs, outputs in (
        ("second-prepare", ["sample"], ["second-library"]),
        ("merge", ["library", "second-library"], ["merged-library"]),
    ):
        candidate["operations"].append({**deepcopy(candidate["operations"][0]), "id": ident,
                                        "inputs": inputs, "outputs": outputs})
        candidate["modules"][0]["operation_ids"].append(ident)
    for index, (source, target, kind) in enumerate((
        ("sample", "second-prepare", "material_input"),
        ("second-prepare", "second-library", "material_output"),
        ("library", "merge", "material_input"),
        ("second-library", "merge", "material_input"),
        ("merge", "merged-library", "material_output"),
    )):
        extra_flow(candidate, source, target, kind)
        candidate["flows"][-1]["id"] = f"merge-flow-{index}"
    candidate["operations"][2]["inputs"] = ["merged-library"]
    candidate["flows"][4]["source"] = "merged-library"
    Workflow.model_validate(candidate)


@pytest.mark.parametrize("field", ["states", "operations", "flows", "modules"])
def test_empty_workflow_collections_are_not_candidates(candidate, field):
    candidate[field] = []
    with pytest.raises(ValidationError, match="at least 1"):
        Workflow.model_validate(candidate)


def test_modules_and_operations_cannot_be_empty_placeholders(candidate):
    candidate["modules"][0]["operation_ids"] = []
    candidate["operations"][0]["outputs"] = []
    with pytest.raises(ValidationError, match="at least 1"):
        Workflow.model_validate(candidate)


def test_resource_driven_operation_can_have_no_input_state(candidate):
    candidate["operations"][0]["inputs"] = []
    candidate["flows"].pop(0)
    Workflow.model_validate(candidate)


@pytest.mark.parametrize("mutation", ["precedence_cycle", "self_edge", "routed_self_dependency"])
def test_v1_rejects_cycles_without_assuming_all_protocols_are_acyclic(candidate, mutation):
    if mutation == "precedence_cycle":
        extra_flow(candidate, "sequence", "prepare", "precedence")
    elif mutation == "self_edge":
        extra_flow(candidate, "sequence", "sequence", "precedence")
    else:
        candidate["operations"][0]["inputs"].append("library")
        extra_flow(candidate, "library", "prepare", "material_input")
    with pytest.raises(ValidationError, match="rejects self-dependency and routed cycles"):
        Workflow.model_validate(candidate)


def test_control_requires_a_real_condition(candidate):
    candidate["flows"][-1]["kind"] = "control"
    with pytest.raises(ValidationError, match="control requires a condition"):
        Workflow.model_validate(candidate)
    candidate["flows"][-1]["condition"] = "Concentration falls within the reported range"
    Workflow.model_validate(candidate)


def test_quote_checks_are_recursive_and_only_validate_provenance(candidate, packet):
    candidate["operations"][0]["resources"] = [{"kind": "condition",
        "description": "An inferred resource claim", "evidence": [
            {**evidence(quote="Quantify   an aliquot\nbefore sequencing."),
             "support": "inferred"}]}]
    workflow = Workflow.model_validate(candidate)
    assert validate_evidence(workflow, packet) == []
    candidate["operations"][0]["resources"][0]["evidence"][0]["quote"] = "Fabricated passage"
    errors = validate_evidence(Workflow.model_validate(candidate), packet)
    assert len(errors) == 1
    assert "operations[0].resources[0].evidence[0]" in errors[0]
    assert "quote is not present" in errors[0]


def test_unknown_reference_and_duplicate_packet_passages_are_rejected(packet):
    assert "unknown passage" in validate_evidence(evidence(passage="absent"), packet)[0]
    packet["sources"][0]["passages"].append(deepcopy(packet["sources"][0]["passages"][0]))
    assert "duplicate passage" in validate_evidence(evidence(), packet)[0]


def test_digest_is_canonical_and_binds_the_actual_workflow(candidate):
    workflow = Workflow.model_validate(candidate)
    dumped = workflow.model_dump(mode="json")
    assert digest(workflow) == digest(dict(reversed(list(dumped.items()))))
    candidate["scope"] = "A different source scope"
    assert digest(Workflow.model_validate(candidate)) != digest(workflow)


def test_review_integrity_also_checks_candidate_evidence(candidate, packet):
    candidate["states"][0]["evidence"][0]["quote"] = "A fabricated candidate quote"
    workflow = Workflow.model_validate(candidate)
    review = WorkflowReview.model_validate(review_for(workflow, packet))
    errors = validate_review(review, workflow, packet)
    assert any("quote is not present" in error for error in errors)


def test_supported_review_covers_only_state_operation_and_flow_targets(candidate, packet):
    workflow = Workflow.model_validate(candidate)
    review = WorkflowReview.model_validate(review_for(workflow, packet))
    assert validate_review(review, workflow, packet) == []
    assert review_outcome(review, workflow) == "supported"


def test_omission_requires_an_explicit_repair_or_retrieval_action():
    raw = {"description": "Consequential missing claim", "blocking": True,
           "evidence": [evidence()]}
    with pytest.raises(ValidationError, match="action"):
        Omission.model_validate(raw)
    with pytest.raises(ValidationError, match="revise_record.*retrieve_evidence"):
        Omission.model_validate({**raw, "action": "guess_from_description"})


@pytest.mark.parametrize("action,blocking,verdict,expected", [
    ("revise_record", True, "supported", "needs_revision"),
    ("retrieve_evidence", True, "supported", "research_needed"),
    ("retrieve_evidence", True, "unsupported", "needs_revision"),
    ("revise_record", True, "unresolved", "needs_revision"),
    ("retrieve_evidence", False, "supported", "supported"),
    ("revise_record", False, "supported", "needs_revision"),
    ("retrieve_evidence", False, "unresolved", "research_needed"),
])
def test_omission_action_controls_outcome_without_parsing_prose(
    candidate, packet, action, blocking, verdict, expected,
):
    workflow = Workflow.model_validate(candidate)
    raw = review_for(workflow, packet, verdict)
    raw["omissions"] = [{"description": "The label alone cannot decide repair versus research",
                          "action": action, "blocking": blocking, "evidence": [evidence()]}]
    review = WorkflowReview.model_validate(raw)
    assert validate_review(review, workflow, packet) == []
    assert review_outcome(review, workflow) == expected


def test_repair_still_takes_priority_when_separate_evidence_retrieval_is_needed(candidate, packet):
    workflow = Workflow.model_validate(candidate)
    raw = review_for(workflow, packet)
    raw["omissions"] = [{"description": description, "blocking": True,
                          "action": action, "evidence": [evidence()]}
                         for description, action in (
                             ("Missing dependency document", "retrieve_evidence"),
                             ("Omitted reported transformation", "revise_record"),
                         )]
    review = WorkflowReview.model_validate(raw)
    assert validate_review(review, workflow, packet) == []
    assert review_outcome(review, workflow) == "needs_revision"


def test_nonblocking_required_record_repair_takes_priority_over_gap_flags(candidate, packet):
    candidate["unresolved"] = [{"id": "recipe-detail", "description": "Unknown internal detail",
                                "blocking": True, "next_evidence": "Inspect cited dependency"}]
    workflow = Workflow.model_validate(candidate)
    raw = review_for(workflow, packet)
    raw["omissions"] = [{"description": "Correct the unsupported blocking classification",
                          "action": "revise_record", "blocking": False,
                          "evidence": [evidence()]}]
    review = WorkflowReview.model_validate(raw)
    assert validate_review(review, workflow, packet) == []
    assert all(check.verdict == "supported" for check in review.checks)
    assert review_outcome(review, workflow) == "needs_revision"


def test_draft_keeps_explicit_scientific_interfaces_without_repeating_io_flows(candidate):
    raw = deepcopy(candidate)
    io_flows = raw.pop("flows")
    raw["ordering_flows"] = [flow for flow in io_flows if flow["kind"] == "precedence"]
    draft = WorkflowDraft.model_validate(raw)
    assert draft.operations[0].inputs == ("sample",)
    assert draft.operations[0].outputs == ("library",)
    assert draft.ordering_flows[0].source == "quantify"
    assert draft.ordering_flows[0].target == "sequence"
    assert draft.format_version == "protocol_workflow/1.0"
    assert draft.gap_resolutions == ()
    raw.pop("ordering_flows")
    assert WorkflowDraft.model_validate(raw).ordering_flows == ()
    with pytest.raises(ValidationError, match="flows"):
        Workflow.model_validate(raw)
    with pytest.raises(ValidationError, match="Extra inputs"):
        WorkflowDraft.model_validate({**raw, "flows": io_flows})


@pytest.mark.parametrize("kind", ["material_input", "material_output", "data_input", "data_output"])
def test_draft_ordering_flows_cannot_supply_a_second_io_claim(candidate, kind):
    raw = deepcopy(candidate)
    raw.pop("flows")
    raw["ordering_flows"] = [{"id": "ordering", "source": "quantify", "target": "sequence",
                              "kind": kind, "basis": "A declared relation", "condition": None,
                              "evidence": [evidence()]}]
    with pytest.raises(ValidationError, match="only precedence/control"):
        WorkflowDraft.model_validate(raw)


def test_review_is_bound_to_packet_context_beyond_the_quoted_fragments(candidate, packet):
    workflow = Workflow.model_validate(candidate)
    review = WorkflowReview.model_validate(review_for(workflow, packet))
    expanded = deepcopy(packet)
    expanded["sources"][0]["passages"].append({"passage_id": "p3", "locator": "Correction",
        "text": "These steps only apply to a different protocol variant."})
    assert validate_evidence(workflow, expanded) == []
    assert validate_evidence(review, expanded) == []
    errors = validate_review(review, workflow, expanded)
    assert any("source_packet_sha256 does not match" in error for error in errors)


def test_supported_operation_review_must_cover_each_resource_passage(candidate, packet):
    p2 = evidence("p2", "Amplified analytical material is only used for quantification.")
    candidate["operations"][0]["resources"] = [
        {"kind": "condition", "description": "Separately supported condition", "evidence": [p2]}]
    workflow = Workflow.model_validate(candidate)
    raw = review_for(workflow, packet)
    errors = validate_review(WorkflowReview.model_validate(raw), workflow, packet)
    assert any("resource 0 evidence passage must be cited and inspected" in error
               for error in errors)
    raw["inspected_passages"].append({"source_id": "paper", "passage_id": "p2"})
    errors = validate_review(WorkflowReview.model_validate(raw), workflow, packet)
    assert any("resource 0" in error for error in errors)
    operation_check = next(check for check in raw["checks"]
                           if check["target_id"] == "operation:prepare")
    operation_check["evidence"].append(p2)
    assert validate_review(WorkflowReview.model_validate(raw), workflow, packet) == []


@pytest.mark.parametrize("mutation,message", [
    ("stale_digest", "does not match"),
    ("missing_target", "missing targets"),
    ("unknown_target", "unknown targets"),
    ("duplicate_target", "duplicate target checks"),
    ("empty_inspection", "nonempty inspected_passages"),
    ("empty_evidence", "requires nonempty evidence"),
    ("unknown_inspection", "unknown passage reference"),
    ("uninspected_evidence", "was not inspected"),
    ("nonoverlapping_support", "must overlap declared target evidence"),
])
def test_review_integrity_rejects_false_positive_gates(candidate, packet, mutation, message):
    workflow = Workflow.model_validate(candidate)
    raw = review_for(workflow, packet)
    p2 = evidence("p2", "Amplified analytical material is only used for quantification.")
    if mutation == "stale_digest":
        raw["workflow_sha256"] = "0" * 64
    elif mutation == "missing_target":
        raw["checks"].pop()
    elif mutation == "unknown_target":
        raw["checks"][0]["target_id"] = "module:module"
    elif mutation == "duplicate_target":
        raw["checks"].append(raw["checks"][0])
    elif mutation == "empty_inspection":
        raw["inspected_passages"] = []
    elif mutation == "empty_evidence":
        raw["checks"][0]["evidence"] = []
    elif mutation == "unknown_inspection":
        raw["inspected_passages"].append({"source_id": "paper", "passage_id": "absent"})
    elif mutation == "uninspected_evidence":
        raw["omissions"] = [{"description": "Analytical branch missing", "blocking": True,
                              "action": "revise_record",
                              "evidence": [p2]}]
    elif mutation == "nonoverlapping_support":
        raw["inspected_passages"].append({"source_id": "paper", "passage_id": "p2"})
        raw["checks"][0]["evidence"] = [p2]
    errors = validate_review(WorkflowReview.model_validate(raw), workflow, packet)
    assert any(message in error for error in errors), errors


def test_supported_overlap_requires_a_passage_subset_not_every_quote(candidate, packet):
    candidate["states"][0]["evidence"].append(
        evidence("p2", "Amplified analytical material is only used for quantification."))
    workflow = Workflow.model_validate(candidate)
    raw = review_for(workflow, packet)
    raw["checks"][0]["evidence"] = [evidence(quote="Prepare a library.")]
    assert validate_review(WorkflowReview.model_validate(raw), workflow, packet) == []


@pytest.mark.parametrize("verdict,expected", [
    ("unsupported", "needs_revision"), ("unresolved", "research_needed"),
])
def test_genuine_negative_reviews_are_integrity_valid(candidate, packet, verdict, expected):
    workflow = Workflow.model_validate(candidate)
    raw = review_for(workflow, packet, verdict)
    raw["inspected_passages"].append({"source_id": "paper", "passage_id": "p2"})
    raw["checks"][0]["evidence"] = [
        evidence("p2", "Amplified analytical material is only used for quantification.")]
    review = WorkflowReview.model_validate(raw)
    assert validate_review(review, workflow, packet) == []
    assert review_outcome(review, workflow) == expected


def test_host_outcome_gates_blockers_and_preserves_nonblocking_uncertainty(candidate, packet):
    candidate["unresolved"] = [{"id": "gap", "description": "Missing detail",
                                "blocking": False, "next_evidence": "Read the supplement"}]
    workflow = Workflow.model_validate(candidate)
    raw = review_for(workflow, packet)
    raw["omissions"] = [{"description": "Detail outside scope", "blocking": False,
                          "action": "retrieve_evidence",
                          "evidence": [evidence()]}]
    review = WorkflowReview.model_validate(raw)
    assert validate_review(review, workflow, packet) == []
    assert review_outcome(review, workflow) == "supported"
    candidate["unresolved"][0]["blocking"] = True
    blocked = Workflow.model_validate(candidate)
    raw["workflow_sha256"] = digest(blocked)
    review = WorkflowReview.model_validate(raw)
    assert validate_review(review, blocked, packet) == []
    assert review_outcome(review, blocked) == "research_needed"
    raw["omissions"][0]["blocking"] = True
    raw["omissions"][0]["action"] = "revise_record"
    review = WorkflowReview.model_validate(raw)
    assert review_outcome(review, blocked) == "needs_revision"


def reader_reports():
    return {role: ReadingReport.model_validate({"role": role,
        "inspected_passages": [{"source_id": "paper", "passage_id": "p1"}],
        "findings": [{"id": "finding", "description": "Read the scoped transformation",
                      "evidence": [evidence()]}],
        "unresolved": [{"id": "condition", "description": "A condition requires clarification",
                        "blocking": role == "chemistry", "next_evidence": "Inspect supplement"}],
    }) for role in ("chemistry", "information")}


def retained_gaps(reports):
    return [{**gap.model_dump(mode="json"), "id": f"{role}:{gap.id}"}
            for role, report in reports.items() for gap in report.unresolved]


def resolution(role="chemistry", gap_id="condition"):
    return {"reader_role": role, "gap_id": gap_id,
            "rationale": "The cited source establishes the answer within the declared scope.",
            "evidence": [evidence()]}


def test_reader_gaps_cannot_silently_disappear(candidate, packet):
    workflow = Workflow.model_validate(candidate)
    errors = validate_reader_gaps(workflow, reader_reports(), packet)
    assert len(errors) == 2
    assert all("retain or resolve exactly once" in error for error in errors)


def test_reader_gap_retention_preserves_role_identity_and_blocking(candidate, packet):
    reports = reader_reports()
    candidate["unresolved"] = retained_gaps(reports)
    workflow = Workflow.model_validate(candidate)
    assert workflow.gap_resolutions == ()
    assert validate_reader_gaps(workflow, reports, packet) == []
    assert review_outcome(WorkflowReview.model_validate(review_for(workflow, packet)),
                          workflow) == "research_needed"


def test_gap_resolution_is_only_provenance_checked(candidate, packet):
    reports = reader_reports()
    candidate["unresolved"] = retained_gaps(reports)[1:]
    candidate["gap_resolutions"] = [resolution()]
    workflow = Workflow.model_validate(candidate)
    assert validate_reader_gaps(workflow, reports, packet) == []
    assert review_outcome(WorkflowReview.model_validate(review_for(workflow, packet)),
                          workflow) == "supported"


def test_gap_resolution_requires_its_own_source_review_check(candidate, packet):
    candidate["gap_resolutions"] = [resolution()]
    workflow = Workflow.model_validate(candidate)
    ident = "resolution:chemistry:condition"
    assert ident in target_ids(workflow)
    raw = review_for(workflow, packet)
    assert validate_review(WorkflowReview.model_validate(raw), workflow, packet) == []
    raw["checks"] = [check for check in raw["checks"] if check["target_id"] != ident]
    errors = validate_review(WorkflowReview.model_validate(raw), workflow, packet)
    assert any("missing targets" in error and ident in error for error in errors)


@pytest.mark.parametrize("fault,message", [
    ("duplicate_resolution", "Duplicate gap resolutions"),
    ("unknown_resolution", "unknown reader gap"),
    ("retained_and_resolved", "retain or resolve exactly once"),
    ("blocking_downgraded", "blocking flag must remain unchanged"),
    ("invented_resolution_quote", "quote is not present"),
    ("unknown_retained", "unknown reader gap"),
    ("duplicate_reader_gap", "duplicate gap ID"),
    ("report_role_mismatch", "role does not match"),
])
def test_reader_gap_dispositions_reject_loss_duplication_or_invented_resolution(
    candidate, packet, fault, message,
):
    reports = reader_reports()
    candidate["unresolved"] = retained_gaps(reports)
    if fault == "duplicate_resolution":
        candidate["unresolved"] = candidate["unresolved"][1:]
        candidate["gap_resolutions"] = [resolution(), resolution()]
        with pytest.raises(ValidationError, match="Duplicate gap resolutions"):
            Workflow.model_validate(candidate)
        return
    elif fault == "unknown_resolution":
        candidate["gap_resolutions"] = [resolution(gap_id="absent")]
    elif fault == "retained_and_resolved":
        candidate["gap_resolutions"] = [resolution()]
    elif fault == "blocking_downgraded":
        candidate["unresolved"][0]["blocking"] = False
    elif fault == "invented_resolution_quote":
        candidate["unresolved"] = candidate["unresolved"][1:]
        candidate["gap_resolutions"] = [resolution()]
        candidate["gap_resolutions"][0]["evidence"][0]["quote"] = "Invented resolution evidence"
    elif fault == "unknown_retained":
        candidate["unresolved"].append({**candidate["unresolved"][0], "id": "chemistry:absent"})
    elif fault == "duplicate_reader_gap":
        raw = reports["chemistry"].model_dump(mode="json")
        raw["unresolved"].append(raw["unresolved"][0])
        reports["chemistry"] = ReadingReport.model_validate(raw)
    elif fault == "report_role_mismatch":
        reports["chemistry"] = reports["information"]
    errors = validate_reader_gaps(Workflow.model_validate(candidate), reports, packet)
    assert any(message in error for error in errors), errors


def test_gap_resolution_requires_evidence_and_no_reader_gap_refs_in_baseline(candidate, packet):
    with pytest.raises(ValidationError, match="at least 1"):
        GapResolution.model_validate({**resolution(), "evidence": []})
    assert validate_reader_gaps(Workflow.model_validate(candidate), {}, packet) == []
    candidate["gap_resolutions"] = [resolution()]
    errors = validate_reader_gaps(Workflow.model_validate(candidate), {}, packet)
    assert any("unknown reader gap" in error for error in errors)


@pytest.mark.parametrize("model", [Workflow, WorkflowDraft])
def test_gap_resolution_keys_are_unique_without_prior_reader_reports(candidate, model):
    first = resolution()
    second = {**resolution(), "rationale": "A different scientific assertion under the same key."}
    candidate["gap_resolutions"] = [first, second]
    if model is WorkflowDraft:
        candidate["ordering_flows"] = [flow for flow in candidate.pop("flows")
                                       if flow["kind"] in {"precedence", "control"}]
    with pytest.raises(ValidationError, match="Duplicate gap resolutions"):
        model.model_validate(candidate)


def test_distinct_reader_roles_with_same_gap_id_have_distinct_review_targets(candidate, packet):
    candidate["gap_resolutions"] = [resolution("chemistry"), resolution("information")]
    workflow = Workflow.model_validate(candidate)
    assert {"resolution:chemistry:condition", "resolution:information:condition"} <= target_ids(
        workflow)
    review = WorkflowReview.model_validate(review_for(workflow, packet))
    assert validate_review(review, workflow, packet) == []


def test_record_repair_omissions_require_source_evidence():
    raw = {"description": "A reported route is missing", "blocking": False,
           "action": "revise_record", "evidence": []}
    with pytest.raises(ValidationError, match="revise_record.*evidence"):
        Omission.model_validate(raw)
    omission = Omission.model_validate({**raw, "evidence": [evidence()]})
    assert omission.action == "revise_record"


def test_unavailable_evidence_request_may_have_no_quote():
    omission = Omission.model_validate({"description": "The cited dependency is unavailable",
        "blocking": True, "action": "retrieve_evidence", "evidence": []})
    assert omission.action == "retrieve_evidence" and omission.evidence == ()
