import copy
import json

import pytest

from experiments.transcriptomics_revision_20261104.global_plan import (
    GlobalCandidate, GlobalReview, execute_component, load_component_run, resolve_decisions,
    validate_candidate, validate_review,
)
from experiments.transcriptomics_revision_20261104.planning import digest
from genomics_discovery.illumina.extraction import read, sha, write


def fixture():
    units, registries = [], {}
    for name, rid in (("a", "r1"), ("b", "r2"), ("c", "r3")):
        ev = {"record_id": rid, "source_id": "card:" + rid, "basis": "text", "support": "reported",
              "locator": "method description", "quote": "the depicted workflow", "finding": "Synthetic identity support"}
        registries[rid] = {"card:" + rid: {"kind": "text", "content_level": "catalogue",
                                          "title": "Example", "text": "Example describes the depicted workflow."}}
        unit = {"unit_id": name, "method_name": "Name " + name, "implementation_scope": "Implementation " + name,
                "distinction_from_other_units": "Previously reviewed scope",
                "bindings": [{"record_id": rid, "role": "primary_depiction", "selected_scope": "Scope " + name, "evidence": [ev]}]}
        units.append({"global_unit_id": name, "unit": unit, "bundle": {"unit_id": name}})
    component = {"component_id": "component_001", "prior_units": units[:2],
                 "all_unit_index": [{"global_unit_id": u["global_unit_id"]} for u in units],
                 "candidate_pairs": [{"left": "a", "right": "b"}]}
    packet = {"reviewed_units": units, "pending_source_record_ids": ["unreviewed_source"],
              "candidate_cross_batch_pairs": [{"left": "a", "right": "b"}]}
    index = {"components": [{"component_id": "component_001", "member_unit_ids": ["a", "b"]}], "unchanged_unit_ids": ["c"]}
    evidence = [u["unit"]["bindings"][0]["evidence"][0] for u in units[:2]]
    raw = {"component_sha256": digest(component), "groups": [{"id": "same_method", "member_unit_ids": ["a", "b"],
            "representative_unit_id": "a", "basis": "same_scoped_method", "reason": "Synthetic approved identity", "evidence": evidence}],
           "holds": [], "additional_alias_requests": []}
    return packet, index, component, registries, copy.deepcopy(raw), evidence


def reviewed(candidate, component, registries, evidence):
    required = validate_candidate(candidate, component, registries)
    review = GlobalReview.model_validate({"candidate_sha256": digest(candidate.model_dump(mode="json")),
        "assessments": [{"target": target, "decision": "accept", "reason": "Synthetic review", "evidence": evidence}
                        for target in required]})
    validate_review(review, candidate, component, registries)
    return review


def test_same_scope_aliases_become_one_dispatch_unit_with_all_sources():
    packet, index, component, registries, raw, evidence = fixture()
    original = copy.deepcopy(packet)
    candidate = GlobalCandidate.model_validate(raw); review = reviewed(candidate, component, registries, evidence)
    result = resolve_decisions(packet, index, {"component_001": (component, candidate, review)})
    assert len(result["admitted_units"]) == 2  # one merged method plus unchanged c
    merged = next(u for u in result["admitted_units"] if len(u["prior_unit_ids"]) == 2)
    assert merged["unit"]["implementation_scope"] == "Implementation a"
    assert {b["record_id"] for b in merged["unit"]["bindings"]} == {"r1", "r2"}
    assert sum(b["role"] == "primary_depiction" for b in merged["unit"]["bindings"]) == 1
    assert packet == original
    assert result["pending_source_record_ids"] == ["unreviewed_source"]


def test_distinct_variants_remain_distinct_even_when_nominated_together():
    packet, index, component, registries, raw, evidence = fixture()
    raw["groups"] = [{"id": x, "member_unit_ids": [x], "representative_unit_id": x, "basis": "unchanged",
                      "reason": "Distinct implementation", "evidence": evidence} for x in ("a", "b")]
    candidate = GlobalCandidate.model_validate(raw); review = reviewed(candidate, component, registries, evidence)
    result = resolve_decisions(packet, index, {"component_001": (component, candidate, review)})
    assert len(result["admitted_units"]) == 3
    assert {u["unit"]["implementation_scope"] for u in result["admitted_units"]} == {"Implementation a", "Implementation b", "Implementation c"}


@pytest.mark.parametrize("mutation,match", [
    (lambda p: p["groups"][0].update(member_unit_ids=["a"], basis="unchanged"), "Every prior implementation"),
    (lambda p: p["groups"][0].update(representative_unit_id="c"), "unavailable/nonmember"),
    (lambda p: p["groups"][0]["evidence"].pop(), "Each affected implementation"),
    (lambda p: p["groups"][0]["evidence"][0].update(quote="invented"), "absent from registered"),
])
def test_unsafe_identity_plan_controls(mutation, match):
    _, _, component, registries, raw, _ = fixture(); mutation(raw)
    with pytest.raises(ValueError, match=match): validate_candidate(GlobalCandidate.model_validate(raw), component, registries)


def test_missing_or_stale_global_review_is_rejected():
    _, _, component, registries, raw, evidence = fixture()
    candidate = GlobalCandidate.model_validate(raw); review = reviewed(candidate, component, registries, evidence)
    bad = review.model_dump(mode="json"); bad["assessments"].pop()
    with pytest.raises(ValueError, match="all prior units"):
        validate_review(GlobalReview.model_validate(bad), candidate, component, registries)
    bad = review.model_dump(mode="json"); bad["candidate_sha256"] = "changed"
    with pytest.raises(ValueError, match="stale"):
        validate_review(GlobalReview.model_validate(bad), candidate, component, registries)


def test_outside_alias_request_holds_both_sides_including_unchanged_unit():
    packet, index, component, registries, raw, evidence = fixture()
    raw["groups"] = [{"id": "b", "member_unit_ids": ["b"], "representative_unit_id": "b", "basis": "unchanged",
                      "reason": "Different method", "evidence": evidence}]
    raw["holds"] = [{"unit_id": "a", "decision": "held", "reason": "Potential outside alias", "evidence": evidence}]
    raw["additional_alias_requests"] = [{"inside_unit_id": "a", "outside_unit_id": "c", "reason": "Requires expanded source comparison"}]
    candidate = GlobalCandidate.model_validate(raw); review = reviewed(candidate, component, registries, evidence)
    result = resolve_decisions(packet, index, {"component_001": (component, candidate, review)})
    assert [u["prior_unit_ids"] for u in result["admitted_units"]] == [["b"]]
    assert set(result["held_prior_units"]) == {"a", "c"}


def test_unreviewed_component_is_held_without_repeating_other_units():
    packet, index, *_ = fixture()
    result = resolve_decisions(packet, index, {})
    assert [u["prior_unit_ids"] for u in result["admitted_units"]] == [["c"]]
    assert set(result["held_prior_units"]) == {"a", "b"}


def test_conflicting_grouped_source_scopes_are_held_instead_of_rewritten():
    packet, index, component, registries, raw, evidence = fixture()
    # A grouped source was previously assigned two different scoped variants.
    component["prior_units"][1]["unit"]["bindings"][0]["record_id"] = "r1"
    raw["component_sha256"] = digest(component)
    candidate = GlobalCandidate.model_validate(raw); review = reviewed(candidate, component, registries, evidence)
    result = resolve_decisions(packet, index, {"component_001": (component, candidate, review)})
    assert set(result["held_prior_units"]) == {"a", "b"}
    assert [u["prior_unit_ids"] for u in result["admitted_units"]] == [["c"]]


@pytest.mark.asyncio
async def test_global_native_runner_requires_slurm(tmp_path, monkeypatch):
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="Sherlock Slurm"):
        await execute_component(tmp_path / "absent", "component_001", tmp_path / "run")
    assert not (tmp_path / "run").exists()


@pytest.mark.asyncio
async def test_fake_native_global_run_reopens_with_omitted_defaults(tmp_path, monkeypatch):
    from experiments.transcriptomics_revision_20261104.test_extract_plan import archive
    from tests.illumina.test_illumina_batch_controls import png
    packet, index, component, registries, raw, evidence = fixture()
    rows = []
    for rid in ("r1", "r2"):
        directory = tmp_path / rid; directory.mkdir()
        image = directory / "diagram.png"; image.write_bytes(png())
        registries[rid]["diagram"] = {"kind": "image", "path": str(image), "sha256": sha(image)}
        write(directory / "source_registry.json", registries[rid])
        write(directory / "card.json", {"record_id": rid, "source_title": rid})
        rows.append({"record_id": rid, "source_registry_path": str(directory / "source_registry.json"),
                     "card_path": str(directory / "card.json"), "registered_sources": registries[rid]})
    component["rows"] = rows; raw["component_sha256"] = digest(component)
    candidate = GlobalCandidate.model_validate(raw)
    folder = tmp_path / "index"; folder.mkdir()
    write(folder / "component_001.json", component); write(folder / "reconciliation.json", packet)
    control = tmp_path / "control.json"; write(control, {"fixture": True})
    index.update(format_version="transcriptomics_global_review_index/1", input_pins={str(control): sha(control)},
                 reconciliation_sha256=sha(folder / "reconciliation.json"))
    index["components"][0].update(path="component_001.json", sha256=sha(folder / "component_001.json"), source_occurrences=2)
    write(folder / "index.json", index)
    monkeypatch.setenv("SLURM_JOB_ID", "test-only")
    class Worker:
        async def run(self, prompt, attempt, image_paths=()):
            context = json.loads(prompt.split("\nCONTEXT\n", 1)[1])
            document = candidate if context["role"] == "author" else reviewed(candidate, component, registries, evidence)
            return archive(attempt, prompt, image_paths, document.model_dump(mode="json", exclude_defaults=True), "global-" + attempt.name)
    output = tmp_path / "run"
    state = await execute_component(folder / "index.json", "component_001", output, worker=Worker())
    assert state["status"] == "reviewed"
    reopened = load_component_run(output, folder / "index.json", index["components"][0])
    assert reopened[1] == candidate
    assert "format_version" not in read(output / "attempts/001_author/artifact.json")
    assert "format_version" in read(output / "candidate.json")
    result = resolve_decisions(packet, index, {"component_001": reopened})
    assert len(result["admitted_units"]) == 2
