"""Injected native jobs exercise extraction, fresh review, and safe candidate output."""

from __future__ import annotations

import asyncio
import copy
import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from genomics_discovery.curation.codex_worker import CodexWorker, WorkerResult
from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowReview, digest
from genomics_discovery.curation.workflow_extraction.projection import (
    export_candidate,
    export_diagnostic,
    query_workflow,
)
from genomics_discovery.curation.workflow_extraction.sources import build_packet


@pytest.fixture
def source_case(tmp_path):
    original = tmp_path / "original.txt"
    original.write_text(
        "Wash nuclei with buffer at 4 C. Split washed nuclei into aliquots A and B. "
        "Sequence aliquot A to produce reads A. Sequence aliquot B to produce reads B. "
        "Sequence A only if its aliquot is available. "
        "Merge reads A and reads B into combined reads.", encoding="utf-8")
    packet = build_packet(
        method_id="branched_assay", title="Branched assay", scope="Nuclei washing to reads",
        variant="source_variant", sources=[{
            "source_id": "original", "title": "Original protocol",
            "url": "https://example.org/original", "role": "primary_paper", "path": str(original),
        }])
    path = tmp_path / "packet.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    source = packet["sources"][0]
    evidence = {
        "source_id": source["source_id"], "passage_id": source["passages"][0]["passage_id"],
        "quote": source["passages"][0]["text"], "support": "reported",
        "rationale": "The cited protocol describes the scoped route.",
    }
    states = [{"id": sid, "kind": kind, "description": description, "evidence": [evidence]}
              for sid, kind, description in (
                  ("nuclei", "material", "Starting nuclei"),
                  ("washed", "material", "Washed nuclei split into the declared aliquots"),
                  ("reads_a", "data", "Sequence reads from aliquot A"),
                  ("reads_b", "data", "Sequence reads from aliquot B"),
                  ("combined", "data", "Combined reads from the two aliquots"))]
    operations = [{
        "id": oid, "name": name, "action": action, "inputs": inputs, "outputs": outputs,
        "resources": resources, "information_effect": effect, "evidence": [evidence],
    } for oid, name, action, inputs, outputs, resources, effect in (
        ("seq_b", "Sequence B", "Sequence aliquot B", ["washed"], ["reads_b"], [],
         "Read sequence from aliquot B"),
        ("wash", "Wash", "Wash and split nuclei into aliquots A and B", ["nuclei"], ["washed"],
         [{"kind": "reagent", "description": "Buffer", "evidence": [evidence]},
          {"kind": "condition", "description": "4 C", "evidence": [evidence]}],
         "Retain the scoped nuclei input"),
        ("seq_a", "Sequence A", "Sequence aliquot A", ["washed"], ["reads_a"], [],
         "Read sequence from aliquot A"),
        ("merge", "Merge readouts", "Merge reads A and B", ["reads_a", "reads_b"], ["combined"],
         [], "Combine the two scoped readouts"))]
    flows = [{
        "id": fid, "source": start, "target": end, "kind": kind,
        "basis": "The source describes this route.", "condition": condition,
        "evidence": [evidence],
    } for fid, start, end, kind, condition in (
        ("start", "nuclei", "wash", "material_input", None),
        ("washed_out", "wash", "washed", "material_output", None),
        ("branch_a", "washed", "seq_a", "material_input", "Use aliquot A"),
        ("branch_b", "washed", "seq_b", "material_input", "Use aliquot B"),
        ("reads_a_out", "seq_a", "reads_a", "data_output", None),
        ("reads_b_out", "seq_b", "reads_b", "data_output", None),
        ("control_a", "wash", "seq_a", "control", "Aliquot A is available"),
        ("merge_a", "reads_a", "merge", "data_input", None),
        ("merge_b", "reads_b", "merge", "data_input", None),
        ("merged_out", "merge", "combined", "data_output", None),
        ("before_merge", "wash", "merge", "precedence", None))]
    workflow = {
        **{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
        "format_version": "protocol_workflow/1.0", "states": states, "operations": operations,
        "flows": flows, "modules": [
            {"id": "preparation", "name": "Preparation", "operation_ids": ["wash"],
             "boundary_rationale": "Curator groups the input preparation action."},
            {"id": "readout", "name": "Readout", "operation_ids": ["seq_a", "seq_b", "merge"],
             "boundary_rationale": "Curator groups the two readout branches."}], "unresolved": [],
    }
    return packet, path, workflow


def make_review(workflow_raw, packet, verdict="supported"):
    workflow = Workflow.model_validate(workflow_raw)
    checks = []
    for kind, items in (("state", workflow.states), ("operation", workflow.operations),
                        ("flow", workflow.flows)):
        for item in items:
            evidence = [*item.evidence, *(ev for resource in getattr(item, "resources", ())
                                         for ev in resource.evidence)]
            checks.append({
                "target_id": f"{kind}:{item.id}",
                "verdict": verdict if item.id == "wash" else "supported",
                "rationale": "Fresh source inspection of the scoped assertion.",
                "evidence": list({digest(ev): ev.model_dump(mode="json")
                                  for ev in evidence}.values()),
            })
    checks.extend({
        "target_id": f"resolution:{resolution.reader_role}:{resolution.gap_id}",
        "verdict": "supported", "rationale": "Fresh source inspection of the gap resolution.",
        "evidence": [item.model_dump(mode="json") for item in resolution.evidence],
    } for resolution in workflow.gap_resolutions)
    inspected = sorted({(item["source_id"], item["passage_id"])
                        for check in checks for item in check["evidence"]})
    return {"workflow_sha256": digest(workflow), "source_packet_sha256": digest(packet),
            "inspected_passages": [{"source_id": sid, "passage_id": pid} for sid, pid in inspected],
            "checks": checks, "omissions": [], "notes": ["Boundaries are curator interpretations."]}


class FakeWorker:
    """Exercise native archive checks without running models or native processes."""

    def __init__(self, source_case, *, fault=None, review_verdicts=(), blocking=False,
                 reader_gap=None):
        self.packet, self.packet_path, workflow = source_case
        self.workflow = copy.deepcopy(workflow)
        if blocking:
            self.workflow["unresolved"] = [{
                "id": "missing_condition", "description": "A condition remains unknown.",
                "blocking": True, "next_evidence": "Inspect the relevant original supplement.",
            }]
        self.fault = fault
        self.reader_gap = reader_gap
        self.reader_gap_detail = {
            "id": "reader_missing", "description": "Reader found a missing condition.",
            "blocking": True, "next_evidence": "Inspect the relevant original supplement.",
        }
        if reader_gap == "retained":
            self.workflow["unresolved"].append({
                **self.reader_gap_detail, "id": "chemistry:reader_missing"})
        if reader_gap == "resolved":
            self.workflow["gap_resolutions"] = [{
                "reader_role": "chemistry", "gap_id": "reader_missing",
                "rationale": "The cited passage specifies the 4 C condition.",
                "evidence": self.workflow["operations"][1]["evidence"],
            }]
        self.review_verdicts = list(review_verdicts)
        self.calls = []
        self.reader_ready = asyncio.Event()
        self.active_readers = self.max_active_readers = 0
        self.integrations = self.reviews = 0

    async def run(self, prompt, attempt_dir):
        role = re.search(r"ROLE:\s*(\w+)", prompt).group(1)
        attempt_dir = Path(attempt_dir)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        index = len(self.calls) + 1
        self.calls.append({"role": role, "prompt": prompt, "directory": attempt_dir})
        (attempt_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        if role in {"chemistry", "information"}:
            self.active_readers += 1
            self.max_active_readers = max(self.max_active_readers, self.active_readers)
            if self.active_readers == 2:
                self.reader_ready.set()
            await asyncio.wait_for(self.reader_ready.wait(), timeout=2)
            self.active_readers -= 1
            evidence = self.workflow["states"][0]["evidence"][0]
            artifact = {"role": role,
                        "inspected_passages": [{key: evidence[key]
                                                for key in ("source_id", "passage_id")}],
                        "findings": [{"id": role + "_finding",
                                      "description": "Scoped source route.",
                                      "evidence": [evidence]}],
                        "unresolved": [self.reader_gap_detail]
                        if role == "chemistry" and self.reader_gap else []}
        elif role == "integrator":
            self.integrations += 1
            if self.integrations > 1:
                self.workflow["operations"][1]["action"] += "; retain the declared aliquot routes"
            artifact = copy.deepcopy(self.workflow)
            if self.fault == "model_field":
                artifact["accepted"] = True
            if self.fault == "evidence_quote":
                artifact["operations"][0]["evidence"][0]["quote"] = "A fictional evidence quote"
            if self.fault == "changed_source":
                Path(self.packet["sources"][0]["original_path"]).write_text("Changed source bytes")
        else:
            assert role == "reviewer"
            verdict = self.review_verdicts[self.reviews] if self.reviews < len(
                self.review_verdicts) else "supported"
            self.reviews += 1
            artifact = make_review(self.workflow, self.packet, verdict)
        serialized = ("{" if self.fault == "malformed" and role == "integrator"
                      else json.dumps(artifact))
        (attempt_dir / "artifact.json").write_text(serialized, encoding="utf-8")
        thread_id = "reused-thread" if self.fault == "reused_thread" else f"fresh-thread-{index}"
        events = [{"type": "thread.started", "thread_id": thread_id}, {"type": "turn.started"}]
        if self.fault != "unfinished_turn":
            events.append({"type": "turn.completed", "usage": {}})
        (attempt_dir / "events.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events))
        (attempt_dir / "final.txt").write_text("Created artifact.json")
        (attempt_dir / "final.json").write_text(json.dumps({"artifact": "artifact.json"}))
        result = WorkerResult(9 if self.fault == "worker_exit" else 0, thread_id, {}, .01, None)
        (attempt_dir / "result.json").write_text(json.dumps(asdict(result)))
        if self.fault == "returned_exit":
            return WorkerResult(9, thread_id, {}, .01, None)
        if self.fault == "returned_error":
            return WorkerResult(0, thread_id, {}, .01, "Returned worker error")
        if self.fault == "returned_thread":
            return WorkerResult(0, "different-thread", {}, .01, None)
        return result


async def run_case(source_case, tmp_path, *, mode="specialists", initial_workflow=None, **kwargs):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case, **kwargs)
    if mode == "review_only" and initial_workflow is None:
        initial_workflow = tmp_path / "seed_workflow.json"
        initial_workflow.write_text(json.dumps(worker.workflow), encoding="utf-8")
    state = await execute(source_case[1], tmp_path / "run", worker=worker, mode=mode,
                          initial_workflow=initial_workflow)
    return state, worker


async def test_specialists_parallel_then_integration_and_fresh_source_review(source_case, tmp_path):
    state, worker = await run_case(source_case, tmp_path)
    assert state["status"] == "supported"
    assert worker.max_active_readers == 2
    assert [call["role"] for call in worker.calls] == [
        "chemistry", "information", "integrator", "reviewer"]
    integration = worker.calls[2]["prompt"]
    review = worker.calls[3]["prompt"]
    assert "chemistry" in integration and "information" in integration
    assert "workflow_sha256" in review
    assert digest(Workflow.model_validate(worker.workflow)) in review
    assert digest(worker.packet) in review
    for call in worker.calls:
        assert "schema" in call["prompt"].lower() and "packet" in call["prompt"].lower()
    assert "chemistry/artifact.json" not in review and "information/artifact.json" not in review
    assert "chemistry.json" not in review and "information.json" not in review
    assert len({attempt["thread_id"] for attempt in state["attempts"]}) == 4
    graph_path = tmp_path / "run" / "graph_candidate.json"
    protocol_path = tmp_path / "run" / "protocol.txt"
    assert graph_path.is_file() and protocol_path.is_file()
    graph = json.loads(graph_path.read_text())
    assert graph["status"] == "model_supported_within_declared_scope"
    assert graph["workflow_sha256"] == state["workflow_digest"]
    assert graph["review"]["source_packet_sha256"] == graph["source_packet_sha256"]
    assert graph["scope"] == source_case[0]["scope"]
    assert "graph_candidate" in state["artifacts"] and "protocol" in state["artifacts"]


async def test_baseline_has_no_readers(source_case, tmp_path):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    state = await execute(source_case[1], tmp_path / "baseline", worker=worker, mode="baseline")
    assert state["status"] == "supported"
    assert [call["role"] for call in worker.calls] == ["integrator", "reviewer"]
    assert worker.max_active_readers == 0


@pytest.mark.parametrize("blocking", [False, True])
async def test_review_only_seed_gets_one_fresh_review_and_scoped_output(
    source_case, tmp_path, blocking,
):
    state, worker = await run_case(source_case, tmp_path, mode="review_only", blocking=blocking)
    assert state["status"] == ("research_needed" if blocking else "supported")
    assert [call["role"] for call in worker.calls] == ["reviewer"]
    assert worker.integrations == worker.max_active_readers == 0 and worker.reviews == 1
    graph_name = "graph_diagnostic.json" if blocking else "graph_candidate.json"
    graph = json.loads((tmp_path / "run" / graph_name).read_text())
    assert graph["review"]["workflow_sha256"] == digest(Workflow.model_validate(worker.workflow))
    assert graph["review"]["source_packet_sha256"] == digest(worker.packet)
    assert graph["status"] == ("diagnostic_research_needed" if blocking
                               else "model_supported_within_declared_scope")
    assert json.loads((tmp_path / "seed_workflow.json").read_text()) == worker.workflow
    config = json.loads((tmp_path / "run" / "config.json").read_text())
    assert config["mode"] == "review_only"
    assert config["initial_workflow"]["role"] == "unaccepted_candidate"
    assert config["initial_workflow"]["prior_transport_verified"] is False
    assert config["initial_workflow"]["prior_review_inherited"] is False


async def test_review_only_requires_seed_before_claiming_run(source_case, tmp_path):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    run = tmp_path / "missing_seed"
    with pytest.raises(ValueError):
        await execute(source_case[1], run, worker=worker, mode="review_only")
    assert worker.calls == [] and not run.exists()


async def test_review_only_missing_seed_file_never_starts_worker(source_case, tmp_path):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    state = await execute(source_case[1], tmp_path / "missing_file_run", worker=worker,
                          mode="review_only", initial_workflow=tmp_path / "absent_seed.json")
    assert state["status"] == "failed" and worker.calls == []


async def test_review_only_changed_original_seed_fails_before_export(source_case, tmp_path):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    seed = tmp_path / "seed_workflow.json"
    seed.write_text(json.dumps(worker.workflow), encoding="utf-8")
    fake_run = worker.run

    async def changing_seed(prompt, directory):
        result = await fake_run(prompt, directory)
        seed.write_text("{}", encoding="utf-8")
        return result

    worker.run = changing_seed
    run = tmp_path / "changed_seed_run"
    state = await execute(source_case[1], run, worker=worker, mode="review_only",
                          initial_workflow=seed)
    assert state["status"] == "failed" and "changed" in state["error"].lower()
    assert [call["role"] for call in worker.calls] == ["reviewer"]
    assert not any((run / name).exists() for name in (
        "graph_candidate.json", "protocol.txt", "graph_diagnostic.json", "protocol_diagnostic.txt"))


def repair_inputs(worker, tmp_path, review=None):
    seed = tmp_path / "repair_seed.json"
    seed.write_text(json.dumps(worker.workflow), encoding="utf-8")
    feedback = tmp_path / "repair_feedback.json"
    payload = review if review is not None else make_review(
        worker.workflow, worker.packet, "unsupported")
    feedback.write_text(json.dumps(payload), encoding="utf-8")
    return seed, feedback


async def test_repair_uses_bound_feedback_then_fresh_review_without_initial_review(
    source_case, tmp_path,
):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    seed, feedback = repair_inputs(worker, tmp_path)
    run = tmp_path / "repair"
    state = await execute(source_case[1], run, worker=worker, mode="repair",
                          initial_workflow=seed, repair_review=feedback)
    assert state["status"] == "supported" and state["revision"] == 1
    assert [call["role"] for call in worker.calls] == ["integrator", "reviewer"]
    assert "previous_review.json" in worker.calls[0]["prompt"]
    assert "previous_review.json" not in worker.calls[1]["prompt"]
    assert "inspect" in worker.calls[1]["prompt"]
    assert len({attempt["thread_id"] for attempt in state["attempts"]}) == 2
    config = json.loads((run / "config.json").read_text())
    assert config["repair_review"]["role"] == "repair_instructions"
    assert config["repair_review"]["prior_verdict_inherited"] is False
    assert config["repair_review"]["prior_transport_verified"] is False
    assert (run / "repair_review.json").read_bytes() == feedback.read_bytes()


def expanded_repair_case(source_case, tmp_path, *, clone_original=False):
    packet, _, raw = source_case
    dependency = tmp_path / "dependency.txt"
    dependency.write_text("Use the documented device.", encoding="utf-8")
    sources = [{**{key: source[key] for key in ("source_id", "title", "url", "role")},
                "path": source["original_path"]} for source in packet["sources"]]
    if clone_original:
        clone = tmp_path / "current_original.txt"
        clone.write_bytes(Path(sources[0]["path"]).read_bytes())
        sources[0]["path"] = str(clone)
    sources.append({"source_id": "dependency", "title": "Registered dependency",
                    "url": "https://example.org/dependency", "role": "manufacturer_protocol",
                    "path": str(dependency)})
    expanded = build_packet(
        **{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
        sources=sources)
    path = tmp_path / "expanded_packet.json"
    path.write_text(json.dumps(expanded), encoding="utf-8")
    worker = FakeWorker((expanded, path, raw))
    wash = next(operation for operation in worker.workflow["operations"]
                if operation["id"] == "wash")
    wash["resources"].append({
        "kind": "equipment", "description": "Documented device from the added source",
        "evidence": [{"source_id": "dependency", "passage_id": "p00001",
                      "quote": "Use the documented device.", "support": "reported",
                      "rationale": "The added source specifies the device."}],
    })
    return worker


async def test_repair_source_expansion_binds_old_feedback_and_new_fresh_outputs(
    source_case, tmp_path,
):
    from genomics_discovery.curation.run_state import sha
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    seed, feedback = repair_inputs(FakeWorker(source_case), tmp_path)
    worker = expanded_repair_case(source_case, tmp_path)
    run = tmp_path / "expanded_repair"
    state = await execute(worker.packet_path, run, worker=worker, mode="repair",
                          initial_workflow=seed, repair_review=feedback,
                          repair_feedback_packet=source_case[1])
    assert state["status"] == "supported" and state["revision"] == 1
    assert [call["role"] for call in worker.calls] == ["integrator", "reviewer"]
    assert "old_feedback_sources.json" in worker.calls[0]["prompt"]
    assert "old_feedback_sources.json" not in worker.calls[1]["prompt"]
    config = json.loads((run / "config.json").read_text())
    old = config["repair_feedback_packet"]
    assert old["path"] == str(source_case[1].resolve())
    assert old["sha256"] == sha(source_case[1])
    assert old["snapshot_sha256"] == sha(Path(old["snapshot"]))
    assert old["source_packet_digest"] == digest(source_case[0])
    assert old["role"] == "prior_feedback_context" and old["prior_verdict_inherited"] is False
    assert old["originals"] == [{"path": source_case[0]["sources"][0]["original_path"],
                                "sha256": source_case[0]["sources"][0]["original_sha256"]}]
    copied = run / "old_feedback_sources.json"
    assert copied.read_bytes() == source_case[1].read_bytes()
    stage_copy = worker.calls[0]["directory"] / "old_feedback_sources.json"
    assert stage_copy.read_bytes() == copied.read_bytes()
    assert state["attempts"][0]["inputs"]["old_feedback_sources"]["sha256"] == sha(stage_copy)
    graph = json.loads(Path(state["graph_candidate"]).read_text())
    assert graph["source_registry"] == worker.packet["sources"]
    assert graph["review"]["source_packet_sha256"] == digest(worker.packet)
    assert graph["review"]["source_packet_sha256"] != digest(source_case[0])
    wash_check = next(check for check in graph["review"]["checks"]
                      if check["target_id"] == "operation:wash")
    assert {ev["source_id"] for ev in wash_check["evidence"]} == {"original", "dependency"}


def test_cli_feedback_packet_reaches_expanded_repair(source_case, tmp_path, monkeypatch, capsys):
    import sys

    from genomics_discovery.curation.workflow_extraction import __main__ as cli

    seed, feedback = repair_inputs(FakeWorker(source_case), tmp_path)
    worker = expanded_repair_case(source_case, tmp_path)
    monkeypatch.setattr(cli, "CodexWorker", lambda **kwargs: worker)
    monkeypatch.setattr(sys, "argv", [
        "workflow", "run", str(worker.packet_path), "--run", str(tmp_path / "cli_repair"),
        "--mode", "repair", "--initial-workflow", str(seed), "--repair-review", str(feedback),
        "--feedback-packet", str(source_case[1]),
    ])
    cli.main()
    assert json.loads(capsys.readouterr().out)["status"] == "supported"
    assert [call["role"] for call in worker.calls] == ["integrator", "reviewer"]
    assert "old_feedback_sources.json" in worker.calls[0]["prompt"]


async def test_source_expansion_without_feedback_packet_cannot_rebind_old_review(
    source_case, tmp_path,
):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    seed, feedback = repair_inputs(FakeWorker(source_case), tmp_path)
    worker = expanded_repair_case(source_case, tmp_path)
    state = await execute(worker.packet_path, tmp_path / "rejected", worker=worker, mode="repair",
                          initial_workflow=seed, repair_review=feedback)
    assert state["status"] == "failed" and worker.calls == []
    assert "source_packet_sha256" in state["error"]


@pytest.mark.parametrize("fault", ["removed", "replacement", "method_id", "title", "scope",
                                  "variant"])
async def test_expanded_repair_rejects_changed_sources_or_identity_before_worker(
    source_case, tmp_path, fault,
):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    seed, feedback = repair_inputs(FakeWorker(source_case), tmp_path)
    worker = expanded_repair_case(source_case, tmp_path)
    if fault == "removed":
        worker.packet["sources"].pop(0)
    elif fault == "replacement":
        replacement = tmp_path / "replacement.txt"
        replacement.write_text("A replacement protocol.", encoding="utf-8")
        source = worker.packet["sources"][0]
        replacement_packet = build_packet(
            **{key: worker.packet[key] for key in ("method_id", "title", "scope", "variant")},
            sources=[{**{key: source[key] for key in ("source_id", "title", "url", "role")},
                      "path": str(replacement)}])
        worker.packet["sources"][0] = replacement_packet["sources"][0]
    else:
        worker.packet[fault] += " changed"
    worker.packet_path.write_text(json.dumps(worker.packet), encoding="utf-8")
    state = await execute(worker.packet_path, tmp_path / "rejected", worker=worker, mode="repair",
                          initial_workflow=seed, repair_review=feedback,
                          repair_feedback_packet=source_case[1])
    assert state["status"] == "failed" and worker.calls == []
    assert "Expanded packet" in state["error"]
    assert "graph_candidate" not in state and "graph_diagnostic" not in state


@pytest.mark.parametrize("mode", ["specialists", "baseline", "review_only"])
async def test_feedback_packet_requires_repair_before_claiming_run(source_case, tmp_path, mode):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    seed, _ = repair_inputs(worker, tmp_path)
    run = tmp_path / "invalid_mode"
    with pytest.raises(ValueError, match="repair_feedback_packet"):
        await execute(source_case[1], run, worker=worker, mode=mode,
                      initial_workflow=seed if mode == "review_only" else None,
                      repair_feedback_packet=source_case[1])
    assert not run.exists() and worker.calls == []


@pytest.mark.parametrize("fault", ["packet", "snapshot", "stage_snapshot", "original"])
async def test_expanded_repair_pins_old_packet_copies_and_originals(
    source_case, tmp_path, fault,
):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    seed, feedback = repair_inputs(FakeWorker(source_case), tmp_path)
    worker = expanded_repair_case(source_case, tmp_path, clone_original=True)
    native_run = worker.run
    run = tmp_path / "pin_check"

    async def mutate_feedback_context(prompt, directory):
        result = await native_run(prompt, directory)
        changed = {"packet": source_case[1], "snapshot": run / "old_feedback_sources.json",
                   "stage_snapshot": directory / "old_feedback_sources.json",
                   "original": Path(source_case[0]["sources"][0]["original_path"])}[fault]
        changed.write_text(changed.read_text() + "\n ", encoding="utf-8")
        return result

    worker.run = mutate_feedback_context
    state = await execute(worker.packet_path, run, worker=worker, mode="repair",
                          initial_workflow=seed, repair_review=feedback,
                          repair_feedback_packet=source_case[1])
    assert state["status"] == "failed" and "changed" in state["error"].lower()
    assert [call["role"] for call in worker.calls] == ["integrator"]
    assert "graph_candidate" not in state and "graph_diagnostic" not in state


async def test_expanded_repair_fresh_review_must_bind_current_packet(source_case, tmp_path):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    seed, feedback = repair_inputs(FakeWorker(source_case), tmp_path)
    worker = expanded_repair_case(source_case, tmp_path)
    native_run = worker.run

    async def stale_new_review(prompt, directory):
        result = await native_run(prompt, directory)
        if worker.calls[-1]["role"] == "reviewer":
            path = directory / "artifact.json"
            document = json.loads(path.read_text())
            document["source_packet_sha256"] = digest(source_case[0])
            path.write_text(json.dumps(document), encoding="utf-8")
        return result

    worker.run = stale_new_review
    state = await execute(worker.packet_path, tmp_path / "stale_review", worker=worker,
                          mode="repair", initial_workflow=seed, repair_review=feedback,
                          repair_feedback_packet=source_case[1])
    assert state["status"] == "failed" and worker.reviews == 1
    assert "source_packet_sha256" in state["error"]
    assert "graph_candidate" not in state and "graph_diagnostic" not in state


@pytest.mark.parametrize("rebind", [False, True])
async def test_feedback_passage_ids_stay_old_until_revised_evidence_is_rebound(
    source_case, tmp_path, rebind,
):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    original = tmp_path / "structured.xml"
    body = source_case[0]["sources"][0]["passages"][0]["text"]
    original.write_text('<article><front><article-meta><title-group><article-title>Example'
                        '</article-title></title-group></article-meta></front><body>'
                        '<disp-formula><tex-math>x=1</tex-math></disp-formula><p>' + body
                        + '</p></body></article>', encoding="utf-8")
    kwargs = {**{key: source_case[0][key] for key in ("method_id", "title", "scope", "variant")},
              "sources": [{"source_id": "original", "title": "Original protocol",
                           "url": "https://example.org/original", "role": "primary_paper",
                           "path": str(original)}]}
    old_packet = build_packet(**kwargs, format_version="workflow_sources/1.0")
    old_path = tmp_path / "old_packet.json"
    old_path.write_text(json.dumps(old_packet), encoding="utf-8")
    seed, feedback = repair_inputs(FakeWorker((old_packet, old_path, source_case[2])), tmp_path)
    current_packet = build_packet(**kwargs)
    current_path = tmp_path / "current_packet.json"
    current_path.write_text(json.dumps(current_packet), encoding="utf-8")
    assert old_packet["sources"][0]["passages"][0]["text"] == body
    assert current_packet["sources"][0]["passages"][1]["text"] == body
    worker = FakeWorker((current_packet, current_path, source_case[2]))

    def revise_refs(value):
        if isinstance(value, list):
            for child in value:
                revise_refs(child)
        elif isinstance(value, dict):
            if value.get("source_id") == "original" and "quote" in value:
                value["passage_id"] = "p00002"
            for child in value.values():
                revise_refs(child)

    if rebind:
        revise_refs(worker.workflow)
    state = await execute(current_path, tmp_path / "shifted_repair", worker=worker, mode="repair",
                          initial_workflow=seed, repair_review=feedback,
                          repair_feedback_packet=old_path)
    assert state["status"] == ("supported" if rebind else "failed")
    assert worker.integrations == 1 and worker.reviews == int(rebind)
    old_review = json.loads((worker.calls[0]["directory"] / "previous_review.json").read_text())
    assert old_review["checks"][0]["evidence"][0]["passage_id"] == "p00001"
    if rebind:
        new_review = json.loads(Path(state["review"]).read_text())
        assert new_review["checks"][0]["evidence"][0]["passage_id"] == "p00002"
        assert new_review["source_packet_sha256"] == digest(current_packet)
    else:
        assert "quote" in state["error"].lower()
        assert "graph_candidate" not in state and "graph_diagnostic" not in state


async def test_repair_stops_after_one_correction_and_nonpositive_fresh_review(
    source_case, tmp_path,
):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case, review_verdicts=["unsupported"])
    seed, feedback = repair_inputs(worker, tmp_path)
    state = await execute(source_case[1], tmp_path / "repair", worker=worker, mode="repair",
                          initial_workflow=seed, repair_review=feedback)
    assert state["status"] == "needs_revision" and state["revision"] == 1
    assert worker.integrations == worker.reviews == 1
    assert Path(state["graph_diagnostic"]).is_file()
    assert "graph_candidate" not in state


@pytest.mark.parametrize("fault", ["supported", "retrieval_only", "workflow_hash", "packet_hash",
                                  "missing_target", "quote"])
async def test_repair_rejects_unbound_or_nonrepair_feedback_before_worker(
    source_case, tmp_path, fault,
):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    review = make_review(worker.workflow, worker.packet,
                         "supported" if fault in {"supported", "retrieval_only"} else "unsupported")
    if fault == "retrieval_only":
        review["omissions"] = [{"description": "A necessary fact is unavailable.", "blocking": True,
                                "action": "retrieve_evidence", "evidence": []}]
    elif fault == "workflow_hash":
        review["workflow_sha256"] = "0" * 64
    elif fault == "packet_hash":
        review["source_packet_sha256"] = "0" * 64
    elif fault == "missing_target":
        review["checks"].pop()
    elif fault == "quote":
        review["checks"][0]["evidence"][0]["quote"] = "Unsupported fictional quote"
    seed, feedback = repair_inputs(worker, tmp_path, review)
    state = await execute(source_case[1], tmp_path / "repair", worker=worker, mode="repair",
                          initial_workflow=seed, repair_review=feedback)
    assert state["status"] == "failed" and worker.calls == []
    assert "graph_candidate" not in state and "graph_diagnostic" not in state


@pytest.mark.parametrize("mode,seed,feedback,budget", [
    ("repair", True, False, 1), ("repair", False, True, 1), ("repair", True, True, 0),
    ("baseline", True, True, 1), ("review_only", True, True, 1),
])
async def test_repair_arguments_reject_before_claiming_run(
    source_case, tmp_path, mode, seed, feedback, budget,
):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    candidate, review = repair_inputs(worker, tmp_path)
    run = tmp_path / "invalid_arguments"
    with pytest.raises(ValueError):
        await execute(source_case[1], run, worker=worker, mode=mode, max_revisions=budget,
                      initial_workflow=candidate if seed else None,
                      repair_review=review if feedback else None)
    assert not run.exists() and worker.calls == []


async def test_repair_changed_feedback_stops_before_fresh_review(source_case, tmp_path):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    seed, feedback = repair_inputs(worker, tmp_path)
    fake_run = worker.run

    async def changing_feedback(prompt, directory):
        result = await fake_run(prompt, directory)
        feedback.write_text("{}", encoding="utf-8")
        return result

    worker.run = changing_feedback
    state = await execute(source_case[1], tmp_path / "repair", worker=worker, mode="repair",
                          initial_workflow=seed, repair_review=feedback)
    assert state["status"] == "failed" and "changed" in state["error"]
    assert [call["role"] for call in worker.calls] == ["integrator"]


async def test_repair_scope_resolution_preserves_unknown_recipe_detail(source_case, tmp_path):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    detail = {"id": "chemistry:recipe_volume", "description": "Transfer volume is not reported.",
              "blocking": True, "next_evidence": "Inspect the delegated recipe."}
    worker.workflow["unresolved"] = [detail]
    seed, feedback = repair_inputs(worker, tmp_path)
    worker.workflow["unresolved"] = [{**detail, "id": "recipe_volume_detail", "blocking": False}]
    worker.workflow["gap_resolutions"] = [{
        "reader_role": "chemistry", "gap_id": "recipe_volume",
        "rationale": "The source establishes washing inputs, outputs and role; volume remains "
                     "an explicit recipe detail gap outside the scoped architectural claim.",
        "evidence": worker.workflow["operations"][1]["evidence"],
    }]
    state = await execute(source_case[1], tmp_path / "repair", worker=worker, mode="repair",
                          initial_workflow=seed, repair_review=feedback)
    assert state["status"] == "supported"
    repaired = Workflow.model_validate_json(Path(state["workflow"]).read_bytes())
    assert repaired.unresolved[0].description == detail["description"]
    assert repaired.unresolved[0].blocking is False
    assert repaired.gap_resolutions[0].gap_id == "recipe_volume"
    review = WorkflowReview.model_validate_json(Path(state["review"]).read_bytes())
    assert "resolution:chemistry:recipe_volume" in {check.target_id for check in review.checks}


@pytest.mark.parametrize(("web_search", "network_access"), [(True, True), (True, False),
                                                             (False, True)])
async def test_native_worker_search_or_network_rejected_before_any_job(
    source_case, tmp_path, monkeypatch, web_search, network_access,
):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = CodexWorker(web_search=web_search, network_access=network_access)

    async def forbidden_job(*args, **kwargs):
        pytest.fail("An invalid native worker must never launch a job")

    monkeypatch.setattr(worker, "run", forbidden_job)
    run = tmp_path / "rejected_worker"
    with pytest.raises(ValueError):
        await execute(source_case[1], run, worker=worker)
    assert not run.exists()


@pytest.mark.parametrize("fault", ["worker_exit", "returned_exit", "returned_error",
                                    "returned_thread", "reused_thread", "unfinished_turn",
                                    "malformed", "model_field", "evidence_quote", "changed_source"])
async def test_failed_transport_artifact_or_provenance_never_promotes(source_case, tmp_path, fault):
    state, worker = await run_case(source_case, tmp_path, fault=fault)
    assert state["status"] == "failed" and state["error"]
    assert worker.reviews == 0
    assert not (tmp_path / "run" / "graph_candidate.json").exists()
    assert not (tmp_path / "run" / "protocol.txt").exists()


async def test_unsupported_review_gets_exactly_one_revision_and_fresh_review(source_case, tmp_path):
    state, worker = await run_case(source_case, tmp_path,
                                  review_verdicts=["unsupported", "supported"])
    assert state["status"] == "supported"
    assert [call["role"] for call in worker.calls] == [
        "chemistry", "information", "integrator", "reviewer", "integrator", "reviewer"]
    assert worker.integrations == worker.reviews == 2
    assert digest(Workflow.model_validate(source_case[2])) != digest(
        Workflow.model_validate(worker.workflow))
    assert "unsupported" in worker.calls[4]["prompt"].lower()
    assert "004_reviewer" not in worker.calls[5]["prompt"]
    assert len({attempt["thread_id"] for attempt in state["attempts"]}) == 6


async def test_remaining_unsupported_review_stops_after_one_revision(source_case, tmp_path):
    state, worker = await run_case(source_case, tmp_path,
                                  review_verdicts=["unsupported", "unsupported"])
    assert state["status"] == "needs_revision"
    assert worker.integrations == worker.reviews == 2
    assert not (tmp_path / "run" / "graph_candidate.json").exists()
    graph = json.loads((tmp_path / "run" / "graph_diagnostic.json").read_text())
    assert graph["status"] == "diagnostic_needs_revision"
    assert (tmp_path / "run" / "protocol_diagnostic.txt").is_file()


async def test_blocking_gap_stops_as_research_needed(source_case, tmp_path):
    state, worker = await run_case(source_case, tmp_path, blocking=True)
    assert state["status"] == "research_needed"
    assert worker.integrations == worker.reviews == 1
    assert not (tmp_path / "run" / "graph_candidate.json").exists()
    graph = json.loads((tmp_path / "run" / "graph_diagnostic.json").read_text())
    assert graph["status"] == "diagnostic_research_needed"
    assert "[missing_condition]" in (tmp_path / "run" / "protocol_diagnostic.txt").read_text()


@pytest.mark.parametrize("reader_gap", ["dropped", "retained"])
async def test_blocking_reader_gap_cannot_disappear_during_integration(
    source_case, tmp_path, reader_gap,
):
    state, worker = await run_case(source_case, tmp_path, reader_gap=reader_gap)
    assert state["status"] == ("failed" if reader_gap == "dropped" else "research_needed")
    assert worker.integrations == 1
    assert worker.reviews == (0 if reader_gap == "dropped" else 1)
    assert not (tmp_path / "run" / "graph_candidate.json").exists()
    if reader_gap == "dropped":
        assert "gap" in state["error"].lower()
    if reader_gap == "retained":
        saved = json.loads(Path(state["workflow"]).read_text())
        assert saved["unresolved"][0]["id"] == "chemistry:reader_missing"
        assert saved["unresolved"][0]["blocking"] is True


async def test_reader_gap_resolution_requires_and_preserves_source_evidence(source_case, tmp_path):
    state, worker = await run_case(source_case, tmp_path, reader_gap="resolved")
    assert state["status"] == "supported"
    assert worker.integrations == worker.reviews == 1
    graph = json.loads((tmp_path / "run" / "graph_candidate.json").read_text())
    resolution = graph["workflow"]["gap_resolutions"][0]
    assert resolution["reader_role"] == "chemistry" and resolution["gap_id"] == "reader_missing"
    assert resolution["evidence"][0]["source_id"] == "original"
    protocol = (tmp_path / "run" / "protocol.txt").read_text()
    assert "Reader gap resolutions:" in protocol and "[chemistry:reader_missing]" in protocol


def test_query_and_candidate_preserve_typed_routes_scope_and_source_binding(source_case, tmp_path):
    packet, _, raw = source_case
    workflow = Workflow.model_validate(raw)
    review = WorkflowReview.model_validate(make_review(raw, packet))
    queried = query_workflow(workflow, "wash")
    assert queried["scope"] == packet["scope"]
    assert [state["id"] for state in queried["inputs"]] == ["nuclei"]
    assert [state["id"] for state in queried["outputs"]] == ["washed"]
    assert {resource["kind"] for resource in queried["resources"]} == {"reagent", "condition"}
    assert {relation["operation_id"] for relation in queried["consumers"]} == {
        "seq_a", "seq_b", "merge"}
    assert {tuple(relation["flow_types"]) for relation in queried["consumers"]} == {
        ("material_output", "material_input"), ("control",), ("precedence",)}
    assert query_workflow(workflow, "seq_a")["consumers"][0]["flow_types"] == [
        "data_output", "data_input"]
    assert queried["predecessors"] == []
    accepted = tmp_path / "accepted_atlas.json"
    accepted.write_text('{"sentinel":"accepted production graph"}')
    graph = export_candidate(workflow, review, packet, tmp_path / "candidate")
    assert accepted.read_text() == '{"sentinel":"accepted production graph"}'
    assert graph["workflow"] == workflow.model_dump(mode="json")
    assert graph["review"]["workflow_sha256"] == digest(workflow)
    assert graph["source_registry"] == packet["sources"]
    assert {node["node_type"] for node in graph["nodes"]} == {"state", "operation", "module"}
    assert len(graph["module_memberships"]) == 4
    assert {edge["kind"] for edge in graph["edges"]} == {"material_input", "material_output",
                                                            "data_output", "data_input",
                                                            "control", "precedence"}
    assert len(graph["edges"]) == len(workflow.flows)
    assert all(node["source_refs"] for node in graph["nodes"])
    assert all(edge["source_refs"] for edge in graph["edges"])
    assert all(node["id"].startswith("workflow:branched_assay:source_variant:")
               for node in graph["nodes"])
    protocol = (tmp_path / "candidate" / "protocol.txt").read_text()
    assert (protocol.index("[wash]:") < protocol.index("[seq_a]:")
            < protocol.index("[seq_b]:") < protocol.index("[merge]:"))
    assert "next consumers: seq_a, seq_b" in protocol
    assert "Input [washed]" in protocol and "condition: Use aliquot A" in protocol
    assert "curator interpretations" in protocol and "original:p00001" in protocol
    other_packet = {**packet, "variant": "second_variant"}
    other = Workflow.model_validate({**raw, "variant": "second_variant"})
    other_graph = export_candidate(other, WorkflowReview.model_validate(make_review(
        other.model_dump(mode="json"), other_packet)), other_packet, tmp_path / "second")
    assert {node["id"] for node in graph["nodes"]}.isdisjoint(
        {node["id"] for node in other_graph["nodes"]})


@pytest.mark.parametrize("fault", ["unsupported", "stale_hash", "missing_target", "bad_quote"])
def test_export_requires_supported_bound_review_with_complete_provenance(
    source_case, tmp_path, fault,
):
    packet, _, raw = source_case
    review = make_review(raw, packet, "unsupported" if fault == "unsupported" else "supported")
    if fault == "stale_hash":
        review["workflow_sha256"] = "0" * 64
    if fault == "missing_target":
        review["checks"].pop()
    if fault == "bad_quote":
        review["checks"][0]["evidence"][0]["quote"] = "An uncited claim"
    with pytest.raises(ValueError):
        export_candidate(Workflow.model_validate(raw), WorkflowReview.model_validate(review),
                         packet, tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize("diagnostic", [False, True])
def test_old_review_cannot_rebind_to_an_expanded_valid_source_packet(
    source_case, tmp_path, diagnostic,
):
    packet, _, raw = source_case
    review = WorkflowReview.model_validate(make_review(
        raw, packet, "unresolved" if diagnostic else "supported"))
    additional = tmp_path / "additional_original.txt"
    additional.write_text("A separately registered protocol passage.", encoding="utf-8")
    expanded = build_packet(
        **{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
        sources=[{**{key: source[key] for key in ("source_id", "title", "url", "role")},
                  "path": source["original_path"]} for source in packet["sources"]] + [{
                      "source_id": "additional", "title": "Additional source",
                      "url": "https://example.org/additional", "role": "supplement",
                      "path": str(additional)}])
    with pytest.raises(ValueError, match="source_packet_sha256"):
        exporter = export_diagnostic if diagnostic else export_candidate
        exporter(Workflow.model_validate(raw), review, expanded, tmp_path / "candidate")
    assert not (tmp_path / "candidate").exists()


@pytest.mark.parametrize(("verdict", "outcome"), [("unsupported", "needs_revision"),
                                                    ("unresolved", "research_needed")])
def test_diagnostic_preserves_nonpositive_target_verdicts_without_candidate_promotion(
    source_case, tmp_path, verdict, outcome,
):
    packet, _, raw = source_case
    workflow = Workflow.model_validate(raw)
    review = WorkflowReview.model_validate(make_review(raw, packet, verdict))
    run = tmp_path / "diagnostic"
    graph = export_diagnostic(workflow, review, packet, run)
    assert graph["status"] == "diagnostic_" + outcome
    assert graph["review_outcome"] == outcome
    assert graph["format_version"] == "workflow_graph_diagnostic/1.0"
    assert graph["workflow"] == workflow.model_dump(mode="json")
    assert graph["review"] == review.model_dump(mode="json")
    assert graph["source_registry"] == packet["sources"]
    checks = {check.target_id: check.verdict for check in review.checks}
    for node in graph["nodes"]:
        if node["node_type"] == "module":
            assert node["boundary_status"] == "curator_interpretation"
            assert "review_verdict" not in node
        else:
            assert node["review_verdict"] == checks[f"{node['node_type']}:{node['source_id']}"]
    for edge in graph["edges"]:
        assert edge["review_verdict"] == checks["flow:" + edge["original"]["id"]]
    assert (run / "graph_diagnostic.json").is_file()
    text = (run / "protocol_diagnostic.txt").read_text()
    assert "Status: diagnostic_" + outcome in text
    assert f"[operation:wash] {verdict}:" in text
    assert "does not establish human acceptance or experimental validation" in text
    assert not (run / "graph_candidate.json").exists() and not (run / "protocol.txt").exists()
    with pytest.raises(ValueError, match="Only a supported"):
        export_candidate(workflow, review, packet, run)


@pytest.mark.parametrize("field", ["method_id", "title", "scope", "variant"])
async def test_repair_seed_metadata_cannot_override_packet_with_a_self_bound_review(
    source_case, tmp_path, field,
):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case)
    worker.workflow[field] += " changed"
    seed, feedback = repair_inputs(worker, tmp_path)
    state = await execute(source_case[1], tmp_path / "repair_metadata", worker=worker,
                          mode="repair", initial_workflow=seed, repair_review=feedback)
    assert state["status"] == "failed" and "frozen source scope" in state["error"]
    assert worker.calls == []


async def test_repair_fresh_research_outcome_never_inherits_old_verdict(source_case, tmp_path):
    from genomics_discovery.curation.workflow_extraction.runtime import execute

    worker = FakeWorker(source_case, review_verdicts=["unresolved"])
    seed, feedback = repair_inputs(worker, tmp_path)
    state = await execute(source_case[1], tmp_path / "repair_research", worker=worker,
                          mode="repair", initial_workflow=seed, repair_review=feedback)
    assert state["status"] == "research_needed" and state["revision"] == 1
    assert worker.integrations == worker.reviews == 1
    review = WorkflowReview.model_validate_json(Path(state["review"]).read_bytes())
    workflow = Workflow.model_validate_json(Path(state["workflow"]).read_bytes())
    assert review.workflow_sha256 == digest(workflow)
    assert review.source_packet_sha256 == digest(worker.packet)
    assert any(check.verdict == "unresolved" for check in review.checks)
    assert Path(state["graph_diagnostic"]).is_file() and "graph_candidate" not in state


async def test_final_export_source_mutation_makes_final_state_failed(
    source_case, tmp_path, monkeypatch,
):
    from genomics_discovery.curation.workflow_extraction import runtime

    original_export = runtime.export_candidate

    def export_then_mutate_original(workflow, review, packet, run):
        graph = original_export(workflow, review, packet, run)
        original = Path(packet["sources"][0]["original_path"])
        original.write_text(original.read_text() + "\nChanged source bytes.", encoding="utf-8")
        return graph

    monkeypatch.setattr(runtime, "export_candidate", export_then_mutate_original)
    worker = FakeWorker(source_case)
    state = await runtime.execute(source_case[1], tmp_path / "late_mutation", worker=worker,
                                  mode="baseline")
    assert state["status"] == "failed" and "changed" in state["error"].lower()
    saved = json.loads((tmp_path / "late_mutation" / "state.json").read_text())
    assert saved["status"] == "failed"
    # Any retained files are archived work; final failed state cannot publish them.
