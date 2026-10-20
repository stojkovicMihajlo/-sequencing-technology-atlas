"""Small real-packet fixtures and fake native archives; no model calls."""

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from scripts.benchmark.run_grounding_diagnostic import CONFIGS, main, run_diagnostic
from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.protocol_benchmark.grounding_diagnostic import (
    Dataset, Labels, Result, read_closed, score_result, validate_result, verify_dataset,
)
from genomics_discovery.curation.run_state import sha, write
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import build_packet


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "fixture-no-native")
    source = tmp_path / "original.txt"
    source.write_text("Retain the amplified library. The separate QC aliquot is discarded.")
    packet = build_packet(method_id="host-only-method", title="Fixture", scope="Procedure",
        variant="Fixed", sources=[{"source_id": "s1", "path": str(source),
            "title": "Fixture", "url": "https://example.org/source", "role": "primary_paper"}])
    packet_path = tmp_path / "packet.json"
    write(packet_path, packet)
    passage = packet["sources"][0]["passages"][0]
    cases = {"format_version": "protocol_grounding_cases/1.0",
        "annotation_status": "model_authored_diagnostic_candidate",
        "boundary": "HOST_METADATA_MUST_NOT_REACH_READER",
        "contexts": [{"id": "c1", "method_id": "host-only-method",
            "family": "HOST_FAMILY", "split": "HOST_SPLIT",
            "source_packet_sha256": sha(packet_path), "source_packet_digest": digest(packet),
            "source_packet_path": str(packet_path),
            "passages": [{"source_id": "s1", "passage_id": passage["passage_id"], "text": passage["text"]}]}],
        "cases": [{"id": case_id, "context_id": "c1", "field_pointer": "/operations/0/action", "value": value}
                  for case_id, value in (("i1", "Retain the amplified library."),
                      ("i2", "Perform qPCR on every sample."), ("i3", "Discard the amplified library."))]}
    labels = {"format_version": "protocol_grounding_labels/1.0",
        "annotation_status": "model_authored_diagnostic_candidate",
        "cases": [{"case_id": case["id"], "outcome": outcome,
            "phenomenon": "HOST_PHENOMENON", "rationale": "SECRET_LABEL_RATIONALE",
            "decisive_passages": [{"source_id": "s1", "passage_id": passage["passage_id"]}]}
            for case, outcome in zip(cases["cases"], ("supported", "not_established", "contradicted"))]}
    case_path, label_path = tmp_path / "cases.json", tmp_path / "labels.json"
    write(case_path, cases)
    write(label_path, labels)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    for config in CONFIGS:
        (prompts / (config + ".txt")).write_text("Frozen fixture policy: " + config)
    return {"cases": case_path, "labels": label_path, "prompts": prompts,
            "source": source, "packet": packet_path, "root": tmp_path}


def judgments(dataset, outcomes=("supported", "not_established", "contradicted")):
    return {"format_version": "protocol_grounding_result/1.0", "results": [
        {"case_id": case.id, "value_sha256": digest(case.value), "outcome": outcome,
         "rationale": "Fixture source-relative judgment.",
         "inspected_passages": [{"source_id": "s1", "passage_id": "p00001"}]}
        for case, outcome in zip(dataset.cases, outcomes)]}


class Pool:
    def __init__(self, fault=None, source=None):
        self.calls, self.fault, self.source = [], fault, source

    def worker(self):
        pool = self

        class FakeWorker:
            async def run(self, prompt, attempt):
                pool.calls.append((prompt, attempt))
                index = len(pool.calls)
                public = json.loads((attempt / "reader_input.json").read_text())
                document = {"format_version": "protocol_grounding_result/1.0", "results": [
                    {"case_id": case["id"], "value_sha256": case["value_sha256"],
                     "outcome": outcome, "rationale": "A fixture judgment.",
                     "inspected_passages": [{"source_id": "s1", "passage_id": "p00001"}]}
                    for case, outcome in zip(public["cases"], ("supported", "not_established", "contradicted"))]}
                if pool.fault == "missing_row" and index == 1:
                    document["results"].pop()
                if pool.fault == "foreign_passage":
                    document["results"][0]["inspected_passages"][0]["source_id"] = "foreign"
                if pool.fault == "stale_value":
                    document["results"][0]["value_sha256"] = "0" * 64
                write(attempt / "artifact.json", document)
                (attempt / "prompt.txt").write_text(prompt)
                thread = "reuse" if pool.fault in {"thread_reuse", "failed_thread_reuse"} else f"thread-{index}"
                usage = {"input_tokens": 20, "output_tokens": 8}
                events = [{"type": "thread.started", "thread_id": thread}, {"type": "turn.started"}]
                failed = index == 1 and pool.fault in {"failed_native", "failed_thread_reuse"}
                if not failed:
                    events.append({"type": "turn.completed", "usage": usage})
                (attempt / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
                result = WorkerResult(0, thread, usage, .2, None)
                write(attempt / "result.json", asdict(result))
                for name in ("command.json", "started.json"):
                    write(attempt / name, {"fixture": True})
                (attempt / "stderr.log").write_text("")
                (attempt / "final.txt").write_text("Fixture complete")
                if pool.fault == "scratch":
                    write(attempt / "scratch.json", {})
                if pool.fault == "mutate_public":
                    write(attempt / "reader_input.json", {})
                if pool.fault == "mutate_source_second" and index == 2:
                    pool.source.write_text("Changed source after the second session.")
                return result

        return FakeWorker()


def execute(fixture, pool, **kwargs):
    return asyncio.run(run_diagnostic(fixture["cases"], fixture["root"] / "run",
        labels_path=fixture["labels"], prompt_directory=fixture["prompts"],
        worker_factory=pool.worker, **kwargs))


def test_two_matched_fresh_sessions_and_host_metrics(fixture):
    pool = Pool()
    state = execute(fixture, pool)
    assert state["status"] == "completed"
    assert len(pool.calls) == 2
    assert state["distinct_observed_thread_count"] == 2
    assert state["completed_native_sessions"] == 2
    first, second = state["attempts"]
    assert first["reader_input_sha256"] == second["reader_input_sha256"]
    assert first["schema_sha256"] == second["schema_sha256"]
    assert first["instructions_sha256"] != second["instructions_sha256"]
    report = json.loads((fixture["root"] / "run/report.json").read_text())
    metric = report["configurations"]["basic_review"]["overall"]
    assert metric["label_accuracy"] == {"numerator": 3, "denominator": 3, "rate": 1}
    assert metric["unsupported_acceptance"]["denominator"] == 2
    assert report["configurations"]["basic_review"]["whole_graph_precision"] is None


def test_reader_staging_and_prompt_have_no_hidden_metadata(fixture):
    state = execute(fixture, Pool(), prepare_only=True)
    assert state["status"] == "prepared"
    for record in state["attempts"]:
        attempt = Path(record["attempt"])
        assert {p.name for p in attempt.iterdir()} == {
            "reader_input.json", "output_schema.json", "review_instructions.txt"}
        visible = "\n".join(p.read_text() for p in attempt.iterdir())
        visible += (fixture["root"] / "run/prompts" / (record["configuration"] + ".txt")).read_text()
        for hidden in ("HOST_METADATA", "HOST_FAMILY", "HOST_SPLIT", "HOST_PHENOMENON",
                       "SECRET_LABEL_RATIONALE", "host-only-method", str(fixture["packet"]), str(fixture["labels"])):
            assert hidden not in visible
        assert "replace artifact.json incrementally" in visible
        assert "scratch/helper JSON" in visible


@pytest.mark.parametrize("mutation", ["packet_hash", "canonical_hash", "passage", "source_bytes"])
def test_preflight_replays_exact_source_bindings(fixture, mutation):
    raw = json.loads(fixture["cases"].read_text())
    if mutation == "packet_hash":
        raw["contexts"][0]["source_packet_sha256"] = "0" * 64
    elif mutation == "canonical_hash":
        raw["contexts"][0]["source_packet_digest"] = sha(fixture["packet"])
    elif mutation == "passage":
        raw["contexts"][0]["passages"][0]["text"] += " Invented support."
    else:
        fixture["source"].write_text("Different original bytes.")
    write(fixture["cases"], raw)
    pool = Pool()
    with pytest.raises(ValueError):
        execute(fixture, pool)
    assert not pool.calls


@pytest.mark.parametrize("fault", ["missing_row", "foreign_passage", "stale_value", "scratch", "mutate_public"])
def test_closed_contract_failure_is_not_repaired(fixture, fault):
    pool = Pool(fault)
    state = execute(fixture, pool)
    assert state["status"] == "failed"
    assert state["attempts"][0]["status"] == "failed"
    assert len(pool.calls) <= 2
    report = json.loads((fixture["root"] / "run/report.json").read_text())
    first = report["configurations"]["basic_review"]["overall"]
    assert first["failure_item_count"] == 3
    assert first["label_accuracy"]["rate"] is None


def test_failed_native_keeps_failure_denominator_and_second_session(fixture):
    pool = Pool("failed_native")
    state = execute(fixture, pool)
    assert len(pool.calls) == 2
    assert state["completed_native_sessions"] == 1
    assert state["attempts"][1]["status"] == "completed"
    report = json.loads((fixture["root"] / "run/report.json").read_text())
    first = report["configurations"]["basic_review"]["overall"]
    assert first["failure_item_count"] == 3
    assert first["unsupported_acceptance"] == {"numerator": 0, "denominator": 2, "rate": None}


@pytest.mark.parametrize("fault", ["thread_reuse", "failed_thread_reuse"])
def test_reject_thread_reuse_even_after_failed_native(fixture, fault):
    state = execute(fixture, Pool(fault))
    assert state["attempts"][1]["status"] == "failed"
    assert "reuses a thread" in state["attempts"][1]["error"]


def test_neutral_is_not_contradicted_and_abstention_earns_no_accuracy(fixture):
    dataset = read_closed(fixture["cases"], Dataset)
    labels = read_closed(fixture["labels"], Labels)
    result = Result.model_validate(judgments(dataset, ("unassessed", "contradicted", "contradicted")))
    score = score_result(dataset, labels, result)["overall"]
    assert score["label_accuracy"] == {"numerator": 1, "denominator": 3, "rate": 1 / 3}
    assert score["supported_retention"]["rate"] == 0
    assert score["unassessed_item_count"] == 1
    assert score["judged_item_count"] == 2


def test_both_neutral_and_contradicted_acceptances_count(fixture):
    dataset = read_closed(fixture["cases"], Dataset)
    labels = read_closed(fixture["labels"], Labels)
    result = Result.model_validate(judgments(dataset, ("supported", "supported", "supported")))
    scores = score_result(dataset, labels, result)
    assert scores["overall"]["unsupported_acceptance"] == {"numerator": 2, "denominator": 2, "rate": 1}
    assert scores["by_family"]["HOST_FAMILY"]["case_count"] == 3
    assert scores["by_split"]["HOST_SPLIT"]["case_count"] == 3
    assert scores["by_phenomenon"]["HOST_PHENOMENON"]["case_count"] == 3


def test_later_source_mutation_withholds_earlier_score(fixture):
    state = execute(fixture, Pool("mutate_source_second", fixture["source"]))
    assert state["status"] == "failed"
    assert "integrity_error" in state
    report = json.loads((fixture["root"] / "run/report.json").read_text())
    for config in CONFIGS:
        assert report["configurations"][config]["metrics_status"] == "withheld_input_integrity"
        assert report["configurations"][config]["overall"]["label_accuracy"]["rate"] is None


def test_optional_labels_never_invent_accuracy(fixture):
    pool = Pool()
    state = asyncio.run(run_diagnostic(fixture["cases"], fixture["root"] / "run",
        prompt_directory=fixture["prompts"], worker_factory=pool.worker))
    assert state["status"] == "completed"
    report = json.loads((fixture["root"] / "run/report.json").read_text())
    assert report["labels_available"] is False
    assert report["configurations"]["basic_review"]["metrics_status"] == "labels_not_supplied"
    assert "label_accuracy" not in report["configurations"]["basic_review"]


def test_duplicate_extra_and_missing_result_cases_rejected(fixture):
    dataset = read_closed(fixture["cases"], Dataset)
    raw = judgments(dataset)
    raw["results"].append(raw["results"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_result(Result.model_validate(raw), dataset)
    raw = judgments(dataset)
    raw["results"][0]["case_id"] = "not-assigned"
    with pytest.raises(ValueError, match="every assigned case"):
        validate_result(Result.model_validate(raw), dataset)


def test_strict_json_rejects_duplicate_keys_prose_and_extra_fields(fixture):
    path = fixture["root"] / "bad.json"
    for content in ('{"format_version":"a","format_version":"b"}',
                    'Sure, here is JSON: {}',
                    '{"format_version":"protocol_grounding_result/1.0","results":[],"looks_supported":true}'):
        path.write_text(content)
        with pytest.raises(ValueError):
            read_closed(path, Result)


def test_cli_prepare_and_slurm_guard(fixture, monkeypatch, capsys):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="Slurm"):
        execute(fixture, Pool())
    assert main(["--cases", str(fixture["cases"]), "--labels", str(fixture["labels"]),
        "--output", str(fixture["root"] / "prepared"), "--prompt-directory", str(fixture["prompts"]),
        "--prepare-only"]) == 0
    assert json.loads(capsys.readouterr().out)["case_count"] == 3


def test_dataset_ids_and_labels_are_exhaustive(fixture):
    raw = json.loads(fixture["cases"].read_text())
    raw["cases"].append(raw["cases"][0])
    with pytest.raises(ValueError, match="Duplicate case IDs"):
        verify_dataset(Dataset.model_validate(raw))
    labels = json.loads(fixture["labels"].read_text())
    labels["cases"].pop()
    write(fixture["labels"], labels)
    with pytest.raises(ValueError, match="every case"):
        execute(fixture, Pool())
