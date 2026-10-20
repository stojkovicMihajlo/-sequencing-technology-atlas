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
        self.correction_snapshot = None
        self.projection_before_fresh_review = None
        raw = self.workflow.model_dump(mode="json")
        raw["unresolved"] = [{"id": "reference-" + item.id,
            "description": item.question + " " + item.consequence, "blocking": False,
            "next_evidence": item.next_evidence} for item in self.reference.unknowns]
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
                    if round_number == 1 and pool.fault in {"graph_revision_once", "factual_unsupported_once"}:
                        document["operations"][0]["action"] = "Discard all indexed-well attribution."
                    if round_number == 2:
                        prior = {name: Path(path) for name, path in re.findall(
                            r"^(previous_\w+): (.+)$", prompt, re.MULTILINE)}
                        case_root = attempt.parent.parent
                        pool.correction_snapshot = {"pins": {str(path): sha(path) for path in prior.values()},
                            "canonical_exists": (case_root / "workflow.json").exists(),
                            "projection_exists": (case_root / "projection").exists()}
                    if pool.fault == "missing_bindings":
                        document["operations"][0]["input_bindings"] = None
                else:
                    workflow_path = Path(re.search(r"^workflow: (.+)$", prompt, re.MULTILINE)[1])
                    workflow = Workflow.model_validate_json(workflow_path.read_bytes())
                    reference_path = Path(re.search(r"^reference: (.+)$", prompt, re.MULTILINE)[1])
                    reference = ProtocolReference.model_validate_json(reference_path.read_bytes())
                    assessment = pool.assessment.model_dump(mode="json")
                    assessment.update(workflow_sha256=digest(workflow), reference_sha256=digest(reference),
                        factual_audits=[], format_version="protocol_benchmark_assessment/1.1",
                        unknown_checks=[{"unknown_id": item.id, "outcome": "retained",
                            "targets": ["gap:reference-" + item.id],
                            "rationale": "The synthetic gap retains this scoped question and next evidence.",
                            "evidence": []} for item in reference.unknowns])
                    if pool.fault == "legacy_assessment":
                        assessment.update(format_version="protocol_benchmark_assessment/1.0")
                        assessment.pop("unknown_checks")
                    elif pool.fault == "missing_unknown_check":
                        assessment["unknown_checks"] = []
                    elif pool.fault == "unknown_partial" or (pool.fault == "unknown_partial_once" and round_number == 1):
                        assessment["unknown_checks"][0]["outcome"] = "partial"
                    elif pool.fault == "unknown_revision":
                        assessment["unknown_checks"][0].update(outcome="reference_revision_needed",
                            evidence=[reference.obligations[0].evidence[0].model_dump(mode="json")])
                    if pool.fault == "critical_unassessed":
                        assessment["obligations"][0].update(outcome="unassessed", targets=[], evidence=[])
                    if pool.fault == "critical_partial_once" and round_number == 1:
                        assessment["obligations"][0]["outcome"] = "partial"
                    if pool.fault == "noncritical_partial":
                        assessment["obligations"][1]["outcome"] = "partial"
                    review = make_review(workflow.model_dump(mode="json"), pool.packet)
                    if pool.fault == "unsupported_graph" or (round_number == 1 and pool.fault in {
                            "graph_revision_once", "graph_revision_then_uncertain", "graph_repair_missing_artifact"}):
                        review["checks"][0]["verdict"] = "unsupported"
                    if pool.fault == "graph_uncertain" or (pool.fault == "graph_revision_then_uncertain" and round_number == 2):
                        review["checks"][0]["verdict"] = "unresolved"
                    audit_verdict = {"factual_unsupported_always": "unsupported",
                        "factual_uncertain": "uncertain", "factual_unassessed": "unassessed"}.get(pool.fault)
                    if pool.fault == "factual_unsupported_once":
                        audit_verdict = "unsupported" if round_number == 1 else "supported"
                    if audit_verdict:
                        assessment["factual_audits"] = [{"id": "attribution_claim",
                            "claim": workflow.operations[0].action, "targets": ["operation:separate"],
                            "verdict": audit_verdict, "rationale": "Independent synthetic claim judgment.",
                            "evidence": [reference.obligations[0].evidence[0].model_dump(mode="json")]}]
                    if round_number == 2:
                        pool.projection_before_fresh_review = (attempt.parent.parent / "projection").exists()
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
                if pool.fault == "graph_repair_missing_artifact" and role == "graph_constructor" and round_number == 2:
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
    for _, role, prompt, _ in pool.calls[:2]:
        assert "Counterfactual inclusion test" in prompt
        assert "same assay-necessity test to ReferenceUnknowns" in prompt
        assert "pulse/chase" in prompt
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
    assert scores["unknown_preservation"]["status"] == "retained"
    assert scores["unknown_preservation"]["denominator"] == 1
    assert "protocol_benchmark_assessment/1.1" in pool.calls[-1][2]
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
    assert len(pool.calls) == (6 if fault == "unsupported_graph" else 4)


async def test_noncritical_partial_is_visible_without_fabricating_full_coverage(records, tmp_path):
    pool = FixturePool(records, fault="noncritical_partial")
    state = await run_batch(manifest_for(records, tmp_path), tmp_path / "partial",
                            worker_factory=pool.worker)
    assert state["status"] == "completed"
    assert state["cases"]["fixture"]["obligation_completeness"] == "partial"


@pytest.mark.parametrize("fault,status", [("unknown_partial", "reference_unknowns_not_retained"),
    ("unknown_revision", "reference_unknown_revision_needed"),
    ("missing_unknown_check", "graph_reviewer_failed"), ("legacy_assessment", "graph_reviewer_failed")])
async def test_new_runs_require_v2_and_uncertainty_preservation_before_export(records, tmp_path, fault, status):
    pool = FixturePool(records, fault=fault)
    output = tmp_path / "unknowns"
    state = await run_batch(manifest_for(records, tmp_path), output, worker_factory=pool.worker)
    case = state["cases"]["fixture"]
    assert case["status"] == status
    assert len(pool.calls) == (6 if fault == "unknown_partial" else 4)
    assert not (output / "cases/fixture/projection/protocol.txt").exists()
    if fault in {"unknown_partial", "unknown_revision"}:
        scores = json.loads((output / "cases/fixture/scores.json").read_bytes())
        assert scores["obligation_coverage"]["strict_covered_fraction"] == 1
        assert scores["unknown_preservation"]["status"] == "incomplete"


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


@pytest.mark.parametrize("fault", ["graph_revision_once", "factual_unsupported_once",
                                 "critical_partial_once", "unknown_partial_once"])
async def test_one_graph_correction_preserves_negative_round_and_requires_fresh_review(records, tmp_path, fault):
    pool = FixturePool(records, fault=fault)
    output = tmp_path / "corrected"
    state = await run_batch(manifest_for(records, tmp_path), output, worker_factory=pool.worker)
    case, root = state["cases"]["fixture"], output / "cases/fixture"
    assert case["status"] == "model_reviewed_reference_candidate"
    assert case["graph_repairs_used"] == 1 and case["selected_graph_round"] == 2
    assert len(pool.calls) == state["distinct_native_threads"] == 6
    assert [row["role"] for row in case["attempts"] if row["data_repair"]] == ["graph_constructor"]
    first, second = case["graph_rounds"]
    assert first["status"] != "model_reviewed_reference_candidate"
    assert second["status"] == "model_reviewed_reference_candidate"
    snapshot = pool.correction_snapshot
    assert not snapshot["canonical_exists"] and not snapshot["projection_exists"]
    assert pool.projection_before_fresh_review is False
    assert len(snapshot["pins"]) == 5
    final_pins = json.loads((root / "bindings.json").read_bytes())
    assert all(sha(Path(path)) == value == final_pins[path] for path, value in snapshot["pins"].items())
    for number in (1, 2):
        for name in ("workflow", "workflow_review", "assessment", "scores"):
            path = root / f"graph_rounds/{number:03d}/{name}.json"
            assert final_pins[str(path)] == sha(path)
    assert json.loads((root / "workflow.json").read_bytes()) == json.loads(
        (root / "graph_rounds/002/workflow.json").read_bytes())
    repair_prompt, review_prompt = pool.calls[4][2], pool.calls[5][2]
    repair_digests = json.loads((pool.calls[4][3] / "instruction.json").read_bytes())["canonical_object_digests"]
    for name in ("previous_artifact", "previous_workflow", "previous_workflow_review", "previous_assessment", "previous_scores"):
        path = Path(re.search(rf"^{name}: (.+)$", repair_prompt, re.MULTILINE)[1])
        assert repair_digests[name + "_sha256"] == digest(json.loads(path.read_bytes()))
    assert "previous_artifact:" not in review_prompt and "feedback:" not in review_prompt
    assert str(root / "graph_rounds/002/workflow.json") in review_prompt
    assert all(json.loads((call[3] / "instruction.json").read_bytes())["canonical_object_digests"][
        "reference_sha256"] == case["reference_sha256"] for call in pool.calls[2:])
    if fault in {"graph_revision_once", "factual_unsupported_once"}:
        assert first["workflow_sha256"] != second["workflow_sha256"]
        assert "Discard all indexed-well attribution" in (root / "graph_rounds/001/workflow.json").read_text()
        assert "Discard all indexed-well attribution" not in (root / "workflow.json").read_text()
    assert (root / "projection/graph_candidate.json").is_file()


@pytest.mark.parametrize("fault,status,calls,selected", [
    ("factual_unsupported_always", "factual_assertions_unsupported", 6, 2),
    ("unsupported_graph", "graph_needs_revision", 6, 2),
    ("graph_revision_then_uncertain", "graph_uncertain", 6, 2),
    ("graph_repair_missing_artifact", "graph_constructor_failed", 5, 1),
    ("graph_uncertain", "graph_uncertain", 4, 1)])
async def test_failed_or_uncertain_graph_correction_never_exports_or_retries_again(records, tmp_path, fault, status, calls, selected):
    pool = FixturePool(records, fault=fault)
    output = tmp_path / "negative"
    state = await run_batch(manifest_for(records, tmp_path), output, worker_factory=pool.worker)
    case, root = state["cases"]["fixture"], output / "cases/fixture"
    assert case["status"] == status and case["selected_graph_round"] == selected
    assert len(pool.calls) == calls and case["graph_repairs_used"] <= 1
    assert not (root / "projection").exists()
    assert len(case["graph_rounds"]) == selected
    for name in ("workflow", "workflow_review", "assessment", "scores"):
        assert json.loads((root / f"{name}.json").read_bytes()) == json.loads(
            (root / f"graph_rounds/{selected:03d}/{name}.json").read_bytes())
    if fault == "factual_unsupported_always":
        assert case["unsupported_factual_audit_ids"] == ["attribution_claim"]
        scores = json.loads((root / "scores.json").read_bytes())
        assert scores["obligation_coverage"]["strict_covered_fraction"] == 1
        assert scores["factual_audit"]["counts"]["unsupported"] == 1
        assert all(row["unsupported_factual_audit_ids"] == ["attribution_claim"] for row in case["graph_rounds"])


@pytest.mark.parametrize("fault", ["factual_uncertain", "factual_unassessed"])
async def test_undecided_factual_audits_do_not_become_negatives_or_trigger_correction(records, tmp_path, fault):
    pool = FixturePool(records, fault=fault)
    output = tmp_path / "undecided"
    state = await run_batch(manifest_for(records, tmp_path), output, worker_factory=pool.worker)
    case = state["cases"]["fixture"]
    assert len(pool.calls) == 4 and case["graph_repairs_used"] == 0
    assert case["status"] == "model_reviewed_reference_candidate"
    assert case["unsupported_factual_audit_ids"] == []
    scores = json.loads((output / "cases/fixture/scores.json").read_bytes())
    assert scores["factual_audit"]["counts"]["unsupported"] == 0
    assert scores["factual_audit"]["supported_claim_precision"] is None


async def test_declared_source_report_is_bound_in_every_case_and_legacy_remains_unbound(records, tmp_path):
    manifest = manifest_for(records, tmp_path, ids=("one", "two"))
    document = json.loads(manifest.read_bytes())
    report = tmp_path / "source_report.json"
    report.write_text('{"cases": {"one": {}, "two": {}}}')
    document["source_report"] = {"path": report.name, "sha256": sha(report)}
    for row in document["cases"]:
        row["source_report"] = report.name
    manifest.write_text(json.dumps(document))
    output = tmp_path / "bound"
    state = await run_batch(manifest, output, prepare_only=True)
    assert state["source_report_binding"] == "declared_sha256_verified"
    for ident in ("one", "two"):
        bindings = json.loads((output / f"cases/{ident}/bindings.json").read_bytes())
        assert bindings[str(report)] == sha(report)
    copied = json.loads((output / "manifest.json").read_bytes())
    assert copied["source_report"]["path"] == str(report)
    assert all(row["source_report"] == str(report) for row in copied["cases"])
    document.pop("source_report")
    manifest.write_text(json.dumps(document))
    legacy = await run_batch(manifest, tmp_path / "legacy", prepare_only=True)
    assert legacy["source_report_binding"] == "legacy_unbound"


@pytest.mark.parametrize("defect", ["changed", "missing", "wrong_row", "unselected_wrong_row"])
async def test_source_report_custody_failures_stop_before_any_native_call(records, tmp_path, defect):
    manifest = manifest_for(records, tmp_path, ids=("one", "two"))
    document = json.loads(manifest.read_bytes())
    report = tmp_path / "source_report.json"
    report.write_text('{"cases": {}}')
    document["source_report"] = {"path": report.name, "sha256": sha(report)}
    for row in document["cases"]:
        row["source_report"] = report.name
    if defect == "changed":
        report.write_text('{"cases": {"changed": {}}}')
    elif defect == "missing":
        report.unlink()
    else:
        document["cases"][1 if defect == "unselected_wrong_row" else 0]["source_report"] = "other_report.json"
    manifest.write_text(json.dumps(document))
    output = tmp_path / "blocked"
    pool = FixturePool(records)
    with pytest.raises(ValueError, match="Frozen file changed|globally bound source report"):
        await run_batch(manifest, output, case_ids=["one"], worker_factory=pool.worker)
    assert not pool.calls and not output.exists()
