"""Explicit post-acquisition primary-PDF registration, without another finder.

Public bibliographic metadata verifies identity, not scientific completeness.
Only the primary PDF is registered; adjacent supplements are never promoted.
This standalone entry point does not alter the live acquisition runtime.
"""

import argparse
from html import unescape
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import unicodedata
from urllib.parse import quote, urlencode, urlsplit
from xml.etree import ElementTree as ET

from .. import cached_documents
from ..cached_documents import ArticleHTML, _publish_rendition
from ..document_manifest import MAX_BYTES
from ..workflow_extraction.sources import build_packet, load_packet
from .downloads import Download, download, public_url as check_public_url
from .models import Scope


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _doi(value):
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.strip().casefold())


def _title(value):
    # Typography and line wrapping differ between bibliographic records and PDFs.
    text = unicodedata.normalize("NFKC", _plain_title(value)).casefold()
    return "".join(c for c in text if c.isalnum())


def _plain_title(value):
    # Crossref titles can contain inline publisher presentation markup (<scp>,
    # <i>, <sub>, etc.). Keep its visible text; retain the unmodified raw record.
    parser = ArticleHTML()
    parser.feed(unescape(value))
    parser.close()
    return " ".join(parser.text.split())


def bibliography(raw, final_url, expected):
    """Parse host-fetched metadata; reject ambiguous records and identity drift."""
    host = urlsplit(final_url)
    if host.scheme != "https":
        raise ValueError("Bibliographic metadata must use verified HTTPS")
    if expected.pmid:
        if host.hostname != "eutils.ncbi.nlm.nih.gov":
            raise ValueError("PubMed metadata was redirected outside the authoritative host")
        if re.search(br"<!ENTITY\s", raw, re.I):
            raise ValueError("Metadata entity declarations are unsupported")
        root = ET.fromstring(raw)
        articles = root.findall("./PubmedArticle")
        if len(articles) != 1:
            raise ValueError("PubMed response must contain exactly one article")
        article = articles[0]
        pmid = article.findtext("./MedlineCitation/PMID")
        title = "".join(article.find("./MedlineCitation/Article/ArticleTitle").itertext())
        dois = {_doi(node.text or "") for node in article.findall("./PubmedData/ArticleIdList/ArticleId")
                if node.get("IdType") == "doi"}
        if len(dois) > 1:
            raise ValueError("PubMed metadata has ambiguous DOI identity")
        doi = next(iter(dois), None)
        if pmid != expected.pmid:
            raise ValueError("Authoritative publication PMID mismatch")
    else:
        if host.hostname != "api.crossref.org":
            raise ValueError("DOI metadata was redirected outside the authoritative host")
        record = json.loads(raw)
        if record.get("status") != "ok" or record.get("message-type") != "work":
            raise ValueError("Expected one Crossref work record")
        work = record["message"]
        titles = work.get("title", [])
        if len(titles) != 1:
            raise ValueError("DOI metadata needs exactly one title")
        title, doi, pmid = titles[0], _doi(work.get("DOI", "")), None
    if not title or len(_title(title)) < 12:
        raise ValueError("Bibliographic publication title is missing or too short")
    if expected.doi and doi != _doi(expected.doi):
        raise ValueError("Authoritative publication DOI mismatch")
    if expected.title and _title(title) != _title(expected.title):
        raise ValueError("Authoritative publication title mismatch")
    return {"title": _plain_title(title), "doi": doi, "pmid": pmid}


def verify_pdf_text(first_pages, identity):
    """Require the full title and an exact identifier on the same early page.

    An older reprint without printed identifiers remains an explicit inspection
    need. A title match or a finder-declared identifier alone is insufficient.
    """
    title = _title(identity["title"])
    for number, page in enumerate(first_pages.split("\f")[:2], 1):
        front = page[:12000]
        if re.search(r"(?im)^\s*(?:correction|author correction|publisher correction|erratum|"
                     r"corrigendum|retraction|expression of concern)\b", front[:3000]):
            raise ValueError("Correction/retraction notice requires explicit publication inspection")
        references = re.search(r"(?im)^\s*(?:references(?:\s+and\s+notes)?|bibliography|literature cited)\s*$", front)
        identity_front = front[:references.start()] if references else front
        if title not in _title(identity_front[:3000]):
            continue
        doi = identity.get("doi")
        printed_dois = {_doi(m.group().rstrip(".,;:)]}")) for m in re.finditer(
            r"10\.\d{4,9}/[^\s<>\"']+", identity_front, re.I)}
        if doi and printed_dois - {_doi(doi)}:
            raise ValueError("Conflicting early-page DOI requires explicit publication inspection")
        doi_match = bool(doi and re.search(
            r"(?<![\w])" + re.escape(doi) + r"(?![\w./-])", identity_front, re.I))
        pmid = identity.get("pmid")
        pmid_match = bool(pmid and re.search(r"\bPMID\s*:?\s*" + re.escape(pmid) + r"\b", identity_front, re.I))
        if doi_match or pmid_match:
            return {"pdf_page": number, "full_title_match": True,
                    "printed_identifier": "doi" if doi_match else "pmid"}
    raise ValueError("First two PDF pages do not bind full publication title and exact DOI/PMID")


def select_article_text(text, selection):
    """Apply an explicit source-preparer decision; never infer article boundaries.

    Whole-document use is a recorded inspection assertion, not an identity-test
    consequence. Exact selectors retain excluded text as separate, unregistered
    artifacts. Ambiguous, inverted and absent selectors cannot publish a packet.
    """
    if not isinstance(selection, dict) or not isinstance(selection.get("basis"), str) or len(selection["basis"].strip()) < 20:
        raise ValueError("Explicit inspected article-content selection with a basis is required")
    report = selection.get("inspection_report")
    if (not isinstance(report, dict) or set(report) != {"path", "sha256"}
            or not isinstance(report["path"], str) or not report["path"]
            or not isinstance(report["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", report["sha256"])):
        raise ValueError("Article selection must bind its genuine inspection report")
    if selection.get("kind") == "whole_document":
        if set(selection) != {"kind", "basis", "inspection_report"}:
            raise ValueError("Whole-document selection has unsupported fields")
        return text, "", "", {**selection, "start_char": 0, "end_char": len(text)}
    if selection.get("kind") != "exact_text_bounds" or set(selection) != {"kind", "basis", "start", "end", "inspection_report"}:
        raise ValueError("Expected explicit whole_document or exact_text_bounds selection")
    start, end = selection["start"], selection["end"]
    if any(not isinstance(marker, str) or len(marker) < 12 or text.count(marker) != 1 for marker in (start, end)):
        raise ValueError("Article start/end text must each be exact and unique (at least 12 characters)")
    begin, finish = text.index(start), text.index(end) + len(end)
    if begin >= text.index(end):
        raise ValueError("Article text boundaries are inverted or overlapping")
    return text[begin:finish], text[:begin], text[finish:], {**selection, "start_char": begin, "end_char": finish}


def verify_selected_pdf_text(text, identity, selection, evidence, *, original_sha256):
    """Check an explicitly inspected article whose identifier is not on page one.

    This route records the source preparer's publication-identifier interpretation.
    It does not infer article boundaries or turn a reference-list DOI into identity.
    The conservative early-page route remains the default for uninspected PDFs.
    """
    required = {"format_version", "original_sha256", "inspection_report", "title_witness",
                "identifier_witness", "identifier_context", "identifier_role", "basis"}
    if (not isinstance(evidence, dict) or set(evidence) != required
            or evidence["format_version"] != "selected_article_identity/1.0"
            or evidence["original_sha256"] != original_sha256
            or evidence["identifier_role"] != "article_publication_identifier"
            or not isinstance(evidence["basis"], str) or len(evidence["basis"].strip()) < 20):
        raise ValueError("Explicit inspected article-identity evidence and original binding required")
    decision = {k: v for k, v in selection.items() if k not in {"start_char", "end_char"}}
    if decision.get("kind") != "exact_text_bounds":
        raise ValueError("Inspected identity route requires explicit exact article boundaries")
    if evidence["inspection_report"] != decision.get("inspection_report"):
        raise ValueError("Identity evidence and article selection must bind the same inspection")
    _, _, _, bounds = select_article_text(text, decision)
    opening = text[bounds["start_char"]:bounds["start_char"] + 3000]
    if re.search(r"(?im)^\s*(?:correction|author correction|publisher correction|erratum|"
                 r"corrigendum|retraction|expression of concern)\b", opening):
        raise ValueError("Correction/retraction notice requires separate publication inspection")

    def witness(name):
        item = evidence[name]
        if (not isinstance(item, dict) or set(item) != {"start_char", "end_char", "quote", "page"}
                or type(item["start_char"]) is not int or type(item["end_char"]) is not int
                or not bounds["start_char"] <= item["start_char"] < item["end_char"] <= bounds["end_char"]
                or not isinstance(item["quote"], str)
                or text[item["start_char"]:item["end_char"]] != item["quote"]
                or type(item["page"]) is not int
                or text[:item["start_char"]].count("\f") + 1 != item["page"]):
            raise ValueError(f"Invalid exact selected-article identity witness: {name}")
        return item

    title = witness("title_witness")
    identifier = witness("identifier_witness")
    context = witness("identifier_context")
    if (_title(title["quote"]) != _title(identity["title"])
            or title["start_char"] - bounds["start_char"] > 3000):
        raise ValueError("Inspected title must match the authoritative title at the article opening")
    line_start = text.rfind("\n", 0, title["start_char"]) + 1
    line_end = text.find("\n", title["end_char"])
    if line_end < 0:
        line_end = len(text)
    if text[line_start:title["start_char"]].strip() or text[title["end_char"]:line_end].strip():
        raise ValueError("Inspected title witness must cover its complete header lines")
    expected = identity.get("doi")
    value = identifier["quote"].strip()
    if not expected or _doi(value) != _doi(expected):
        raise ValueError("Inspected publication identifier must match the authoritative DOI")
    before = text[max(0, identifier["start_char"] - 1):identifier["start_char"]]
    after = text[identifier["end_char"]:identifier["end_char"] + 1]
    if re.fullmatch(r"[\w./-]", before) or re.fullmatch(r"[\w./-]", after):
        raise ValueError("Inspected publication identifier has conflicting DOI boundary")
    if (not context["start_char"] <= identifier["start_char"] < identifier["end_char"] <= context["end_char"]
            or len(context["quote"]) > 1000 or len(context["quote"]) <= len(identifier["quote"])):
        raise ValueError("Publication DOI needs a bounded inspected context, not an isolated citation")
    dois = {_doi(m.group().rstrip(".,;:)]}")) for m in re.finditer(
        r"10\.\d{4,9}/[^\s<>\"']+", context["quote"], re.I)}
    if dois != {_doi(expected)}:
        raise ValueError("Inspected publication-identifier context has conflicting DOI identity")
    return {"route": "inspected_selected_article", "title_witness": title,
            "identifier_witness": identifier, "identifier_context": context,
            "identifier_role": evidence["identifier_role"], "basis": evidence["basis"],
            "boundary": "Exact selected-text/bibliography bindings plus an explicit inspected "
                        "publication-identifier interpretation; not automatic article purity, "
                        "visual comprehension or scientific completeness."}


def register_pdf(scope, original, original_sha256, public_url, output, *, fetch=download,
                 acquisition_record=None, document_id=None, bibliographic_receipt=None,
                 original_receipt=None, selection=None, identity_evidence=None):
    """Register pinned original bytes after independent host identity checks.

    ``fetch`` is an injectable host downloader for tests, never a finder result.
    Production CLI always uses the existing TLS-verifying anonymous downloader.
    Every destination is new; original bytes and old runs are left unchanged.
    """
    scope = Scope.model_validate(scope)
    original, output = Path(original).resolve(), Path(output).resolve()
    helper_path = Path(__file__).resolve()
    helper_sha = sha(helper_path)
    check_public_url(public_url)
    if original.stat().st_size > MAX_BYTES or sha(original) != original_sha256:
        raise ValueError("Original PDF bytes differ from the acquisition binding or exceed the size limit")
    with original.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError("Primary original has no PDF signature")
    acquisition_binding = None
    if acquisition_record is not None:
        acquisition_record = Path(acquisition_record).resolve()
        acquisition_sha = sha(acquisition_record)
        acquisition = json.loads(acquisition_record.read_bytes())
        if acquisition.get("method_id") != scope.method_id:
            raise ValueError("Acquisition belongs to another method")
        rows = [row for row in acquisition["documents"] if row.get("id") == document_id]
        if len(rows) != 1:
            raise ValueError("Acquisition needs an exact unique document ID")
        row = rows[0]
        if (row.get("role") != "primary_paper" or row.get("format") != "pdf"
                or Path(row.get("path", "")).resolve() != original
                or row.get("original_sha256") != original_sha256 or row.get("final_url") != public_url):
            raise ValueError("Original does not match the exact acquisition document binding")
        acquisition_binding = {"path": str(acquisition_record), "sha256": acquisition_sha,
                               "document_id": document_id, "record": row}
    elif document_id is not None:
        raise ValueError("Document ID requires an acquisition record")
    original_binding = None
    if acquisition_record is None:
        if original_receipt is None:
            raise ValueError("Direct PDF registration requires its actual host download receipt")
        original_receipt = Path(original_receipt).resolve()
        record = json.loads(original_receipt.read_bytes())
        if (record.get("status") != "downloaded" or Path(record.get("path", "")).resolve() != original
                or record.get("sha256") != original_sha256 or record.get("final_url") != public_url):
            raise ValueError("Actual host download receipt does not bind this PDF and public URL")
        original_binding = {"path": str(original_receipt), "sha256": sha(original_receipt), "record": record}
    executable = shutil.which("pdftotext")
    if executable is None:
        raise ValueError("pdftotext is not installed")
    output.mkdir(parents=True, exist_ok=False)
    expected = scope.primary_identity
    if expected.pmid:
        metadata_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urlencode(
            {"db": "pubmed", "id": expected.pmid, "retmode": "xml"})
        suffix = ".xml"
    else:
        metadata_url = "https://api.crossref.org/works/" + quote(_doi(expected.doi), safe="/")
        suffix = ".json"
    cached_pins = {}
    if bibliographic_receipt is not None:
        cached_receipt = Path(bibliographic_receipt).resolve()
        cached_record = json.loads(cached_receipt.read_bytes())
        cached_original = Path(cached_record["path"]).resolve()
        if (cached_record.get("status", "downloaded") != "downloaded"
                or cached_record.get("requested_url", "").casefold() != metadata_url.casefold()
                or sha(cached_original) != cached_record["sha256"]):
            raise ValueError("Cached bibliography does not match the exact host receipt")
        cached_pins = {str(cached_receipt): sha(cached_receipt),
                       str(cached_original): cached_record["sha256"]}
        fetched = Download(cached_original.read_bytes(), cached_record["final_url"],
                           cached_record["content_type"])
        retrieval = {"retriever": "cached host retrieval replay; no HTTP request in this registration",
                     "cached_receipt": str(cached_receipt), "cached_receipt_sha256": sha(cached_receipt),
                     "original_retrieval": cached_record}
    else:
        try:
            fetched = fetch(metadata_url)
        except Exception as exc:
            _write(output / "retrieval_failure.json", {"requested_url": metadata_url,
                   "error": type(exc).__name__ + ": " + str(exc)})
            raise
        retrieval = {"retriever": "source_acquisition.downloads.download; anonymous HTTPS; no TLS bypass"}
    if not isinstance(fetched, Download) or not isinstance(fetched.content, bytes):
        raise ValueError("Metadata downloader must return host bytes and response metadata")
    if not fetched.content or len(fetched.content) > MAX_BYTES:
        raise ValueError("Empty or oversized metadata response")
    metadata_path = output / ("bibliographic_original" + suffix)
    with metadata_path.open("xb") as handle:
        handle.write(fetched.content)
    _write(output / "bibliographic_retrieval.json", {
        "status": "downloaded",
        "requested_url": metadata_url, "final_url": fetched.final_url,
        "content_type": fetched.content_type, "path": str(metadata_path), "sha256": sha(metadata_path),
        **retrieval})
    identity = bibliography(fetched.content, fetched.final_url, expected)
    if acquisition_binding:
        declared = acquisition_binding["record"].get("identity", {})
        for kind in ("doi", "pmid", "title"):
            value = declared.get(kind)
            normal = _doi if kind == "doi" else _title if kind == "title" else str
            if value and (not identity.get(kind) or normal(value) != normal(identity[kind])):
                raise ValueError("Acquisition-declared identity conflicts with authoritative " + kind)
    command = [executable, "-enc", "UTF-8", "-eol", "unix", str(original), "-"]
    converted = subprocess.run(command, check=True, capture_output=True, timeout=120).stdout.decode("utf-8")
    check = verify_pdf_text(converted, identity) if identity_evidence is None else None
    if sha(original) != original_sha256:
        raise ValueError("Original changed during conversion")
    if not converted.strip():
        raise ValueError("PDF text is empty; inspected OCR would be required")
    with (output / "full_pdf_text.txt").open("x", encoding="utf-8") as handle:
        handle.write(converted)
    selected, prefix, suffix, selection = select_article_text(converted, selection)
    identity_binding = None
    if identity_evidence is not None:
        identity_path = Path(identity_evidence).resolve()
        identity_record = json.loads(identity_path.read_bytes())
        identity_binding = {"path": str(identity_path), "sha256": sha(identity_path),
                            "record": identity_record}
        check = verify_selected_pdf_text(converted, identity, selection, identity_record,
                                         original_sha256=original_sha256)
    inspection_path = Path(selection["inspection_report"]["path"]).resolve()
    inspection_pins = {str(inspection_path): selection["inspection_report"]["sha256"]}
    if sha(inspection_path) != inspection_pins[str(inspection_path)]:
        raise ValueError("Article inspection report changed")
    inspection = json.loads(inspection_path.read_bytes())
    decision = {k:v for k,v in selection.items() if k not in {"inspection_report", "start_char", "end_char"}}
    if (inspection.get("method_id") != scope.method_id or inspection.get("original_sha256") != original_sha256
            or inspection.get("selection") != decision):
        raise ValueError("Inspection report does not bind this article and exact selection")
    inspected_files = inspection.get("files")
    if not isinstance(inspected_files, dict) or not inspected_files:
        raise ValueError("Article inspection report must pin its inspected artifacts")
    for filename, expected_sha in inspected_files.items():
        path = Path(filename)
        path = (inspection_path.parent / path).resolve() if not path.is_absolute() else path.resolve()
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or sha(path) != expected_sha:
            raise ValueError("Inspected article artifact differs from its bound bytes")
        inspection_pins[str(path)] = expected_sha
    for name, excluded in (("excluded_prefix.txt", prefix), ("excluded_suffix.txt", suffix)):
        with (output / name).open("x", encoding="utf-8") as handle:
            handle.write(excluded)
    _write(output / "article_selection.json", selection)
    rendition = output / "primary.txt"
    limits = ("Poppler default reading order; no OCR. Page breaks retained. Images and spatial figure/table "
              "content are not rendered; columns, formulas and superscripts may be flattened or reordered. "
              "Inspect the pinned binary PDF for layout-dependent claims. No scientific completeness established.")
    _publish_rendition(rendition, selected, original, original_sha256, " ".join(command[:-2]), limits)
    source = {"source_id": "primary_pdf_" + original_sha256[:12], "title": identity["title"],
              "url": public_url, "role": "primary_paper", "path": str(rendition)}
    options = {k: getattr(scope, k) for k in ("method_id", "title", "scope", "variant")}
    packet = build_packet(**options, sources=[source])
    _write(output / "packet.json", packet)
    snapshots = output / "implementation_snapshot"
    snapshots.mkdir()
    implementation = []
    for source_path in (helper_path, Path(cached_documents.__file__).resolve()):
        snapshot = snapshots / source_path.name
        with snapshot.open("xb") as handle:
            handle.write(source_path.read_bytes())
        implementation.append({"original_path": str(source_path), "sha256": sha(snapshot),
                               "snapshot": str(snapshot)})
    if sha(helper_path) != helper_sha or sha(snapshots / helper_path.name) != helper_sha:
        raise ValueError("Registration helper changed during conversion")
    receipt = {"status": "registered_for_fresh_source_review", "scientific_resolution": False,
               "identity": identity, "first_pages_check": check, "public_url": public_url,
               "original": str(original), "original_sha256": original_sha256,
               "rendition": str(rendition), "rendition_sha256": sha(rendition),
               "metadata": str(metadata_path), "metadata_sha256": sha(metadata_path),
               "acquisition_binding": acquisition_binding,
               "original_download_binding": original_binding,
               "article_selection": selection,
               "identity_boundary": "Early-page title/identifier matching establishes containment; article applicability is the separate explicit selection decision, not guaranteed by substring matching.",
               "implementation_snapshot": implementation,
               "command": command, "converter_version": subprocess.run(
                   [executable, "-v"], capture_output=True, text=True, check=True).stderr.strip(),
               "limits": limits, "scope": scope.model_dump(mode="json"),
               "unregistered_dependencies": "No supplements, cited protocols or adjacent files automatically registered."}
    if identity_binding is not None:
        receipt["identity_evidence"] = identity_binding
        receipt["identity_check"] = receipt.pop("first_pages_check")
        receipt["identity_boundary"] = (
            "Exact title/DOI/context witnesses within an explicitly inspected article selection, "
            "bound to independent bibliography. Identifier context is an inspected interpretation, "
            "not an automatic purity or scientific-completeness guarantee.")
    _write(output / "registration.json", receipt)
    files = [original, metadata_path, output / "bibliographic_retrieval.json", rendition,
             output / "packet.json", output / "registration.json",
             output / "full_pdf_text.txt", output / "excluded_prefix.txt", output / "excluded_suffix.txt",
             output / "article_selection.json",
             *(Path(item["snapshot"]) for item in implementation)]
    if identity_binding is not None:
        if sha(identity_binding["path"]) != identity_binding["sha256"]:
            raise ValueError("Inspected publication-identity evidence changed during registration")
        files.append(Path(identity_binding["path"]))
    if acquisition_record is not None:
        if sha(acquisition_record) != acquisition_binding["sha256"]:
            raise ValueError("Acquisition record changed during registration")
        files.append(acquisition_record)
    if original_binding:
        if sha(original_receipt) != original_binding["sha256"]:
            raise ValueError("Original host download receipt changed during registration")
        files.append(original_receipt)
    pins = {str(p): sha(p) for p in files}
    for filename, expected_sha in cached_pins.items():
        if sha(filename) != expected_sha:
            raise ValueError("Cached bibliography input changed during registration")
        pins[filename] = expected_sha
    for filename, expected_sha in inspection_pins.items():
        if sha(filename) != expected_sha:
            raise ValueError("Article inspection inputs changed during registration")
        if filename in pins and pins[filename] != expected_sha:
            raise ValueError("Conflicting article inspection pin")
        pins[filename] = expected_sha
    if pins[str(original)] != original_sha256:
        raise ValueError("Original changed before registration completed")
    _write(output / "source_receipts.json", {"files": pins})
    load_packet(output / "packet.json")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scopes", type=Path, required=True, help="Explicit method-ID to Scope JSON mapping")
    parser.add_argument("--method", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New directory only")
    parser.add_argument("--acquisition", type=Path, help="Existing acquisition.json; original remains in place")
    parser.add_argument("--document", help="Exact primary PDF document ID in acquisition.json")
    parser.add_argument("--original", type=Path)
    parser.add_argument("--original-sha256")
    parser.add_argument("--public-url")
    parser.add_argument("--original-receipt", type=Path, help="Actual host receipt for direct-original input")
    parser.add_argument("--selection", type=Path, required=True,
                        help="Inspected whole-document decision or exact unique start/end text selectors")
    parser.add_argument("--bibliography-receipt", type=Path,
                        help="Explicit replay of pinned, independently host-retrieved metadata")
    parser.add_argument("--identity-evidence", type=Path,
                        help="Explicit bound title/DOI/context inspection for an identifier outside the early-page default")
    args = parser.parse_args(argv)
    scope = Scope.model_validate(json.loads(args.scopes.read_bytes())[args.method])
    if scope.method_id != args.method:
        parser.error("Scope must preserve the selected method ID")
    if args.acquisition:
        if not args.document or args.original or args.original_sha256 or args.public_url or args.original_receipt:
            parser.error("Use --acquisition with --document, without direct original arguments")
        acquisition = json.loads(args.acquisition.read_bytes())
        if acquisition.get("method_id") != args.method:
            parser.error("Acquisition belongs to another method")
        records = [r for r in acquisition["documents"] if r.get("id") == args.document]
        if len(records) != 1 or records[0].get("role") != "primary_paper" or records[0].get("format") != "pdf":
            parser.error("Choose exactly one primary PDF document")
        row = records[0]
        original, expected_sha, url = row["path"], row["original_sha256"], row["final_url"]
    else:
        if not (args.original and args.original_sha256 and args.public_url and args.original_receipt) or args.document:
            parser.error("Supply --original, --original-sha256, --public-url and --original-receipt, or an acquisition document")
        original, expected_sha, url = args.original, args.original_sha256, args.public_url
    result = register_pdf(scope, original, expected_sha, url, args.output,
                          acquisition_record=args.acquisition, document_id=args.document,
                          bibliographic_receipt=args.bibliography_receipt, original_receipt=args.original_receipt,
                          selection=json.loads(args.selection.read_bytes()), identity_evidence=args.identity_evidence)
    print(json.dumps({"output": str(args.output.resolve()), "status": result["status"],
                      "scientific_resolution": False}))


if __name__ == "__main__":
    main()
