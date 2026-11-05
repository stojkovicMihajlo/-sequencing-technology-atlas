"""Capture public source bytes separately from model-written evidence summaries.

This verifies retrieval origin and byte identity, not bibliographic identity,
scientific support, or completeness of the reading rendition.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
from html.parser import HTMLParser
from pathlib import Path
import socket
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4
from xml.etree import ElementTree

from genomics_discovery.illumina.sources import jats_text


MAX_BYTES = 20_000_000


def _public_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or parts.port not in (None, 443)):
        raise ValueError("Source URL must be public HTTPS without credentials or a non-443 port")
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("Source URL must resolve only to public IP addresses")
    return url


class _PublicRedirects(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _ReadingHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.suppressed += 1
        if not self.suppressed and tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "section"}:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.suppressed:
            self.suppressed -= 1
        if not self.suppressed and tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "section"}:
            self.text.append("\n")

    def handle_data(self, data):
        if not self.suppressed:
            self.text.append(data)


def render_source(raw: bytes, content_type: str, charset: str = "utf-8") -> tuple[str, str]:
    """A reading view; original bytes remain authoritative. No inferred fulltext claim."""
    media = content_type.partition(";")[0].lower().strip()
    if raw.startswith(b"%PDF") or media == "application/pdf":
        raise ValueError("PDF rendition unavailable in this tool; use an accessible HTML/XML source or explicit source-add excerpt")
    if "xml" in media or raw.lstrip().startswith(b"<?xml"):
        root = ElementTree.fromstring(raw)
        if root.tag.split("}")[-1] != "article":
            raise ValueError("XML is not an article; metadata/search results are not article text")
        text, _ = jats_text(raw)
        rendition = "jats_text reading rendition; original XML preserved"
    elif "html" in media or raw.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        parser = _ReadingHTML()
        parser.feed(raw.decode(charset, errors="strict"))
        text = "\n".join(line.strip() for line in "".join(parser.text).splitlines() if line.strip())
        rendition = "HTML visible-text approximation; tables/figures/layout may be incomplete"
    elif media.startswith("text/plain"):
        text = raw.decode(charset, errors="strict")
        rendition = "plain text decoding"
    else:
        raise ValueError(f"Unsupported source content type: {media or 'missing'}")
    if not text.strip():
        raise ValueError("No readable source text recovered")
    return text, rendition


def fetch_source(url: str, directory: Path) -> dict:
    """Fetch one public URL into an immutable new directory and return small receipts.

    DNS and each redirect are checked, but this is not a substitute for managed
    network policy (DNS may change between validation and connection).
    """
    _public_url(url)
    destination = Path(directory) / ("source_" + uuid4().hex)
    destination.mkdir(parents=True, exist_ok=False)
    opener = build_opener(_PublicRedirects())
    request = Request(url, headers={"User-Agent": "SequencingTechnologyAtlas/ideation-evidence"})
    with opener.open(request, timeout=60) as response:
        final_url = _public_url(response.geturl())
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Source exceeds acquisition byte limit")
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset() or "utf-8"
        http_status = response.status
    original = destination / "original"
    original.write_bytes(raw)
    receipt = {"request_url": url, "final_url": final_url, "http_status": http_status,
               "content_type": content_type, "original_path": str(original.resolve()),
               "original_sha256": hashlib.sha256(raw).hexdigest(), "original_bytes": len(raw),
               "origin_status": "host_fetched_public_url; scientific identity and entailment unverified"}
    receipt_file = destination / "retrieval.json"
    try:
        text, rendition = render_source(raw, content_type, charset)
    except (ValueError, LookupError, ElementTree.ParseError) as error:
        receipt["rendition_error"] = str(error)
        receipt_file.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        raise ValueError(f"Source bytes captured but rendition failed; receipt: {receipt_file}: {error}") from error
    text_path = destination / "text.txt"
    text_path.write_text(text, encoding="utf-8")
    receipt.update(text_path=str(text_path.resolve()), text_sha256=hashlib.sha256(text_path.read_bytes()).hexdigest(),
                   rendition=rendition, content_level="excerpt")
    receipt_file.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return {"text_path": str(text_path.resolve()), "content_level": "excerpt", "receipt": receipt}
