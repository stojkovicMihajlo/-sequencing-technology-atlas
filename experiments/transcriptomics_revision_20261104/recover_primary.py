"""Register explicitly pinned cached primary papers in a NEW source bundle.

Offline source preparation only: no discovery, native calls, method association,
scientific review or historical edges. A manifest names original bytes, a pinned
download receipt (optionally a selector into an older ledger), and authoritative
PubMed XML with its own receipt. PDF inputs additionally require an actual pinned
inspection report with an explicit article selection. The report is an authored
assertion, not a guarantee that every scientific statement was inspected.

Usage: PYTHONPATH=src python experiments/transcriptomics_revision_20261104/
recover_primary.py MANIFEST NEW_OUTPUT [--parent-registry PATH --parent-sha256 HASH]

Manifest shape:
  {"format_version":"cached_primary_request/1.0", "documents":[{
    "source_id":"paper:123", "identity":{"pmid":"123","doi":"10.x/y"},
    "format":"pdf", "pdf_text_mode":"default",
    "original":{"path":"paper.pdf","sha256":"..."},
    "receipt":{"path":"downloads.json","sha256":"...","selector":[0]},
    "metadata":{"path":"pubmed.xml","sha256":"..."},
    "metadata_receipt":{"path":"metadata_receipt.json","sha256":"..."},
    "inspection":{"path":"inspection.json","sha256":"..."}
  }]}
Inspection uses existing pdf_registration article-selection semantics:
  {"original_sha256":"...", "rendition_sha256":"...",
    "selection":{"kind":"whole_document",
    "basis":"An explicit account of the inspected article boundaries."},
    "files":{"inspected-page.png":"..."}}
Use format="jats" for standalone JATS or a single-article PMC OAI response;
that path validates the article front and actual body without a PDF inspection.
Relative paths resolve against their containing manifest/report, never by name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from genomics_discovery.illumina.extraction import verify_registry_files
from genomics_discovery.illumina.sources import jats_text, parse_pubmed, write
from genomics_discovery.sources.acquisition.downloads import public_url
from genomics_discovery.sources.acquisition.pdf_registration import (
    _doi, _title, select_article_text, verify_pdf_text,
)
from genomics_discovery.sources.document_manifest import MAX_BYTES


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pinned(pin, base):
    """Read only an explicit regular file; avoid hydrating macOS placeholders."""
    if not isinstance(pin, dict) or not re.fullmatch(r"[a-f0-9]{64}", pin.get("sha256", "")):
        raise ValueError("Every input requires an explicit SHA256 pin")
    path = Path(pin["path"])
    path = path if path.is_absolute() else base / path
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_flags", 0) & 0x40000000:
        raise ValueError("Input is not a materialized regular file")
    if info.st_size > MAX_BYTES:
        raise ValueError("Input exceeds the existing source size limit")
    raw = path.read_bytes()
    if digest(raw) != pin["sha256"]:
        raise ValueError("Input custody mismatch: " + str(path))
    return path.resolve(), raw


def receipt_record(pin, base, expected_raw):
    """Select the actual old receipt; never synthesize a successful retrieval."""
    path, raw = pinned(pin, base)
    record = json.loads(raw)
    for key in pin.get("selector", []):
        if not isinstance(key, (str, int)) or isinstance(key, bool):
            raise ValueError("Receipt selector must contain object keys or array indices")
        record = record[key]
    if not isinstance(record, dict):
        raise ValueError("Receipt selector did not identify a record")
    status = record.get("http_status", record.get("status"))
    if status not in {200, "downloaded"} or record.get("sha256") != digest(expected_raw):
        raise ValueError("Download receipt does not bind successful acquisition of these bytes")
    if "bytes" in record and record["bytes"] != len(expected_raw):
        raise ValueError("Download receipt byte count differs")
    url = public_url(record.get("final_url"))
    return path, raw, record, url


def pdf_text(path, *, mode="default"):
    tool = shutil.which("pdftotext")
    if tool is None:
        raise ValueError("pdftotext is required; no implicit OCR fallback")
    modes = {"default": [], "raw": ["-raw"], "layout": ["-layout"]}
    if mode not in modes:
        raise ValueError("PDF text mode must be explicitly default, raw or layout")
    command = [tool, *modes[mode], "-enc", "UTF-8", "-eol", "unix", str(path), "-"]
    result = subprocess.run(command, capture_output=True, check=True, timeout=120)
    version = subprocess.run([tool, "-v"], capture_output=True, check=True, timeout=10)
    return result.stdout.decode("utf-8"), {
        "command": command, "mode": mode, "version": version.stderr.decode("utf-8", errors="replace").strip(),
        "warnings": result.stderr.decode("utf-8", errors="replace").strip(),
        "limits": "Poppler reading order; no OCR. Figures, columns, formulas and superscripts may be lost or reordered. Inspect original PDF for layout-dependent claims.",
    }


def xml_text(raw, identity):
    if re.search(br"<!ENTITY\s", raw, re.I):
        raise ValueError("XML entity declarations are unsupported")
    root = ET.fromstring(raw)
    local = lambda e: e.tag.rsplit("}", 1)[-1]
    articles = [e for e in root.iter() if local(e) == "article"]
    if len(articles) != 1 or any(local(e) == "error" for e in root.iter()):
        raise ValueError("Expected one full JATS article, without OAI errors")
    article = articles[0]
    front = next((e for e in article if local(e) == "front"), None)
    meta = next((e for e in front if local(e) == "article-meta"), None) if front is not None else None
    if meta is None:
        raise ValueError("JATS article front is missing")
    ids = {}
    for element in meta:
        if local(element) == "article-id":
            ids.setdefault(element.get("pub-id-type"), set()).add("".join(element.itertext()).strip())
    pmids = ids.get("pmid", set())
    dois = {_doi(value) for value in ids.get("doi", set())}
    if (pmids and pmids != {identity["pmid"]}) or (dois and identity["doi"] and dois != {identity["doi"]}):
        raise ValueError("JATS own article identity conflicts with bibliographic identity")
    if not (pmids == {identity["pmid"]} or identity["doi"] and dois == {identity["doi"]}):
        raise ValueError("JATS front lacks an exact PMID or DOI match")
    body = next((e for e in article if local(e) == "body"), None)
    if body is None or not " ".join(body.itertext()).strip():
        raise ValueError("Abstract-only XML cannot be registered as full text")
    article_raw = ET.tostring(article, encoding="utf-8", xml_declaration=True)
    text, blocks = jats_text(article_raw)
    return text, blocks, article_raw


def register(manifest_path, output, *, parent_registry=None, parent_sha256=None):
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    if manifest.get("format_version") != "cached_primary_request/1.0" or not manifest.get("documents"):
        raise ValueError("Expected a nonempty cached_primary_request/1.0 manifest")
    base = manifest_path.parent
    registry, parent_raw = {}, None
    if (parent_registry is None) != (parent_sha256 is None):
        raise ValueError("Parent registry and its exact hash must be provided together")
    if parent_registry is not None:
        parent_registry, parent_raw = pinned({"path": str(parent_registry), "sha256": parent_sha256}, base)
        registry = json.loads(parent_raw)
        verify_registry_files(registry)
    output.mkdir(parents=True, exist_ok=False)
    (output / "request.json").write_bytes(manifest_raw)
    if parent_raw is not None:
        (output / "parent_registry.json").write_bytes(parent_raw)
    recoveries, seen = [], set()
    for document in manifest["documents"]:
        expected = document["identity"]
        pmid, sid = str(expected["pmid"]), document["source_id"]
        if not pmid.isdecimal() or sid != "paper:" + pmid or sid in seen:
            raise ValueError("Each document requires a unique source_id matching its exact PMID")
        seen.add(sid)
        old = registry.get(sid)
        if old is not None and (old.get("content_level") not in {"abstract", "metadata"} or old.get("pmid") != pmid):
            raise ValueError("Recovery cannot overwrite an existing fulltext or a different identity")
        original_path, original_raw = pinned(document["original"], base)
        receipt_path, receipt_raw, receipt, url = receipt_record(document["receipt"], base, original_raw)
        metadata_path, metadata_raw = pinned(document["metadata"], base)
        metadata_receipt_path, metadata_receipt_raw, _, metadata_url = receipt_record(
            document["metadata_receipt"], base, metadata_raw)
        if urlsplit(metadata_url).hostname != "eutils.ncbi.nlm.nih.gov":
            raise ValueError("PubMed metadata requires its authoritative host receipt")
        if re.search(br"<!ENTITY\s", metadata_raw, re.I):
            raise ValueError("Metadata entity declarations are unsupported")
        metadata = parse_pubmed(metadata_raw)
        if pmid not in metadata:
            raise ValueError("Expected PMID absent from authoritative PubMed metadata")
        publication = metadata[pmid]
        dois = {_doi(value) for value in publication["identifiers"].get("doi", [])}
        if len(dois) > 1:
            raise ValueError("Ambiguous DOI identity in authoritative metadata")
        identity = {"pmid": pmid, "title": publication["title"], "doi": next(iter(dois), None)}
        if ((expected.get("doi") and _doi(expected["doi"]) != identity["doi"])
                or (expected.get("title") and _title(expected["title"]) != _title(identity["title"]))):
            raise ValueError("Declared identity disagrees with authoritative metadata")
        folder = output / "papers" / pmid
        folder.mkdir(parents=True)
        custody = []

        def preserve(name, raw):
            path = folder / name
            path.write_bytes(raw)
            pin = {"path": str(path), "sha256": digest(raw)}
            custody.append(pin)
            return pin

        original = preserve("original." + ("pdf" if document["format"] == "pdf" else "xml"), original_raw)
        metadata_copy = preserve("pubmed.xml", metadata_raw)
        receipt_copy = preserve("original_receipt.json", receipt_raw)
        metadata_receipt_copy = preserve("metadata_receipt.json", metadata_receipt_raw)
        if document["format"] == "pdf":
            if not original_raw.startswith(b"%PDF-"):
                raise ValueError("Original has no PDF signature")
            text, conversion = pdf_text(Path(original["path"]), mode=document.get("pdf_text_mode", "default"))
            identity_check = verify_pdf_text(text, identity)
            inspection_path, inspection_raw = pinned(document["inspection"], base)
            inspection = json.loads(inspection_raw)
            if (inspection.get("original_sha256") != original["sha256"] or not inspection.get("files")
                    or inspection.get("rendition_sha256") != digest(text.encode("utf-8"))):
                raise ValueError("PDF inspection must bind the original and actual inspected artifacts")
            inspection_imports = []
            for index, (path, expected_hash) in enumerate(inspection["files"].items(), 1):
                inspected_path, raw = pinned({"path": path, "sha256": expected_hash}, inspection_path.parent)
                imported = preserve(f"inspected_{index:03d}" + inspected_path.suffix, raw)
                inspection_imports.append({"declared_path": path, "resolved_input_path": str(inspected_path),
                                           "imported": imported})
            inspection_copy = preserve("inspection.json", inspection_raw)
            selection = {**inspection["selection"], "inspection_report": inspection_copy}
            text, prefix, suffix, selection = select_article_text(text, selection)
            # A correct title in an excluded cover/article cannot establish the
            # identity of a different selected body. Ambiguous mixed PDFs stay
            # held for the richer existing selected-article registration path.
            selected_identity_check = verify_pdf_text(text, identity)
            preserve("excluded_prefix.txt", prefix.encode())
            preserve("excluded_suffix.txt", suffix.encode())
            blocks = [{"block_id": f"b{i:05d}", "kind": "pdf_text_page",
                       "locator": f"Selected rendition segment {i}; consult article selection offsets", "text": page.strip()}
                      for i, page in enumerate(text.split("\f"), 1) if page.strip()]
            conversion.update(identity_check=identity_check, selected_identity_check=selected_identity_check,
                selection=selection, inspection_imports=inspection_imports,
                content_boundary="Pinned authored article inspection; not scientific content review or machine proof of full-paper reading")
            source_format = "registered_pdf_text"
        elif document["format"] == "jats":
            text, blocks, article_raw = xml_text(original_raw, identity)
            article = preserve("article.xml", article_raw)
            conversion = {"converter": "illumina.sources.jats_text", "article": article,
                "limits": "JATS reading blocks; original XML/OAI retained. Figure images are not rendered."}
            source_format = "JATS"
        else:
            raise ValueError("Only explicitly identified pdf or jats originals are supported")
        if not text.strip():
            raise ValueError("Selected publication text is empty")
        rendition = preserve("article.txt", text.encode("utf-8"))
        blocks_pin = preserve("blocks.json", (json.dumps({"source_id": sid,
            "original_sha256": original["sha256"], "blocks": blocks}, ensure_ascii=False, indent=2) + "\n").encode())
        record = {"source_id": sid, "status": "fulltext_acquired", "format": source_format,
            **original, "text_path": rendition["path"], "text_sha256": rendition["sha256"],
            "blocks_path": blocks_pin["path"], "blocks_sha256": blocks_pin["sha256"],
            "url": url, "identity": identity, "conversion": conversion,
            "metadata_source": metadata_copy["path"], "metadata_source_sha256": metadata_copy["sha256"],
            "acquisition_receipt": receipt_copy, "metadata_receipt": metadata_receipt_copy,
            "scientific_support_status": "fulltext_available_not_content_reviewed"}
        registration = preserve("registration.json", (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode())
        registry[sid] = {"kind": "text", "content_level": "fulltext", "text": text,
            "path": rendition["path"], "sha256": rendition["sha256"], **identity,
            "url": url, "original": record, "custody_files": custody,
            "association_status": "publication_identity_checked; no_new_method_association_or_history_claim"}
        recoveries.append({"source_id": sid, "registration": registration, "replaced_source": old,
            "original_input": {"path": str(original_path), "sha256": digest(original_raw)},
            "receipt_input": {"path": str(receipt_path), "sha256": digest(receipt_raw),
                              "selector": document["receipt"].get("selector", []), "recorded_receipt_path": receipt.get("path")},
            "metadata_input": {"path": str(metadata_path), "sha256": digest(metadata_raw)},
            "metadata_receipt_input": {"path": str(metadata_receipt_path), "sha256": digest(metadata_receipt_raw)}})
    if manifest_path.read_bytes() != manifest_raw or parent_registry and parent_registry.read_bytes() != parent_raw:
        raise ValueError("Registration inputs changed during preparation")
    verify_registry_files(registry)
    write(output / "source_registry.json", registry)
    result = {"format_version": "primary_source_recovery/1.0", "request_sha256": digest(manifest_raw),
        "parent_registry_sha256": parent_sha256, "recoveries": recoveries,
        "source_registry": {"path": str(output / "source_registry.json"),
                            "sha256": digest((output / "source_registry.json").read_bytes())},
        "scientific_review": False, "native_calls": 0,
        "boundary": "New source registration only; no method match, bibliography interpretation or historical edge established."}
    write(output / "recovery_bundle.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--parent-registry", type=Path)
    parser.add_argument("--parent-sha256")
    args = parser.parse_args()
    result = register(args.manifest, args.output, parent_registry=args.parent_registry, parent_sha256=args.parent_sha256)
    print(json.dumps({"registered": len(result["recoveries"]), "source_registry": result["source_registry"]}, indent=2))


if __name__ == "__main__":
    main()
