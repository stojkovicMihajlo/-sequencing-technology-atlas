"""Test comparison provenance and support gates; fixtures are not scientific evidence."""

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from test_workflow_transformations import candidate_for, review_for
from test_workflow_transformations import cases as cases

from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.workflow_extraction.models import WorkflowReview, digest
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipCandidate,
    cut_transformation,
)
from playground import workflow_transformations_live as driver
from playground.workflow_transformations_live import comparison_graph, execute
from playground.workflow_unit_review_inputs import validate_review_view


def test_documented_module_entrypoint_loads_without_pytest_path_injection():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "playground.workflow_transformations_live", "--help"],
        cwd=project, env={**os.environ, "PYTHONPATH": str(project / "src"),
                          "PYTHONDONTWRITEBYTECODE": "1"},
        text=True, capture_output=True, check=True,
    )
    assert "--proposal" in result.stdout and "--effort" in result.stdout


class NativeFixture:
    model, effort = "fixture", "fixture"

    def __init__(self, cases, fault=None, verdict="supported"):
        self.cases, self.fault, self.verdict = cases, fault, verdict
        self.calls = 0
        self.prompts = []

    async def run(self, prompt, directory):
        self.calls += 1
        self.prompts.append(prompt)
        compiler = "ROLE: transformation_compiler" in prompt
        candidate = (candidate_for(self.cases) if compiler else
                     MembershipCandidate.model_validate_json(
                         (directory / "candidate.json").read_bytes()))
        review_cases = ({member.occurrence_id: self.cases[0][member.occurrence_id]
                         for member in candidate.members}, self.cases[1], self.cases[2])
        if self.fault == "extra_checks":
            review_cases = self.cases
        document = ({"candidates": [candidate.model_dump(mode="json")], "notes": []}
                    if compiler else
                    review_for(candidate, review_cases, self.verdict).model_dump(mode="json"))
        (directory / "artifact.json").write_text("{" if self.fault == "malformed"
                                                else json.dumps(document))
        (directory / "prompt.txt").write_text(prompt if self.fault != "prompt" else "Other task")
        thread = "reused" if self.fault == "thread" else f"thread-{self.calls}"
        if self.fault == "missing_thread" and self.calls == 2:
            thread = None
        usage = {"input_tokens": 17, "output_tokens": 4}
        events = ([{"type": "thread.started", "thread_id": thread}] if thread else [])
        events.append({"type": "turn.started"})
        if self.fault != "incomplete":
            events.append({"type": "turn.completed", "usage": usage})
        (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        failed = self.fault in {"timeout", "raised"}
        result = WorkerResult(124 if failed else 0, thread, usage, 2.5 if failed else .01,
                              "Fixture transport failure" if failed else None)
        (directory / "result.json").write_text(json.dumps(asdict(result)))
        for name in ("command.json", "started.json"):
            (directory / name).write_text("{}")
        (directory / "stderr.log").write_text("")
        (directory / "final.txt").write_text("Saved artifact")
        if self.fault == "raised":
            raise RuntimeError("Fixture raised after archiving transport outcome")
        return result


def manifest_for(cases, tmp_path):
    rows = {ident: {key: str(tmp_path / f"{occurrence.method_id}.{key}.json")
                    for key in ("occurrence", "packet", "workflow", "review")}
            for ident, occurrence in cases[0].items()}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(rows))
    return path


def proposal_for(cases, tmp_path, candidates=None):
    path = tmp_path / "proposal.json"
    candidates = candidates if candidates is not None else [candidate_for(cases)]
    path.write_text(json.dumps({"candidates": [candidate.model_dump(mode="json")
                                              for candidate in candidates],
                                "notes": ["Unaccepted proposal input."]}))
    return path


async def test_native_comparison_keeps_parent_status_and_all_interfaces(cases, tmp_path):
    state = await execute(manifest_for(cases, tmp_path), tmp_path / "run",
                          worker=NativeFixture(cases))
    assert state["status"] == "completed"
    graph = json.loads((tmp_path / "run/graph_candidate.json").read_text())
    assert [a["role"] for a in state["attempts"]] == ["compiler", "reviewer"]
    assert all(a["status"] == "validated" for a in state["attempts"])
    assert len({a["thread_id"] for a in state["attempts"]}) == 2
    assert {node["review_status"] for node in graph["nodes"]
            if node["kind"] == "workflow"} == {"needs_revision"}
    assert len([e for e in graph["edges"] if e["kind"] == "shared_mechanism"]) == 2
    assert all(node["record"]["requirements"] for node in graph["nodes"]
               if node["kind"] == "transformation_occurrence")
    prompt = (tmp_path / "run/002_reviewer/prompt.txt").read_text()
    inputs = json.loads(next(line.removeprefix("Inputs: ") for line in prompt.splitlines()
                             if line.startswith("Inputs: ")))
    for ident, row in inputs.items():
        view = json.loads(Path(row["occurrence"]).read_text())
        validate_review_view(view, cases[0][ident])
        assert "selected_checks" not in view["record"]
    config = json.loads((tmp_path / "run/config.json").read_text())
    assert any(path.endswith("workflow_unit_review_inputs.py") for path in config["files"])


async def test_seeded_proposal_uses_only_fresh_reviews_and_pins_declared_unaccepted_input(
    cases, tmp_path,
):
    seed = proposal_for(cases, tmp_path)
    worker = NativeFixture(cases)
    run = tmp_path / "seeded"
    state = await execute(manifest_for(cases, tmp_path), run, worker=worker, initial_proposal=seed)
    assert state["status"] == "completed" and worker.calls == 1
    assert [attempt["role"] for attempt in state["attempts"]] == ["reviewer"]
    config = json.loads((run / "config.json").read_text())
    metadata = config["initial_proposal"]
    assert metadata["role"] == "unaccepted_proposal"
    assert metadata["prior_transport_verified"] is metadata["prior_review_inherited"] is False
    assert metadata["sha256"] == config["files"][str(seed)]
    assert metadata["snapshot_sha256"] == config["files"][str(run / "initial_proposal.json")]
    assert (run / "initial_proposal.json").read_bytes() == seed.read_bytes()


@pytest.mark.parametrize("extra_checks", [False, True])
async def test_each_review_only_receives_candidate_member_ids_schema_and_selfcheck(
    cases, tmp_path, extra_checks,
):
    occurrences, packets, workflows = cases
    original_ids = list(occurrences)
    extra = {}
    manifest = json.loads(manifest_for(cases, tmp_path).read_text())
    for ident, workflow in zip(original_ids, workflows, strict=True):
        occurrence = cut_transformation(workflow, packets[ident], ["insert"])
        path = tmp_path / f"{workflow.method_id}.single.json"
        path.write_text(occurrence.model_dump_json())
        manifest[occurrence.id] = {**manifest[ident], "occurrence": str(path), "review": None}
        extra[occurrence.id] = occurrence
        packets[occurrence.id] = packets[ident]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    extended = ({**occurrences, **extra}, packets, workflows)
    pairs = [(occurrences, packets, workflows), (extra, packets, workflows)]
    candidates = [candidate_for(pair) for pair in pairs]
    worker = NativeFixture(extended, fault="extra_checks" if extra_checks else None)
    seed = proposal_for(extended, tmp_path, candidates)
    run = tmp_path / "scoped"
    state = await execute(manifest_path, run, worker=worker, initial_proposal=seed)
    if extra_checks:
        assert state["status"] == "failed" and worker.calls == 1
        assert "exactly one check" in state["error"]
        attempt = state["attempts"][0]
        assert attempt["status"] == "failed" and "events.jsonl" in attempt["archive_sha256"]
        assert len(json.loads((Path(attempt["path"]) / "artifact.json").read_text())["checks"]) == 4
        assert not (run / "graph_candidate.json").exists()
        return
    assert state["status"] == "completed" and worker.calls == 2
    for prompt, attempt, candidate in zip(
        worker.prompts, state["attempts"], candidates, strict=True,
    ):
        inputs = json.loads(next(line.removeprefix("Inputs: ") for line in prompt.splitlines()
                                 if line.startswith("Inputs: ")))
        ids = [member.occurrence_id for member in candidate.members]
        assert set(inputs) == set(ids)
        schema = json.loads((Path(attempt["path"]) / "schema.json").read_text())
        assert schema["properties"]["checks"]["minItems"] == schema[
            "properties"]["checks"]["maxItems"] == 2
        assert schema["$defs"]["MemberCheck"]["properties"]["occurrence_id"]["enum"] == ids
        assert ".workflow_extraction.transformations check " in prompt
        assert str(run / "manifest.json") in prompt


@pytest.mark.parametrize("fault", ["timeout", "incomplete"])
async def test_seeded_review_failure_retains_fresh_transport_archive_without_compiler_claim(
    cases, tmp_path, fault,
):
    run = tmp_path / "seed_failure"
    state = await execute(manifest_for(cases, tmp_path), run,
                          worker=NativeFixture(cases, fault=fault),
                          initial_proposal=proposal_for(cases, tmp_path))
    assert state["status"] == "failed" and not (run / "graph_candidate.json").exists()
    assert len(state["attempts"]) == 1 and state["attempts"][0]["role"] == "reviewer"
    attempt = state["attempts"][0]
    assert attempt["status"] == "failed" and "result.json" in attempt["archive_sha256"]
    assert attempt["elapsed_seconds"] == (2.5 if fault == "timeout" else .01)
    assert attempt["usage"] == {"input_tokens": 17, "output_tokens": 4}


async def test_changed_seeded_proposal_and_invalid_member_binding_fail_without_graph(
    cases, tmp_path,
):
    seed = proposal_for(cases, tmp_path)
    worker = NativeFixture(cases)
    original_run = worker.run

    async def changed_seed(prompt, directory):
        result = await original_run(prompt, directory)
        seed.write_text("{}")
        return result

    worker.run = changed_seed
    state = await execute(manifest_for(cases, tmp_path), tmp_path / "changed_seed", worker=worker,
                          initial_proposal=seed)
    assert state["status"] == "failed" and "Frozen comparison input" in state["error"]
    assert state["attempts"][0]["status"] == "failed"
    candidate = candidate_for(cases).model_dump(mode="json")
    candidate["members"][0]["occurrence_sha256"] = "0" * 64
    seed.write_text(json.dumps({"candidates": [candidate], "notes": []}))
    worker = NativeFixture(cases)
    state = await execute(manifest_for(cases, tmp_path), tmp_path / "invalid_seed", worker=worker,
                          initial_proposal=seed)
    assert state["status"] == "failed" and worker.calls == 0 and state["attempts"] == []


async def test_rejected_membership_remains_unlinked_and_cannot_promote_workflows(cases, tmp_path):
    state = await execute(manifest_for(cases, tmp_path), tmp_path / "run",
                          worker=NativeFixture(cases, verdict="unsupported"))
    assert state["status"] == "completed"
    assert state["comparisons"][0]["status"] == "needs_revision"
    graph = json.loads((tmp_path / "run/graph_candidate.json").read_text())
    assert {e["kind"] for e in graph["edges"]} == {"contains_unit"}
    assert len([n for n in graph["nodes"] if n["kind"] == "transformation_occurrence"]) == 2


@pytest.mark.parametrize("fault", ["prompt", "thread", "incomplete", "missing_thread"])
async def test_bad_native_evidence_never_exports_comparison_edges(cases, tmp_path, fault):
    state = await execute(manifest_for(cases, tmp_path), tmp_path / "run",
                          worker=NativeFixture(cases, fault=fault))
    assert state["status"] == "failed"
    assert not (tmp_path / "run/graph_candidate.json").exists()


async def test_changed_parent_fails_before_any_native_call(cases, tmp_path):
    path = manifest_for(cases, tmp_path)
    parent = tmp_path / "a.workflow.json"
    record = json.loads(parent.read_text())
    record["operations"][0]["action"] = "Invent a different transformation."
    parent.write_text(json.dumps(record))
    worker = NativeFixture(cases)
    state = await execute(path, tmp_path / "run", worker=worker)
    assert state["status"] == "failed" and state["attempts"] == []
    assert json.loads((tmp_path / "run/state.json").read_text())["status"] == "failed"
    assert worker.calls == 0


@pytest.mark.parametrize("explicit_null", [False, True])
async def test_unreviewed_parent_accepts_omitted_or_null_review(cases, tmp_path, explicit_null):
    occurrences, packets, workflows = cases
    unreviewed = {}
    for occurrence, workflow in zip(occurrences.values(), workflows, strict=True):
        rebuilt = cut_transformation(workflow, packets[occurrence.id],
                                     [operation.id for operation in occurrence.operations])
        unreviewed[rebuilt.id] = rebuilt
        (tmp_path / f"{rebuilt.method_id}.occurrence.json").write_text(rebuilt.model_dump_json())
    updated = (unreviewed, packets, workflows)
    manifest_path = manifest_for(updated, tmp_path)
    manifest = json.loads(manifest_path.read_text())
    for row in manifest.values():
        if explicit_null:
            row["review"] = None
        else:
            row.pop("review")
    manifest_path.write_text(json.dumps(manifest))
    state = await execute(manifest_path, tmp_path / "unreviewed", worker=NativeFixture(updated))
    assert state["status"] == "completed"
    graph = json.loads((tmp_path / "unreviewed/graph_candidate.json").read_text())
    assert {node["review_status"] for node in graph["nodes"]
            if node["kind"] == "workflow"} == {"not_reviewed"}


@pytest.mark.parametrize("fault", ["timeout", "raised", "malformed"])
async def test_failed_attempt_keeps_owned_archive_duration_and_usage(cases, tmp_path, fault):
    state = await execute(manifest_for(cases, tmp_path), tmp_path / "failed",
                          worker=NativeFixture(cases, fault=fault))
    assert state["status"] == "failed"
    record = state["attempts"][-1]
    assert record["status"] == "failed" and record["finished_at"]
    assert record["elapsed_seconds"] == (2.5 if fault != "malformed" else .01)
    assert record["usage"] == {"input_tokens": 17, "output_tokens": 4}
    assert "result.json" in record["archive_sha256"]
    assert not (tmp_path / "failed/graph_candidate.json").exists()


async def test_cancellation_finalizes_attempt_after_worker_archives(cases, tmp_path):
    ready = asyncio.Event()
    worker = NativeFixture(cases)
    original_run = worker.run

    async def cancellable(prompt, directory):
        result = await original_run(prompt, directory)
        ready.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            metadata = asdict(result)
            metadata.update(exit_code=-15, elapsed_seconds=.25, error="Fixture cancelled")
            (directory / "result.json").write_text(json.dumps(metadata))
            raise

    worker.run = cancellable
    run = tmp_path / "cancelled"
    task = asyncio.create_task(execute(manifest_for(cases, tmp_path), run, worker=worker))
    await asyncio.wait_for(ready.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    state = json.loads((run / "state.json").read_text())
    record = state["attempts"][-1]
    assert state["status"] == record["status"] == "cancelled"
    assert record["elapsed_seconds"] == .25 and record["exit_code"] == -15
    assert record["usage"] == {"input_tokens": 17, "output_tokens": 4}
    assert record["finished_at"] and "result.json" in record["archive_sha256"]


@pytest.mark.parametrize("dependency", ["document_manifest.py", "artifacts.py"])
async def test_direct_dependency_pins_detect_change_without_editing_live_package(
    cases, tmp_path, monkeypatch, dependency,
):
    worker = NativeFixture(cases)
    original_run, original_sha = worker.run, driver.sha
    changed = False

    def changed_sha(path):
        return "0" * 64 if changed and Path(path).name == dependency else original_sha(path)

    async def trigger_change(prompt, directory):
        nonlocal changed
        result = await original_run(prompt, directory)
        changed = True
        return result

    monkeypatch.setattr(driver, "sha", changed_sha)
    worker.run = trigger_change
    state = await execute(manifest_for(cases, tmp_path), tmp_path / "pins", worker=worker)
    assert state["status"] == "failed" and "implementation changed" in state["error"]
    assert worker.calls == 1
    config = json.loads((tmp_path / "pins/config.json").read_text())
    assert any(Path(path).name == dependency for path in config["files"])


def test_comparison_overlay_does_not_force_connections(cases):
    graph = comparison_graph(cases[0], [])
    assert {e["kind"] for e in graph["edges"]} == {"contains_unit"}
    assert len(graph["nodes"]) == 4
    assert not any(n["kind"] == "transformation_definition" for n in graph["nodes"])


def test_parent_review_contexts_are_lossless_and_independent_of_occurrence_order(cases, tmp_path):
    occurrences, packets, workflows = cases
    initial = next(iter(occurrences.values()))
    packet = packets[initial.id]
    workflow = workflows[0]
    review_raw = json.loads((tmp_path / f"{initial.method_id}.review.json").read_text())
    review_raw["omissions"] = []
    supported_review = WorkflowReview.model_validate(review_raw)
    supported_cut = cut_transformation(workflow, packet, ["insert"], review=supported_review)
    assert initial.workflow_sha256 == supported_cut.workflow_sha256
    assert initial.workflow_review_status == "needs_revision"
    assert supported_cut.workflow_review_status == "supported"
    pair = {initial.id: initial, supported_cut.id: supported_cut}
    forward = comparison_graph(pair, [])
    reverse = comparison_graph(dict(reversed(list(pair.items()))), [])
    assert forward == reverse
    parent = next(node for node in forward["nodes"] if node["kind"] == "workflow")
    assert parent["review_status"] == "multiple_review_contexts"
    expected = [
        {"review_sha256": initial.workflow_review_sha256, "status": "needs_revision"},
        {"review_sha256": digest(supported_review), "status": "supported"},
    ]
    assert parent["review_bindings"] == sorted(
        expected, key=lambda item: (item["review_sha256"] or "", item["status"]))
    records = {node["id"]: node["record"] for node in forward["nodes"]
               if node["kind"] == "transformation_occurrence"}
    assert records[initial.id]["workflow_review_sha256"] == initial.workflow_review_sha256
    assert records[supported_cut.id]["workflow_review_sha256"] == digest(supported_review)
