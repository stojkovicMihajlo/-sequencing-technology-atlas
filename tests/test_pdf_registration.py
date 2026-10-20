"""Conservative identity and pinned-original post-acquisition PDF registration."""

import json
from pathlib import Path
import shutil

import pytest

from genomics_discovery.curation.source_acquisition import pdf_registration as pdf
from genomics_discovery.curation.source_acquisition.downloads import Download
from genomics_discovery.curation.source_acquisition.models import Identity


TITLE = "A primary experimental paper"
DOI = "10.1234/example.567"


def metadata(doi=DOI, title=TITLE):
    return json.dumps({"status": "ok", "message-type": "work",
                       "message": {"DOI": doi, "title": [title]}}).encode()


def scope():
    return {"method_id": "example", "title": "Example assay", "scope": "Original paper implementation",
            "variant": "original", "primary_identity": {"doi": DOI, "title": TITLE}}


def make_pdf(path, text):
    # Real single-page PDF; exercise Poppler rather than mirror its implementation.
    lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in text.splitlines()]
    stream = ("BT /F1 12 Tf 72 720 Td " + " 0 -18 Td ".join("(" + line + ") Tj" for line in lines) + " ET").encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data = b"%PDF-1.4\n"; offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data)); data += str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    data += b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n" + str(start).encode() + b"\n%%EOF\n"
    path.write_bytes(data)


def fetched(_url):
    return Download(metadata(), "https://api.crossref.org/works/" + DOI, "application/json")


def register(*args, **kwargs):
    kwargs.setdefault("selection", {"kind": "whole_document", "basis": "Inspected generated single-page test fixture; all text is within this test document."})
    selection = kwargs.get("selection")
    if selection is not None and "inspection_report" not in selection:
        original = Path(args[1]); report = original.parent / (Path(args[4]).name + "_inspection.json")
        report.write_text(json.dumps({"method_id": args[0]["method_id"], "original_sha256": pdf.sha(original),
            "selection": selection, "files": {str(original): pdf.sha(original)}}))
        kwargs["selection"] = {**selection, "inspection_report": {"path": str(report), "sha256": pdf.sha(report)}}
    if kwargs.get("acquisition_record") is None and "original_receipt" not in kwargs:
        original = Path(args[1])
        receipt = original.parent / "actual_host_receipt.json"
        if not receipt.exists():
            receipt.write_text(json.dumps({"status": "downloaded", "path": str(original), "sha256": pdf.sha(original), "final_url": args[3], "retriever": "test host response fixture"}))
        kwargs["original_receipt"] = receipt
    return pdf.register_pdf(*args, **kwargs)


def test_authoritative_metadata_checks_identity():
    expected = Identity(doi="https://doi.org/" + DOI.upper(), title=TITLE)
    assert pdf.bibliography(metadata(), "https://api.crossref.org/works/x", expected)["doi"] == DOI


def test_crossref_publisher_title_markup_is_visible_text():
    title = "EpiRADseq: scalable methylation analysis"
    raw = metadata(title="Epi<scp>RAD</scp>seq: scalable methylation analysis")
    actual = pdf.bibliography(raw, "https://api.crossref.org/works/x", Identity(doi=DOI, title=title))
    assert actual["title"] == title


@pytest.mark.parametrize("raw,url,error", [
    (metadata(doi="10.1234/wrong"), "https://api.crossref.org/works/x", "DOI mismatch"),
    (metadata(title="An unrelated experimental paper"), "https://api.crossref.org/works/x", "title mismatch"),
    (metadata(), "https://example.org/works/x", "authoritative host"),
    (metadata(), "http://api.crossref.org/works/x", "verified HTTPS"),
])
def test_reject_wrong_metadata(raw, url, error):
    with pytest.raises(ValueError, match=error):
        pdf.bibliography(raw, url, Identity(doi=DOI, title=TITLE))


def test_pubmed_host_record_binds_doi_and_pmid():
    raw = (f'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>{TITLE}</ArticleTitle>'
           f'</Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">{DOI}</ArticleId>'
           '</ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>').encode()
    identity = pdf.bibliography(raw, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", Identity(pmid="123", doi=DOI))
    assert identity == {"title": TITLE, "doi": DOI, "pmid": "123"}
    with pytest.raises(ValueError, match="PMID mismatch"):
        pdf.bibliography(raw, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", Identity(pmid="456"))


@pytest.mark.parametrize("text", [
    TITLE + "\nNo identifier here", "A different paper\n" + DOI,
    TITLE + "\nReferences\nSomeone. doi: " + DOI,
    TITLE + "\n" + DOI + "another", TITLE + "\n" + DOI + "/extension",
    TITLE + "\f" + DOI,
])
def test_title_only_reference_identifier_or_different_page_rejected(text):
    with pytest.raises(ValueError, match="First two PDF pages|Conflicting early-page DOI"):
        pdf.verify_pdf_text(text, {"title": TITLE, "doi": DOI})


def test_full_title_and_printed_identifier():
    assert pdf.verify_pdf_text(TITLE + "\ndoi: " + DOI, {"title": TITLE, "doi": DOI})["pdf_page"] == 1
    assert pdf.verify_pdf_text("Cover\f" + TITLE + "\nPMID: 123", {"title": TITLE, "pmid": "123"})["pdf_page"] == 2


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler unavailable")
def test_original_rendition_packet_and_receipts_roundtrip(tmp_path):
    original = tmp_path / "article.pdf"; make_pdf(original, TITLE + "\ndoi: " + DOI + "\nMethods\nOriginal procedure text.")
    original_bytes = original.read_bytes(); expected = pdf.sha(original)
    row = {"id": "primary", "role": "primary_paper", "format": "pdf", "path": str(original),
           "original_sha256": expected, "final_url": "https://author.example.org/paper.pdf",
           "identity": {"doi": DOI, "title": TITLE}}
    acquisition = tmp_path / "acquisition.json"
    acquisition.write_text(json.dumps({"method_id": "example", "documents": [row]}))
    first, second = tmp_path / "first", tmp_path / "second"
    result = register(scope(), original, expected, row["final_url"], first,
                              fetch=fetched, acquisition_record=acquisition, document_id="primary")
    register(scope(), original, expected, row["final_url"], second, fetch=fetched)
    assert result["scientific_resolution"] is False
    assert result["acquisition_binding"]["record"] == row
    assert original.read_bytes() == original_bytes
    assert (first / "primary.txt").read_bytes() == (second / "primary.txt").read_bytes()
    assert len(pdf.load_packet(first / "packet.json")["sources"]) == 1
    pins = json.loads((first / "source_receipts.json").read_bytes())["files"]
    assert pins[str(acquisition)] == pdf.sha(acquisition)
    assert pins[str(original)] == expected
    assert str(Path(pdf.__file__).resolve()) not in pins
    assert any("implementation_snapshot/pdf_registration.py" in path for path in pins)
    assert all(pdf.sha(Path(path)) == value for path, value in pins.items())
    with pytest.raises(FileExistsError):
        register(scope(), original, expected, row["final_url"], first, fetch=fetched)


def test_modified_original_rejected_before_output(tmp_path):
    original = tmp_path / "article.pdf"; make_pdf(original, TITLE + "\n" + DOI)
    expected = pdf.sha(original); original.write_bytes(original.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="differ from the acquisition binding"):
        register(scope(), original, expected, "https://author.example.org/p.pdf", tmp_path / "output", fetch=fetched)
    assert not (tmp_path / "output").exists()


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler unavailable")
def test_uncertain_pdf_never_publishes_packet(tmp_path):
    original = tmp_path / "article.pdf"; make_pdf(original, TITLE + "\nReferences\n" + DOI)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="First two PDF pages|Conflicting early-page DOI"):
        register(scope(), original, pdf.sha(original), "https://author.example.org/p.pdf", output, fetch=fetched)
    assert (output / "bibliographic_original.json").exists()
    assert not (output / "packet.json").exists()


def test_acquisition_document_ownership_spoof_rejected(tmp_path):
    original = tmp_path / "article.pdf"; make_pdf(original, TITLE + "\n" + DOI)
    acquisition = tmp_path / "acquisition.json"
    acquisition.write_text(json.dumps({"method_id": "other", "documents": []}))
    with pytest.raises(ValueError, match="another method"):
        register(scope(), original, pdf.sha(original), "https://author.example.org/p.pdf", tmp_path / "output",
                          fetch=fetched, acquisition_record=acquisition, document_id="primary")


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler unavailable")
def test_cached_host_metadata_replay_is_explicit_and_pinned(tmp_path):
    original = tmp_path / "article.pdf"; make_pdf(original, TITLE + "\n" + DOI)
    raw = tmp_path / "host_metadata.json"; raw.write_bytes(metadata())
    receipt = tmp_path / "host_receipt.json"
    receipt.write_text(json.dumps({"status": "downloaded", "requested_url": "https://api.crossref.org/works/" + DOI,
        "final_url": "https://api.crossref.org/works/" + DOI, "path": str(raw),
        "sha256": pdf.sha(raw), "content_type": "application/json", "retriever": "recorded original host retrieval"}))
    def forbidden_fetch(url):
        pytest.fail("Cached replay must not make another request")
    output = tmp_path / "registration"
    register(scope(), original, pdf.sha(original), "https://author.example.org/p.pdf", output,
                      fetch=forbidden_fetch, bibliographic_receipt=receipt)
    recorded = json.loads((output / "bibliographic_retrieval.json").read_bytes())
    assert "no HTTP request" in recorded["retriever"]
    pins = json.loads((output / "source_receipts.json").read_bytes())["files"]
    assert pins[str(raw)] == pdf.sha(raw) and pins[str(receipt)] == pdf.sha(receipt)
    raw.write_bytes(b"changed")
    with pytest.raises(ValueError, match="exact host receipt"):
        register(scope(), original, pdf.sha(original), "https://author.example.org/p.pdf", tmp_path / "rejected",
                          fetch=forbidden_fetch, bibliographic_receipt=receipt)


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler unavailable")
def test_cli_replays_exact_acquisition_document_without_finder(tmp_path, capsys):
    original = tmp_path / "article.pdf"; make_pdf(original, TITLE + "\n" + DOI)
    row = {"id": "selected", "role": "primary_paper", "format": "pdf", "path": str(original),
           "original_sha256": pdf.sha(original), "final_url": "https://author.example.org/p.pdf", "identity": {"doi": DOI}}
    acquisition = tmp_path / "acquisition.json"
    acquisition.write_text(json.dumps({"method_id": "example", "documents": [row]}))
    scopes = tmp_path / "scopes.json"; scopes.write_text(json.dumps({"example": scope()}))
    raw = tmp_path / "metadata.json"; raw.write_bytes(metadata())
    host = tmp_path / "receipt.json"
    host.write_text(json.dumps({"requested_url": "https://api.crossref.org/works/" + DOI,
        "final_url": "https://api.crossref.org/works/" + DOI, "path": str(raw),
        "sha256": pdf.sha(raw), "content_type": "application/json"}))
    output = tmp_path / "registered"
    selection = tmp_path / "selection.json"
    decision = {"kind": "whole_document", "basis": "Inspected generated single-page test fixture: no adjacent articles."}
    inspection = tmp_path / "inspection.json"
    inspection.write_text(json.dumps({"method_id": "example", "original_sha256": pdf.sha(original), "selection": decision,
        "files": {str(original): pdf.sha(original)}}))
    selection.write_text(json.dumps({**decision, "inspection_report": {"path": str(inspection), "sha256": pdf.sha(inspection)}}))
    pdf.main(["--scopes", str(scopes), "--method", "example", "--acquisition", str(acquisition),
              "--document", "selected", "--selection", str(selection), "--bibliography-receipt", str(host), "--output", str(output)])
    assert json.loads(capsys.readouterr().out)["scientific_resolution"] is False
    assert pdf.load_packet(output / "packet.json")["method_id"] == "example"
    with pytest.raises(SystemExit):
        pdf.main(["--scopes", str(scopes), "--method", "example", "--acquisition", str(acquisition),
                  "--document", "missing", "--selection", str(selection), "--bibliography-receipt", str(host), "--output", str(tmp_path / "bad")])
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("text", [
    "Correction: " + TITLE + "\nCorrection DOI:10.9999/wrong\nOriginal publication doi:" + DOI,
    "Different paper\nDOI:10.9999/wrong\nReferences and Notes\n1." + TITLE + ". doi:" + DOI,
    TITLE + "\nDOI:10.9999/wrong\nOriginal publication doi:" + DOI,
])
def test_correction_citing_article_and_conflicting_doi_are_not_original_identity(text):
    with pytest.raises(ValueError):
        pdf.verify_pdf_text(text, {"title": TITLE, "doi": DOI})


def test_article_selectors_are_unique_exact_and_preserve_exclusions():
    text = "Prior article.\nUnique target start.\nTarget body.\nUnique target end.\nFollowing article."
    selection = {"kind": "exact_text_bounds", "basis": "Inspected article boundaries against the original PDF shared pages.",
                 "start": "Unique target start.", "end": "Unique target end.", "inspection_report": {"path": "inspection.json", "sha256": "0" * 64}}
    chosen, before, after, record = pdf.select_article_text(text, selection)
    assert before + chosen + after == text
    assert chosen == "Unique target start.\nTarget body.\nUnique target end."
    assert "Prior article" in before and "Following article" in after
    assert record["start_char"] == len(before)
    with pytest.raises(ValueError, match="exact and unique"):
        pdf.select_article_text(text + "\nUnique target start.", selection)
    with pytest.raises(ValueError, match="inverted"):
        pdf.select_article_text(text, {**selection, "start": selection["end"], "end": selection["start"]})
    with pytest.raises(ValueError, match="selection with a basis"):
        pdf.select_article_text(text, None)


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler unavailable")
def test_early_identity_does_not_automatically_register_entire_pdf(tmp_path):
    original = tmp_path / "bundle.pdf"; make_pdf(original, TITLE + "\n" + DOI + "\nAn adjacent article.")
    output = tmp_path / "unselected"
    with pytest.raises(ValueError, match="selection with a basis"):
        register(scope(), original, pdf.sha(original), "https://author.example.org/p.pdf", output,
                 fetch=fetched, selection=None)
    assert (output / "full_pdf_text.txt").exists()
    assert not (output / "packet.json").exists()


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler unavailable")
def test_explicit_bounds_exclude_adjacent_articles_from_packet(tmp_path):
    original = tmp_path / "bundle.pdf"
    make_pdf(original, "Prior article content.\n" + TITLE + "\n" + DOI + "\nOriginal procedure.\nEnd of target paper.\nFollowing article content.")
    output = tmp_path / "selected"
    selection = {"kind": "exact_text_bounds", "basis": "Inspected target title through end marker on this generated shared page.",
                 "start": TITLE, "end": "End of target paper."}
    register(scope(), original, pdf.sha(original), "https://author.example.org/p.pdf", output,
             fetch=fetched, selection=selection)
    packet = pdf.load_packet(output / "packet.json")
    text = " ".join(p["text"] for p in packet["sources"][0]["passages"])
    assert "Original procedure" in text
    assert "Prior article content" not in text and "Following article content" not in text
    assert "Prior article content" in (output / "excluded_prefix.txt").read_text()
    assert "Following article content" in (output / "excluded_suffix.txt").read_text()


def test_direct_original_requires_actual_download_receipt(tmp_path):
    original = tmp_path / "article.pdf"; make_pdf(original, TITLE + "\n" + DOI)
    with pytest.raises(ValueError, match="actual host download receipt"):
        pdf.register_pdf(scope(), original, pdf.sha(original), "https://author.example.org/p.pdf", tmp_path / "output", fetch=fetched)


def test_whole_document_assertion_without_inspection_report_rejected():
    with pytest.raises(ValueError, match="genuine inspection report"):
        pdf.select_article_text(TITLE, {"kind": "whole_document", "basis": "A bare claim that the entire file is the desired article."})


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Poppler unavailable")
@pytest.mark.parametrize("problem", ["wrong_article", "changed_inspected_artifact"])
def test_whole_document_inspection_is_bound_to_article_and_evidence(tmp_path, problem):
    original = tmp_path / "article.pdf"; make_pdf(original, TITLE + "\n" + DOI)
    evidence = tmp_path / "visual_inspection.txt"; evidence.write_text("Inspected complete generated test page.")
    decision = {"kind": "whole_document", "basis": "Inspected the complete generated fixture against its rendered page."}
    doc = {"method_id": "example", "original_sha256": pdf.sha(original), "selection": decision,
           "files": {str(original): pdf.sha(original), str(evidence): pdf.sha(evidence)}}
    if problem == "wrong_article":
        doc["original_sha256"] = "0" * 64
    else:
        evidence.write_text("Changed after inspection.")
    report = tmp_path / "inspection.json"; report.write_text(json.dumps(doc))
    selection = {**decision, "inspection_report": {"path": str(report), "sha256": pdf.sha(report)}}
    with pytest.raises(ValueError, match="Inspection report does not bind|Inspected article artifact differs"):
        register(scope(), original, pdf.sha(original), "https://author.example.org/p.pdf", tmp_path / "output",
                 fetch=fetched, selection=selection)
    assert not (tmp_path / "output/packet.json").exists()
