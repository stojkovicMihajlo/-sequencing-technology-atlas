"""Comparison orchestration checks with local fixtures and independently archived fake calls."""

import asyncio
from dataclasses import asdict
from html import escape
import json
from pathlib import Path
import re

import pytest

from test_protocol_comparisons import comparison_records as comparison_records
from test_workflow_extraction_runtime import make_review
from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.protocol_benchmark.comparison_runtime import (
    ARCHIVES, run_comparison_batch,
)
from genomics_discovery.curation.protocol_benchmark.comparisons import ComparisonReference, ComparisonReview
from genomics_discovery.curation.protocol_benchmark.models import BenchmarkAssessment, ProtocolReference, ReferenceReview
from genomics_discovery.curation.run_state import sha, write
from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.models import WorkflowDraft, digest
from genomics_discovery.curation.workflow_extraction.sources import build_packet


def native_archive(attempt, document, thread, prompt="Fixture native reference call"):
    attempt.mkdir(parents=True, exist_ok=True)
    write(attempt / "artifact.json", document)
    (attempt / "prompt.txt").write_text(prompt)
    usage = {"input_tokens": 10, "output_tokens": 2}
    result = WorkerResult(0, thread, usage, .01, None)
    events = [{"type": "thread.started", "thread_id": thread}, {"type": "turn.started"},
              {"type": "turn.completed", "usage": usage}]
    (attempt / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    write(attempt / "result.json", asdict(result))
    for name in ("command.json", "started.json"):
        write(attempt / name, {"fixture": True})
    (attempt / "stderr.log").write_text("")
    (attempt / "final.txt").write_text("Synthetic fixture, not scientific validation.")
    return result


@pytest.fixture
def ready_run(comparison_records, tmp_path):
    comparison, review, args, task, workflows = comparison_records
    references, packets = args[:2], args[2:]
    reference_run = tmp_path / "references"
    rows, reports = [], {}
    comparison_raw, review_raw = comparison.model_dump(mode="json"), review.model_dump(mode="json")
    for side, reference, packet, workflow in zip(("left", "right"), references, packets, workflows):
        primary = tmp_path / (side + "_primary.xml")
        primary.write_text('<article><front><article-meta><article-id pub-id-type="pmid">'
            + ("123" if side == "left" else "456") + '</article-id><article-id pub-id-type="doi">10.1234/'
            + side + '</article-id><title-group><article-title>Synthetic publication '
            + side + '</article-title></title-group></article-meta></front><body><sec><title>Methods</title>'
            + ''.join('<p>' + escape(passage["text"]) + '</p>' for passage in packet["sources"][0]["passages"])
            + '</sec></body></article>')
        packet = build_packet(**{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
            sources=[{"source_id": "paper", "title": "Synthetic primary source", "url": f"https://example.org/{side}",
                "role": "primary_paper", "path": str(primary)}])
        reference = ProtocolReference.model_validate({**reference.model_dump(mode="json"), "source_packet_sha256": digest(packet)})
        comparison_raw[side].update(reference_sha256=digest(reference), source_packet_sha256=digest(packet))
        review_raw[side + "_reference_sha256"] = digest(reference)
        review_raw[side + "_source_packet_sha256"] = digest(packet)
        directory = reference_run / "cases" / side
        directory.mkdir(parents=True)
        packet_path = tmp_path / (side + "_packet.json")
        write(packet_path, packet)
        write(directory / "input.json", {"case_id": side, "packet": str(packet_path),
            "packet_sha256": sha(packet_path), "source_packet_sha256": digest(packet),
            **{key: packet[key] for key in ("method_id", "title", "scope", "variant")}})
        inspected = [{"source_id": row.source_id, "passage_id": pid}
            for row in reference.source_coverage for pid in row.inspected_passage_ids]
        evidence = [ev.model_dump(mode="json") for obligation in reference.obligations for ev in obligation.evidence]
        ref_review = ReferenceReview.model_validate({"reference_sha256": digest(reference),
            "source_packet_sha256": digest(packet), "inspected_passages": inspected,
            **{field: [{"target_id": item.id, "verdict": "supported", "rationale": "Synthetic source check.",
                "evidence": evidence} for item in getattr(reference, collection)] for field, collection in (
                ("obligation_checks", "obligations"), ("decomposition_checks", "admissible_decompositions"),
                ("unknown_checks", "unknowns"), ("query_checks", "query_cases"))}, "missing_obligations": []})
        raw = workflow.model_dump(mode="json")
        raw["ordering_flows"] = [flow for flow in raw.pop("flows") if flow["kind"] in {"control", "precedence"}]
        for operation in raw["operations"]:
            for direction in ("input", "output"):
                operation[direction + "_bindings"] = [{"state_id": sid, "basis": "Source-bound fixture association.",
                    "selection": "required", "evidence": operation["evidence"]} for sid in operation[direction + "s"]]
        draft = WorkflowDraft.model_validate(raw)
        workflow = assemble_workflow(draft, require_bindings=True)
        workflow_review = make_review(workflow.model_dump(mode="json"), packet)
        assessment = BenchmarkAssessment.model_validate({"reference_sha256": digest(reference),
            "source_packet_sha256": digest(packet), "workflow_sha256": digest(workflow),
            "inspected_passages": inspected, "obligations": [{"obligation_id": item.id,
                "outcome": "covered", "targets": ["operation:capture"], "rationale": "Synthetic witness.",
                "evidence": [item.evidence[0].model_dump(mode="json")]} for item in reference.obligations],
            "factual_audits": []})
        artifacts = {"reference": reference.model_dump(mode="json"), "reference_review": ref_review.model_dump(mode="json"),
            "workflow": workflow.model_dump(mode="json"), "workflow_review": workflow_review,
            "assessment": assessment.model_dump(mode="json")}
        for name, document in artifacts.items():
            write(directory / (name + ".json"), document)
        attempts = []
        for index, (role, document) in enumerate((("reference_annotator", artifacts["reference"]),
                ("reference_reviewer", artifacts["reference_review"]), ("graph_constructor", draft.model_dump(mode="json")),
                ("graph_reviewer", {"workflow_review": workflow_review, "assessment": artifacts["assessment"]})), 1):
            attempt = directory / "attempts" / f"{index:03}_{role}"
            result = native_archive(attempt, document, f"reference-{side}-{index}")
            attempts.append({"role": role, "attempt": str(attempt), "status": "complete",
                "transport_complete": True, "thread_id": result.thread_id, "usage": result.usage})
        write(directory / "state.json", {"case_id": side, "status": "model_reviewed_reference_candidate", "attempts": attempts})
        pins = {str(path): sha(path) for path in directory.rglob("*") if path.is_file() and path.name != "state.json"}
        pins.update({str(packet_path): sha(packet_path), **{source["original_path"]: source["original_sha256"] for source in packet["sources"]}})
        write(directory / "bindings.json", pins)
        report_path = tmp_path / "source_report.json"
        rows.append({"case_id": side, "packet": str(packet_path), "packet_sha256": sha(packet_path), "source_report": str(report_path)})
        reports[packet["method_id"]] = {"status": "registered_for_source_first_annotation", "packet": str(packet_path),
            "packet_sha256": sha(packet_path), "source_packet_sha256": digest(packet)}
    write(reference_run / "manifest.json", {"cases": rows})
    write(tmp_path / "source_report.json", {"cases": reports})
    tasks_path = tmp_path / "tasks.json"
    write(tasks_path, {"comparisons": [task.model_dump(mode="json")], "annotation_policy": {"unknown": "not false"}})
    comparison = ComparisonReference.model_validate(comparison_raw)
    review = ComparisonReview.model_validate({**review_raw, "comparison_sha256": digest(comparison)})
    return reference_run, tasks_path, comparison, review, task


def declare_dataset(ready, tmp_path):
    reference_run, tasks, *_ = ready
    cases = []
    for side in ("left", "right"):
        inputs = json.loads((reference_run / "cases" / side / "input.json").read_bytes())
        cases.append({"case_id": side, **{key: inputs[key] for key in ("method_id", "title", "scope", "variant")},
            "primary_identity": {"pmid": "123" if side == "left" else "456", "doi": "10.1234/" + side}})
    design = tmp_path / "design/cases.json"
    write(design, {"cases": cases})
    document = json.loads(tasks.read_bytes())
    document["cases_manifest"] = "design/cases.json"
    write(tasks, document)
    return design


class FixturePool:
    def __init__(self, ready, *, fault=None):
        self.reference_run, self.tasks, self.comparison, self.review, self.task = ready
        self.fault, self.calls, self.active, self.maximum = fault, [], 0, 0

    def worker(self):
        pool = self

        class Worker:
            async def run(self, prompt, attempt):
                role = re.search(r"ROLE: benchmark_(\w+)", prompt)[1]
                session = re.search(r"Assigned logical \w+: (.+)", prompt)[1]
                task = json.loads(Path(re.search(r"^task: (.+)$", prompt, re.MULTILINE)[1]).read_bytes())
                bindings = json.loads(re.search(r"```json\n(.*?)\n```", prompt, re.DOTALL)[1])
                pool.calls.append((role, prompt, attempt))
                index = len(pool.calls)
                pool.active += 1
                pool.maximum = max(pool.maximum, pool.active)
                await asyncio.sleep(0)
                if role == "comparison_annotator":
                    document = pool.comparison.model_dump(mode="json")
                    document.update(comparison_id=task["comparison_id"], task_sha256=bindings["task_sha256"], annotation_session_id=session)
                    for side in ("left", "right"):
                        document[side]["reference_sha256"] = bindings[side + "_reference_sha256"]
                        document[side]["source_packet_sha256"] = bindings[side + "_source_packet_sha256"]
                        document[side]["unit"]["workflow_sha256"] = bindings[side + "_workflow_sha256"]
                    if pool.fault == "side_evidence":
                        document["left"]["unit"]["evidence"][0] = document["right"]["unit"]["evidence"][0]
                    elif pool.fault == "wrong_hash":
                        document["left"]["reference_sha256"] = "f" * 64
                    elif pool.fault == "wrong_workflow":
                        document["left"]["unit"]["workflow_sha256"] = None
                    elif pool.fault == "wrong_session":
                        document["annotation_session_id"] = "unassigned-session"
                    elif pool.fault == "no_claims":
                        document.update(claims=[], no_claims_reason="No relation established in the inspected fixture scope.")
                else:
                    authored = ComparisonReference.model_validate_json(Path(re.search(r"^comparison: (.+)$", prompt, re.MULTILINE)[1]).read_bytes())
                    document = pool.review.model_dump(mode="json")
                    document.update(comparison_sha256=digest(authored), task_sha256=bindings["task_sha256"], review_session_id=session)
                    for side in ("left", "right"):
                        document[side + "_reference_sha256"] = bindings[side + "_reference_sha256"]
                        document[side + "_source_packet_sha256"] = bindings[side + "_source_packet_sha256"]
                    if pool.fault == "self_approval":
                        document["review_session_id"] = authored.annotation_session_id
                    elif pool.fault == "no_claims":
                        document.update(claim_checks=[], no_claims_check="supported", no_claims_rationale="Both declared scopes inspected without a forced edge.")
                thread = "reused-native" if pool.fault == "duplicate_thread" else f"comparison-native-{index}"
                if pool.fault == "reference_thread":
                    thread = "reference-left-1"
                if pool.fault == "failed_reference_thread":
                    thread = "previous-incomplete-reference-thread"
                result = native_archive(attempt, document, thread, prompt)
                if pool.fault == "missing_archive":
                    (attempt / "command.json").unlink()
                if pool.fault == "source_mutation":
                    packet = json.loads(Path(re.search(r"^left_packet: (.+)$", prompt, re.MULTILINE)[1]).read_bytes())
                    Path(packet["sources"][0]["original_path"]).write_text("Changed after review.")
                pool.active -= 1
                return result

        return Worker()


async def test_two_fresh_bound_stages_keep_unknowns_and_only_benchmark_exports(ready_run, tmp_path):
    references, tasks, _, _, task = ready_run
    before = {path: path.read_bytes() for path in references.rglob("*") if path.is_file()}
    pool = FixturePool(ready_run)
    output = tmp_path / "comparisons"
    state = await run_comparison_batch(tasks, references, output, worker_factory=pool.worker)
    assert state["status"] == "completed"
    assert state["benchmark_1_1_ready_pair_count"] == 0 and not state["benchmark_1_1_ready"]
    assert state["dataset_definition"]["status"] == "unbound_dataset_definition"
    assert [call[0] for call in pool.calls] == ["comparison_annotator", "comparison_reviewer"]
    assert state["completed_native_sessions"] == state["distinct_native_threads"] == 2
    assert state["inherited_reference_threads"] == 8
    assert state["usage"] == {"input_tokens": 20, "output_tokens": 4}
    pair = output / "cases" / task.comparison_id
    score = json.loads((pair / "scores.json").read_bytes())
    assert score["counts"] == {"supported": 1, "contradicted": 1, "not_established": 2}
    relations = json.loads((pair / "relations.json").read_bytes())
    assert [row["claim_type"] for row in relations["relations"]] == ["shared_purpose"]
    assert len(relations["unexported_claims"]) == 3
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not (output / "projection").exists()
    assert not (output / "registry").exists()
    attempts = state["pairs"][task.comparison_id]["attempts"]
    assert attempts[0]["logical_session_id"] != attempts[1]["logical_session_id"]
    assert attempts[0]["thread_id"] != attempts[1]["thread_id"]
    assert json.loads((pair / "reference.json").read_bytes())["annotation_session_id"] == attempts[0]["logical_session_id"]
    assert json.loads((pair / "review.json").read_bytes())["review_session_id"] == attempts[1]["logical_session_id"]
    pins = json.loads((pair / "bindings.json").read_bytes())
    assert all(sha(Path(path)) == expected for path, expected in pins.items())
    inputs = json.loads((pair / "input.json").read_bytes())
    gate = inputs["source_gates"]["left"]
    assert gate["report_binding_stage"] == "comparison_preflight" and not gate["original_report_byte_pin_available"]
    assert gate["unknown_preservation"]["status"] == "not_assessed" and not gate["benchmark_1_1_ready"]
    assert gate["report_sha256"] == sha(Path(gate["report_path"]))
    assert "not authenticated as the original report version" in gate["custody_limitation"]
    for role, prompt, attempt in pool.calls:
        assert all((attempt / name).is_file() for name in ARCHIVES)
        assert (attempt / "prompt.txt").read_text() == prompt
        instruction = json.loads((attempt / "instruction.json").read_bytes())
        supplied = json.loads(re.search(r"```json\n(.*?)\n```", prompt, re.DOTALL)[1])
        assert instruction["canonical_object_digests"] == supplied
        assert all(key in supplied for key in ("task_sha256", "left_reference_sha256", "right_reference_sha256",
            "left_workflow_sha256", "right_workflow_sha256", "left_source_packet_sha256", "right_source_packet_sha256"))
        assert "expected_label" not in prompt and "not file-byte hashes" in prompt
        assert "scope, judgment, evidence" not in prompt or role == "comparison_reviewer"
        if role == "comparison_reviewer":
            assert "comparison_sha256" in supplied and "all seven fields" in prompt


@pytest.mark.parametrize("fault,calls", [("side_evidence", 1), ("wrong_hash", 1), ("wrong_workflow", 1),
    ("wrong_session", 1), ("duplicate_thread", 2), ("reference_thread", 1), ("missing_archive", 1),
    ("self_approval", 2), ("source_mutation", 1)])
async def test_invalid_outputs_are_archived_and_withheld_without_repairs(ready_run, tmp_path, fault, calls):
    references, tasks, _, _, task = ready_run
    pool = FixturePool(ready_run, fault=fault)
    output = tmp_path / "failed"
    state = await run_comparison_batch(tasks, references, output, worker_factory=pool.worker)
    assert state["status"] == "finished_with_withheld_or_failed_pairs"
    assert len(pool.calls) == calls
    assert state["pairs"][task.comparison_id]["status"] == "failed"
    assert state["pairs"][task.comparison_id]["repairs_used"] == 0
    assert not (output / "cases" / task.comparison_id / "relations.json").exists()
    assert all((attempt / "artifact.json").is_file() for _, _, attempt in pool.calls)


async def test_incomplete_reference_thread_id_is_reserved(ready_run, tmp_path):
    references, tasks, _, _, task = ready_run
    directory = references / "cases/left"
    attempt = directory / "attempts/000_incomplete"
    attempt.mkdir()
    (attempt / "events.jsonl").write_text(json.dumps({"type": "thread.started", "thread_id": "previous-incomplete-reference-thread"}) + "\n")
    state = json.loads((directory / "state.json").read_bytes())
    state["attempts"].insert(0, {"role": "reference_annotator", "attempt": str(attempt), "transport_complete": False})
    write(directory / "state.json", state)
    pool = FixturePool(ready_run, fault="failed_reference_thread")
    result = await run_comparison_batch(tasks, references, tmp_path / "failed-old-thread", worker_factory=pool.worker)
    assert result["pairs"][task.comparison_id]["error_category"] == "native_transport"
    assert len(pool.calls) == 1


async def test_reference_native_thread_reuse_across_cases_withholds_both_sides(ready_run, tmp_path):
    references, tasks, _, _, task = ready_run
    directory = references / "cases/right"
    state = json.loads((directory / "state.json").read_bytes())
    attempt = Path(state["attempts"][0]["attempt"])
    native_archive(attempt, json.loads((attempt / "artifact.json").read_bytes()), "reference-left-1")
    state["attempts"][0]["thread_id"] = "reference-left-1"
    write(directory / "state.json", state)
    pins = json.loads((directory / "bindings.json").read_bytes())
    pins.update({str(attempt / name): sha(attempt / name) for name in ARCHIVES})
    write(directory / "bindings.json", pins)
    pool = FixturePool(ready_run)
    state = await run_comparison_batch(tasks, references, tmp_path / "cross-case-reuse", worker_factory=pool.worker)
    assert pool.calls == []
    pair = state["pairs"][task.comparison_id]
    assert pair["status"] == "withheld"
    assert set(pair["withheld_cases"]) == {"left", "right"}
    assert {value["error_category"] for value in pair["withheld_cases"].values()} == {"reference_thread_reused"}


async def test_declared_dataset_identity_is_pinned_and_checked(ready_run, tmp_path):
    references, tasks, _, _, task = ready_run
    design = declare_dataset(ready_run, tmp_path)
    pool = FixturePool(ready_run)
    output = tmp_path / "dataset-bound"
    state = await run_comparison_batch(tasks, references, output, worker_factory=pool.worker)
    assert state["status"] == "completed" and len(pool.calls) == 2
    assert state["dataset_definition"]["path"] == str(design)
    assert state["dataset_definition"]["sha256"] == sha(design)
    pins = json.loads((output / "cases" / task.comparison_id / "bindings.json").read_bytes())
    assert pins[str(design)] == sha(design)


async def test_reader_keeps_legacy_and_versioned_assessment_bindings(ready_run, tmp_path):
    references, tasks, _, _, task = ready_run
    directory = references / "cases/left"
    assessment_path = directory / "assessment.json"
    assessment = json.loads(assessment_path.read_bytes())
    assessment.update(format_version="protocol_benchmark_assessment/1.1", unknown_checks=[])
    write(assessment_path, assessment)
    state = json.loads((directory / "state.json").read_bytes())
    graph_path = Path(state["attempts"][-1]["attempt"]) / "artifact.json"
    combined = json.loads(graph_path.read_bytes())
    combined["assessment"] = assessment
    write(graph_path, combined)
    pins = json.loads((directory / "bindings.json").read_bytes())
    pins.update({str(assessment_path): sha(assessment_path), str(graph_path): sha(graph_path)})
    write(directory / "bindings.json", pins)
    pool = FixturePool(ready_run)
    result = await run_comparison_batch(tasks, references, tmp_path / "mixed-versions", worker_factory=pool.worker)
    assert result["pairs"][task.comparison_id]["status"] == "supported_comparison_candidate"
    assert not result["benchmark_1_1_ready"] and result["benchmark_1_1_ready_pair_count"] == 0
    assert len(pool.calls) == 2


def assessed_unknown(directory, outcome=None):
    reference_path, review_path = directory / "reference.json", directory / "reference_review.json"
    reference, review = json.loads(reference_path.read_bytes()), json.loads(review_path.read_bytes())
    if outcome is not None:
        coverage = reference["source_coverage"][0]
        reference["unknowns"] = [{"id": "capture-uncertainty", "question": "Which additional capture conditions are unreported?",
            "consequence": "Preserve scoped uncertainty rather than add an unsupported condition.",
            "inspected_passages": [{"source_id": coverage["source_id"], "passage_id": coverage["inspected_passage_ids"][0]}],
            "next_evidence": "A source reporting those additional capture conditions."}]
        review["unknown_checks"] = [{"target_id": "capture-uncertainty", "verdict": "supported",
            "rationale": "A scoped synthetic unknown checked against the declared passages.", "evidence": []}]
    reference_digest = digest(ProtocolReference.model_validate(reference))
    review["reference_sha256"] = reference_digest
    assessment_path = directory / "assessment.json"
    assessment = json.loads(assessment_path.read_bytes())
    assessment.update(format_version="protocol_benchmark_assessment/1.1", reference_sha256=reference_digest, unknown_checks=[])
    if outcome is not None:
        assessment["unknown_checks"] = [{"unknown_id": "capture-uncertainty", "outcome": outcome,
            "targets": ["operation:capture"] if outcome in {"retained", "partial"} else [],
            "rationale": "An explicit synthetic uncertainty-preservation judgment.",
            "evidence": reference["obligations"][0]["evidence"] if outcome == "reference_revision_needed" else []}]
    state = json.loads((directory / "state.json").read_bytes())
    artifacts = {record["role"]: Path(record["attempt"]) / "artifact.json" for record in state["attempts"]}
    graph = json.loads(artifacts["graph_reviewer"].read_bytes())
    graph["assessment"] = assessment
    updates = {reference_path: reference, review_path: review, assessment_path: assessment,
        artifacts["reference_annotator"]: reference, artifacts["reference_reviewer"]: review,
        artifacts["graph_reviewer"]: graph}
    for path, value in updates.items():
        write(path, value)
    pins = json.loads((directory / "bindings.json").read_bytes())
    pins.update({str(path): sha(path) for path in updates})
    write(directory / "bindings.json", pins)


@pytest.mark.parametrize("outcome", ["retained", "partial", "missing", "reference_revision_needed", "unassessed"])
async def test_assessment_1_1_unknown_gate_precedes_comparison_calls(ready_run, tmp_path, outcome):
    references, tasks, _, _, task = ready_run
    assessed_unknown(references / "cases/left", outcome)
    assessed_unknown(references / "cases/right")
    pool = FixturePool(ready_run)
    output = tmp_path / "unknown-gate"
    result = await run_comparison_batch(tasks, references, output, worker_factory=pool.worker)
    pair = result["pairs"][task.comparison_id]
    if outcome == "retained":
        assert pair["status"] == "supported_comparison_candidate" and len(pool.calls) == 2
        assert pair["benchmark_1_1_ready"] and result["benchmark_1_1_ready_pair_count"] == 1
        assert result["benchmark_1_1_ready"]
        inputs = json.loads((output / "cases" / task.comparison_id / "input.json").read_bytes())
        assert inputs["source_gates"]["left"]["unknown_preservation"]["status"] == "retained"
        assert inputs["source_gates"]["right"]["unknown_preservation"]["status"] == "not_applicable"
    else:
        assert pair["status"] == "withheld" and pool.calls == []
        assert pair["withheld_cases"]["left"]["error_category"] == "unknown_preservation_gate"
        assert not result["benchmark_1_1_ready"] and result["benchmark_1_1_ready_pair_count"] == 0


@pytest.mark.parametrize("version", ["legacy", "1.1"])
@pytest.mark.parametrize("verdict", ["unsupported", "uncertain", "unassessed"])
async def test_known_unsupported_factual_audit_withholds_before_calls_without_turning_uncertainty_false(
        ready_run, tmp_path, version, verdict):
    references, tasks, _, _, task = ready_run
    directory = references / "cases/left"
    if version == "1.1":
        assessed_unknown(directory)
    assessment_path = directory / "assessment.json"
    assessment = json.loads(assessment_path.read_bytes())
    reference = json.loads((directory / "reference.json").read_bytes())
    assessment["factual_audits"] = [{"id": "emitted-extra-assertion", "claim": "An explicitly audited emitted assertion.",
        "targets": ["operation:capture"], "verdict": verdict, "rationale": "Separate source-qualified factual judgment.",
        "evidence": reference["obligations"][0]["evidence"]}]
    write(assessment_path, assessment)
    state = json.loads((directory / "state.json").read_bytes())
    native_artifact = Path(state["attempts"][-1]["attempt"]) / "artifact.json"
    archived = json.loads(native_artifact.read_bytes())
    archived["assessment"] = assessment
    write(native_artifact, archived)
    pins = json.loads((directory / "bindings.json").read_bytes())
    pins.update({str(assessment_path): sha(assessment_path), str(native_artifact): sha(native_artifact)})
    write(directory / "bindings.json", pins)
    pool = FixturePool(ready_run)
    result = await run_comparison_batch(tasks, references, tmp_path / "factual-gate", worker_factory=pool.worker)
    pair = result["pairs"][task.comparison_id]
    if verdict == "unsupported":
        assert pair["status"] == "withheld" and pool.calls == []
        assert pair["withheld_cases"]["left"]["error_category"] == "unsupported_factual_audit"
        assert "emitted-extra-assertion" in pair["withheld_cases"]["left"]["error"]
    else:
        assert pair["status"] == "supported_comparison_candidate" and len(pool.calls) == 2


@pytest.mark.parametrize("field", ["method_id", "title", "scope", "variant", "pmid", "doi"])
async def test_same_case_id_cannot_substitute_different_preregistered_method_or_publication(ready_run, tmp_path, field):
    references, tasks, _, _, task = ready_run
    design = declare_dataset(ready_run, tmp_path)
    document = json.loads(design.read_bytes())
    if field in {"pmid", "doi"}:
        document["cases"][0]["primary_identity"][field] = "999" if field == "pmid" else "10.9999/other"
    else:
        document["cases"][0][field] = "Different declared identity/scope"
    write(design, document)
    pool = FixturePool(ready_run)
    state = await run_comparison_batch(tasks, references, tmp_path / "dataset-withheld", worker_factory=pool.worker)
    assert pool.calls == []
    pair = state["pairs"][task.comparison_id]
    assert pair["status"] == "withheld"
    expected = "dataset_publication_identity" if field in {"pmid", "doi"} else "dataset_case_identity"
    assert pair["withheld_cases"]["left"]["error_category"] == expected


async def test_failed_unrelated_reference_case_thread_is_still_reserved(ready_run, tmp_path):
    references, tasks, _, _, task = ready_run
    manifest = json.loads((references / "manifest.json").read_bytes())
    manifest["cases"].append({"case_id": "failed-unrelated"})
    write(references / "manifest.json", manifest)
    attempt = references / "cases/failed-unrelated/attempts/001_failed"
    attempt.mkdir(parents=True)
    (attempt / "events.jsonl").write_text(json.dumps({"type": "thread.started", "thread_id": "previous-incomplete-reference-thread"}) + "\n")
    write(attempt.parents[1] / "state.json", {"attempts": [{"attempt": str(attempt), "transport_complete": False}]})
    pool = FixturePool(ready_run, fault="failed_reference_thread")
    result = await run_comparison_batch(tasks, references, tmp_path / "failed-unrelated-reuse", worker_factory=pool.worker)
    assert result["pairs"][task.comparison_id]["error_category"] == "native_transport"
    assert len(pool.calls) == 1


@pytest.mark.parametrize("field", ["partial_required", "review_hash", "source_report", "published_artifact", "source_original"])
async def test_revalidates_whole_reference_gate_before_native_calls(ready_run, tmp_path, field):
    references, tasks, _, _, task = ready_run
    directory = references / "cases/left"
    if field == "source_report":
        path = tmp_path / "source_report.json"
        value = json.loads(path.read_bytes())
        value["cases"]["left"]["status"] = "source_acquisition_needed"
    elif field == "source_original":
        packet = json.loads((tmp_path / "left_packet.json").read_bytes())
        path = Path(packet["sources"][0]["original_path"])
        path.write_text("Changed original")
        value = None
    else:
        path = directory / ("assessment.json" if field == "partial_required" else "reference_review.json" if field == "review_hash" else "reference.json")
        value = json.loads(path.read_bytes())
        if field == "partial_required":
            value["obligations"][0]["outcome"] = "partial"
        elif field == "review_hash":
            value["reference_sha256"] = "a" * 64
        else:
            value["measurement"] = "Changed after native review"
    if value is not None:
        write(path, value)
    # Update the local file pin to ensure this is a semantic/binding recheck,
    # rather than only a stale file-byte checksum test.
    pins = json.loads((directory / "bindings.json").read_bytes())
    if str(path) in pins:
        pins[str(path)] = sha(path)
    write(directory / "bindings.json", pins)
    pool = FixturePool(ready_run)
    state = await run_comparison_batch(tasks, references, tmp_path / "withheld", worker_factory=pool.worker)
    assert pool.calls == []
    assert state["pairs"][task.comparison_id]["status"] == "withheld"


async def test_missing_pair_does_not_cancel_eligible_pair_and_global_barriers_hold(ready_run, tmp_path):
    references, tasks, _, _, task = ready_run
    document = json.loads(tasks.read_bytes())
    document["comparisons"] += [{**task.model_dump(mode="json"), "comparison_id": "missing", "right_case_id": "absent"},
        {**task.model_dump(mode="json"), "comparison_id": "second-valid"}]
    write(tasks, document)
    pool = FixturePool(ready_run)
    state = await run_comparison_batch(tasks, references, tmp_path / "mixed", concurrency=2, worker_factory=pool.worker)
    assert state["pairs"]["missing"]["status"] == "withheld"
    assert state["pairs"][task.comparison_id]["status"] == state["pairs"]["second-valid"]["status"] == "supported_comparison_candidate"
    assert [role for role, _, _ in pool.calls] == ["comparison_annotator"] * 2 + ["comparison_reviewer"] * 2
    assert pool.maximum == 2


async def test_empty_independently_reviewed_pair_is_retained_without_forced_edge(ready_run, tmp_path):
    references, tasks, _, _, task = ready_run
    pool = FixturePool(ready_run, fault="no_claims")
    output = tmp_path / "empty"
    state = await run_comparison_batch(tasks, references, output, worker_factory=pool.worker)
    assert state["status"] == "completed" and len(pool.calls) == 2
    relations = json.loads((output / "cases" / task.comparison_id / "relations.json").read_bytes())
    assert relations["relations"] == []
    assert json.loads((output / "cases" / task.comparison_id / "scores.json").read_bytes())["authored_claim_count"] == 0


async def test_native_guard_and_no_resume(ready_run, tmp_path, monkeypatch):
    references, tasks, _, _, _ = ready_run
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    output = tmp_path / "not-created"
    with pytest.raises(RuntimeError, match="Slurm"):
        await run_comparison_batch(tasks, references, output)
    assert not output.exists()
    pool = FixturePool(ready_run)
    await run_comparison_batch(tasks, references, output, worker_factory=pool.worker)
    with pytest.raises(FileExistsError):
        await run_comparison_batch(tasks, references, output, worker_factory=pool.worker)
    with pytest.raises(ValueError, match="1 through 4"):
        await run_comparison_batch(tasks, references, tmp_path / "too-many", concurrency=5, worker_factory=pool.worker)


async def test_cancellation_preserves_partial_native_identity_and_accounts_queued_pairs(ready_run, tmp_path):
    references, tasks, _, _, task = ready_run
    document = json.loads(tasks.read_bytes())
    document["comparisons"].append({**task.model_dump(mode="json"), "comparison_id": "queued"})
    write(tasks, document)
    started = asyncio.Event()

    class BlockingWorker:
        async def run(self, prompt, attempt):
            (attempt / "prompt.txt").write_text(prompt)
            (attempt / "events.jsonl").write_text(json.dumps({"type": "thread.started", "thread_id": "interrupted-native-thread"}) + "\n")
            started.set()
            await asyncio.Event().wait()

    output = tmp_path / "interrupted"
    running = asyncio.create_task(run_comparison_batch(tasks, references, output,
        concurrency=1, worker_factory=BlockingWorker))
    await asyncio.wait_for(started.wait(), timeout=5)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    summary = json.loads((output / "summary.json").read_bytes())
    assert summary["status"] == "cancelled_or_batch_integrity_failure"
    assert {pair["status"] for pair in summary["pairs"].values()} == {"cancelled"}
    attempt = summary["pairs"][task.comparison_id]["attempts"][0]
    assert attempt["status"] == "cancelled" and not attempt["transport_complete"]
    assert attempt["observed_native_threads"] == ["interrupted-native-thread"]
    assert (Path(attempt["attempt"]) / "events.jsonl").is_file()
    assert summary["pairs"]["queued"]["attempts"] == []
    assert not list(output.rglob("relations.json"))
