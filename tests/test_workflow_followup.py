"""Adversarial accounting/provenance checks; not scientific accuracy tests."""

from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_workflow_extraction_contract import candidate as candidate
from test_workflow_extraction_contract import packet as packet
from test_workflow_extraction_contract import review_for

from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowReview, digest
from genomics_discovery.curation.workflow_followup.models import GapAudit
from genomics_discovery.curation.workflow_followup.tools import followup_queue, validate_audit


@pytest.fixture
def audit_case(candidate, packet):
    candidate["unresolved"] = [{"id": "temperature", "description": "Temperature unknown",
                                "blocking": False, "next_evidence": "Original methods"},
                               {"id": "guide", "description": "Manual not registered",
                                "blocking": False, "next_evidence": "Original manual"}]
    workflow = Workflow.model_validate(candidate)
    review = WorkflowReview.model_validate(review_for(workflow, packet))
    evidence = {"source_id": "paper", "passage_id": "p1",
                "quote": "Quantify an aliquot before sequencing.",
                "support": "reported", "rationale": "The procedural context is recorded."}
    issue = {"id": "temperature-question", "question": "What temperature is reported?",
             "targets": ["operation:prepare"], "action": "retain_unknown",
             "rationale": "This inspected passage does not supply a temperature.",
             "consequence_for_scope": "Recipe detail; this boundary remains described.",
             "inspected_passages": [{"source_id": "paper", "passage_id": "p1"}],
             "evidence": [evidence]}
    raw = {"workflow_sha256": digest(workflow), "source_packet_sha256": digest(packet),
           "workflow_review_sha256": digest(review),
           "checks": [{"gap_id": "temperature", "issues": [issue]}]}
    return workflow, review, packet, raw


def test_scoped_audit_keeps_unaudited_gaps_explicit_and_never_edits_parent(audit_case):
    workflow, review, packet, raw = audit_case
    before = digest(workflow)
    audit = GapAudit.model_validate(raw)
    queue = followup_queue(audit, workflow, review, packet, ["temperature"])
    assert queue["unreviewed_gap_ids"] == ["guide"]
    assert queue["requests"][0]["status"] == "proposed_not_resolved"
    assert queue["action_counts"] == {"retain_unknown": 1}
    assert digest(workflow) == before


@pytest.mark.parametrize("fault", ["missing", "extra", "duplicate", "stale_workflow",
                                   "stale_packet", "stale_review", "target", "quote",
                                   "inspection", "duplicate_issue", "unknown_passage"])
def test_rejects_incomplete_stale_or_untraceable_audit(audit_case, fault):
    workflow, review, packet, original = audit_case
    raw = deepcopy(original)
    check = raw["checks"][0]
    issue = check["issues"][0]
    if fault == "missing":
        raw["checks"] = []
    elif fault == "extra":
        check["gap_id"] = "made-up"
    elif fault == "duplicate":
        raw["checks"].append(deepcopy(check))
    elif fault.startswith("stale_"):
        raw[{"stale_workflow": "workflow_sha256", "stale_packet": "source_packet_sha256",
             "stale_review": "workflow_review_sha256"}[fault]] = "0" * 64
    elif fault == "target":
        issue["targets"] = ["operation:hallucinated"]
    elif fault == "quote":
        issue["evidence"][0]["quote"] = "A fabricated recipe at 99 C."
    elif fault == "inspection":
        issue["inspected_passages"][0]["passage_id"] = "p2"
    elif fault == "duplicate_issue":
        check["issues"].append(deepcopy(issue))
    elif fault == "unknown_passage":
        issue["inspected_passages"][0]["passage_id"] = "absent"
    assert validate_audit(GapAudit.model_validate(raw), workflow, review, packet, ["temperature"])


@pytest.mark.parametrize("action", ["retrieve_source", "inspect_original", "repair_record"])
def test_followup_cannot_be_an_unactionable_label(audit_case, action):
    raw = deepcopy(audit_case[-1])
    raw["checks"][0]["issues"][0]["action"] = action
    with pytest.raises(ValidationError):
        GapAudit.model_validate(raw)


def test_compound_gap_can_request_different_actions_without_closing_it(audit_case):
    workflow, review, packet, raw = audit_case
    raw = deepcopy(raw)
    other = deepcopy(raw["checks"][0]["issues"][0])
    other.update(id="manual-retrieval", action="retrieve_source", next_evidence={
        "required_property": "Temperature for the exact delegated enzyme version",
        "expected_document": "Original version of the named manufacturer manual",
        "query": "Original manufacturer enzyme protocol revision temperature",
    })
    raw["checks"][0]["issues"].append(other)
    queue = followup_queue(GapAudit.model_validate(raw), workflow, review, packet, ["temperature"])
    assert queue["action_counts"] == {"retain_unknown": 1, "retrieve_source": 1}
    assert len({r["request_id"] for r in queue["requests"]}) == 2


def test_correction_cannot_be_smuggled_into_an_unknown(audit_case):
    raw = deepcopy(audit_case[-1])
    raw["checks"][0]["issues"][0]["proposed_correction"] = "Assume the usual buffer."
    with pytest.raises(ValidationError):
        GapAudit.model_validate(raw)


def test_original_inspection_cannot_be_claimed_without_bound_originals(audit_case):
    workflow, review, packet, raw = audit_case
    raw = deepcopy(raw)
    raw["checks"][0]["issues"][0]["inspected_originals"] = [{
        "source_id": "paper", "page": 1, "render_receipt": "fabricated.json"}]
    errors = validate_audit(GapAudit.model_validate(raw), workflow, review, packet, ["temperature"])
    assert any("invalid original inspection" in error for error in errors)


def test_audit_cannot_define_an_invented_gap_as_its_requested_scope(audit_case):
    workflow, review, packet, raw = audit_case
    raw = deepcopy(raw)
    raw["checks"][0]["gap_id"] = "invented"
    errors = validate_audit(GapAudit.model_validate(raw), workflow, review, packet, ["invented"])
    assert any("belong to this workflow" in error for error in errors)


def test_valid_original_page_is_not_misclassified_as_a_text_passage(audit_case, tmp_path):
    import json

    from genomics_discovery.curation.run_state import sha
    workflow, review, packet, raw = audit_case
    raw = deepcopy(raw)
    image = tmp_path / "page.png"
    image.write_bytes(b"Synthetic page receipt; not a scientific image test.")
    originals = {"revision_id": "frozen-binding", "bindings": {
        "paper": {"original_sha256": "0" * 64}}}
    receipt = tmp_path / "render_receipt.json"
    receipt.write_text(json.dumps({"source_id": "paper", "page": 1,
        "original_sha256": "0" * 64, "binding_revision_id": "frozen-binding",
        "image": str(image), "image_sha256": sha(image)}))
    raw["checks"][0]["issues"][0]["inspected_originals"] = [{
        "source_id": "paper", "page": 1, "render_receipt": str(receipt)}]
    assert validate_audit(GapAudit.model_validate(raw), workflow, review, packet, ["temperature"],
                          originals=originals, attempt=tmp_path) == []
