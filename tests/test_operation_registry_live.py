"""Exercise the native-archive gate without claiming fixture labels are real science."""

import json
from dataclasses import asdict

import pytest
from test_workflow_transformations import cases as cases
from test_operation_registry import assignment, review, decision_for, admission_for

from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.operation_registry.models import Assignment
from genomics_discovery.curation.operation_registry.admission import VocabularyProposal, VocabularyReview
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.operation_registry.store import Registry
from playground.workflow_concepts_live import execute


class WorkerFixture:
    model, effort = "fixture", "fixture"

    def __init__(self, cases, snapshot, fault=None, verdict="supported"):
        self.cases, self.snapshot = cases, snapshot
        self.fault, self.verdict, self.calls = fault, verdict, 0

    async def run(self, prompt, path):
        self.calls += 1
        if self.calls == 1:
            proposal = assignment(self.cases, self.snapshot)
            decision = decision_for(proposal)
            if self.fault == "abstain":
                decision = decision.model_copy(update={"assignment": None})
            document = VocabularyProposal(decisions=(decision,)).model_dump(mode="json")
        else:
            batch = VocabularyProposal.model_validate_json((path.parent / "proposal.json").read_bytes())
            decision = batch.decisions[0]
            source_review = review(decision.assignment, self.cases, self.verdict) if decision.assignment else None
            checked = admission_for(decision, source_review,
                                    "admit" if decision.assignment else "provisional")
            document = VocabularyReview(proposal_sha256=digest(batch), reviews=(checked,)).model_dump(mode="json")
        (path / "artifact.json").write_text(json.dumps(document))
        (path / "prompt.txt").write_text(prompt if self.fault != "prompt" else "Wrong prompt")
        thread = "same" if self.fault == "thread" else f"thread-{self.calls}"
        usage = {"input_tokens": 10, "output_tokens": 10}
        events = [{"type": "thread.started", "thread_id": thread}, {"type": "turn.started"}]
        if self.fault != "incomplete":
            events.append({"type": "turn.completed", "usage": usage})
        (path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        result = WorkerResult(0, thread, usage, .01, None)
        (path / "result.json").write_text(json.dumps(asdict(result)))
        for name in ("command.json", "started.json"):
            (path / name).write_text("{}")
        (path / "stderr.log").write_text("")
        (path / "final.txt").write_text("Saved artifact")
        return result


def prepare(cases, tmp_path):
    ident, occurrence = next(iter(cases[0].items()))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({ident: {k: str(tmp_path / f"{occurrence.method_id}.{k}.json")
        for k in ("workflow", "packet", "review", "occurrence")}}))
    registry = Registry(tmp_path / "labels.sqlite")
    registry.initialize()
    return manifest, registry


async def test_two_fresh_threads_commit_and_withhold_old_reviews(cases, tmp_path):
    manifest, registry = prepare(cases, tmp_path)
    result = await execute(manifest, registry.path, tmp_path / "run",
                           worker=WorkerFixture(cases, registry.snapshot()), curate=True)
    assert result["status"] == "committed"
    assert len({a["thread_id"] for a in result["attempts"]}) == 2
    row = next(iter(registry.snapshot()["assignments"].values()))
    assert row["review_execution"]["independent_execution_verified"]
    visible = next(iter(json.loads((tmp_path / "run/selections.json").read_text()).values()))
    assert "selected_checks" not in visible["record"]
    catalog = json.loads((tmp_path / "run/registry.json").read_text())
    assert "assignments" not in catalog
    from genomics_discovery.curation.run_state import sha
    bindings = json.loads((tmp_path / "run/bindings.json").read_text())
    assert bindings[str(manifest)] == sha(manifest)
    assert bindings[str(tmp_path / "run/002_reviewer/artifact.json")] == sha(
        tmp_path / "run/002_reviewer/artifact.json")


@pytest.mark.parametrize("fault", ["thread", "incomplete", "prompt"])
async def test_transport_faults_cannot_assign(cases, tmp_path, fault):
    manifest, registry = prepare(cases, tmp_path)
    with pytest.raises(ValueError):
        await execute(manifest, registry.path, tmp_path / "run",
                      worker=WorkerFixture(cases, registry.snapshot(), fault=fault), curate=True)
    assert not registry.snapshot()["assignments"]
    assert json.loads((tmp_path / "run/state.json").read_text())["status"] == "failed"


@pytest.mark.parametrize("verdict", ["unsupported", "unresolved"])
async def test_scientific_negatives_recorded_without_commit(cases, tmp_path, verdict):
    manifest, registry = prepare(cases, tmp_path)
    result = await execute(manifest, registry.path, tmp_path / "run",
                           worker=WorkerFixture(cases, registry.snapshot(), verdict=verdict), curate=True)
    assert result["status"] == "completed_without_admission"
    assert result["commit"]["outcomes"][0]["status"] in {"needs_revision", "research_needed"}
    assert not registry.snapshot()["concepts"]


async def test_unclassified_selection_is_reviewed_and_retained(cases, tmp_path):
    manifest, registry = prepare(cases, tmp_path)
    worker = WorkerFixture(cases, registry.snapshot(), fault="abstain")
    result = await execute(manifest, registry.path, tmp_path / "run", worker=worker, curate=True)
    assert result["status"] == "completed_without_admission" and worker.calls == 2
    assert not registry.snapshot()["concepts"]
    assert len(registry.snapshot()["decisions"]) == 1


async def test_supplied_proposal_has_one_fresh_review_and_explicit_provenance(cases, tmp_path):
    manifest, registry = prepare(cases, tmp_path)
    decision = decision_for(assignment(cases, registry.snapshot()))
    proposal = tmp_path / 'supplied.json'
    proposal.write_text(VocabularyProposal(decisions=(decision,)).model_dump_json())
    worker = WorkerFixture(cases, registry.snapshot())
    worker.calls = 1  # Exercise the fixture's reviewer branch; there is no compiler execution.
    result = await execute(manifest, registry.path, tmp_path / 'run', worker=worker,
                           supplied_proposal=proposal, curate=True)
    assert result['proposal_origin'] == 'supplied' and len(result['attempts']) == 1
    assert result['attempts'][0]['role'] == 'reviewer'
    provenance = next(iter(registry.snapshot()['assignments'].values()))['review_execution']
    assert provenance['kind'] == 'supplied_proposal_native_review'
    assert not provenance['independent_execution_verified']
    assert provenance['fresh_review_execution_verified']
