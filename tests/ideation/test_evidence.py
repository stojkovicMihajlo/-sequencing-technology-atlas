"""Acquisition origin and rendition checks do not certify scientific support."""
import hashlib
import json
from email.message import Message
from pathlib import Path

import pytest

from genomics_discovery.ideation import evidence


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.8.8", 443))])


@pytest.mark.parametrize("url", ["http://example.org", "https://user:pass@example.org", "https://example.org:8080"])
def test_reject_nonpublic_url_forms(url, public_dns):
    with pytest.raises(ValueError):
        evidence._public_url(url)


def test_reject_private_dns_and_redirect(monkeypatch):
    monkeypatch.setattr(evidence.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="public IP"):
        evidence._public_url("https://localhost/article")
    with pytest.raises(ValueError, match="public IP"):
        evidence._PublicRedirects().redirect_request(None, None, 302, "", {}, "https://localhost/article")


def test_html_rendition_omits_scripts_without_claiming_fulltext():
    text, kind = evidence.render_source(b"<html><h1>Paper</h1><script>FAKE</script><p>RNA &amp; DNA</p></html>", "text/html")
    assert "FAKE" not in text and "RNA & DNA" in text
    assert "incomplete" in kind


def test_xml_uses_existing_jats_locators_and_rejects_metadata():
    text, kind = evidence.render_source(b"<article><body><p>Evidence here.</p></body></article>", "application/xml")
    assert "[b00001] Evidence here." in text and "JATS" in kind.upper()
    with pytest.raises(ValueError, match="not an article"):
        evidence.render_source(b"<PubmedArticleSet/>", "application/xml")


def mocked_network(monkeypatch, raw, media):
    class Response:
        headers = Message()
        headers["Content-Type"] = media
        status = 200

        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit): return raw[:limit]
        def geturl(self): return "https://example.org/article"

    class Opener:
        def open(self, *args, **kwargs): return Response()

    monkeypatch.setattr(evidence, "build_opener", lambda *args: Opener())


def test_capture_receipt_hashes_original_and_rendition(tmp_path, monkeypatch, public_dns):
    raw = b"<html><p>Accessible evidence.</p></html>"
    mocked_network(monkeypatch, raw, "text/html")
    result = evidence.fetch_source("https://example.org/article", tmp_path)
    receipt = result["receipt"]
    assert Path(receipt["original_path"]).read_bytes() == raw
    assert receipt["original_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["text_sha256"] == hashlib.sha256(Path(result["text_path"]).read_bytes()).hexdigest()
    assert result["content_level"] == "excerpt"
    assert "unverified" in receipt["origin_status"]
    assert json.loads((Path(result["text_path"]).parent / "retrieval.json").read_text()) == receipt


def test_unsupported_pdf_keeps_original_and_failure_receipt(tmp_path, monkeypatch, public_dns):
    mocked_network(monkeypatch, b"%PDF-1.7 original", "application/pdf")
    with pytest.raises(ValueError, match="Source bytes captured"):
        evidence.fetch_source("https://example.org/article", tmp_path)
    records = list(tmp_path.glob("*/retrieval.json"))
    assert len(records) == 1
    receipt = json.loads(records[0].read_text())
    assert "rendition_error" in receipt
    assert Path(receipt["original_path"]).read_bytes().startswith(b"%PDF")
    assert "text_sha256" not in receipt


def test_oversized_body_is_not_registered(tmp_path, monkeypatch, public_dns):
    monkeypatch.setattr(evidence, "MAX_BYTES", 10)
    mocked_network(monkeypatch, b"01234567890", "text/plain")
    with pytest.raises(ValueError, match="byte limit"):
        evidence.fetch_source("https://example.org/article", tmp_path)
    assert not list(tmp_path.glob("*/text.txt"))
