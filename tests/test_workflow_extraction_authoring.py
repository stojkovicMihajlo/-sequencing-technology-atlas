"""Compact authoring preserves graph semantics and the existing provenance gate."""

import json
import sys
from copy import deepcopy

import pytest

from genomics_discovery.curation.workflow_extraction.__main__ import main
from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.authoring import (
    authoring_schema,
    authoring_view,
    expand_authoring,
)
from genomics_discovery.curation.workflow_extraction.models import (
    Workflow,
    WorkflowDraft,
    WorkflowReview,
    digest,
    target_ids,
    validate_evidence,
    validate_review,
)
from genomics_discovery.curation.workflow_extraction.sources import build_packet

EVIDENCE = {"source_id": "paper", "passage_id": "p1", "quote": "Reported transformation.",
            "support": "reported", "rationale": "Source description."}


@pytest.fixture
def draft():
    return WorkflowDraft.model_validate({
        "method_id": "example", "title": "Protocol", "scope": "General architecture",
        "variant": "version1", "states": [
            {"id": s, "kind": k, "description": s, "evidence": [EVIDENCE]}
            for s, k in [("native", "material"), ("processed", "material"),
                         ("observations", "data")]],
        "operations": [
            {"id": i, "name": i, "action": "Reported transformation.", "inputs": inputs,
             "outputs": outputs, "resources": [], "information_effect": "Preserve identity.",
             "evidence": [EVIDENCE]}
            for i, inputs, outputs in [("prepare", ["native"], ["processed"]),
                                      ("measure", ["processed"], ["observations"])]],
        "modules": [{"id": "main", "name": "Preparation and readout",
                     "operation_ids": ["prepare", "measure"], "boundary_rationale": "Scope."}],
        "unresolved": [{"id": "recipe", "description": "Internal recipe unavailable.",
                        "blocking": False, "next_evidence": "Exact source protocol."}],
        "ordering_flows": [{"id": "before", "source": "prepare", "target": "measure",
                            "kind": "precedence", "basis": "Explicit source order.",
                            "evidence": [EVIDENCE]}]})


def test_draft_registry_expands_to_identical_scientific_workflow(draft):
    view = authoring_view(draft)
    assert len(view["evidence_registry"]) == 1
    expanded = expand_authoring(view)
    assert expanded == draft
    assert digest(assemble_workflow(expanded)) == digest(assemble_workflow(draft))
    assert authoring_schema("draft")["$defs"]["Operation"]["required"][-1] == "evidence_refs"


def test_full_revision_preserves_manual_flow_evidence_and_uncertainty(draft):
    workflow = assemble_workflow(draft)
    data = workflow.model_dump(mode="json")
    data["flows"][1]["evidence"][0]["support"] = "inferred"
    data["flows"][1]["evidence"][0]["rationale"] = "Explicit interpretation of routing."
    original = Workflow.model_validate(data)
    view = authoring_view(original)
    assert len(view["evidence_registry"]) == 2
    assert digest(expand_authoring(view)) == digest(original)
    view["record"]["operations"][0]["action"] = "Reported transformation with exact wording."
    revised = expand_authoring(view)
    assert revised.flows == original.flows
    assert revised.unresolved == original.unresolved
    assert revised.states == original.states
    assert digest(revised) != digest(original)
    old_review = WorkflowReview.model_validate({
        "workflow_sha256": digest(original), "source_packet_sha256": digest({"sources": []}),
        "inspected_passages": [], "checks": [], "omissions": [], "notes": []})
    assert any("workflow" in error.lower()
               for error in validate_review(old_review, revised, {"sources": []}))


@pytest.mark.parametrize("change", ["unknown", "unused", "mixed", "empty", "string_refs",
                                   "acceptance", "wrong_kind", "extra_registry_field"])
def test_encoding_cannot_bypass_strict_scientific_model(draft, change):
    view = authoring_view(draft)
    if change == "unknown":
        view["record"]["states"][0]["evidence_refs"] = ["invented"]
    elif change == "unused":
        view["evidence_registry"]["unused"] = deepcopy(EVIDENCE)
    elif change == "mixed":
        view["record"]["states"][0]["evidence"] = [EVIDENCE]
    elif change == "empty":
        view["record"]["states"][0]["evidence_refs"] = []
    elif change == "string_refs":
        view["record"]["states"][0]["evidence_refs"] = "e0001"
    elif change == "acceptance":
        view["record"]["accepted"] = True
    elif change == "wrong_kind":
        view["record_kind"] = "review"
    else:
        view["evidence_registry"]["e0001"]["verdict"] = "supported"
    with pytest.raises(ValueError):
        expand_authoring(view)


def test_registered_quotes_still_need_exact_source_integrity(draft):
    view = authoring_view(draft)
    view["evidence_registry"]["e0001"]["quote"] = "Invented transformation."
    expanded = assemble_workflow(expand_authoring(view))
    packet = {"sources": [{"source_id": "paper", "passages": [
        {"passage_id": "p1", "text": "Reported transformation."}]}]}
    assert validate_evidence(expanded, packet)


def test_review_registry_preserves_every_check_rationale_and_exact_bindings(draft):
    workflow = assemble_workflow(draft)
    packet = {"sources": [{"source_id": "paper", "passages": [
        {"passage_id": "p1", "text": "Reported transformation."}]}]}
    review = WorkflowReview.model_validate({
        "workflow_sha256": digest(workflow), "source_packet_sha256": digest(packet),
        "inspected_passages": [{"source_id": "paper", "passage_id": "p1"}],
        "checks": [{"target_id": target, "verdict": "supported",
                    "rationale": f"Independent explanation for {target}.", "evidence": [EVIDENCE]}
                   for target in sorted(target_ids(workflow))],
        "omissions": [], "notes": ["Remaining recipe uncertainty retained."]})
    view = authoring_view(review)
    assert view["record_kind"] == "review"
    assert len(view["evidence_registry"]) == 1
    expanded = expand_authoring(view)
    assert expanded == review
    assert digest(expanded) == digest(review)
    assert validate_review(expanded, workflow, packet) == []
    # Editing this envelope never authorizes dropping required independent checks.
    view["record"]["checks"].pop()
    assert validate_review(expand_authoring(view), workflow, packet)


def test_review_shared_registry_remains_subject_to_exact_source_quote_checks(draft):
    workflow = assemble_workflow(draft)
    packet = {"sources": [{"source_id": "paper", "passages": [
        {"passage_id": "p1", "text": "Reported transformation."}]}]}
    review = WorkflowReview.model_validate({
        "workflow_sha256": digest(workflow), "source_packet_sha256": digest(packet),
        "inspected_passages": [{"source_id": "paper", "passage_id": "p1"}],
        "checks": [{"target_id": target, "verdict": "supported",
                    "rationale": f"Independent source comparison for {target}.",
                    "evidence": [EVIDENCE]} for target in sorted(target_ids(workflow))],
        "omissions": [], "notes": []})
    view = authoring_view(review)
    view["evidence_registry"]["e0001"]["quote"] = "Absent source condition."
    assert validate_review(expand_authoring(view), workflow, packet)


@pytest.mark.parametrize("kind", ["draft", "workflow", "review"])
def test_cli_expands_to_existing_artifact_with_exact_source_and_candidate_binding(
    draft, kind, tmp_path, monkeypatch, capsys,
):
    original = tmp_path / "source.txt"
    original.write_text("Reported transformation.")
    packet = build_packet(
        **{key: getattr(draft, key) for key in ("method_id", "title", "scope", "variant")},
        sources=[{"source_id": "paper", "title": "Protocol paper", "role": "primary_paper",
                  "url": "https://example.org/protocol", "path": str(original)}])
    pid = packet["sources"][0]["passages"][0]["passage_id"]
    view = authoring_view(draft)
    view["evidence_registry"]["e0001"]["passage_id"] = pid
    draft = expand_authoring(view)
    workflow = assemble_workflow(draft)
    record = draft if kind == "draft" else workflow
    if kind == "review":
        record = WorkflowReview.model_validate({
            "workflow_sha256": digest(workflow), "source_packet_sha256": digest(packet),
            "inspected_passages": [{"source_id": "paper", "passage_id": pid}],
            "checks": [{"target_id": target, "verdict": "supported",
                        "rationale": f"Independent comparison for {target}.",
                        "evidence": [{**EVIDENCE, "passage_id": pid}]}
                       for target in sorted(target_ids(workflow))],
            "omissions": [], "notes": []})
    source_path, candidate_path = tmp_path / "packet.json", tmp_path / "workflow.json"
    source_path.write_text(json.dumps(packet))
    candidate_path.write_text(workflow.model_dump_json())
    input_path = tmp_path / "input.json"
    input_path.write_text(record.model_dump_json())
    monkeypatch.setattr(sys, "argv", ["workflow", "compact-authoring", str(input_path),
                                      "--kind", kind])
    main()
    compact_path = tmp_path / "authoring.json"
    compact_path.write_text(capsys.readouterr().out)
    output_path = tmp_path / "artifact.json"
    args = ["workflow", "expand-authoring", str(compact_path), "--packet", str(source_path),
            "--output", str(output_path)]
    if kind == "review":
        args.extend(["--candidate", str(candidate_path)])
        # A source-bound review cannot be expanded without its specific candidate.
        monkeypatch.setattr(sys, "argv", args[:-2])
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2
        assert not output_path.exists()
    monkeypatch.setattr(sys, "argv", args)
    main()
    expected = record if kind == "review" else workflow
    assert type(expected).model_validate_json(output_path.read_bytes()) == expected
