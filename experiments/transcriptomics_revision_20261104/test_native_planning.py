import json
from types import SimpleNamespace

import pytest

from experiments.transcriptomics_revision_20261104 import native_planning as native
from experiments.transcriptomics_revision_20261104.planning import PlanReview, WorkPlan, digest, validate_plan
from experiments.transcriptomics_revision_20261104.test_planning import fixture


def source_fixture(tmp_path):
    census, raw, _ = fixture()
    registries = {}
    for row in census["rows"]:
        rid = row["record_id"]
        directory = tmp_path / rid; directory.mkdir()
        body = "Example method original implementation uses the same depicted sample route."
        registry = {"card:" + rid: {"kind": "text", "content_level": "catalogue", "title": "Example", "text": body}}
        card = {"record_id": rid, "source_id": "card:" + rid, "source_title": "Example", "references": []}
        native.write(directory / "source_registry.json", registry); native.write(directory / "card.json", card)
        row.update(source_registry_path=str(directory / "source_registry.json"), card_path=str(directory / "card.json"))
        row["registered_sources"] = {"card:" + rid: {"kind": "text", "content_level": "catalogue"}}
        registries[rid] = registry
    raw["census_sha256"] = digest(census)
    def convert(value):
        if isinstance(value, dict):
            if "finding" in value:
                value.update(basis="text", support="reported", quote="uses the same depicted sample route")
            for child in value.values(): convert(child)
        elif isinstance(value, list):
            for child in value: convert(child)
    convert(raw)
    census_path = tmp_path / "census.json"; native.write(census_path, census)
    return census_path, WorkPlan.model_validate(raw), registries


def test_source_support_rejects_invented_quotes_and_title_only_identity(tmp_path):
    _, plan, registries = source_fixture(tmp_path)
    native.validate_source_support(plan, registries)
    raw = plan.model_dump(mode="json")
    raw["identity_decisions"][0]["evidence"][0]["quote"] = "invented"
    with pytest.raises(ValueError, match="not present"):
        native.validate_source_support(WorkPlan.model_validate(raw), registries)
    raw = plan.model_dump(mode="json")
    raw["identity_decisions"][0]["evidence"][0]["quote"] = "Example"
    with pytest.raises(ValueError, match="not metadata/title alone"):
        native.validate_source_support(WorkPlan.model_validate(raw), registries)


def test_review_support_uses_required_sides_from_exact_plan(tmp_path):
    _, plan, registries = source_fixture(tmp_path)
    review = PlanReview.model_validate({"plan_sha256": digest(plan.model_dump(mode="json")),
        "assessments": [{"target": "unit:one_method", "decision": "accept", "reason": "Synthetic review",
                         "evidence": [e.model_dump(mode="json") for e in plan.identity_decisions[0].evidence]}]})
    native.validate_source_support(review, registries, plan)
    bad = review.model_dump(mode="json"); bad["assessments"][0]["evidence"].pop()
    with pytest.raises(ValueError, match="each side"):
        native.validate_source_support(PlanReview.model_validate(bad), registries, plan)
    registries["poster"]["card:poster"]["content_level"] = "metadata"
    with pytest.raises(ValueError, match="each side"):
        native.validate_source_support(review, registries, plan)


@pytest.mark.asyncio
async def test_no_native_planning_outside_slurm(tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="Slurm"):
        await native.execute(tmp_path / "absent", tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.asyncio
async def test_mocked_native_handoff_one_method_not_two_depiction_jobs(tmp_path, monkeypatch):
    census_path, plan, registries = source_fixture(tmp_path)
    monkeypatch.setenv("SLURM_JOB_ID", "test-only")
    calls = []
    class Worker:
        async def run(self, prompt, attempt, image_paths=()):
            calls.append(attempt.name)
            context = json.loads(prompt.split("\nCONTEXT\n", 1)[1])
            if context["role"] == "author": payload = plan.model_dump(mode="json")
            else:
                evidence = plan.identity_decisions[0].evidence
                payload = {"plan_sha256": digest(plan.model_dump(mode="json")),
                    "assessments": [{"target": t, "decision": "accept", "reason": "Synthetic transport test",
                                     "evidence": [e.model_dump(mode="json") for e in evidence]}
                                    for t in validate_plan(native.read(census_path), plan)]}
            native.write(attempt / "artifact.json", payload)
            (attempt / "prompt.txt").write_text(prompt)
            native.write(attempt / "command.json", {"argv": [], "image_inputs": []})
            return SimpleNamespace(exit_code=0, error=None, thread_id=attempt.name, usage={"total_tokens": 1})
    monkeypatch.setattr(native, "assess_archived_attempt", lambda path: SimpleNamespace(complete=True, thread_id=path.name, usage={"total_tokens": 1}))
    output = tmp_path / "run"
    bundles = await native.execute(census_path, output, worker=Worker())
    assert calls == ["001_author", "002_reviewer"]
    assert len(bundles) == 1
    registry = native.read(bundles[0]["registry_path"])
    assert set(registry) == {"web::card:web", "poster::card:poster"}
    card = native.read(bundles[0]["card_path"])
    assert card["record_id"] == "one_method"
    assert card["source_id"] == "web::card:web"
    assert native.read(output / "state.json")["status"] == "reviewed"
    assert native.read(tmp_path / "web/card.json")["record_id"] == "web"
