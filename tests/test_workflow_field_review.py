"""Offline accountability controls; these do not evaluate scientific entailment."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from genomics_discovery.curation.validation import canonical_hash
from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.field_review import (
    FieldAdjudication,
    WorkflowFieldReview,
    field_review_outcome,
    make_field_checks,
    make_field_review,
    validate_field_review,
)
from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowDraft, digest


@pytest.fixture
def case():
    passages = ["Prepare a library from the specimen.",
                "Quantify an aliquot before sequencing.",
                "Sequence the library; retain the sequence reads."]
    packet = {"method_id": "synthetic", "title": "Scoped protocol", "scope": "Library and readout",
              "variant": "Original", "sources": [{"source_id": "paper", "passages": [
                  {"passage_id": f"p{i}", "text": text, "locator": f"Methods {i}"}
                  for i, text in enumerate(passages)]}]}
    evidence = [{"source_id": "paper", "passage_id": f"p{i}", "quote": text,
                 "support": "reported", "rationale": "Reported procedure in this variant."}
                for i, text in enumerate(passages)]
    draft = {**{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
        "states": [{"id": sid, "kind": kind, "description": sid, "evidence": [evidence[i]]}
                   for sid, kind, i in (("specimen", "material", 0), ("library", "material", 0),
                                        ("quantity", "data", 1), ("reads", "data", 2))],
        "operations": [], "modules": [{"id": "main", "name": "Library and readout",
            "operation_ids": ["prepare", "quantify", "sequence"],
            "boundary_rationale": "Interpretive grouping of the supported operations."}],
        "unresolved": [{"id": "recipe", "description": "A recipe detail remains unestablished.",
            "blocking": False, "next_evidence": "Inspect the cited recipe dependency."}],
        "gap_resolutions": [{"reader_role": "chemistry", "gap_id": "library_identity",
            "rationale": "Retain the source-described library boundary.", "evidence": [evidence[0]]}]}
    for i, (ident, source, product) in enumerate((
        ("prepare", "specimen", "library"), ("quantify", "library", "quantity"),
        ("sequence", "library", "reads"))):
        draft["operations"].append({"id": ident, "name": ident, "action": passages[i],
            "inputs": [source], "outputs": [product], "resources": [{"kind": "condition",
                "description": "Source-qualified condition", "evidence": [evidence[i]]}],
            "information_effect": "Retain the scoped material or information identity.",
            "evidence": [evidence[i]],
            "input_bindings": [{"state_id": source, "basis": "Supported input association.",
                                "evidence": [evidence[i]]}],
            "output_bindings": [{"state_id": product, "basis": "Supported output association.",
                                 "evidence": [evidence[i]]}]})
    return assemble_workflow(WorkflowDraft.model_validate(draft), require_bindings=True), packet


def supported(workflow, packet, **history):
    data = make_field_review(workflow, packet, **history).model_dump(mode="json")
    # A synthetic evaluator judgment, not a claim of real contextual entailment.
    passage = packet["sources"][0]["passages"][0]
    for check in data["checks"]:
        check.update(verdict="supported", rationale="The evaluator inspected this exact group.",
                     inspected_passages=[{"source_id": "paper", "passage_id": passage["passage_id"]}],
                     evidence=[{"source_id": "paper", "passage_id": passage["passage_id"],
                         "quote": passage["text"], "support": "reported", "rationale": "Contextual judgment."}])
    return WorkflowFieldReview.model_validate(data)


def change_check(review, group="operation:quantify", **updates):
    data = review.model_dump(mode="json")
    next(item for item in data["checks"] if item["group_id"] == group).update(updates)
    return WorkflowFieldReview.model_validate(data)


def negative(workflow, packet, verdict="unsupported", pointer="/operations/1/action"):
    review = supported(workflow, packet)
    return change_check(review, verdict=verdict, affected_fields=[pointer],
                        rationale="This exact field overstates the inspected support.")


def adjudication(prior, workflow, pointer="/operations/1/action"):
    # Values are bound to the actual prior field, not the evaluator's paraphrase.
    value = workflow.model_dump(mode="json")
    for key in pointer[1:].split("/"):
        value = value[int(key)] if isinstance(value, list) else value[key]
    check = next(item for item in prior.checks if item.group_id == "operation:quantify")
    return FieldAdjudication(prior_group_id=check.group_id,
        prior_field_binding={"field_pointer": pointer, "value_sha256": canonical_hash(value)},
        rationale="Prior reviewer misread the scoped source; explicit reconsideration.",
        inspected_passages=check.inspected_passages, evidence=check.evidence)


def test_scaffold_is_deterministic_unassessed_and_nonmutating(case):
    workflow, packet = case
    before = workflow.model_dump(mode="json"), deepcopy(packet)
    review = make_field_review(workflow, packet)
    assert review.model_dump(mode="json")["checks"] == make_field_checks(workflow)
    assert make_field_review(workflow, packet) == review
    assert validate_field_review(review, workflow, packet) == []
    assert field_review_outcome(review, workflow, packet) == "unassessed"
    assert validate_field_review(review, workflow, packet, require_supported=True)
    assert before == (workflow.model_dump(mode="json"), packet)


def test_inventory_includes_branch_qualifiers_uncertainty_and_interpretation(case):
    workflow, _ = case
    checks = {item["group_id"]: item for item in make_field_checks(workflow)}
    paths = {binding["field_pointer"] for item in checks.values() for binding in item["field_bindings"]}
    assert {"/scope", "/variant", "/states/0/kind", "/states/0/description",
            "/operations/1/action", "/operations/1/resources", "/operations/1/information_effect",
            "/operations/1/input_bindings", "/operations/1/output_bindings",
            "/flows/0/basis", "/flows/0/condition", "/flows/0/selection",
            "/modules/0/boundary_rationale",
            "/unresolved/0/description", "/unresolved/0/next_evidence",
            "/gap_resolutions/0/rationale", "/states/0/evidence"} <= paths
    assert checks["workflow"]["field_bindings"][2] == {
        "field_pointer": "/scope", "value_sha256": canonical_hash(workflow.scope)}


def test_conditional_alternative_routes_are_bound_as_authored(case):
    workflow, _ = case
    data = workflow.model_dump(mode="json")
    data.pop("flows")
    alternative = deepcopy(data["states"][2])
    alternative["id"] = "alternate_quantity"
    data["states"].append(alternative)
    measurement = data["operations"][1]
    measurement["outputs"].append("alternate_quantity")
    measurement["output_bindings"].append(deepcopy(measurement["output_bindings"][0]))
    for binding, sid, condition in zip(measurement["output_bindings"],
        ("quantity", "alternate_quantity"), ("Reported format A applies.", "Reported format B applies.")):
        binding.update(state_id=sid, selection="alternative", alternative_group="format", condition=condition)
    conditional = assemble_workflow(WorkflowDraft.model_validate(data), require_bindings=True)
    groups = make_field_checks(conditional)
    assert any(binding["field_pointer"].endswith("/alternative_group")
               for group in groups for binding in group["field_bindings"])
    check = next(item for item in groups if item["group_id"] == "operation:quantify")
    assert {item["field_pointer"]: item["value_sha256"] for item in check["field_bindings"]}[
        "/operations/1/output_bindings"] == canonical_hash(
            conditional.model_dump(mode="json")["operations"][1]["output_bindings"])


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "stale_pointer", "stale_hash"])
def test_positive_requires_exact_complete_host_inventory(case, mutation):
    workflow, packet = case
    data = supported(workflow, packet).model_dump(mode="json")
    if mutation == "missing":
        data["checks"].pop()
    elif mutation == "duplicate":
        data["checks"].append(deepcopy(data["checks"][0]))
    elif mutation == "unknown":
        data["checks"][0]["group_id"] = "invented"
    else:
        binding = data["checks"][0]["field_bindings"][0]
        binding["field_pointer" if mutation == "stale_pointer" else "value_sha256"] = (
            "/operations/0/action" if mutation == "stale_pointer" else "0" * 64)
    review = WorkflowFieldReview.model_validate(data)
    assert validate_field_review(review, workflow, packet)
    with pytest.raises(ValueError):
        field_review_outcome(review, workflow, packet)


@pytest.mark.parametrize("field", ["action", "information_effect", "resources", "evidence"])
def test_appended_claim_or_changed_evidence_invalidates_prior_binding(case, field):
    workflow, packet = case
    review = supported(workflow, packet)
    data = workflow.model_dump(mode="json")
    if field == "resources":
        data["operations"][1][field][0]["description"] += "; never recoverable."
    elif field == "evidence":
        data["operations"][1][field][0]["rationale"] += " No material survives."
    else:
        data["operations"][1][field] += " No monitoring product can ever be recovered."
    changed = Workflow.model_validate(data)
    errors = validate_field_review(review, changed, packet)
    assert any("workflow digest" in error for error in errors)
    assert any("operation:quantify: stale" in error for error in errors)


@pytest.mark.parametrize("mutation", ["quote", "unknown_ref", "uninspected", "duplicate_inspection", "packet"])
def test_evidence_integrity_and_declared_inspection_are_required(case, mutation):
    workflow, packet = case
    data = supported(workflow, packet).model_dump(mode="json")
    check = data["checks"][0]
    if mutation == "quote":
        check["evidence"][0]["quote"] = "A nonexistent quotation."
    elif mutation == "unknown_ref":
        check["inspected_passages"][0]["passage_id"] = "not_registered"
    elif mutation == "uninspected":
        check["inspected_passages"][0]["passage_id"] = "p1"
    elif mutation == "duplicate_inspection":
        check["inspected_passages"] *= 2
    else:
        packet = deepcopy(packet)
        packet["sources"][0]["passages"].append({"passage_id": "new", "text": "Different context."})
    assert validate_field_review(WorkflowFieldReview.model_validate(data), workflow, packet)


@pytest.mark.parametrize("verdict", ["unsupported", "not_established"])
def test_negative_is_valid_and_blocks_positive_without_equating_unknown_with_false(case, verdict):
    workflow, packet = case
    review = negative(workflow, packet, verdict)
    if verdict == "not_established":
        review = change_check(review, evidence=[])
    assert validate_field_review(review, workflow, packet) == []
    assert field_review_outcome(review, workflow, packet) == verdict
    assert validate_field_review(review, workflow, packet, require_supported=True)


@pytest.mark.parametrize("pointer", ["/scope", "/operations/1/action/absent",
    "/operations/1/resources/00/description", "/operations/1/resources/0/missing",
    "/operations/1/resources/0/description~2", "/operations/10/action"])
def test_negative_must_identify_real_fields_inside_its_group(case, pointer):
    workflow, packet = case
    assert validate_field_review(negative(workflow, packet, pointer=pointer), workflow, packet)


def test_negative_requires_affected_fields_and_meaningful_inspection(case):
    workflow, packet = case
    review = negative(workflow, packet)
    for updates in ({"affected_fields": []}, {"evidence": []}, {"inspected_passages": []},
                    {"affected_fields": ["/operations/1/action"] * 2}):
        assert validate_field_review(change_check(review, **updates), workflow, packet)
    assert validate_field_review(change_check(supported(workflow, packet),
        affected_fields=["/operations/1/action"]), workflow, packet)


def test_inference_uses_quoted_premises_without_literal_quote_of_deduction(case):
    workflow, packet = case
    review = supported(workflow, packet)
    data = review.model_dump(mode="json")
    check = next(item for item in data["checks"] if item["group_id"] == "module:main")
    check["evidence"][0].update(support="inferred", rationale=
        "Grouping the cited operations preserves their interfaces; the paper need not name this module.")
    review = WorkflowFieldReview.model_validate(data)
    assert validate_field_review(review, workflow, packet, require_supported=True) == []
    assert field_review_outcome(review, workflow, packet) == "supported"


def test_accurate_unknown_qualification_can_be_supported_while_readiness_stays_blocked(case):
    workflow, packet = case
    data = workflow.model_dump(mode="json")
    data["unresolved"][0]["blocking"] = True
    qualified = Workflow.model_validate(data)
    before = digest(qualified)
    # The inspected source context is quoted; no fabricated absence quotation.
    review = supported(qualified, packet)
    assert "unestablished" not in review.checks[0].evidence[0].quote
    assert field_review_outcome(review, qualified, packet) == "supported"
    assert qualified.unresolved[0].blocking is True
    assert digest(qualified) == before


@pytest.mark.parametrize("verdict", ["unsupported", "not_established"])
def test_unchanged_previous_negative_needs_explicit_reconsideration(case, verdict):
    workflow, packet = case
    prior = negative(workflow, packet, verdict)
    history = {"prior_review": prior, "prior_workflow": workflow}
    current = supported(workflow, packet, **history)
    errors = validate_field_review(current, workflow, packet, **history)
    assert any("unchanged prior negative field" in error for error in errors)
    current = current.model_copy(update={"adjudications": (adjudication(prior, workflow),)})
    assert field_review_outcome(current, workflow, packet, **history) == "supported"
    # Retaining a negative judgment needs no attempt to overrule itself.
    still_negative = change_check(supported(workflow, packet, **history), verdict=verdict,
                                 affected_fields=["/operations/1/action"])
    assert field_review_outcome(still_negative, workflow, packet, **history) == verdict


def test_unrelated_edit_and_record_reorder_do_not_clear_rejected_field(case):
    workflow, packet = case
    prior = negative(workflow, packet)
    history = {"prior_review": prior, "prior_workflow": workflow}
    data = workflow.model_dump(mode="json")
    data["operations"][1]["information_effect"] = "A corrected, unrelated qualification."
    data["operations"].reverse()
    changed = Workflow.model_validate(data)
    errors = validate_field_review(supported(changed, packet, **history), changed, packet, **history)
    assert any("unchanged prior negative field" in error for error in errors)


def test_nested_negative_is_protected_if_other_resource_fields_change(case):
    workflow, packet = case
    pointer = "/operations/1/resources/0/description"
    prior = negative(workflow, packet, pointer=pointer)
    history = {"prior_review": prior, "prior_workflow": workflow}
    data = workflow.model_dump(mode="json")
    data["operations"][1]["resources"][0]["evidence"][0]["rationale"] = "Improved context."
    changed = Workflow.model_validate(data)
    assert validate_field_review(supported(changed, packet, **history), changed, packet, **history)


def test_changed_affected_field_is_not_automatically_certified_correct(case):
    workflow, packet = case
    prior = negative(workflow, packet)
    history = {"prior_review": prior, "prior_workflow": workflow}
    data = workflow.model_dump(mode="json")
    data["operations"][1]["action"] = "Quantify the aliquot; its later material fate is unestablished."
    changed = Workflow.model_validate(data)
    unassessed = make_field_review(changed, packet, **history)
    assert field_review_outcome(unassessed, changed, packet, **history) == "unassessed"
    assert field_review_outcome(supported(changed, packet, **history), changed, packet, **history) == "supported"


@pytest.mark.parametrize("mutation", ["wrong_hash", "wrong_path", "duplicate", "quote", "inspection"])
def test_adjudication_cannot_be_stale_unbound_or_uncited(case, mutation):
    workflow, packet = case
    prior = negative(workflow, packet)
    history = {"prior_review": prior, "prior_workflow": workflow}
    current = supported(workflow, packet, **history).model_dump(mode="json")
    item = adjudication(prior, workflow).model_dump(mode="json")
    if mutation == "wrong_hash":
        item["prior_field_binding"]["value_sha256"] = "0" * 64
    elif mutation == "wrong_path":
        item["prior_field_binding"]["field_pointer"] = "/operations/1/information_effect"
    elif mutation == "quote":
        item["evidence"][0]["quote"] = "Not present in source."
    elif mutation == "inspection":
        item["inspected_passages"][0]["passage_id"] = "p1"
    current["adjudications"] = [item, deepcopy(item)] if mutation == "duplicate" else [item]
    assert validate_field_review(WorkflowFieldReview.model_validate(current), workflow, packet, **history)


def test_history_custody_is_explicit_and_prior_review_integrity_rechecked(case):
    workflow, packet = case
    prior = negative(workflow, packet)
    history = {"prior_review": prior, "prior_workflow": workflow}
    review = supported(workflow, packet, **history)
    assert validate_field_review(review, workflow, packet)
    assert validate_field_review(review, workflow, packet, prior_review=prior)
    assert validate_field_review(review.model_copy(update={"prior_review_sha256": "0" * 64}),
                                workflow, packet, **history)
    malformed_prior = change_check(prior, affected_fields=["/missing"])
    assert validate_field_review(supported(workflow, packet, prior_review=malformed_prior,
        prior_workflow=workflow), workflow, packet, prior_review=malformed_prior, prior_workflow=workflow)
    with pytest.raises(ValueError, match="both prior"):
        make_field_review(workflow, packet, prior_review=prior)


def test_semantic_truth_is_not_inferred_from_valid_pointer_bindings(case):
    workflow, packet = case
    data = workflow.model_dump(mode="json")
    data["operations"][1]["action"] += " No monitoring product can ever be recovered."
    changed = Workflow.model_validate(data)
    # Intentionally incorrect injected evaluator: integrity cannot decide entailment.
    review = supported(changed, packet)
    assert field_review_outcome(review, changed, packet) == "supported"
    assert field_review_outcome(negative(changed, packet), changed, packet) == "unsupported"


def test_sidecar_schema_rejects_invented_fields_and_unsupported_verdicts(case):
    workflow, packet = case
    data = make_field_review(workflow, packet).model_dump(mode="json")
    data["production_approved"] = True
    with pytest.raises(ValidationError):
        WorkflowFieldReview.model_validate(data)
    data.pop("production_approved")
    data["checks"][0]["verdict"] = "probably_fine"
    with pytest.raises(ValidationError):
        WorkflowFieldReview.model_validate(data)
