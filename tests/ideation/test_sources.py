"""Agent handoffs retain evidence custody without promoting metadata claims."""
from copy import deepcopy
import hashlib
import json

import pytest

from genomics_discovery.ideation.sources import merge_sources


def write(path, value):
    path.write_text(json.dumps(value))


def source_bundle(tmp_path):
    text = tmp_path / "text.txt"
    text.write_text("The captured primary-source passage.")
    pin = hashlib.sha256(text.read_bytes()).hexdigest()
    entry = {"source_id": "lit", "path": str(text), "content_sha256": pin,
        "url": "https://example.org/paper", "title": "Short title", "content_level": "excerpt",
        "captured_at": "first", "provenance_status": "agent_imported_text"}
    registry = tmp_path / "sources.json"
    write(registry, {"format_version": "ideation_sources/1", "sources": {"lit": entry}})
    receipt = {"request_url": entry["url"], "final_url": entry["url"], "http_status": 200,
        "origin_status": "host_fetched_public_url; scientific identity and entailment unverified",
        "original_path": str(text), "original_sha256": pin, "text_path": str(text), "text_sha256": pin}
    return registry, entry, receipt


def test_inherited_host_recapture_and_metadata_survive_handoff_without_promotion(tmp_path):
    registry, entry, receipt = source_bundle(tmp_path)
    receipt_path = tmp_path / "origin_receipts.json"
    declarations_path = tmp_path / "capture_declarations.json"
    write(receipt_path, {"lit": [receipt]})
    declaration = {"title": "Full publication title", "content_level": "fulltext",
        "captured_at": "second", "provenance_status": "agent_imported_text"}
    write(declarations_path, {"lit": [declaration]})
    pins = {path: path.read_bytes() for path in (registry, receipt_path, declarations_path)}
    merged = merge_sources([registry])
    actual = merged["sources"]["lit"]
    assert actual["origin_receipts"] == [receipt]
    assert declaration in actual["capture_declarations"]
    assert actual["metadata_status"].startswith("differing_capture_declarations")
    for field in ("title", "content_level", "provenance_status", "path", "content_sha256"):
        assert actual[field] == entry[field]
    assert all(path.read_bytes() == original for path, original in pins.items())

    # Downstream agents need no reference to the predecessor's sidecar directory.
    downstream = tmp_path / "downstream"
    downstream.mkdir()
    final_registry = downstream / "sources.json"
    write(final_registry, merged)
    assert merge_sources([final_registry]) == merged
    from genomics_discovery.ideation.run import CitationCheck, citation_has_host_capture
    check = CitationCheck(source_id="lit", content_sha256=entry["content_sha256"],
        declared_url=entry["url"], declared_title=entry["title"], checked_identity="Publication identity",
        verification_url=entry["url"], access_status="abstract", status="verified_agent_review",
        verification_evidence=[{"source_id": "lit", "content_sha256": entry["content_sha256"],
            "locator": "Abstract", "quote": "The captured primary-source passage."}],
        reason="Fixture checks custody only, not scientific entailment.")
    assert citation_has_host_capture(check, final_registry)


@pytest.mark.parametrize("fault", ["hash", "url", "unregistered", "missing_hash", "malformed"])
def test_sidecar_cannot_attach_unrelated_custody(tmp_path, fault):
    registry, entry, receipt = source_bundle(tmp_path)
    ident, records = "lit", [receipt]
    if fault == "hash":
        receipt["text_sha256"] = "f" * 64
    elif fault == "url":
        receipt.update(request_url="https://example.org/other", final_url="https://example.org/other")
    elif fault == "unregistered":
        ident = "another_source"
    elif fault == "missing_hash":
        receipt.pop("text_sha256")
    else:
        records = {"receipt": receipt}
    write(tmp_path / "origin_receipts.json", {ident: records})
    with pytest.raises(ValueError):
        merge_sources([registry])


def test_repeated_handoff_deduplicates_identical_receipts_and_preserves_alternatives(tmp_path):
    registry, entry, receipt = source_bundle(tmp_path)
    write(tmp_path / "origin_receipts.json", {"lit": [receipt]})
    first = merge_sources([registry])
    other = tmp_path / "other"
    other.mkdir()
    second_registry = other / "sources.json"
    second_entry = {**deepcopy(entry), "title": "Different declared title"}
    write(second_registry, {"format_version": "ideation_sources/1", "sources": {"lit": second_entry}})
    write(other / "origin_receipts.json", {"lit": [receipt]})
    merged = merge_sources([registry, second_registry, registry])["sources"]["lit"]
    assert merged["origin_receipts"] == first["sources"]["lit"]["origin_receipts"]
    assert {row["title"] for row in merged["capture_declarations"]} == {entry["title"], second_entry["title"]}


def test_missing_registry_and_mutated_source_fail_instead_of_silently_dropping_evidence(tmp_path):
    with pytest.raises(FileNotFoundError):
        merge_sources([tmp_path / "missing.json"])
    registry, entry, _ = source_bundle(tmp_path)
    (tmp_path / "text.txt").write_text("Changed source bytes.")
    with pytest.raises(ValueError, match="Captured source changed"):
        merge_sources([registry])


def test_legacy_incomplete_receipts_are_retained_without_adding_verification(tmp_path):
    registry, entry, _ = source_bundle(tmp_path)
    entry["origin_receipts"] = [{"original_path": "old_location", "original_sha256": "a" * 64}]
    write(registry, {"format_version": "ideation_sources/1", "sources": {"lit": entry}})
    assert merge_sources([registry])["sources"]["lit"] == entry
