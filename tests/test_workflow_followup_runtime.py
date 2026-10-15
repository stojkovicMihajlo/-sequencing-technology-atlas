"""Fake transport tests fail-closed behavior; no scientific validity claims."""

import json
from dataclasses import asdict

import pytest
from test_workflow_extraction_contract import candidate as candidate
from test_workflow_extraction_contract import packet as packet
from test_workflow_followup import audit_case as audit_case

from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_followup import runtime
from genomics_discovery.curation.workflow_followup.revalidation import revalidate_completed


@pytest.fixture
def injected(audit_case, tmp_path, monkeypatch):
    workflow, review, packet, raw = audit_case
    for source in packet["sources"]:
        source.update(title="Synthetic source", role="primary_paper")
    raw["source_packet_sha256"] = digest(packet)
    packet_path = tmp_path / "packet.json"
    packet_path.write_text(json.dumps(packet))
    graph_path = tmp_path / "graph.json"
    graph_path.write_text("{\"revision_id\": \"synthetic\"}")
    record = {"packet_path": str(packet_path)}
    inputs = ({"revision_id": "synthetic"}, record, workflow, review, packet, ["temperature"])
    monkeypatch.setattr(runtime, "load_inputs", lambda *args: inputs)
    return graph_path, raw, inputs


class Worker:
    web_search = False
    network_access = False
    model = "synthetic"
    effort = "synthetic"

    def __init__(self, raw, graph_path, fault=None):
        self.raw, self.graph_path, self.fault = raw, graph_path, fault

    async def run(self, prompt, attempt):
        attempt.mkdir()
        (attempt / "prompt.txt").write_text(prompt)
        if self.fault == "quote":
            self.raw["checks"][0]["issues"][0]["evidence"][0]["quote"] = "Fabricated chemistry."
        if self.fault == "missing_gap":
            self.raw["checks"] = []
        if self.fault == "changed_input":
            self.graph_path.write_text("tampered source graph")
        (attempt / "artifact.json").write_text(json.dumps(self.raw))
        events = [{"type": "thread.started", "thread_id": "fresh-auditor"},
                  {"type": "turn.started"}]
        if self.fault != "unfinished":
            events.append({"type": "turn.completed", "usage": {"input_tokens": 1}})
        (attempt / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        (attempt / "final.txt").write_text("Synthetic record saved")
        result = WorkerResult(1 if self.fault == "exit" else 0, "fresh-auditor",
                              {"input_tokens": 1}, .01, None)
        (attempt / "result.json").write_text(json.dumps(asdict(result)))
        return result


@pytest.mark.parametrize("fault", [None, "quote", "missing_gap", "changed_input", "unfinished",
                                   "exit"])
async def test_pipeline_emits_worklist_only_after_native_and_evidence_checks(injected, tmp_path,
                                                                          fault):
    graph_path, raw, inputs = injected
    before = digest(inputs[2])
    run = tmp_path / "run"
    state = await runtime.execute(graph_path, "workflow:synthetic", run,
                                  worker=Worker(raw, graph_path, fault))
    assert state["status"] == ("followup_proposed" if fault is None else "failed")
    assert (run / "followup_queue.json").exists() is (fault is None)
    assert digest(inputs[2]) == before
    assert (run / "auditor" / "prompt.txt").exists()
    assert not (run / "graph_candidate.json").exists()


async def test_old_attempt_is_never_overwritten(injected, tmp_path):
    graph_path, raw, _ = injected
    run = tmp_path / "run"
    await runtime.execute(graph_path, "workflow:synthetic", run, worker=Worker(raw, graph_path))
    with pytest.raises(FileExistsError):
        await runtime.execute(graph_path, "workflow:synthetic", run, worker=Worker(raw, graph_path))


async def test_completed_audit_uses_frozen_code_but_active_run_checks_live_code(injected, tmp_path,
                                                                            monkeypatch):
    graph_path, raw, _ = injected
    run = tmp_path / "run"
    await runtime.execute(graph_path, "workflow:synthetic", run, worker=Worker(raw, graph_path))
    actual_sha = runtime.sha
    source = runtime.Path(runtime.__file__).resolve()
    monkeypatch.setattr(runtime, "sha", lambda p: "0" * 64 if p.resolve() == source
                        else actual_sha(p))
    runtime.check_output(run, run / "auditor" / "artifact.json")
    state = json.loads((run / "state.json").read_text())
    state["status"] = "running"
    (run / "state.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="implementation changed"):
        runtime.check_output(run, run / "auditor" / "artifact.json")


async def test_explicit_revalidation_preserves_original_failure_record(injected, tmp_path):
    graph_path, raw, _ = injected
    run = tmp_path / "run"
    state = await runtime.execute(graph_path, "workflow:synthetic", run,
                                  worker=Worker(raw, graph_path))
    # Simulate a prior deterministic checker failure with intact, completed native output.
    state.update(status="failed", error="Prior validator confused distinct locator types")
    (run / "state.json").write_text(json.dumps(state))
    original_state = (run / "state.json").read_bytes()
    report = revalidate_completed(run, tmp_path / "new_validation")
    assert report["status"] == "followup_revalidated"
    assert report["source_run_status"] == "failed"
    assert (run / "state.json").read_bytes() == original_state
    assert (tmp_path / "new_validation" / "followup_queue.json").exists()


@pytest.mark.parametrize("fault", ["unfinished", "quote", "missing_gap", "changed_input"])
async def test_revalidation_never_recovers_incomplete_or_invalid_evidence(injected, tmp_path,
                                                                        fault):
    graph_path, raw, _ = injected
    run = tmp_path / "run"
    await runtime.execute(graph_path, "workflow:synthetic", run,
                          worker=Worker(raw, graph_path, fault))
    with pytest.raises(ValueError):
        revalidate_completed(run, tmp_path / "new_validation")
    assert not (tmp_path / "new_validation" / "followup_queue.json").exists()
