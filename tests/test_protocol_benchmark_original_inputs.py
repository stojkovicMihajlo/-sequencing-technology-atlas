"""Optional original-source inputs use fixture bytes and fake native archives only."""

import json
import re
import subprocess
import pytest

from test_comparison_profiles import example as example
from test_protocol_benchmark import records as records
from test_protocol_reference_batch import FixturePool, manifest_for

from genomics_discovery.curation.protocol_benchmark.runtime import run_batch
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.source_originals import bind_originals


def bind_fixture(records, tmp_path, *, other_packet=False):
    manifest = manifest_for(records, tmp_path)
    packet = tmp_path / "packet.json"
    if other_packet:
        packet = tmp_path / "different-packet.json"
        packet.write_bytes((tmp_path / "packet.json").read_bytes())
    original = tmp_path / "original document.pdf"
    original.write_bytes(b"%PDF-1.4\nFixture bytes; never parsed or rendered in this test.\n")
    ledger = tmp_path / "provenance.json"
    source = records[1]["sources"][0]
    ledger.write_text(json.dumps([{"source_id": source["source_id"],
        "rendition": source["original_path"], "rendition_sha256": source["original_sha256"],
        "original": str(original), "original_sha256": sha(original)}]))
    bindings = tmp_path / "original bindings.json"
    bindings.write_text(json.dumps(bind_originals(packet, ledger)))
    document = json.loads(manifest.read_text())
    document["cases"][0]["original_bindings"] = {"path": bindings.name, "sha256": sha(bindings)}
    manifest.write_text(json.dumps(document))
    return manifest, bindings, original, ledger


async def test_bound_originals_reach_every_stage_and_are_rechecked(records, tmp_path):
    manifest, bindings, original, ledger = bind_fixture(records, tmp_path)
    pool = FixturePool(records)
    output = tmp_path / "run"
    state = await run_batch(manifest, output, worker_factory=pool.worker)
    assert state["status"] == "completed"
    case = state["cases"]["fixture"]
    source_id = records[1]["sources"][0]["source_id"]
    assert case["original_bindings"]["path"] == str(bindings)
    assert case["original_bindings"]["bound_source_ids"] == [source_id]
    for _, _, prompt, attempt in pool.calls:
        assert f"original_bindings: {bindings}" in prompt
        assert "no access to\ntheir directories or unbound ledger entries" in prompt
        assert "never silently correct the\nrendition by plausibility" in prompt
        pins = json.loads((attempt / "input_bindings.json").read_text())
        for path in (bindings, original, ledger):
            assert pins[str(path)] == sha(path)
    # Exercise the exact inspection command emitted to a worker from its own cwd.
    command = re.search(r"^`(.+source_originals inspect .+)`\.$", pool.calls[0][2], re.MULTILINE)[1]
    command = command.replace("SOURCE_ID", source_id)
    inspected = subprocess.run(command, shell=True, check=True, capture_output=True,
        text=True, cwd=pool.calls[0][3])
    assert json.loads(inspected.stdout)["original"] == str(original)


@pytest.mark.parametrize("fault", ["wrong_manifest_hash", "changed_original", "other_packet"])
async def test_invalid_original_custody_stops_before_any_worker(records, tmp_path, fault):
    manifest, bindings, original, _ = bind_fixture(records, tmp_path,
        other_packet=fault == "other_packet")
    if fault == "wrong_manifest_hash":
        document = json.loads(manifest.read_text())
        document["cases"][0]["original_bindings"]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(document))
    elif fault == "changed_original":
        original.write_bytes(b"%PDF-1.4\nChanged cached original.\n")
    pool = FixturePool(records)
    state = await run_batch(manifest, tmp_path / "run", worker_factory=pool.worker)
    assert not pool.calls
    case = state["cases"]["fixture"]
    assert case["status"] == "source_failed"
    if fault == "other_packet":
        # Identical packet contents at another path do not satisfy the exact binding.
        assert case["error_category"] == "original_bindings_packet"


@pytest.mark.parametrize("target", ["original", "ledger", "bindings"])
async def test_original_custody_change_during_worker_blocks_next_stage(records, tmp_path, target):
    manifest, bindings, original, ledger = bind_fixture(records, tmp_path)
    changed = {"original": original, "ledger": ledger, "bindings": bindings}[target]
    pool = FixturePool(records)

    def worker():
        native = pool.worker()

        class MutatingWorker:
            async def run(self, prompt, attempt):
                result = await native.run(prompt, attempt)
                changed.write_bytes(changed.read_bytes() + b"\nchanged\n")
                return result

        return MutatingWorker()

    state = await run_batch(manifest, tmp_path / "run", worker_factory=worker)
    assert len(pool.calls) == 1
    assert state["cases"]["fixture"]["error_category"] == "frozen_input_changed"
    assert not (tmp_path / "run/cases/fixture/projection").exists()


@pytest.mark.parametrize("declared", [None, {}, {"path": "x"},
    {"path": "x", "sha256": "0" * 64, "directory": True}])
async def test_malformed_original_binding_declaration_cannot_silently_downgrade(records, tmp_path, declared):
    manifest = manifest_for(records, tmp_path)
    document = json.loads(manifest.read_text())
    document["cases"][0]["original_bindings"] = declared
    manifest.write_text(json.dumps(document))
    pool = FixturePool(records)
    with pytest.raises(ValueError, match="Original bindings require"):
        await run_batch(manifest, tmp_path / "run", worker_factory=pool.worker)
    assert not pool.calls
    assert not (tmp_path / "run").exists()


async def test_legacy_case_does_not_claim_bound_visual_inputs(records, tmp_path):
    manifest = manifest_for(records, tmp_path)
    pool = FixturePool(records)
    state = await run_batch(manifest, tmp_path / "run", worker_factory=pool.worker)
    assert state["status"] == "completed"
    assert "original_bindings" not in state["cases"]["fixture"]
    assert all("source_originals inspect " not in prompt for _, _, prompt, _ in pool.calls)
