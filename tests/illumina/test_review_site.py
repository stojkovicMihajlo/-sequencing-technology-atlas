"""The review display must not silently promote rejected or duplicate records."""

import gzip
import json

import pytest

from scripts.illumina.review_site.build import build, read, sha, write


def audit_fixture(tmp_path):
    audit = tmp_path / "audit"
    audit.mkdir()
    rows = []
    for ident in ("kept", "rejected"):
        rows.append({
            "record_id": ident, "title": "Same method name", "batch": "catalogue",
            "status": "supported", "reason": "", "card": {}, "attempt_directory": "/archived/" + ident,
            "candidate": {"record_id": ident, "states": [{"id": "s1"}],
                          "operations": [{"id": "o1", "information_effect": "Original wording"}],
                          "flows": [{"id": "f1", "source": "s1", "target": "o1"}]},
            "review": {"verdict": "supported"}, "sources": {"paper:1": {"title": "A paper"}},
        })
    save_rows(audit, rows)
    write(audit / "GRAPH_PUBLICATION.json", {
        "selected_source_occurrences": 2, "counts": {"supported": 1, "integrity_error": 1},
        "new_admission_failures": [{"record_id": "rejected", "baseline_status": "supported",
                                    "current_status": "integrity_error", "reason": "Unsupported correction"}],
    })
    return audit, rows


def save_rows(audit, rows):
    archive = audit / "records.jsonl.gz"
    with gzip.open(archive, "wt") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")
    write(audit / "AUDIT_STATUS.json", {"raw_audit_receipts": {
        archive.name: {"sha256": sha(archive), "bytes": archive.stat().st_size}}})


def test_later_rejection_overrides_review_without_rewriting_candidate(tmp_path):
    audit, rows = audit_fixture(tmp_path)
    output = tmp_path / "site"
    manifest = build(audit, output)
    entries = read(output / "index.json")["records"]
    assert manifest["counts"] == {"supported": 1, "integrity_error": 1}
    assert manifest["unique_method_count"] is None
    assert manifest["native_model_calls"] == 0
    assert len(entries) == 2  # Equal names must not collapse source occurrences.
    for original, entry in zip(rows, entries, strict=True):
        saved = read(output / entry["file"])
        assert saved["candidate"] == original["candidate"]
        assert saved["review"] == original["review"]
    assert entries[1]["status"] == "integrity_error"
    assert entries[1]["baseline_status"] == "supported"


def test_changed_archive_is_not_silently_used(tmp_path):
    audit, _ = audit_fixture(tmp_path)
    with (audit / "records.jsonl.gz").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="differ from the audit receipt"):
        build(audit, tmp_path / "site")
    assert not (tmp_path / "site").exists()


def test_duplicate_record_ids_are_rejected(tmp_path):
    audit, rows = audit_fixture(tmp_path)
    save_rows(audit, rows + [rows[0]])
    with pytest.raises(ValueError, match="Duplicate source record IDs"):
        build(audit, tmp_path / "site")


def test_unmatched_revalidation_cannot_be_ignored(tmp_path):
    audit, _ = audit_fixture(tmp_path)
    publication = read(audit / "GRAPH_PUBLICATION.json")
    publication["new_admission_failures"][0]["record_id"] = "absent"
    write(audit / "GRAPH_PUBLICATION.json", publication)
    with pytest.raises(ValueError, match="unmatched revalidation"):
        build(audit, tmp_path / "site")


def test_counts_must_reconcile_before_writing(tmp_path):
    audit, _ = audit_fixture(tmp_path)
    publication = read(audit / "GRAPH_PUBLICATION.json")
    publication["counts"] = {"supported": 2}
    write(audit / "GRAPH_PUBLICATION.json", publication)
    with pytest.raises(ValueError, match="publication counts"):
        build(audit, tmp_path / "site")
    assert not (tmp_path / "site").exists()


def test_existing_export_is_preserved(tmp_path):
    audit, _ = audit_fixture(tmp_path)
    output = tmp_path / "site"
    build(audit, output)
    before = sha(output / "index.json")
    with pytest.raises(FileExistsError):
        build(audit, output)
    assert sha(output / "index.json") == before
