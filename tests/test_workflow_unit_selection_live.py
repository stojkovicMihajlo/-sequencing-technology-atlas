"""Planner provenance and contextual-cut replay; fixture judgments are not evidence."""

import json
from dataclasses import asdict

import pytest
from test_workflow_transformations import cases as cases

from genomics_discovery.curation.codex_worker import CodexWorker, WorkerResult
from playground.workflow_unit_selection import UnitSelection, build_units, load_parents
from playground.workflow_unit_selection_live import execute


def parents_for(tmp_path):
    path = tmp_path / "parents.json"
    path.write_text(json.dumps({ident: {key: f"{ident}.{key}.json"
        for key in ("workflow", "packet", "review")} for ident in ("a", "b")}))
    return path


class PlannerFixture:
    model, effort = "fixture", "fixture"

    def __init__(self, *, fault=None, empty=False):
        self.fault, self.empty = fault, empty

    async def run(self, prompt, directory):
        selection = UnitSelection.model_validate({"cuts": [] if self.empty else [
            {"parent_id": ident, "operation_ids": ["insert", "repair"],
             "rationale": "Whole substrate-to-repaired-product transformation"}
            for ident in ("a", "b")]})
        (directory / "selection.json").write_text(selection.model_dump_json())
        build_units(selection, load_parents(directory / "parents.json"), directory / "units")
        if self.fault == "selection":
            (directory / "selection.json").write_text('{"cuts": []}')
        elif self.fault == "receipt":
            receipt = directory / "units/selection_receipt.json"
            record = json.loads(receipt.read_text())
            record["cuts"][0]["rationale"] = "Different rationale"
            receipt.write_text(json.dumps(record))
        elif self.fault == "parent":
            parents = load_parents(directory / "parents.json")
            path = parents["a"].workflow
            path.write_text(path.read_text() + " ")
        (directory / "prompt.txt").write_text(prompt if self.fault != "prompt" else "Other task")
        usage = {"input_tokens": 20, "output_tokens": 5}
        events = [{"type": "thread.started", "thread_id": "planner-fixture"},
                  {"type": "turn.started"}]
        if self.fault != "incomplete":
            events.append({"type": "turn.completed", "usage": usage})
        (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        result = WorkerResult(0, "planner-fixture", usage, .01, None)
        (directory / "result.json").write_text(json.dumps(asdict(result)))
        (directory / "final.txt").write_text("Saved selection and zero-link contextual cuts")
        return result


async def test_planner_replays_context_without_establishing_links(cases, tmp_path):
    state = await execute(parents_for(tmp_path), tmp_path / "run", worker=PlannerFixture())
    assert state["status"] == "completed" and state["units"] == 2
    assert state["links_created"] == 0 and state["thread_id"] == "planner-fixture"
    receipt = json.loads((tmp_path / "run/units/selection_receipt.json").read_text())
    for cut in receipt["cuts"]:
        assert set(cut["operation_ids"]) == {"insert", "repair"}
    assert "No network" in (tmp_path / "run/prompt.txt").read_text()


async def test_empty_selection_is_valid_and_creates_no_relations(cases, tmp_path):
    state = await execute(
        parents_for(tmp_path), tmp_path / "run", worker=PlannerFixture(empty=True))
    assert state["status"] == "completed" and state["units"] == state["links_created"] == 0


@pytest.mark.parametrize("fault", ["selection", "receipt", "parent", "prompt", "incomplete"])
async def test_bad_native_or_changed_inputs_never_claim_completed_units(cases, tmp_path, fault):
    state = await execute(
        parents_for(tmp_path), tmp_path / "run", worker=PlannerFixture(fault=fault))
    assert state["status"] == "failed" and "units" not in state
    assert "result.json" in state["archives"]


async def test_planner_rejects_network_worker_before_run_creation(tmp_path):
    with pytest.raises(ValueError, match="network access disabled"):
        await execute(tmp_path / "parents", tmp_path / "run",
                      worker=CodexWorker(web_search=True, network_access=True))
    assert not (tmp_path / "run").exists()
