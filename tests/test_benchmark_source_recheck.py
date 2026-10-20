"""Reassessment of exact prior source custody; no native worker or retrieval."""

from pathlib import Path

import pytest

from scripts.benchmark import recheck_sources
from test_benchmark_source_preparation import case, html_packet, packet
from genomics_discovery.curation.run_state import sha, write
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet


@pytest.fixture
def prior_sources(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Offline source recheck cannot construct a native worker")

    monkeypatch.setattr("genomics_discovery.curation.source_acquisition.runtime.CodexWorker", forbidden)
    prior = tmp_path / "prior/sources"
    prior.mkdir(parents=True)
    eligible, restored, pending = case("eligible"), case("restored"), case("pending")
    selected = packet(prior / "initial_packets/eligible", eligible)
    recovered, original, rendition, ledger = html_packet(prior / "acquisition/methods/restored", restored)
    partial = packet(prior / "acquisition/methods/pending", pending)
    cases_path = tmp_path / "cases.json"
    write(cases_path, {"cases": [eligible, restored, pending]})
    report_path = prior / "source_report.json"
    write(report_path, {"cases": {
        "eligible": {"case_id": eligible["case_id"], "status": "registered_for_source_first_annotation",
            "packet": str(selected), "packet_sha256": sha(selected), "source_packet_sha256": digest(load_packet(selected))},
        "restored": {"case_id": restored["case_id"], "status": "source_identity_or_scope_inspection_needed",
            "retained_packet": str(recovered), "reason": "'identifiers'"},
        "pending": {"case_id": pending["case_id"], "status": "source_acquisition_needed",
            "retained_packet": str(partial), "source_search_required": True}}})
    write(prior / "annotation_manifest.json", {"case_design_sha256": sha(cases_path),
        "source_report": {"path": str(report_path), "sha256": sha(report_path)}, "cases": []})
    write(prior / "input_bindings.json", {str(cases_path): sha(cases_path), str(selected): sha(selected),
        **{source["original_path"]: source["original_sha256"] for source in load_packet(selected)["sources"]}})
    receipt = prior / "acquisition/source_receipts.json"
    files = {str(path): sha(path) for path in (prior / "acquisition").rglob("*") if path.is_file()}
    write(receipt, {"format_version": "public_source_acquisition/1.0", "files": files, "scientific_resolution": False})
    return cases_path, prior, tmp_path / "rechecked", recovered, original, ledger, receipt


def test_rechecks_pinned_html_without_retrieval_and_preserves_other_case_states(prior_sources):
    cases_path, prior, output, selected, original, ledger, receipt = prior_sources
    before = {path: path.read_bytes() for path in prior.rglob("*") if path.is_file()}
    result = recheck_sources.recheck(cases_path, prior, output, case_ids=["restored_original"])
    assert [row["case_id"] for row in result["cases"]] == ["restored_original"]
    assert result["cases"][0]["packet"] == str(selected)
    assert result["cases"][0]["packet_sha256"] == sha(selected)
    reports = recheck_sources.read(output / "source_report.json")["cases"]
    assert reports["restored"]["status"] == reports["eligible"]["status"] == "registered_for_source_first_annotation"
    assert reports["pending"]["status"] == "source_acquisition_needed"
    assert reports["pending"]["identity_recheck"]["status"] == "pending_prior_source_gate"
    assert reports["restored"]["identity_recheck"]["packet_pin_origin"] == "prior_source_receipts"
    assert reports["restored"]["identity_recheck"]["prior_reason"] == "'identifiers'"
    pins = recheck_sources.read(output / "input_bindings.json")
    assert all(pins[str(path)] == sha(path) for path in (selected, original, ledger, receipt))
    assert all(path.read_bytes() == value for path, value in before.items())
    assert not list(output.rglob("events.jsonl"))
    assert "No new retrieval" in result["boundary"]


@pytest.mark.parametrize("missing", ["packet", "ledger"])
def test_unbound_retained_path_or_new_receipt_cannot_be_promoted(prior_sources, missing):
    cases_path, prior, output, selected, original, ledger, receipt = prior_sources
    contents = recheck_sources.read(receipt)
    contents["files"].pop(str(selected if missing == "packet" else ledger))
    write(receipt, contents)
    result = recheck_sources.recheck(cases_path, prior, output)
    assert [row["method_id"] for row in result["cases"]] == ["eligible"]
    report = recheck_sources.read(output / "source_report.json")["cases"]["restored"]
    assert report["status"] == "source_identity_or_scope_inspection_needed"
    assert "pin" in report["reason"] or "bound" in report["reason"]


@pytest.mark.parametrize("changed", ["original", "ledger", "packet", "report", "cases"])
def test_changed_prior_bytes_abort_before_creating_annotation_phase(prior_sources, changed):
    cases_path, prior, output, selected, original, ledger, receipt = prior_sources
    target = {"original": original, "ledger": ledger, "packet": selected,
        "report": prior / "source_report.json", "cases": cases_path}[changed]
    target.write_bytes(target.read_bytes() + b"\nchanged after prior custody")
    with pytest.raises(ValueError):
        recheck_sources.recheck(cases_path, prior, output)
    assert not output.exists()


def test_consistently_registered_wrong_publication_stays_withheld(prior_sources):
    cases_path, prior, output, selected, original, ledger, receipt = prior_sources
    original.write_bytes(original.read_bytes().replace(b'citation_pmid" content="123"', b'citation_pmid" content="999"'))
    rows = recheck_sources.read(ledger)
    rows[0]["original_sha256"] = sha(original)
    write(ledger, rows)
    contents = recheck_sources.read(receipt)
    contents["files"].update({str(original): sha(original), str(ledger): sha(ledger)})
    write(receipt, contents)
    result = recheck_sources.recheck(cases_path, prior, output)
    assert [row["method_id"] for row in result["cases"]] == ["eligible"]
    report = recheck_sources.read(output / "source_report.json")["cases"]["restored"]
    assert report["status"] == "source_identity_or_scope_inspection_needed" and "pmid mismatch" in report["reason"]


def test_conflicting_prior_pin_and_output_reuse_are_rejected(prior_sources):
    cases_path, prior, output, selected, original, ledger, receipt = prior_sources
    contents = recheck_sources.read(receipt)
    contents["files"][str(cases_path)] = "a" * 64
    write(receipt, contents)
    with pytest.raises(ValueError, match="Conflicting prior"):
        recheck_sources.recheck(cases_path, prior, output)
    assert not output.exists()
    with pytest.raises(ValueError, match="outside the prior"):
        recheck_sources.recheck(cases_path, prior, prior / "rechecked")
