"""Offline cache identity/custody tests; no network, subprocess or model calls."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location("illumina_cached_sources", Path(__file__).parents[1] / "scripts/illumina_cached_sources.py")
cache = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cache
spec.loader.exec_module(cache)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def receipt(tmp_path):
    path = tmp_path / "sources.json"
    write(path, {"publications": {"123": {"pmid": "123", "title": "Article", "identifiers": {"doi": ["10.1234/actual"]}}},
                 "fulltexts": {"123": {"status": "no_pmc_identifier_in_pubmed_metadata"}}})
    return path


def jats(ids='<article-id pub-id-type="pmid">123</article-id><article-id pub-id-type="doi">10.1234/actual</article-id>', body=True):
    return ('<article><front><article-meta>' + ids + '</article-meta></front>' +
            ('<body><sec><title>Methods</title><p>' + 'Experimental body text. ' * 50 + '</p></sec></body>' if body else '') + '</article>').encode()


def acquisition(tmp_path, raw, supplied=None):
    root = tmp_path / "cache"
    root.mkdir(exist_ok=True)
    original = root / "article.xml"
    original.write_bytes(raw)
    write(root / "acquisition.json", {"documents": [{"role": "primary_paper", "format": "jats", "path": str(original),
          "original_sha256": cache.digest(raw), "identity": supplied or {"pmid": "123", "doi": "10.1234/actual"}, "url": "https://example.org/article.xml"}]})
    return root


def test_exact_doi_normalization_and_conflicting_pmid():
    assert cache.normalize_doi("https://doi.org/10.1234/Actual") == "10.1234/actual"
    target = {"123": {"pmid": {"123"}, "doi": {"10.1234/actual"}}}
    assert cache.match_targets({"pmid": set(), "doi": {"10.1234/actual"}}, target) == ["123"]
    assert not cache.match_targets({"pmid": {"456"}, "doi": {"10.1234/actual"}}, target)


def test_jats_recovery_preserves_original_hash_and_body(tmp_path):
    root = acquisition(tmp_path, jats())
    r = cache.recover(receipt(tmp_path), [root], tmp_path / "output", tmp_path)
    item = r["recoveries"]["123"]
    assert item["status"] == "fulltext_acquired"
    assert (tmp_path / "output" / item["path"]).read_bytes() == jats()
    assert item["scientific_support_status"] == "fulltext_available_not_content_reviewed"
    assert item["cache_provenance"]["original"]["verified"]
    assert (tmp_path / "output" / "papers/123/cache_registration.json").is_file()


def test_supplied_identity_cannot_override_article_front(tmp_path):
    root = acquisition(tmp_path, jats('<article-id pub-id-type="pmid">456</article-id>'))
    r = cache.recover(receipt(tmp_path), [root], tmp_path / "out", tmp_path)
    assert not r["recoveries"]
    assert r["statistics"]["candidate_gap_types"] == {"article_front_identity_not_unique_or_mismatched": 1}


def test_abstract_only_jats_not_fulltext(tmp_path):
    root = acquisition(tmp_path, jats(body=False))
    r = cache.recover(receipt(tmp_path), [root], tmp_path / "out", tmp_path)
    assert not r["recoveries"]
    assert "jats_no_article_body" in r["statistics"]["candidate_gap_types"]


def test_no_doi_from_reference_list_as_identity(tmp_path):
    raw = b'<article><front><article-meta><article-id pub-id-type="doi">10.1234/other</article-id></article-meta></front><body><p>' + b'body ' * 100 + b'</p></body><back><ref>10.1234/actual</ref></back></article>'
    root = acquisition(tmp_path, raw)
    r = cache.recover(receipt(tmp_path), [root], tmp_path / "out", tmp_path)
    assert not r["recoveries"]


def test_changed_original_not_recovered(tmp_path):
    root = acquisition(tmp_path, jats())
    (root / "article.xml").write_bytes(jats() + b" ")
    r = cache.recover(receipt(tmp_path), [root], tmp_path / "out", tmp_path)
    assert not r["recoveries"]
    assert "sha256_mismatch" in r["statistics"]["candidate_gap_types"]


def test_dataless_guard_does_not_open_file(tmp_path, monkeypatch):
    p = tmp_path / "placeholder"
    monkeypatch.setattr(cache, "file_state", lambda _: "dataless_not_opened")
    with pytest.raises(ValueError, match="dataless_not_opened"):
        cache.read_materialized(p)


def test_symlink_not_read(tmp_path):
    target = tmp_path / "target"
    target.write_text("secret")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symlink_not_followed"):
        cache.read_materialized(link)


def pdf_registration(tmp_path, first_page="DOI: 10.1234/actual", body_doi=""):
    root = tmp_path / "cache"
    root.mkdir()
    original = root / "article.pdf"
    original.write_bytes(b"%PDF-1.7\nfixture")
    text = root / "primary.txt"
    text.write_text(first_page + "\f" + "Methods and results " * 200 + body_doi + "\f")
    write(root / "registration.json", {"identity": {"pmid": "123", "doi": "10.1234/actual"},
          "first_pages_check": {"full_title_match": True, "printed_identifier": "doi"},
          "original": str(original), "original_sha256": cache.digest(original.read_bytes()),
          "rendition": str(text), "rendition_sha256": cache.digest(text.read_bytes()), "public_url": "https://example.org/article.pdf"})
    return root


def test_registered_pdf_rendition_and_original_both_pinned(tmp_path):
    root = pdf_registration(tmp_path)
    r = cache.recover(receipt(tmp_path), [root], tmp_path / "out", tmp_path)
    assert r["recoveries"]["123"]["format"] == "registered_pdf_text"
    assert r["recoveries"]["123"]["url"] == "https://example.org/article.pdf"


def test_pdf_bibliography_doi_not_article_identity(tmp_path):
    root = pdf_registration(tmp_path, first_page="unidentified article", body_doi="References 10.1234/actual")
    r = cache.recover(receipt(tmp_path), [root], tmp_path / "out", tmp_path)
    assert not r["recoveries"]
    assert "pdf_first_page_identifier_not_in_rendition" in r["statistics"]["candidate_gap_types"]


def test_registered_rendition_without_original_pin_is_not_recovered(tmp_path):
    root = pdf_registration(tmp_path)
    path = root / "registration.json"
    registration = json.loads(path.read_text())
    registration.pop("original_sha256")
    write(path, registration)
    r = cache.recover(receipt(tmp_path), [root], tmp_path / "out", tmp_path)
    assert not r["recoveries"]
    assert "original_sha256_missing_or_invalid" in r["statistics"]["candidate_gap_types"]


def test_original_source_packet_preserved_with_passage_hash(tmp_path):
    root = acquisition(tmp_path, jats())
    write(root / "packet.json", {"sources": [{"source_id": "original_source", "role": "primary_paper",
          "original_sha256": cache.digest(jats()), "passages": [{"passage_id": "p00001", "text": "Original reading block"}]}]})
    r = cache.recover(receipt(tmp_path), [root], tmp_path / "out", tmp_path)
    pin = r["recoveries"]["123"]["cache_provenance"]["packets"][0]
    preserved = tmp_path / "out" / pin["preserved_path"]
    assert preserved.read_bytes() == (root / "packet.json").read_bytes()
    assert len(pin["passages_sha256"]) == 64


def test_unknown_root_is_explicit_and_no_workspace_scan(tmp_path):
    r = cache.recover(receipt(tmp_path), [tmp_path / "missing"], tmp_path / "out", tmp_path)
    assert r["statistics"]["metadata_records"] == 0
    assert r["statistics"]["remaining"] == 1
    assert r["statistics"]["metadata_gap_types"] == {"cache_root_missing": 1}


def test_legacy_pubmed_references_cannot_become_own_identity(tmp_path):
    path = receipt(tmp_path)
    data = json.loads(path.read_text())
    data["publications"]["123"]["identifiers"]["pubmed"] = ["123", "456"]
    write(path, data)
    root = acquisition(tmp_path, jats())
    r = cache.recover(path, [root], tmp_path / "out", tmp_path)
    assert "123" in r["recoveries"]
