import asyncio
import copy
import json

import pytest

from experiments.transcriptomics_revision_20261104.batching import (
    execute_batches, load_batches, partition, reconcile, write_batches,
)
from experiments.transcriptomics_revision_20261104.planning import digest
from genomics_discovery.illumina.extraction import read, sha, write


def census_fixture():
    return {"rows": [{"record_id": x, "source_method_labels": [x],
                       "nomination_reasons": ["RNA context"] if x == "a" else []}
                      for x in ("a", "b", "c", "d", "e")],
            "candidate_identity_pairs": [{"left": "a", "right": "b"}, {"left": "b", "right": "c"}]}


def test_identity_components_stay_whole_and_scope_is_explicit():
    census = census_fixture()
    result = partition(census, ["a"], max_occurrences=2)
    assert result["selected_record_ids"] == ["a", "b", "c"]
    assert result["identity_neighbor_additions"] == ["b", "c"]
    assert result["pending_scope_record_ids"] == ["d", "e"]
    assert len(result["batches"]) == 1
    assert result["batches"][0]["oversized"]
    assert result["batches"][0]["status"] == "held_oversized_component"
    with pytest.raises(ValueError, match="Explicit existing"):
        partition(census, [])


def test_write_and_load_reject_changed_coverage(tmp_path):
    census = census_fixture(); source = tmp_path / "source.json"; write(source, census)
    folder = tmp_path / "batches"
    manifest = write_batches(source, folder, ["a", "d", "e"], max_occurrences=3)
    assert len(manifest["batches"]) == 2
    load_batches(folder / "manifest.json")
    manifest["batches"][1]["record_ids"].append("a")
    write(folder / "manifest.json", manifest)
    with pytest.raises(ValueError, match="cuts/repeats"):
        load_batches(folder / "manifest.json")


@pytest.mark.asyncio
async def test_parallel_planning_requires_chosen_batches_and_preserves_holds(tmp_path, monkeypatch):
    source = tmp_path / "source.json"; write(source, census_fixture())
    folder = tmp_path / "batches"
    manifest = write_batches(source, folder, ["d", "e"], max_occurrences=1)
    monkeypatch.setenv("SLURM_JOB_ID", "test-only")
    calls, active, maximum = [], 0, 0
    async def mock_executor(census_path, output):
        nonlocal active, maximum
        calls.append(census_path.name); active += 1; maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return None if census_path.name == "batch_001.json" else []
    state = await execute_batches(folder / "manifest.json", tmp_path / "run", ["batch_001", "batch_002"],
                                  workers=2, executor=mock_executor)
    assert maximum == 2
    assert state["batches"]["batch_001"]["status"] == "held_after_review"
    assert state["batches"]["batch_002"]["status"] == "reviewed"
    assert len(calls) == 2
    with pytest.raises(ValueError, match="Explicit, unique"):
        await execute_batches(folder / "manifest.json", tmp_path / "bad", [], executor=mock_executor)


@pytest.mark.asyncio
async def test_oversized_component_cannot_be_silently_dispatched(tmp_path, monkeypatch):
    source = tmp_path / "source.json"; write(source, census_fixture())
    folder = tmp_path / "batches"; write_batches(source, folder, ["a"], max_occurrences=2)
    monkeypatch.setenv("SLURM_JOB_ID", "test-only")
    with pytest.raises(ValueError, match="Oversized identity component"):
        await execute_batches(folder / "manifest.json", tmp_path / "run", ["batch_001"])


def test_reconciliation_flags_alias_collision_without_authorizing_jobs(tmp_path):
    source = tmp_path / "source.json"; write(source, census_fixture())
    folder = tmp_path / "batches"; manifest = write_batches(source, folder, ["d", "e"], max_occurrences=1)
    runs, prepared = {}, {}
    for batch in manifest["batches"]:
        directory = tmp_path / batch["batch_id"]; (directory / "inputs").mkdir(parents=True)
        subset = read(folder / batch["census_file"]); write(directory / "inputs/census.json", subset)
        rid = batch["record_ids"][0]
        unit = {"unit_id": "same_local_id", "method_name": "Alias resolved by separate planners", "implementation_scope": "Original implementation",
                "bindings": [{"record_id": rid, "role": "primary_depiction", "selected_scope": "Original"}]}
        prepared[str(directory)] = ({"census_sha256": digest(subset), "units": [unit]}, [{"unit_id": "same_local_id"}])
        runs[batch["batch_id"]] = {"status": "reviewed", "planning_run": str(directory)}
    state = tmp_path / "run_state.json"
    write(state, {"format_version": "transcriptomics_planning_batch_run/1", "manifest_sha256": sha(folder / "manifest.json"), "batches": runs})
    result = reconcile(folder / "manifest.json", state, plan_loader=lambda p: prepared[str(p)])
    assert result["status"] == "global_identity_review_required"
    assert result["extraction_authorized"] is False
    assert len(result["candidate_cross_batch_pairs"]) == 1
    assert len({u["global_unit_id"] for u in result["reviewed_units"]}) == 2
    assert result["pending_source_record_ids"] == ["a", "b", "c"]
    assert "same_recorded_name_and_scope; do_not_schedule_twice" in result["candidate_cross_batch_pairs"][0]["reasons"]
