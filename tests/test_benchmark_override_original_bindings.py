"""Optional original access survives whole-packet overlays; no rendering/models."""

from pathlib import Path

import pytest

from scripts.benchmark import apply_source_overrides as overlay
from test_benchmark_source_overrides import registered as registered, apply
from genomics_discovery.curation.protocol_benchmark.runtime import run_batch
from genomics_discovery.curation.run_state import sha, write
from genomics_discovery.curation.source_originals import bind_originals
from genomics_discovery.curation.workflow_extraction.sources import load_packet


def declared_originals(fixture, *, packet=None, native_leaf=False):
    packet = packet or fixture.packet
    source = load_packet(packet)["sources"][-1]
    original = Path(source["original_path"]) if native_leaf else fixture.root / "documented_original.pdf"
    if not native_leaf:
        original.write_bytes(b"%PDF-1.4\nSynthetic original bytes; no conversion or visual-equivalence claim.\n")
    ledger = fixture.root / "explicit_originals_ledger.json"
    write(ledger, [{"source_id": source["source_id"], "rendition": source["original_path"],
        "rendition_sha256": source["original_sha256"], "original": str(original),
        "original_sha256": sha(original), "boundary": "Injected documentary association only."}])
    artifact = fixture.root / "original_bindings.json"
    write(artifact, bind_originals(packet, ledger))
    return {"path": str(artifact), "sha256": sha(artifact)}, original, ledger


def register_binding(fixture, declaration):
    overrides = overlay.read(fixture.overrides)
    overrides["registrations"][0]["original_bindings"] = declaration
    write(fixture.overrides, overrides)


async def test_override_preserves_exact_original_binding_into_manifest_report_pins_and_runtime(registered, monkeypatch):
    f = registered
    declaration, original, ledger = declared_originals(f)
    register_binding(f, declaration)
    before = {path: path.read_bytes() for path in f.root.rglob("*") if path.is_file()}
    monkeypatch.setattr("genomics_discovery.curation.source_originals.subprocess.run",
        lambda *a, **k: pytest.fail("An overlay cannot convert or render originals"))
    result = apply(f)
    row = next(row for row in result["cases"] if row["method_id"] == "method1")
    assert row["original_bindings"] == declaration
    report = overlay.read(f.output / "source_report.json")["cases"]["method1"]
    assert report["original_bindings"] == declaration
    assert report["source_override"]["status"] == "registered"
    pins = overlay.read(f.output / "input_bindings.json")
    assert all(pins[str(path)] == sha(path) for path in (original, ledger, Path(declaration["path"])))
    assert all(path.read_bytes() == content for path, content in before.items())
    state = await run_batch(f.output / "annotation_manifest.json", f.root / "reference_preflight", prepare_only=True)
    assert state["cases"][f.cases[1]["case_id"]]["original_bindings"]["bound_source_ids"] == ["supplement"]
    assert state["completed_native_sessions"] == 0


def test_inherited_row_preserves_validated_optional_binding_without_an_override(registered):
    f = registered
    base_packet = Path(f.reports["method0"]["packet"])
    declaration, _, _ = declared_originals(f, packet=base_packet, native_leaf=True)
    path = f.base / "annotation_manifest.json"
    manifest = overlay.read(path)
    manifest["cases"][0]["original_bindings"] = declaration
    write(path, manifest)
    result = apply(f)
    row = next(row for row in result["cases"] if row["method_id"] == "method0")
    assert row["original_bindings"] == declaration
    assert overlay.read(f.output / "input_bindings.json")[declaration["path"]] == declaration["sha256"]


@pytest.mark.parametrize("fault", ["artifact_hash", "same_bytes_other_packet", "original_changed",
    "ledger_changed", "relative_path", "malformed_hash", "null"])
def test_invalid_declared_binding_withholds_requested_override_without_fallback(registered, fault):
    f = registered
    selected = f.packet
    if fault == "same_bytes_other_packet":
        selected = f.root / "another_packet.json"
        selected.write_bytes(f.packet.read_bytes())
    declaration, original, ledger = declared_originals(f, packet=selected)
    if fault == "artifact_hash":
        declaration["sha256"] = "0" * 64
    elif fault == "original_changed":
        original.write_bytes(original.read_bytes() + b"changed")
    elif fault == "ledger_changed":
        ledger.write_bytes(ledger.read_bytes() + b"\nchanged")
    elif fault == "relative_path":
        declaration["path"] = Path(declaration["path"]).name
    elif fault == "malformed_hash":
        declaration["sha256"] = "invalid"
    elif fault == "null":
        declaration = None
    register_binding(f, declaration)
    result = apply(f)
    assert [row["method_id"] for row in result["cases"]] == ["method0"]
    report = overlay.read(f.output / "source_report.json")["cases"]["method1"]
    assert report["status"] == "source_identity_or_scope_inspection_needed"
    assert report["source_override"]["status"] == "withheld"
    assert report["previous_disposition"]["status"] == "registered_for_source_first_annotation"
    assert "original_bindings" not in report and "packet" not in report


def test_original_binding_cannot_overwrite_existing_conflicting_custody_pin(registered):
    f = registered
    declaration, original, _ = declared_originals(f)
    pins = {str(original): "0" * 64}
    with pytest.raises(ValueError, match="Conflicting custody pin"):
        overlay._original_bindings(declaration, f.packet, load_packet(f.packet), pins)
    assert pins[str(original)] == "0" * 64


def test_undeclared_optional_binding_does_not_invent_a_new_annotation_contract(registered):
    result = apply(registered)
    assert all("original_bindings" not in row for row in result["cases"])
