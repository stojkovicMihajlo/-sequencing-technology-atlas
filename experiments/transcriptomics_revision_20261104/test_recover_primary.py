"""Offline source registration/custody checks; no model or HTTP calls."""
import json
from pathlib import Path

import pytest

from experiments.transcriptomics_revision_20261104 import recover_primary as mod

PDF_TEXT = "A primary publication with a real title\nDOI:10.1234/example\nFull article text\fReferences\f"


def write(path, data):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    path.write_bytes(raw)
    return {"path": str(path), "sha256": mod.digest(raw)}


def request(tmp_path, *, fmt="jats", body=True, original_pmid="123"):
    title, doi = "A primary publication with a real title", "10.1234/example"
    metadata = write(tmp_path / "metadata.xml", (
        '<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>'
        f'<ArticleTitle>{title}</ArticleTitle></Article></MedlineCitation><PubmedData>'
        f'<ArticleIdList><ArticleId IdType="doi">{doi}</ArticleId></ArticleIdList>'
        '</PubmedData></PubmedArticle></PubmedArticleSet>').encode())
    metadata_receipt = write(tmp_path / "metadata_receipt.json", {
        "http_status": 200, **metadata, "final_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=123"})
    original_raw = ((f'<article><front><article-meta><article-id pub-id-type="pmid">{original_pmid}</article-id>'
        f'<article-id pub-id-type="doi">{doi}</article-id></article-meta></front>'
        + ('<body><p>' + 'Actual full article content. ' * 20 + '</p></body>' if body else '<abstract>Abstract only</abstract>')
        + '<back><ref>PMID:123</ref></back></article>').encode() if fmt == "jats" else b"%PDF-1.7\nTEST PDF BYTES")
    original = write(tmp_path / ("original." + ("xml" if fmt == "jats" else "pdf")), original_raw)
    # Reuse an exact selected record from an older multi-document ledger.
    receipt = write(tmp_path / "receipt.json", [{"status": 200, **original,
        "path": "/old/machine/original.pdf", "bytes": len(original_raw), "final_url": "https://publisher.example.org/article"}])
    receipt["selector"] = [0]
    document = {"source_id": "paper:123", "identity": {"pmid": "123", "doi": doi},
        "format": fmt, "original": original, "receipt": receipt, "metadata": metadata,
        "metadata_receipt": metadata_receipt}
    if fmt == "pdf":
        viewed = write(tmp_path / "viewed-page.png", b"ACTUAL INSPECTED FIXTURE")
        document["inspection"] = write(tmp_path / "inspection.json", {
            "original_sha256": original["sha256"], "rendition_sha256": mod.digest(PDF_TEXT.encode()),
            "selection": {"kind": "whole_document",
                "basis": "The inspected fixture contains just the target article and its references."},
            "files": {viewed["path"]: viewed["sha256"]}})
    path = tmp_path / "request.json"
    write(path, {"format_version": "cached_primary_request/1.0", "documents": [document]})
    return path


def rewrite(path, update):
    value = json.loads(path.read_bytes())
    update(value)
    write(path, value)


def test_jats_new_bundle_preserves_old_registry_and_original_bytes(tmp_path):
    manifest = request(tmp_path)
    parent = tmp_path / "parent.json"
    parent_pin = write(parent, {"paper:123": {"content_level": "abstract", "pmid": "123", "text": "Short abstract"}})
    before = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    out = tmp_path / "new"
    result = mod.register(manifest, out, parent_registry=parent, parent_sha256=parent_pin["sha256"])
    registry = json.loads((out / "source_registry.json").read_bytes())
    source = registry["paper:123"]
    assert source["content_level"] == "fulltext"
    assert "Actual full article" in source["text"]
    assert source["original"]["format"] == "JATS"
    assert source["original"]["scientific_support_status"] == "fulltext_available_not_content_reviewed"
    assert result["recoveries"][0]["replaced_source"]["content_level"] == "abstract"
    assert result["recoveries"][0]["receipt_input"]["recorded_receipt_path"] == "/old/machine/original.pdf"
    assert result["native_calls"] == 0 and result["scientific_review"] is False
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert (out / "request.json").read_bytes() == manifest.read_bytes()
    assert all(Path(pin["path"]).is_relative_to(out) for pin in source["custody_files"])
    mod.verify_registry_files(registry)
    with pytest.raises(FileExistsError):
        mod.register(manifest, out)


def test_abstract_only_cannot_be_promoted(tmp_path):
    with pytest.raises(ValueError, match="Abstract-only"):
        mod.register(request(tmp_path, body=False), tmp_path / "new")
    assert not (tmp_path / "new/source_registry.json").exists()


def test_cited_expected_id_does_not_override_wrong_own_article(tmp_path):
    with pytest.raises(ValueError, match="own article identity"):
        mod.register(request(tmp_path, original_pmid="999"), tmp_path / "new")


def test_original_custody_change_blocks_publication(tmp_path):
    manifest = request(tmp_path)
    (tmp_path / "original.xml").write_bytes(b"different source")
    with pytest.raises(ValueError, match="custody"):
        mod.register(manifest, tmp_path / "new")


def test_false_receipt_cannot_bind_original(tmp_path):
    manifest = request(tmp_path)
    receipt = tmp_path / "receipt.json"
    rewrite(receipt, lambda rows: rows[0].update(sha256="0" * 64))
    rewrite(manifest, lambda m: m["documents"][0]["receipt"].update(sha256=mod.digest(receipt.read_bytes())))
    with pytest.raises(ValueError, match="receipt does not bind"):
        mod.register(manifest, tmp_path / "new")


def test_existing_fulltext_cannot_be_silently_replaced(tmp_path):
    manifest = request(tmp_path)
    parent = tmp_path / "parent.json"
    pin = write(parent, {"paper:123": {"content_level": "fulltext", "pmid": "123"}})
    with pytest.raises(ValueError, match="overwrite"):
        mod.register(manifest, tmp_path / "new", parent_registry=parent, parent_sha256=pin["sha256"])


def test_oai_wrapper_retains_exact_response_and_separate_article(tmp_path):
    manifest = request(tmp_path)
    data = json.loads(manifest.read_bytes())
    original = tmp_path / "original.xml"
    raw = b'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><GetRecord><record><metadata>' + original.read_bytes() + b'</metadata></record></GetRecord></OAI-PMH>'
    original.write_bytes(raw)
    data["documents"][0]["original"]["sha256"] = mod.digest(raw)
    receipt = tmp_path / "receipt.json"
    rewrite(receipt, lambda rows: rows[0].update(sha256=mod.digest(raw), bytes=len(raw)))
    data["documents"][0]["receipt"]["sha256"] = mod.digest(receipt.read_bytes())
    write(manifest, data)
    mod.register(manifest, tmp_path / "new")
    directory = tmp_path / "new/papers/123"
    assert (directory / "original.xml").read_bytes() == raw
    assert b"OAI-PMH" not in (directory / "article.xml").read_bytes()


def test_pdf_requires_bound_inspection_and_checks_real_title_identifier(tmp_path, monkeypatch):
    manifest = request(tmp_path, fmt="pdf")
    text = PDF_TEXT
    monkeypatch.setattr(mod, "pdf_text", lambda p, **kwargs: (text, {"converter": "test fixture"}))
    mod.register(manifest, tmp_path / "new")
    registry = json.loads((tmp_path / "new/source_registry.json").read_bytes())
    assert registry["paper:123"]["original"]["format"] == "registered_pdf_text"
    assert registry["paper:123"]["text"] == text
    (tmp_path / "viewed-page.png").write_bytes(b"changed inspected bytes")
    with pytest.raises(ValueError, match="custody"):
        mod.register(manifest, tmp_path / "bad")


def test_pdf_reference_doi_without_own_title_does_not_pass(tmp_path, monkeypatch):
    manifest = request(tmp_path, fmt="pdf")
    monkeypatch.setattr(mod, "pdf_text", lambda p, **kwargs: ("Another article\nReferences 10.1234/example", {}))
    with pytest.raises(ValueError, match="full publication title"):
        mod.register(manifest, tmp_path / "new")


def test_selection_cannot_keep_an_unrelated_body_after_excluding_identity(tmp_path, monkeypatch):
    manifest = request(tmp_path, fmt="pdf")
    text = ("A primary publication with a real title\nDOI:10.1234/example\n"
            "The target article text\fUnrelated appended material begins here\n"
            "A different article without its identity\nUnrelated appended material ends here\f")
    monkeypatch.setattr(mod, "pdf_text", lambda p, **kwargs: (text, {}))
    inspection = tmp_path / "inspection.json"
    rewrite(inspection, lambda data: data.update(rendition_sha256=mod.digest(text.encode()), selection={"kind": "exact_text_bounds",
        "basis": "Invalid selection intentionally excludes the correctly identified article.",
        "start": "Unrelated appended material begins here", "end": "Unrelated appended material ends here"}))
    rewrite(manifest, lambda m: m["documents"][0]["inspection"].update(sha256=mod.digest(inspection.read_bytes())))
    with pytest.raises(ValueError, match="full publication title"):
        mod.register(manifest, tmp_path / "new")
    assert not (tmp_path / "new/source_registry.json").exists()


def test_absent_metadata_doi_is_not_a_conflict_with_exact_pmid():
    raw = (b'<article><front><article-meta><article-id pub-id-type="pmid">123</article-id>'
           b'<article-id pub-id-type="doi">10.1234/example</article-id></article-meta></front>'
           b'<body><p>Full body</p></body></article>')
    text, _, _ = mod.xml_text(raw, {"pmid": "123", "doi": None})
    assert "Full body" in text


def test_metadata_identity_and_host_are_independently_bound(tmp_path):
    manifest = request(tmp_path)
    rewrite(manifest, lambda m: m["documents"][0]["identity"].update(doi="10.1234/not-the-paper"))
    with pytest.raises(ValueError, match="authoritative metadata"):
        mod.register(manifest, tmp_path / "new")


def test_metadata_from_unrecognized_host_is_not_authoritative(tmp_path):
    manifest = request(tmp_path)
    receipt = tmp_path / "metadata_receipt.json"
    rewrite(receipt, lambda row: row.update(final_url="https://untrusted.example.org/metadata"))
    rewrite(manifest, lambda m: m["documents"][0]["metadata_receipt"].update(sha256=mod.digest(receipt.read_bytes())))
    with pytest.raises(ValueError, match="authoritative host"):
        mod.register(manifest, tmp_path / "new")


def test_pdf_inspection_of_another_rendition_cannot_be_reused(tmp_path, monkeypatch):
    manifest = request(tmp_path, fmt="pdf")
    monkeypatch.setattr(mod, "pdf_text", lambda p, **kwargs: (PDF_TEXT + "Changed extraction order", {}))
    with pytest.raises(ValueError, match="inspection must bind"):
        mod.register(manifest, tmp_path / "new")
