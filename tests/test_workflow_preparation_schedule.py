"""Preparation concurrency is a scheduling guarantee, not scientific validation."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from test_workflow_completion_runtime import CompletionWorker
from test_workflow_extraction_runtime import source_case as source_case
from test_workflow_source_obligations import SourceWorker, obligations_for
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute


PREPARATION = ("source_obligations", "chemistry", "information")


class ScheduledWorker(CompletionWorker, SourceWorker):
    """Hold each independent pass at an explicit barrier before writing its archive."""

    def __init__(self, case, *, failure=None, cancellation=None, fault=None):
        super().__init__(case)
        self.failure, self.cancellation, self.archive_fault = failure, cancellation, fault
        self.entered, self.exited = set(), set()
        self.all_started = asyncio.Event()
        self.release = {role: asyncio.Event() for role in PREPARATION}
        self.done = {role: asyncio.Event() for role in PREPARATION}
        self.integrator_entered = False

    async def run(self, prompt, directory):
        role = re.search(r"ROLE:\s*(\w+)", prompt).group(1)
        directory = Path(directory)
        if role in PREPARATION:
            self.entered.add(role)
            if self.entered == set(PREPARATION):
                self.all_started.set()
            try:
                await self.release[role].wait()
                if role == self.failure:
                    raise ValueError(f"Injected {role} preparation failure")
                if role == self.cancellation:
                    raise asyncio.CancelledError()
                result = await super().run(prompt, directory)
                if role == "source_obligations" and self.archive_fault == "invalid_source":
                    artifact = json.loads((directory / "artifact.json").read_text())
                    artifact["source_packet_sha256"] = "0" * 64
                    (directory / "artifact.json").write_text(json.dumps(artifact))
                if role == "source_obligations" and self.archive_fault == "prompt_changed":
                    (directory / "prompt.txt").write_text(prompt + "\nChanged after submission")
                if self.archive_fault == f"invalid_{role}" and role != "source_obligations":
                    artifact = json.loads((directory / "artifact.json").read_text())
                    artifact["role"] = "information" if role == "chemistry" else "chemistry"
                    (directory / "artifact.json").write_text(json.dumps(artifact))
                return result
            finally:
                self.exited.add(role)
                self.done[role].set()
        if role == "integrator":
            self.integrator_entered = True
            state = json.loads((directory.parents[1] / "state.json").read_text())
            preparation = [attempt for attempt in state["attempts"]
                           if attempt["role"] in PREPARATION]
            assert len(preparation) == 3
            assert all(attempt["status"] == "validated" for attempt in preparation)
        return await super().run(prompt, directory)


async def started_run(case, run, **worker_kwargs):
    worker = ScheduledWorker(case, **worker_kwargs)
    task = asyncio.create_task(execute(
        case[1], run, worker=worker, require_source_coverage=True,
        require_coverage=True, require_field_review=True, max_revisions=0))
    try:
        # Deadlock guard only; the assertion concerns event ordering, not latency.
        await asyncio.wait_for(worker.all_started.wait(), timeout=5)
    except BaseException:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise
    return worker, task


@pytest.mark.parametrize("last", ["source_obligations", "information"])
async def test_three_passes_start_together_and_integrator_waits_for_every_validation(
        source_case, tmp_path, last):
    run = tmp_path / "parallel"
    worker, task = await started_run(source_case, run)
    assert not worker.integrator_entered
    # FakeWorker's two-reader barrier requires releasing both readers together.
    early = [role for role in PREPARATION if role != last]
    for role in early:
        worker.release[role].set()
    if last == "source_obligations":
        for role in early:
            await asyncio.wait_for(worker.done[role].wait(), timeout=5)
    else:
        await asyncio.wait_for(worker.done["source_obligations"].wait(), timeout=5)
    assert not worker.integrator_entered
    worker.release[last].set()
    state = await asyncio.wait_for(task, timeout=5)
    assert state["status"] == "supported", state.get("error")
    assert worker.exited == set(PREPARATION)
    assert state["preparation_schedule"] == [list(PREPARATION)]
    config = json.loads((run / "config.json").read_text())
    assert config["preparation_schedule"] == [list(PREPARATION)]
    attempts = state["attempts"]
    assert [attempt["role"] for attempt in attempts] == [*PREPARATION, "integrator", "reviewer"]
    assert len({attempt["directory"] for attempt in attempts}) == len(attempts)
    assert len({attempt["thread_id"] for attempt in attempts}) == len(attempts)
    assert all(attempt["transport_complete"] for attempt in attempts)
    for attempt in attempts[:3]:
        assert attempt["inputs"] == {}
        assert not (Path(attempt["directory"]) / "source_obligations.json").exists()
        prompt = (Path(attempt["directory"]) / "stage_prompt.txt").read_text()
        assert state["artifacts"]["source_obligations"]["document_sha256"] not in prompt
    assert set(attempts[3]["inputs"]) == {"chemistry", "information"}
    assert state["artifacts"]["source_obligations"]["sha256"] == sha(
        Path(state["artifacts"]["source_obligations"]["path"]))
    for artifact in ("graph_candidate.json", "protocol.txt"):
        assert (run / artifact).is_file()
    for artifact in ("field_review", "coverage_review", "source_coverage_review"):
        assert Path(state["artifacts"][artifact]["path"]).is_file()


@pytest.mark.parametrize("role", PREPARATION)
@pytest.mark.parametrize("kind", ["failure", "cancellation"])
async def test_each_preparation_failure_or_cancellation_settles_owned_siblings(
        source_case, tmp_path, role, kind):
    run = tmp_path / "stopped"
    worker, task = await started_run(source_case, run, **{kind: role})
    worker.release[role].set()
    if kind == "cancellation":
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
    else:
        state = await asyncio.wait_for(task, timeout=5)
        assert state["status"] == "failed"
    assert worker.exited == set(PREPARATION)
    assert not worker.integrator_entered
    state = json.loads((run / "state.json").read_text())
    assert state["status"] == ("failed" if kind == "failure" else "cancelled")
    attempts = {attempt["role"]: attempt for attempt in state["attempts"]}
    assert attempts[role]["status"] == ("failed" if kind == "failure" else "cancelled")
    assert all(attempts[sibling]["status"] == "cancelled"
               for sibling in PREPARATION if sibling != role)
    assert not (run / "graph_candidate.json").exists()
    assert not (run / "protocol.txt").exists()


async def test_parent_cancellation_waits_for_all_three_preparations(source_case, tmp_path):
    run = tmp_path / "cancelled"
    worker, task = await started_run(source_case, run)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.exited == set(PREPARATION)
    state = json.loads((run / "state.json").read_text())
    assert state["status"] == "cancelled"
    assert all(attempt["status"] == "cancelled" for attempt in state["attempts"])


@pytest.mark.parametrize("fault", ["invalid_source", "prompt_changed"])
async def test_parallel_preparation_preserves_source_and_prompt_integrity_gates(
        source_case, tmp_path, fault):
    worker, task = await started_run(source_case, tmp_path / "invalid", fault=fault)
    worker.release["source_obligations"].set()
    state = await asyncio.wait_for(task, timeout=5)
    assert state["status"] == "failed"
    assert worker.exited == set(PREPARATION)
    assert not worker.integrator_entered
    expected = "packet digest" if fault == "invalid_source" else "prompt"
    assert expected in state["error"].lower()


@pytest.mark.parametrize("role", ["chemistry", "information"])
async def test_invalid_parallel_reader_cannot_reach_integrator(source_case, tmp_path, role):
    worker, task = await started_run(source_case, tmp_path / "bad_reader", fault=f"invalid_{role}")
    for release in worker.release.values():
        release.set()
    state = await asyncio.wait_for(task, timeout=5)
    assert state["status"] == "failed"
    assert "Reader role does not match" in state["error"]
    assert worker.exited == set(PREPARATION)
    assert not worker.integrator_entered


async def test_parallel_preparation_still_requires_reader_coverage(source_case, tmp_path):
    run = tmp_path / "coverage_missing"
    worker, task = await started_run(source_case, run)
    worker.coverage_fault = "missing_finding"
    for release in worker.release.values():
        release.set()
    state = await asyncio.wait_for(task, timeout=5)
    assert state["status"] == "failed"
    assert worker.integrator_entered
    assert not (run / "graph_candidate.json").exists()


async def test_supplied_obligations_retain_existing_specialist_path(source_case, tmp_path):
    frozen = tmp_path / "obligations.json"
    frozen.write_text(json.dumps(obligations_for(source_case[0])))
    frozen_sha = sha(frozen)
    run = tmp_path / "supplied"
    worker = SourceWorker(source_case)
    state = await execute(source_case[1], run, worker=worker,
                          require_source_coverage=True, source_obligations=frozen,
                          max_revisions=0)
    assert state["status"] == "supported", state.get("error")
    assert [call["role"] for call in worker.calls] == [
        "chemistry", "information", "integrator", "reviewer"]
    assert state["preparation_schedule"] == [["chemistry", "information"]]
    config = json.loads((run / "config.json").read_text())
    assert config["source_obligations_input"] == {"path": str(frozen), "sha256": frozen_sha}
    assert sha(frozen) == frozen_sha
