"""Acquisition origin and rendition checks do not certify scientific support."""
import hashlib
import json
import shutil
import subprocess
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


def test_invalid_pdf_keeps_original_and_failure_receipt(tmp_path, monkeypatch, public_dns):
    mocked_network(monkeypatch, b"%PDF-1.7 original", "application/pdf")
    with pytest.raises(ValueError, match="Source bytes captured"):
        evidence.fetch_source("https://example.org/article", tmp_path)
    records = list(tmp_path.glob("*/retrieval.json"))
    assert len(records) == 1
    receipt = json.loads(records[0].read_text())
    assert "rendition_error" in receipt
    assert Path(receipt["original_path"]).read_bytes().startswith(b"%PDF")
    assert "text_sha256" not in receipt


def make_pdf(original, passage):
    # Minimal real PDF keeps the native deployment test bundle self-contained.
    stream = ('BT /F1 12 Tf 72 720 Td ' + ' 0 -18 Td '.join('(' + line + ') Tj' for line in passage.splitlines()) + ' ET').encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    raw, offsets = b'%PDF-1.4\n', []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(raw)); raw += str(number).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(raw)
    raw += b'xref\n0 6\n0000000000 65535 f \n'
    raw += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets)
    raw += b'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n' + str(xref).encode() + b'\n%%EOF\n'
    original.write_bytes(raw)


@pytest.mark.skipif(not shutil.which('pdftotext'), reason='Real Poppler required')
def test_pdf_capture_records_original_text_conversion_and_page_boundaries(tmp_path, monkeypatch, public_dns):
    original = tmp_path/'fixture.pdf'
    passage = 'The primer carries a cell barcode.\nThis is synthetic test text, not scientific evidence.'
    make_pdf(original, passage)
    raw = original.read_bytes()
    mocked_network(monkeypatch, raw, 'application/pdf')
    result = evidence.fetch_source('https://example.org/article', tmp_path/'fetched')
    receipt = result['receipt']
    text = Path(result['text_path']).read_text()
    assert 'The primer carries a cell barcode.' in text
    assert '\f' in text
    assert 'no OCR' in receipt['rendition'] and 'pdftotext version' in receipt['rendition']
    assert receipt['original_sha256'] == hashlib.sha256(raw).hexdigest()
    assert receipt['text_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert receipt['content_level'] == 'excerpt'
    assert Path(receipt['original_path']).read_bytes() == raw


@pytest.mark.parametrize('failure', ['missing', 'timeout', 'empty'])
def test_pdf_conversion_failures_keep_captured_bytes_without_registering_text(tmp_path, monkeypatch, public_dns, failure):
    from types import SimpleNamespace
    mocked_network(monkeypatch, b'%PDF-1.4 fixture', 'application/pdf')
    monkeypatch.setattr(evidence.shutil, 'which', lambda name: None if failure == 'missing' else '/fixture/pdftotext')

    def execute(command, **kwargs):
        if command[-1] == '-v':
            return SimpleNamespace(stderr=b'pdftotext fixture', stdout=b'')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, 120)
        Path(command[-1]).write_text(' \n\f')
        return SimpleNamespace(stderr=b'', stdout=b'')

    monkeypatch.setattr(evidence.subprocess, 'run', execute)
    with pytest.raises(ValueError, match='Source bytes captured but rendition failed'):
        evidence.fetch_source('https://example.org/article', tmp_path)
    receipt = json.loads(next(tmp_path.glob('*/retrieval.json')).read_text())
    assert Path(receipt['original_path']).read_bytes() == b'%PDF-1.4 fixture'
    assert 'rendition_error' in receipt and 'text_path' not in receipt


@pytest.mark.skipif(not shutil.which('pdftotext'), reason='Real Poppler required')
def test_pdf_origin_is_usable_by_citation_check_but_mutated_original_is_not(tmp_path, monkeypatch, public_dns):
    from genomics_discovery.ideation.run import CitationCheck, citation_has_host_capture

    original = tmp_path/'fixture.pdf'; make_pdf(original, 'Exact source words in a test PDF.')
    mocked_network(monkeypatch, original.read_bytes(), 'application/pdf')
    fetched = evidence.fetch_source('https://example.org/article', tmp_path/'fetched')
    receipt = fetched['receipt']
    sources = tmp_path/'sources.json'
    sources.write_text(json.dumps({'sources': {'pdf': {'path': fetched['text_path'],
        'content_sha256': receipt['text_sha256'], 'origin_receipts': [receipt]}}}))
    check = CitationCheck.model_validate({'source_id': 'pdf', 'content_sha256': receipt['text_sha256'],
        'declared_url': receipt['request_url'], 'declared_title': 'Test PDF',
        'checked_identity': 'Test PDF', 'verification_url': receipt['final_url'],
        'access_status': 'fulltext', 'status': 'verified_agent_review',
        'reason': 'Synthetic fixture checks origin only.', 'verification_evidence': [{
            'source_id': 'pdf', 'content_sha256': receipt['text_sha256'], 'locator': 'PDF page 1',
            'quote': 'Exact source words in a test PDF.'}]})
    assert citation_has_host_capture(check, sources)
    Path(receipt['original_path']).write_bytes(b'%PDF-replaced')
    assert not citation_has_host_capture(check, sources)


def test_oversized_body_is_not_registered(tmp_path, monkeypatch, public_dns):
    monkeypatch.setattr(evidence, "MAX_BYTES", 10)
    mocked_network(monkeypatch, b"01234567890", "text/plain")
    with pytest.raises(ValueError, match="byte limit"):
        evidence.fetch_source("https://example.org/article", tmp_path)
    assert not list(tmp_path.glob("*/text.txt"))
