"""Whole-packet selection respects repair failures and frozen source custody."""

from pathlib import Path

import pytest

from scripts.benchmark import combine_sources, recheck_sources
from test_benchmark_supplement_recovery import source_phase, run
from genomics_discovery.curation.run_state import sha, write
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet


@pytest.fixture
def phases(source_phase):
    f = source_phase
    cases = combine_sources.recovery.read(f.cases)["cases"]
    old = load_packet(f.method / "packet.json")
    scope = {key: cases[1][key] for key in ("method_id", "title", "scope", "variant")}
    descriptors = [{**{key: source[key] for key in ("source_id", "title", "url", "role")}, "path": source["original_path"]}
        for source in old["sources"]]
    packet = f.prior / "initial_packets/untouched/packet.json"
    write(packet, build_packet(**scope, sources=descriptors))
    report_path = f.prior / "source_report.json"
    write(report_path, {"cases": {
        "assay": {"case_id": cases[0]["case_id"], "status": "source_identity_or_scope_inspection_needed",
            "retained_packet": str(f.method / "packet.json"), "reason": "Old identity integration failure"},
        "untouched": {"case_id": cases[1]["case_id"], "status": "registered_for_source_first_annotation",
            "packet": str(packet), "packet_sha256": sha(packet)}}})
    write(f.prior / "annotation_manifest.json", {"case_design_sha256": sha(f.cases),
        "source_report": {"path": str(report_path), "sha256": sha(report_path)}, "cases": []})
    write(f.prior / "input_bindings.json", {str(f.cases): sha(f.cases), str(packet): sha(packet)})
    f.reset()
    f.checked, f.union = f.root / "checked", f.root / "combined"
    recheck_sources.recheck(f.cases, f.prior, f.checked)
    assert len(combine_sources.recovery.read(f.checked / "annotation_manifest.json")["cases"]) == 2
    return f


def combine(f):
    return combine_sources.combine(f.cases, f.checked, f.output, f.union)


def reseal_recovery(f):
    receipt_path = f.output / "source_receipts.json"
    receipt = combine_sources.recovery.read(receipt_path)
    receipt["files"].update({str(path): sha(path) for path in f.output.rglob("*") if path.is_file() and path != receipt_path})
    write(receipt_path, receipt)


def test_repaired_and_prior_eligible_whole_packets_form_one_new_manifest(phases):
    f = phases
    run(f)
    old_bytes = {path: path.read_bytes() for directory in (f.prior, f.checked, f.output)
        for path in directory.rglob("*") if path.is_file()}
    result = combine(f)
    rows = {row["method_id"]: row for row in result["cases"]}
    assert set(rows) == {"assay", "untouched"}
    assert Path(rows["assay"]["packet"]).is_relative_to(f.output)
    assert Path(rows["untouched"]["packet"]).is_relative_to(f.prior)
    assert all(row["source_report"] == str(f.union / "source_report.json") for row in rows.values())
    report = combine_sources.recovery.read(f.union / "source_report.json")
    assert set(report["cases"]) == {"assay", "untouched"}
    assert report["scientific_resolution"] is False and report["annotation_ready_is_not_scientific_completeness"]
    assert report["cases"]["assay"]["source_union"]["recovery_required"] is True
    assert all(path.read_bytes() == raw for path, raw in old_bytes.items())
    assert not list(f.union.rglob("packet.json"))


def test_failed_required_repair_does_not_fall_back_to_rechecked_partial_packet(phases):
    f = phases
    f.body = b"<html>access challenge</html>"
    assert run(f)["cases"] == []
    result = combine(f)
    assert [row["method_id"] for row in result["cases"]] == ["untouched"]
    report = combine_sources.recovery.read(f.union / "source_report.json")["cases"]["assay"]
    assert report["status"] == "source_acquisition_needed"
    assert report["source_union"]["recovery_required"] is True
    assert report["source_union"]["status"] == "withheld"


def test_explicit_deferred_recipe_performs_no_download_and_remains_withheld(phases):
    f = phases
    recipe = combine_sources.recovery.read(f.recipes)
    recipe["recipes"][0].update(supplements=[], deferred_reason="Prior official endpoint returned 404; source acquisition remains needed")
    write(f.recipes, recipe)
    assert run(f)["cases"] == [] and f.calls == []
    result = combine(f)
    assert [row["method_id"] for row in result["cases"]] == ["untouched"]
    report = combine_sources.recovery.read(f.union / "source_report.json")["cases"]["assay"]
    assert report["supplement_recovery"]["status"] == "deferred"
    assert "404" in report["reason"]


@pytest.mark.parametrize("target", ["packet", "original", "report", "recipes", "case_design"])
def test_changed_phase_bytes_fail_before_writing_a_union(phases, target):
    f = phases
    manifest = run(f)
    path = {"packet": Path(manifest["cases"][0]["packet"]), "original": f.primary,
        "report": f.output / "source_report.json", "recipes": f.recipes, "case_design": f.cases}[target]
    path.write_bytes(path.read_bytes() + b"\nchanged after source gate")
    with pytest.raises(ValueError):
        combine(f)
    assert not f.union.exists()


@pytest.mark.parametrize("field", ["family", "split", "case_id", "packet_sha256"])
def test_consistently_resealed_wrong_manifest_metadata_cannot_admit_a_packet(phases, field):
    f = phases
    run(f)
    path = f.output / "annotation_manifest.json"
    value = combine_sources.recovery.read(path)
    value["cases"][0][field] = "wrong" if field != "packet_sha256" else "0" * 64
    write(path, value)
    reseal_recovery(f)
    if field == "packet_sha256":
        assert [row["method_id"] for row in combine(f)["cases"]] == ["untouched"]
    else:
        with pytest.raises(ValueError, match="metadata"):
            combine(f)
        assert not f.union.exists()


def test_recipe_request_cannot_be_dropped_to_enable_old_packet_fallback(phases):
    f = phases
    run(f)
    path = f.output / "annotation_manifest.json"
    manifest = combine_sources.recovery.read(path)
    manifest["requested_method_ids"] = []
    write(path, manifest)
    reseal_recovery(f)
    with pytest.raises(ValueError, match="requested methods"):
        combine(f)
    assert not f.union.exists()


def test_report_object_digest_and_scope_are_checked_again_not_trusted(phases):
    f = phases
    run(f)
    path = f.output / "source_report.json"
    report = combine_sources.recovery.read(path)
    report["cases"]["assay"]["source_packet_sha256"] = "0" * 64
    write(path, report)
    manifest_path = f.output / "annotation_manifest.json"
    manifest = combine_sources.recovery.read(manifest_path)
    manifest["source_report"]["sha256"] = sha(path)
    write(manifest_path, manifest)
    reseal_recovery(f)
    assert [row["method_id"] for row in combine(f)["cases"]] == ["untouched"]
    report = combine_sources.recovery.read(f.union / "source_report.json")["cases"]["assay"]
    assert "object digest" in report["reason"]


def test_existing_output_or_nested_source_output_is_rejected(phases):
    f = phases
    run(f)
    f.union.mkdir()
    with pytest.raises(ValueError, match="new output"):
        combine(f)
    with pytest.raises(ValueError, match="new output"):
        combine_sources.combine(f.cases, f.checked, f.output, f.output / "nested")
