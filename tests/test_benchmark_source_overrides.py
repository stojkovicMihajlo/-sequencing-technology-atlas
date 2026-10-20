"""Explicit source overlays use synthetic registered bytes, never acquisition."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.benchmark import apply_source_overrides as overlay
from test_benchmark_source_preparation import case, packet
from genomics_discovery.curation.run_state import sha, write
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet


@pytest.fixture
def registered(tmp_path):
    cases = [case(f"method{i}") for i in range(12)]
    design = tmp_path / "cases.json"
    write(design, {"cases": cases})
    base = tmp_path / "base"
    base.mkdir()
    files, rows, reports = {str(design): sha(design)}, [], {}
    for index, declared in enumerate(cases):
        if index < 2:
            selected = packet(base / declared["method_id"], declared)
            value = load_packet(selected)
            files.update({str(selected): sha(selected), **{
                source["original_path"]: source["original_sha256"] for source in value["sources"]}})
            reports[declared["method_id"]] = {"case_id": declared["case_id"],
                "status": "registered_for_source_first_annotation", "packet": str(selected),
                "packet_sha256": sha(selected), "source_packet_sha256": digest(value)}
            rows.append({**{field: declared[field] for field in ("case_id", "method_id", "family", "split")},
                "packet": str(selected), "packet_sha256": sha(selected), "source_report": str(base / "source_report.json")})
        else:
            reports[declared["method_id"]] = {"case_id": declared["case_id"],
                "status": "source_acquisition_needed", "reason": "Unregistered source remains absent."}
    write(base / "source_report.json", {"cases": reports})
    write(base / "input_bindings.json", files)
    write(base / "annotation_manifest.json", {"phase": "checked_source_packet_union_only",
        "case_design_sha256": sha(design), "cases": rows,
        "source_report": {"path": str(base / "source_report.json"), "sha256": sha(base / "source_report.json")}})
    selected = packet(tmp_path / "registration", cases[1], supplement=True)
    custody = tmp_path / "custody.json"
    overrides = tmp_path / "overrides.json"
    fixture = SimpleNamespace(root=tmp_path, cases=cases, design=design, base=base, packet=selected,
        custody=custody, overrides=overrides, output=tmp_path / "overlay", reports=reports)
    seal(fixture)
    return fixture


def seal(fixture, *, missing=None):
    value = overlay.read(fixture.packet)
    files = {str(fixture.packet): sha(fixture.packet), **{
        source["original_path"]: source["original_sha256"] for source in value["sources"]}}
    if missing is not None:
        files.pop(missing)
    write(fixture.custody, {"format_version": "benchmark_manual_registration/1.0", "files": files})
    write(fixture.overrides, {"format_version": "benchmark_source_overrides/1.0",
        "cases_sha256": sha(fixture.design), "registrations": [{"method_id": fixture.cases[1]["method_id"],
            "packet": str(fixture.packet), "packet_sha256": sha(fixture.packet),
            "custody": {"path": str(fixture.custody), "sha256": sha(fixture.custody)}}]})


def apply(fixture):
    return overlay.apply_overrides(fixture.design, fixture.base, fixture.overrides, fixture.output)


def test_whole_packet_overlay_retains_all_twelve_dispositions_and_immutable_inputs(registered):
    f = registered
    before = {path: path.read_bytes() for path in f.root.rglob("*") if path.is_file()}
    result = apply(f)
    rows = {row["method_id"]: row for row in result["cases"]}
    assert len(rows) == 2 and rows["method1"]["packet"] == str(f.packet)
    assert rows["method0"]["packet"] == f.reports["method0"]["packet"]
    reports = overlay.read(f.output / "source_report.json")["cases"]
    assert len(reports) == 12 and reports["method2"] == f.reports["method2"]
    assert reports["method1"]["previous_disposition"] == f.reports["method1"]
    assert reports["method1"]["supplement_count"] == 1
    assert reports["method1"]["source_override"]["status"] == "registered"
    assert reports["method1"]["source_override"]["fallback_permitted"] is False
    assert result["source_report"]["sha256"] == sha(f.output / "source_report.json")
    assert all(row["source_report"] == str(f.output / "source_report.json") for row in rows.values())
    pins = overlay.read(f.output / "input_bindings.json")
    assert all(pins[str(path)] == sha(path) for path in
        (f.custody, f.overrides, f.packet, f.base / "annotation_manifest.json", Path(overlay.__file__).resolve()))
    assert all(pins[name] == value for name, value in reports["method1"]["publication_verification"]["pins"].items())
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not list(f.output.rglob("packet.json"))


@pytest.mark.parametrize("fault", ["custody_hash", "custody_shape", "packet_pin", "original_pin", "scope", "publication"])
def test_failed_requested_override_withholds_without_fallback_to_valid_base(registered, fault):
    f = registered
    if fault == "packet_pin":
        seal(f, missing=str(f.packet))
    elif fault == "original_pin":
        seal(f, missing=overlay.read(f.packet)["sources"][-1]["original_path"])
    elif fault == "scope":
        value = overlay.read(f.packet)
        value["scope"] = "Foreign assay scope"
        write(f.packet, value)
        seal(f)
    elif fault == "publication":
        f.packet = packet(f.root / "foreign-publication", f.cases[1], pmid="999")
        seal(f)
    elif fault == "custody_shape":
        write(f.custody, {"format_version": "benchmark_manual_registration/1.0", "files": []})
        value = overlay.read(f.overrides)
        value["registrations"][0]["custody"]["sha256"] = sha(f.custody)
        write(f.overrides, value)
    else:
        value = overlay.read(f.overrides)
        value["registrations"][0]["custody"]["sha256"] = "0" * 64
        write(f.overrides, value)
    result = apply(f)
    assert [row["method_id"] for row in result["cases"]] == ["method0"]
    row = overlay.read(f.output / "source_report.json")["cases"]["method1"]
    assert row["status"] == "source_identity_or_scope_inspection_needed"
    assert row["previous_disposition"]["status"] == "registered_for_source_first_annotation"
    assert row["source_override"]["status"] == "withheld" and row["reason"]
    assert "packet" not in row


@pytest.mark.parametrize("fault", ["report_hash", "input_pin", "manifest_metadata", "cases_hash", "duplicate_override"])
def test_untrustworthy_base_or_override_design_aborts_before_output(registered, fault):
    f = registered
    if fault == "report_hash":
        path = f.base / "source_report.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif fault == "input_pin":
        path = Path(overlay.read(f.reports["method0"]["packet"])["sources"][0]["original_path"])
        path.write_bytes(path.read_bytes() + b"changed")
    elif fault == "manifest_metadata":
        path = f.base / "annotation_manifest.json"
        value = overlay.read(path)
        value["cases"][0]["case_id"] = "foreign-case"
        write(path, value)
    else:
        value = overlay.read(f.overrides)
        if fault == "cases_hash":
            value["cases_sha256"] = "0" * 64
        else:
            value["registrations"].append(deepcopy(value["registrations"][0]))
        write(f.overrides, value)
    with pytest.raises(ValueError):
        apply(f)
    assert not f.output.exists()


def test_cli_contract_and_output_reuse_are_bounded(registered, capsys):
    f = registered
    overlay.main(["--cases", str(f.design), "--base-sources", str(f.base),
        "--overrides", str(f.overrides), "--output", str(f.output)])
    assert '"explicit_source_overrides_only"' in capsys.readouterr().out
    with pytest.raises(ValueError, match="new output"):
        apply(f)
    with pytest.raises(ValueError, match="new output"):
        overlay.apply_overrides(f.design, f.base, f.overrides, f.base / "nested")
