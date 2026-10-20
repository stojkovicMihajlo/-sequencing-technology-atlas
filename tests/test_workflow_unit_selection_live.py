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


async def test_declared_selection_cap_is_in_prompt_and_enforced(cases,tmp_path):
    state=await execute(parents_for(tmp_path),tmp_path/'capped',worker=PlannerFixture(),max_units=1)
    assert state['status']=='failed' and 'selection cap' in state['error']
    assert 'at most 1 cuts' in (tmp_path/'capped/prompt.txt').read_text()
    assert 'not a proposed number of modules' in (tmp_path/'capped/prompt.txt').read_text()


async def test_empty_selection_remains_valid_under_cap(cases,tmp_path):
    state=await execute(parents_for(tmp_path),tmp_path/'capped',worker=PlannerFixture(empty=True),max_units=2)
    assert state['status']=='completed' and state['units']==0


@pytest.mark.parametrize('cap',[0,-1,True])
async def test_invalid_selection_cap_rejected_before_run_creation(tmp_path,cap):
    with pytest.raises(ValueError,match='positive integer'):
        await execute(tmp_path/'parents',tmp_path/'run',worker=PlannerFixture(),max_units=cap)
    assert not (tmp_path/'run').exists()



def targets_for(tmp_path, **updates):
    from test_workflow_transformations import definition
    path = tmp_path / "retrieval_targets.json"
    document = {"format_version": "unit_selection_targets/1.0",
                "definitions": [definition().model_dump(mode="json")], **updates}
    path.write_text(json.dumps(document))
    return path


async def test_target_retrieval_is_frozen_and_still_creates_only_candidate_cuts(cases, tmp_path):
    targets = targets_for(tmp_path)
    state = await execute(parents_for(tmp_path), tmp_path / "targeted", worker=PlannerFixture(),
                          targets=targets)
    assert state["status"] == "completed" and state["links_created"] == 0
    directory = tmp_path / "targeted"
    config = json.loads((directory / "config.json").read_text())
    assert config["selection_purpose"] == "target_retrieval"
    assert len(config["targets_sha256"]) == 64
    assert json.loads((directory / "targets.json").read_text()) == json.loads(targets.read_text())
    prompt = (directory / "prompt.txt").read_text()
    assert "retrieve candidate occurrences" in prompt and "relevant nested region" in prompt
    assert "multiple operations" in prompt and "not evidence that a match exists" in prompt


@pytest.mark.parametrize("where", ["input", "snapshot"])
async def test_changed_target_input_or_snapshot_cannot_claim_completed_selection(cases, tmp_path, where):
    targets = targets_for(tmp_path)
    class TargetMutation(PlannerFixture):
        async def run(self, prompt, directory):
            result = await super().run(prompt, directory)
            path = targets if where == "input" else directory / "targets.json"
            path.write_text(path.read_text() + " ")
            return result
    state = await execute(parents_for(tmp_path), tmp_path / "changed-target", worker=TargetMutation(),
                          targets=targets)
    assert state["status"] == "failed" and "Frozen planner input" in state["error"]
    assert "units" not in state


@pytest.mark.parametrize("fault", ["empty", "duplicate", "prior_opinions"])
async def test_invalid_or_opinion_bearing_targets_are_rejected_before_native_calls(tmp_path, fault):
    targets = targets_for(tmp_path)
    document = json.loads(targets.read_text())
    if fault == "empty":
        document["definitions"] = []
    elif fault == "duplicate":
        document["definitions"] *= 2
    else:
        document["reviewer_opinions"] = ["must match"]
    targets.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        await execute(tmp_path / "absent-parents.json", tmp_path / "invalid-target",
                      worker=PlannerFixture(), targets=targets)
    assert not (tmp_path / "invalid-target").exists()
