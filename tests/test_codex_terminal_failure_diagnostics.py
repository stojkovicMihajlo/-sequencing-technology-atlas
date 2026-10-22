"""Conservative archived-cause diagnostics; no model calls or acceptance changes."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from test_workflow_extraction_runtime import FakeWorker, source_case
from genomics_discovery.curation.codex_worker import (
    WorkerResult, assess_archived_attempt, classify_archived_terminal_failure,
)
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute

CAPACITY = "Selected model is at capacity. Please try a different model."
FIXTURE = Path(__file__).parent / "fixtures/codex_capacity_terminal_0_163"


def archive(path, events, *, exit_code=1, error="Synthetic transport failure"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (path / "result.json").write_text(json.dumps({"exit_code": exit_code,
        "thread_id": "diagnostic-fixture", "usage": {}, "elapsed_seconds": 1., "error": error}))
    (path / "final.txt").write_text("Synthetic final text")
    return path


def test_real_capacity_terminal_tail_has_safe_exact_locator_and_pins():
    before = {name: (FIXTURE / name).read_bytes() for name in ("events.jsonl", "result.json")}
    result = classify_archived_terminal_failure(FIXTURE)
    assert result["category"] == "provider_capacity"
    assert result["basis"] == "terminal_error_record"
    assert result["event"] == {"type": "turn.failed", "line": 2,
        "sha256": hashlib.sha256(before["events.jsonl"].splitlines(keepends=True)[1]).hexdigest(),
        "error_pointer": "/error"}
    assert result["archive_sha256"] == {name: hashlib.sha256(raw).hexdigest()
                                          for name, raw in before.items()}
    assert CAPACITY not in json.dumps(result)
    assert not assess_archived_attempt(FIXTURE).complete
    assert before == {name: (FIXTURE / name).read_bytes() for name in before}


@pytest.mark.parametrize("code", [
    "insufficient_quota", "usage_limit_reached", "rate_limit_exceeded",
    "authentication_error", "invalid_api_key", "invalid_output", "unrecognized_error",
])
def test_unobserved_terminal_codes_remain_unknown_without_invented_taxonomy(tmp_path, code):
    path = archive(tmp_path, [{"type": "turn.failed", "error": {
        "code": code, "message": "private-provider-detail-never-summarize"}}])
    diagnostic = classify_archived_terminal_failure(path)
    assert diagnostic["category"] == "unknown"
    assert "private-provider-detail-never-summarize" not in json.dumps(diagnostic)


@pytest.mark.parametrize("message", [
    "You have exceeded your quota.", "Authentication failed.", "Invalid output.",
])
def test_unobserved_terminal_phrases_are_not_assumed_provider_taxonomy(tmp_path, message):
    diagnostic = classify_archived_terminal_failure(archive(tmp_path, [
        {"type": "turn.failed", "error": {"message": message}}]))
    assert diagnostic["category"] == "unknown"
    assert message not in json.dumps(diagnostic)


@pytest.mark.parametrize("events", [
    [{"type": "error", "message": CAPACITY}, {"type": "turn.completed", "usage": {}}],
    [{"type": "item.completed", "item": {"type": "command_execution",
        "aggregated_output": CAPACITY}}, {"type": "turn.failed", "error": {"message": "unknown"}}],
    [{"type": "item.completed", "item": {"type": "agent_message", "text": CAPACITY}}],
    [{"type": "turn.failed", "error": {"message": "Quoted text: " + CAPACITY}}],
    [{"type": "turn.failed", "error": CAPACITY}],
    [{"type": "turn.failed", "error": {"code": "invalid_api_key", "message": CAPACITY}}],
    [{"type": "turn.failed", "error": {"message": CAPACITY}},
     {"type": "turn.completed", "usage": {}}],
])
def test_untrusted_nonterminal_conflicting_or_recovered_text_never_means_capacity(tmp_path, events):
    diagnostic = classify_archived_terminal_failure(archive(tmp_path, events))
    assert diagnostic["category"] == "unknown"
    assert CAPACITY not in json.dumps(diagnostic)


def test_intermediate_error_followed_by_completion_keeps_existing_acceptance(tmp_path):
    events = [{"type": "thread.started", "thread_id": "diagnostic-fixture"},
              {"type": "turn.started"}, {"type": "error", "message": CAPACITY},
              {"type": "turn.completed", "usage": {}}]
    path = archive(tmp_path, events, exit_code=0, error=None)
    assert assess_archived_attempt(path).complete
    assert classify_archived_terminal_failure(path)["category"] == "unknown"


@pytest.mark.parametrize("later_events", [
    [{"type": "turn.started"}],
    [{"type": "turn.started"}, {"type": "error", "message": CAPACITY}],
    [{"type": "turn.started"}, {"type": "turn.completed", "usage": {}}],
    [{"type": "thread.started", "thread_id": "diagnostic-fixture"}],
])
def test_later_start_invalidates_prior_terminal_cause(tmp_path, later_events):
    events = [{"type": "thread.started", "thread_id": "diagnostic-fixture"},
              {"type": "turn.started"},
              {"type": "turn.failed", "error": {"message": CAPACITY}}, *later_events]
    path = archive(tmp_path, events)
    diagnostic = classify_archived_terminal_failure(path)
    assert diagnostic["category"] == "unknown"
    assert diagnostic["event"] is None
    assert "events.jsonl" in diagnostic["archive_sha256"]
    assert not assess_archived_attempt(path).complete


def test_new_turn_failure_uses_its_own_terminal_event(tmp_path):
    events = [{"type": "thread.started", "thread_id": "diagnostic-fixture"},
              {"type": "turn.failed", "error": {"message": "unknown"}},
              {"type": "turn.started", "thread_id": "diagnostic-fixture"},
              {"type": "turn.failed", "thread_id": "diagnostic-fixture",
               "error": {"message": CAPACITY}}]
    diagnostic = classify_archived_terminal_failure(archive(tmp_path, events))
    assert diagnostic["category"] == "provider_capacity"
    assert diagnostic["event"]["line"] == 4


@pytest.mark.parametrize("events", [
    [{"type": "thread.started", "thread_id": "another-thread"}],
    [{"type": "thread.started", "thread_id": "diagnostic-fixture"},
     {"type": "turn.started", "thread_id": "another-thread"}],
    [{"type": "thread.started", "thread_id": "diagnostic-fixture"},
     {"type": "thread.started", "thread_id": "another-thread"}],
])
def test_conflicting_top_level_thread_ids_prevent_capacity_diagnosis(tmp_path, events):
    path = archive(tmp_path, [*events, {"type": "turn.failed", "error": {"message": CAPACITY}}])
    diagnostic = classify_archived_terminal_failure(path)
    assert diagnostic["category"] == "unknown"
    assert diagnostic["basis"] == "thread_identity_mismatch"
    assert diagnostic["event"] is None


def test_terminal_thread_id_disagreement_prevents_capacity_diagnosis(tmp_path):
    events = [{"type": "thread.started", "thread_id": "diagnostic-fixture"},
              {"type": "turn.failed", "thread_id": "another-thread",
               "error": {"message": CAPACITY}}]
    diagnostic = classify_archived_terminal_failure(archive(tmp_path, events))
    assert diagnostic["category"] == "unknown"
    assert diagnostic["basis"] == "thread_identity_mismatch"


@pytest.mark.parametrize("result_identity", ["absent", "null"])
def test_event_identities_must_agree_even_when_result_has_no_identity(tmp_path, result_identity):
    path = archive(tmp_path, [
        {"type": "thread.started", "thread_id": "first-thread"},
        {"type": "turn.failed", "thread_id": "second-thread", "error": {"message": CAPACITY}}])
    result = json.loads((path / "result.json").read_text())
    if result_identity == "absent":
        del result["thread_id"]
    else:
        result["thread_id"] = None
    (path / "result.json").write_text(json.dumps(result))
    diagnostic = classify_archived_terminal_failure(path)
    assert diagnostic["category"] == "unknown"
    assert diagnostic["basis"] == "thread_identity_mismatch"


@pytest.mark.parametrize("location", ["result", "start", "terminal"])
@pytest.mark.parametrize("bad_id", ["", 42, False, []])
def test_malformed_available_thread_ids_remain_unknown(tmp_path, location, bad_id):
    events = [{"type": "thread.started", "thread_id": "diagnostic-fixture"},
              {"type": "turn.failed", "error": {"message": CAPACITY}}]
    if location == "start":
        events[0]["thread_id"] = bad_id
    elif location == "terminal":
        events[1]["thread_id"] = bad_id
    path = archive(tmp_path, events)
    if location == "result":
        result = json.loads((path / "result.json").read_text())
        result["thread_id"] = bad_id
        (path / "result.json").write_text(json.dumps(result))
    assert classify_archived_terminal_failure(path)["category"] == "unknown"


def test_nested_item_thread_text_is_not_native_identity(tmp_path):
    events = [{"type": "thread.started", "thread_id": "diagnostic-fixture"},
              {"type": "item.completed", "item": {"type": "agent_message",
                  "thread_id": "model-authored-thread"}},
              {"type": "turn.failed", "error": {"message": CAPACITY}}]
    assert classify_archived_terminal_failure(archive(tmp_path, events))["category"] == "provider_capacity"


@pytest.mark.parametrize("location", ["result.json", "final.txt"])
def test_result_or_final_text_cannot_supply_a_terminal_capacity_label(tmp_path, location):
    path = archive(tmp_path, [{"type": "turn.failed", "error": {"message": "unknown"}}])
    if location == "result.json":
        data = json.loads((path / location).read_text())
        data["error"] = CAPACITY
        (path / location).write_text(json.dumps(data))
    else:
        (path / location).write_text(CAPACITY)
    assert classify_archived_terminal_failure(path)["category"] == "unknown"


@pytest.mark.parametrize("fault", ["missing", "invalid_result", "invalid_event", "oversized_event"])
def test_diagnostic_is_nonthrowing_and_unknown_for_unusable_archive(tmp_path, monkeypatch, fault):
    path = archive(tmp_path, [{"type": "turn.failed", "error": {"message": CAPACITY}}])
    if fault == "missing":
        (path / "events.jsonl").unlink()
    elif fault == "invalid_result":
        (path / "result.json").write_text("[]")
    elif fault == "invalid_event":
        with (path / "events.jsonl").open("a") as stream:
            stream.write("not-json\n")
    else:
        monkeypatch.setattr("genomics_discovery.curation.codex_worker._MAX_EVENT_BYTES", 12)
    assert classify_archived_terminal_failure(path)["category"] == "unknown"


@pytest.mark.parametrize("role,mode", [("information", "specialists"), ("integrator", "baseline")])
async def test_pipeline_surfaces_terminal_category_and_pins_without_export_or_retry(
    source_case, tmp_path, role, mode,
):
    class TerminalFailureWorker(FakeWorker):
        async def run(self, prompt, directory):
            current_role = re.search(r"ROLE:\s*(\w+)", prompt).group(1)
            result = await super().run(prompt, directory)
            if current_role == role:
                directory = Path(directory)
                events = [{"type": "thread.started", "thread_id": result.thread_id},
                          {"type": "turn.started"}, {"type": "error", "message": CAPACITY},
                          {"type": "turn.failed", "error": {"message": CAPACITY}}]
                (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
                result = WorkerResult(1, result.thread_id, {}, .01, "Synthetic process failed")
                (directory / "result.json").write_text(json.dumps(asdict(result)))
            return result

    worker = TerminalFailureWorker(source_case)
    run = tmp_path / "failed"
    state = await execute(source_case[1], run, worker=worker, mode=mode, max_revisions=1)
    assert state["status"] == "failed"
    failed = next(attempt for attempt in state["attempts"] if attempt["role"] == role)
    diagnostic = failed["terminal_failure"]
    assert diagnostic["category"] == "provider_capacity"
    assert diagnostic["event"]["line"] == 4
    assert diagnostic["archive_sha256"] == {
        name: hashlib.sha256((Path(failed["directory"]) / name).read_bytes()).hexdigest()
        for name in ("result.json", "events.jsonl")}
    assert state["terminal_failures"][0]["category"] == "provider_capacity"
    assert state["terminal_failures"][0]["role"] == role
    assert len([call for call in worker.calls if call["role"] == role]) == 1
    assert not worker.reviews
    assert not (run / "graph_candidate.json").exists()
    assert not (run / "protocol.txt").exists()
    assert CAPACITY not in json.dumps(state)


async def test_invalid_artifact_is_not_reclassified_as_a_provider_capacity_failure(source_case, tmp_path):
    run = tmp_path / "invalid_artifact"
    state = await execute(source_case[1], run, worker=FakeWorker(source_case, fault="malformed"),
                          mode="baseline", max_revisions=0)
    assert state["status"] == "failed"
    assert "terminal_failures" not in state
    assert all("terminal_failure" not in attempt for attempt in state["attempts"])
    assert not (run / "graph_candidate.json").exists()
