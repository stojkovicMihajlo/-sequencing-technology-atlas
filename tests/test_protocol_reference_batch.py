"""Native-stage integration with fake archives only; no model or network calls."""

import asyncio
import json
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pytest

from test_protocol_benchmark import records as records
from test_comparison_profiles import example as example
from test_workflow_extraction_runtime import make_review

from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.protocol_benchmark.models import ProtocolReference
from genomics_discovery.curation.protocol_benchmark.runtime import run_batch
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowDraft, digest


class FixturePool:
    """Fresh fixture jobs share counters, never model conversations."""

    def __init__(self, records, *, fault=None):
        self.workflow, self.packet, self.reference, self.review, self.assessment, self.source = records
        self.fault, self.calls, self.counts = fault, [], Counter()
        raw = self.workflow.model_dump(mode="json")
        flows = raw.pop("flows")
        raw["ordering_flows"] = [flow for flow in flows if flow["kind"] in {"control", "precedence"}]
        for operation in raw["operations"]:
            for direction in ("input", "output"):
                operation[direction + "_bindings"] = [
                    {"state_id": sid, "basis": "This exact source-qualified association.",
                     "selection": "required", "evidence": operation["evidence"]}
                    for sid in operation[direction + "s"]]
        self.draft = WorkflowDraft.model_validate(raw)

    def worker(self):
        pool = self

        class FixtureWorker:
            async def run(self, prompt, attempt):
                role = re.search(r"ROLE: benchmark_(\w+)", prompt)[1]
                case = re.search(r"Case ID: (.+)", prompt)[1]
                pool.counts[(case, role)] += 1
                round_number = pool.counts[(case, role)]
                pool.calls.append((case, role, prompt, attempt))
                index = len(pool.calls)
                if role == "reference_annotator":
                    document = pool.reference.model_dump(mode="json")
                    document["case_id"] = case
                    if pool.fault == "schema_once" and round_number == 1:
                        document = {"wrong_schema": True}
                    elif pool.fault == "schema_always":
                        document = {"wrong_schema": True}
                elif role == "reference_reviewer":
                    path = Path(re.search(r"^reference: (.+)$", prompt, re.MULTILINE)[1])
                    reference = ProtocolReference.model_validate_json(path.read_bytes())
                    document = pool.review.model_dump(mode="json")
                    document["reference_sha256"] = digest(reference)
                    if pool.fault == "revision_always" or (
                            pool.fault == "revision_once" and round_number == 1):
                        document["obligation_checks"][0]["verdict"] = "needs_revision"
                    if pool.fault == "reference_uncertain":
                        document["obligation_checks"][0]["verdict"] = "uncertain"
                elif role == "graph_constructor":
                    document = pool.draft.model_dump(mode="json")
                    if pool.fault == "missing_bindings":
                        document["operations"][0]["input_bindings"] = None
                else:
                    workflow_path = Path(re.search(r"^workflow: (.+)$", prompt, re.MULTILINE)[1])
                    workflow = Workflow.model_validate_json(workflow_path.read_bytes())
                    reference_path = Path(re.search(r"^reference: (.+)$", prompt, re.MULTILINE)[1])
                    reference = ProtocolReference.model_validate_json(reference_path.read_bytes())
                    assessment = pool.assessment.model_dump(mode="json")
                    assessment.update(workflow_sha256=digest(workflow), reference_sha256=digest(reference),
                                      factual_audits=[])
                    if pool.fault == "critical_unassessed":
                        assessment["obligations"][0].update(outcome="unassessed", targets=[], evidence=[])
                    if pool.fault == "noncritical_partial":
                        assessment["obligations"][1]["outcome"] = "partial"
                    review = make_review(workflow.model_dump(mode="json"), pool.packet)
                    if pool.fault == "unsupported_graph":
                        review["checks"][0]["verdict"] = "unsupported"
                    document = {"workflow_review": review, "assessment": assessment}
                (attempt / "artifact.json").write_text(json.dumps(document))
                (attempt / "prompt.txt").write_text(prompt)
                thread = "duplicate" if pool.fault == "duplicate_thread" else f"fixture-{index}"
                usage = {"input_tokens": 10, "output_tokens": 2}
                events = [{"type": "thread.started", "thread_id": thread}, {"type": "turn.started"}]
                if pool.fault != "transport":
                    events.append({"type": "turn.completed", "usage": usage})
                result = WorkerResult(0, thread, usage, .01, None)
                (attempt / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
                (attempt / "result.json").write_text(json.dumps(asdict(result)))
                for name in ("command.json", "started.json"):
                    (attempt / name).write_text('{"fixture": true}')
                (attempt / "stderr.log").write_text("")
                (attempt / "final.txt").write_text("Fixture artifact only, not real scientific validation.")
                if pool.fault == "mutate_original":
                    pool.source.write_text("Changed immutable original source.")
                if pool.fault == "missing_artifact":
                    (attempt / "artifact.json").unlink()
                await asyncio.sleep(0)
                return result

        return FixtureWorker()


def manifest_for(records, tmp_path, *, ids=("fixture",), bad_first_hash=False):
    packet = tmp_path / "packet.json"
    packet.write_text(json.dumps(records[1]))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"cases": [
        {"case_id": ident, "packet": packet.name,
         "packet_sha256": "0" * 64 if bad_first_hash and index == 0 else sha(packet)}
        for index, ident in enumerate(ids)]}))
    return manifest


async def test_four_fresh_stages_source_only_first_and_deterministic_exports(records, tmp_path, capsys):
    manifest = manifest_for(records, tmp_path)
    pool = FixturePool(records)
    output = tmp_path / "run"
    state = await run_batch(manifest, output, worker_factory=pool.worker)
    assert state["status"] == "completed"
    assert state["completed_native_sessions"] == state["distinct_native_threads"] == 4
    assert [call[1] for call in pool.calls] == [
        "reference_annotator", "reference_reviewer", "graph_constructor", "graph_reviewer"]
    first = pool.calls[0][2]
    assert str((tmp_path / "packet.json").resolve()) in first
    assert str(output / "cases/fixture/reference.json") not in first
    assert "previous_artifact:" not in first
    assert "gold" not in first.casefold()
    assert "schema" in first and "catalog" in first and "read-batch" in first
    assert "not blind" in pool.calls[2][2]
    case = state["cases"]["fixture"]
    assert case["status"] == "model_reviewed_reference_candidate"
    assert case["usage"] == {"input_tokens": 40, "output_tokens": 8}
    projection = output / "cases/fixture/projection"
    assert (projection / "protocol.txt").is_file()
    graph = json.loads((projection / "graph_candidate.json").read_text())
    assert graph["workflow_sha256"] == case["workflow_sha256"]
    scores = json.loads((output / "cases/fixture/scores.json").read_text())
    assert scores["factual_audit"]["denominator"] == 0
    assert scores["factual_audit"]["supported_claim_precision"] is None
    bindings = json.loads((output / "cases/fixture/bindings.json").read_text())
    assert bindings[str(tmp_path / "packet.json")] == sha(tmp_path / "packet.json")
    assert bindings[str(records[-1])] == sha(records[-1])
    assert all(sha(Path(path)) == expected for path, expected in bindings.items())
    for _, _, prompt, attempt in pool.calls:
        assert (attempt / "prompt.txt").read_text() == prompt
        assert (attempt / "input_bindings.json").is_file()
    first_bindings = json.loads(re.search(
        r"Host-computed canonical object digests:\n```json\n(.*?)\n```", first, re.DOTALL)[1])
    assert first_bindings == {"source_packet_sha256": digest(records[1])}
    assert first_bindings["source_packet_sha256"] != sha(tmp_path / "packet.json")
    for _, role, prompt, attempt in pool.calls:
        supplied = json.loads(re.search(
            r"Host-computed canonical object digests:\n```json\n(.*?)\n```", prompt, re.DOTALL)[1])
        assert supplied["source_packet_sha256"] == digest(records[1])
        instruction = json.loads((attempt / "instruction.json").read_text())
        assert instruction["canonical_object_digests"] == supplied
        assert "not file-byte hashes" in prompt and "Do not add extra fields" in prompt
        if role != "reference_annotator":
            reference_path = Path(re.search(r"^reference: (.+)$", prompt, re.MULTILINE)[1])
            reference = ProtocolReference.model_validate_json(reference_path.read_bytes())
            assert supplied["reference_sha256"] == digest(reference)
            assert supplied["reference_sha256"] != sha(reference_path)
            assert "The exact listed artifacts are permitted inputs even when they reside in another" in prompt
            assert "All unlisted attempt files remain prohibited" in prompt
            if role == "reference_reviewer":
                assert reference_path.parent != attempt
                assert "current reference artifact is permitted" in prompt
        if role == "graph_reviewer":
            workflow_path = Path(re.search(r"^workflow: (.+)$", prompt, re.MULTILINE)[1])
            reviewed_workflow = Workflow.model_validate_json(workflow_path.read_bytes())
            assert supplied["workflow_sha256"] == digest(reviewed_workflow)
            assert supplied["workflow_sha256"] != sha(workflow_path)
        else:
            assert "workflow_sha256" not in supplied
    from genomics_discovery.curation.protocol_benchmark.__main__ import main
    assert main(["check", "graph-review", str(pool.calls[-1][3] / "artifact.json"),
        "--packet", str(tmp_path / "packet.json"),
        "--reference", str(output / "cases/fixture/reference.json"),
        "--workflow", str(output / "cases/fixture/workflow.json")]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["integrity_valid"] and checked["workflow_review_outcome"] == "supported"


@pytest.mark.parametrize("fault,expected_calls", [("schema_once", 5), ("revision_once", 6)])
async def test_one_generic_data_repair_gets_new_artifact_and_fresh_review(
        records, tmp_path, fault, expected_calls):
    pool = FixturePool(records, fault=fault)
    state = await run_batch(manifest_for(records, tmp_path), tmp_path / "repair",
                            worker_factory=pool.worker)
    assert state["status"] == "completed"
    assert len(pool.calls) == state["distinct_native_threads"] == expected_calls
    case = state["cases"]["fixture"]
    assert case["repairs_used"] == 1
    repairs = [row for row in case["attempts"] if row["data_repair"]]
    assert len(repairs) == 1 and repairs[0]["role"] == "reference_annotator"
    repair_prompt = next(call[2] for call in pool.calls if call[3] == Path(repairs[0]["attempt"]))
    assert "One bounded data repair" in repair_prompt
    assert "previous_artifact:" in repair_prompt and "feedback:" in repair_prompt
    assert all((call[3] / "artifact.json").exists() for call in pool.calls)
    assert all(not row["data_repair"] for row in case["attempts"] if row["role"] == "reference_reviewer")


@pytest.mark.parametrize("fault,calls", [("schema_always", 2), ("revision_always", 4),
    ("transport", 1), ("missing_artifact", 1), ("reference_uncertain", 2),
    ("missing_bindings", 3), ("duplicate_thread", 2), ("mutate_original", 1)])
async def test_failures_are_withheld_without_retry_or_unbounded_repair(records, tmp_path, fault, calls):
    pool = FixturePool(records, fault=fault)
    output = tmp_path / "failed"
    state = await run_batch(manifest_for(records, tmp_path), output, worker_factory=pool.worker)
    assert state["status"] == "finished_with_failures"
    assert len(pool.calls) == calls
    assert state["cases"]["fixture"]["repairs_used"] <= 1
    assert not (output / "cases/fixture/projection/protocol.txt").exists()
    assert not (output / "cases/fixture/projection/graph_candidate.json").exists()


@pytest.mark.parametrize("fault,status", [("critical_unassessed", "critical_obligations_not_covered"),
                                          ("unsupported_graph", "graph_needs_revision")])
async def test_critical_or_scientific_graph_failure_withholds_positive_render(
        records, tmp_path, fault, status):
    pool = FixturePool(records, fault=fault)
    output = tmp_path / "withheld"
    state = await run_batch(manifest_for(records, tmp_path), output, worker_factory=pool.worker)
    assert state["cases"]["fixture"]["status"] == status
    assert (output / "cases/fixture/scores.json").is_file()
    assert not (output / "cases/fixture/projection/protocol.txt").exists()
    assert len(pool.calls) == 4


async def test_noncritical_partial_is_visible_without_fabricating_full_coverage(records, tmp_path):
    pool = FixturePool(records, fault="noncritical_partial")
    state = await run_batch(manifest_for(records, tmp_path), tmp_path / "partial",
                            worker_factory=pool.worker)
    assert state["status"] == "completed"
    assert state["cases"]["fixture"]["obligation_completeness"] == "partial"


async def test_one_bad_source_case_does_not_cancel_other_cases(records, tmp_path):
    manifest = manifest_for(records, tmp_path, ids=("bad", "good"), bad_first_hash=True)
    pool = FixturePool(records)
    state = await run_batch(manifest, tmp_path / "cohort", concurrency=2, worker_factory=pool.worker)
    assert state["cases"]["bad"]["issue_stage"] == "source"
    assert state["cases"]["bad"]["error_category"] == "packet_byte_hash"
    assert state["cases"]["good"]["status"] == "model_reviewed_reference_candidate"
    assert {call[0] for call in pool.calls} == {"good"}


async def test_prepare_only_case_filter_preserves_declared_pins_and_launches_nothing(records, tmp_path):
    manifest = manifest_for(records, tmp_path, ids=("development", "heldout"))

    def forbidden():
        raise AssertionError("Preparation must not construct a model worker")

    output = tmp_path / "prepared"
    state = await run_batch(manifest, output, case_ids=["heldout"], prepare_only=True,
                            worker_factory=forbidden)
    assert state["status"] == "prepared" and set(state["cases"]) == {"heldout"}
    assert state["completed_native_sessions"] == 0 and state["usage"] == {}
    assert not list(output.rglob("events.jsonl"))
    assert len(list((output / "cases/heldout/schemas").glob("*.json"))) == 4
    with pytest.raises(FileExistsError):
        await run_batch(manifest, output, prepare_only=True)


async def test_native_execution_requires_slurm_and_does_not_resume(records, tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    output = tmp_path / "not-created"
    with pytest.raises(RuntimeError, match="Slurm"):
        await run_batch(manifest_for(records, tmp_path), output)
    assert not output.exists()
