"""Discontinuous source selection uses synthetic bytes and no real conversions."""

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from genomics_discovery.curation.source_acquisition import pdf_registration as pdf
from test_pdf_registration import DOI, TITLE, fetched, register, scope

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=")


def document():
    return ("Foreign leading article.\nTarget segment one begins.\n" + "Target lead-in text.\n" * 180
        + "End of target segment one.\nFOREIGN INTERIOR ARTICLE CONTENT\n" + TITLE
        + "\nActual target body text.\nEnd of target segment two.\fTarget segment three begins.\n"
        + "Accepted original publication record\n" + DOI + "\nEnd of target article.\nForeign following article.")


def decision(text, report=None):
    return {"kind": "exact_text_regions", "basis": "Explicit inspection separates these three target regions from foreign shared-page content.",
        "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(), "regions": [
            {"start": "Target segment one begins.", "end": "End of target segment one."},
            {"start": TITLE, "end": "End of target segment two."},
            {"start": "Target segment three begins.", "end": "End of target article."}],
        "inspection_report": report or {"path": "inspection.json", "sha256": "b" * 64}}


def evidence(text, selection, *, original_sha="a" * 64, image_sha="c" * 64):
    def witness(quote):
        start = text.index(quote)
        return {"start_char": start, "end_char": start + len(quote), "quote": quote,
            "page": text[:start].count("\f") + 1}
    return {"format_version": "selected_article_identity/1.1", "original_sha256": original_sha,
        "inspection_report": selection["inspection_report"], "title_witness": witness(TITLE),
        "identifier_witness": witness(DOI), "identifier_context": witness("Accepted original publication record\n" + DOI),
        "identifier_role": "article_publication_identifier", "title_role": "visually_inspected_article_header",
        "title_page_image_sha256": image_sha,
        "basis": "An explicit source-preparer assertion about the article header and closing publication DOI context."}


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    text = document()
    original = tmp_path / "original.pdf"
    original.write_bytes(b"%PDF-1.4 synthetic fixture never parsed by a converter")
    text_path = tmp_path / "full_text.txt"; text_path.write_text(text)
    pages, inspections, files = [], [], {str(original): pdf.sha(original), str(text_path): pdf.sha(text_path)}
    for number, content in enumerate(text.split("\f"), 1):
        image = tmp_path / f"page-{number}.png"; image.write_bytes(PNG)
        files[str(image)] = pdf.sha(image)
        pages.append({"page": number, "image": str(image), "image_sha256": pdf.sha(image), "text": content})
        inspections.append({"page": number, "image_sha256": pdf.sha(image), "relationship": "mixed_articles",
            "reading_order": "interleaved", "observations": "Synthetic rendered-page binding; not a visual accuracy claim."})
    render_path = tmp_path / "render_manifest.json"
    render_path.write_text(json.dumps({"original": str(original), "original_sha256": pdf.sha(original), "pages": pages}))
    files[str(render_path)] = pdf.sha(render_path)
    selection = decision(text)
    report = {"format_version": "article_content_inspection/1.1", "method_id": "example",
        "original_sha256": pdf.sha(original), "decision": "select", "selected_reading_order": "coherent",
        "selection": {key: value for key, value in selection.items() if key != "inspection_report"},
        "source_text": {"path": str(text_path), "sha256": pdf.sha(text_path)},
        "render_manifest": {"path": str(render_path), "sha256": pdf.sha(render_path)}, "pages": inspections, "files": files}
    report_path = tmp_path / "inspection.json"
    identity_path = tmp_path / "identity.json"
    def seal():
        report_path.write_text(json.dumps(report))
        selection["inspection_report"] = {"path": str(report_path), "sha256": pdf.sha(report_path)}
        identity = evidence(text, selection, original_sha=pdf.sha(original), image_sha=pages[0]["image_sha256"])
        identity_path.write_text(json.dumps(identity))
        return identity
    identity = seal()
    def no_process(command, **kwargs):
        return SimpleNamespace(stdout=text.encode(), stderr="Synthetic Poppler fixture", returncode=0)
    monkeypatch.setattr(pdf.shutil, "which", lambda _name: "/fixture/pdftotext")
    monkeypatch.setattr(pdf.subprocess, "run", no_process)
    return SimpleNamespace(text=text, original=original, text_path=text_path, pages=pages, report=report,
        selection=selection, identity=identity, identity_path=identity_path, report_path=report_path,
        render_path=render_path, seal=seal, output=tmp_path / "registration")


def test_regions_preserve_original_offsets_and_exact_complete_complement():
    text = document(); selected, prefix, suffix, saved = pdf.select_article_text(text, decision(text))
    assert "FOREIGN INTERIOR" not in selected
    assert prefix == "Foreign leading article.\n" and "Foreign following" in suffix
    assert pdf.selection_decision(saved) == decision(text)
    for row in saved["regions"]:
        assert selected[row["selected_start_char"]:row["selected_end_char"]] == text[row["start_char"]:row["end_char"]]
    artifacts = pdf.selected_region_artifacts(text, saved)
    spans = [(row["start_char"], row["end_char"], text[row["start_char"]:row["end_char"]]) for row in saved["regions"]]
    spans += [(row["start_char"], row["end_char"], row["text"]) for row in artifacts["excluded_regions.json"]["regions"]]
    assert "".join(row[2] for row in sorted(spans)) == text
    assert artifacts["source_text_map.json"]["selected_text_sha256"] == hashlib.sha256(selected.encode()).hexdigest()


def test_header_after_3000_characters_requires_explicit_source_page_inspection():
    text = document(); selection = decision(text)
    assert text.index(TITLE) - text.index(selection["regions"][0]["start"]) > 3000
    with pytest.raises(ValueError):
        pdf.verify_pdf_text(text, {"title": TITLE, "doi": DOI})
    proof = pdf.verify_selected_pdf_text(text, {"title": TITLE, "doi": DOI}, selection, evidence(text, selection), original_sha256="a" * 64)
    assert proof["title_witness"]["page"] == 1 and proof["identifier_witness"]["page"] == 2


@pytest.mark.parametrize("problem", ["overlap", "reverse", "duplicate", "ambiguous", "source_hash", "empty"])
def test_unsafe_region_decisions_fail_closed(problem):
    text = document(); selection = decision(text)
    if problem == "overlap": selection["regions"][0]["end"] = "End of target segment two."
    elif problem == "reverse": selection["regions"].reverse()
    elif problem == "duplicate": selection["regions"].append(deepcopy(selection["regions"][-1]))
    elif problem == "ambiguous": text += "\nTarget segment one begins."
    elif problem == "source_hash": selection["source_text_sha256"] = "0" * 64
    else: selection["regions"] = []
    if problem == "ambiguous": selection["source_text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    with pytest.raises(ValueError): pdf.select_article_text(text, selection)


@pytest.mark.parametrize("problem", ["excluded_title", "excluded_doi", "spanning_context", "wrong_page", "later_title_page", "role"])
def test_identity_cannot_borrow_excluded_text_or_a_later_source_page(problem):
    text = document()
    if problem == "later_title_page": text = text.replace(TITLE, "\f" + TITLE)
    selection = decision(text); identity = evidence(text, selection)
    if problem == "excluded_title": selection["regions"].pop(1)
    elif problem == "excluded_doi": selection["regions"].pop()
    elif problem == "spanning_context":
        item = identity["identifier_context"]; item["start_char"] = text.index("End of target segment two.")
        item["quote"] = text[item["start_char"]:item["end_char"]]; item["page"] = 1
    elif problem == "wrong_page": identity["title_witness"]["page"] = 2
    elif problem == "role": identity["title_role"] = "citation_in_other_article"
    with pytest.raises(ValueError):
        pdf.verify_selected_pdf_text(text, {"title": TITLE, "doi": DOI}, selection, identity, original_sha256="a" * 64)


def test_registration_pins_all_regions_exclusions_text_and_rendered_pages(prepared):
    f = prepared
    result = register(scope(), f.original, pdf.sha(f.original), "https://author.example.org/compound.pdf", f.output,
        fetch=fetched, selection=f.selection, identity_evidence=f.identity_path)
    assert result["article_selection"]["kind"] == "exact_text_regions"
    body = (f.output / "primary.txt").read_text()
    assert "Actual target body" in body and "FOREIGN INTERIOR" not in body and "Foreign following" not in body
    artifacts = pdf.selected_region_artifacts(f.text, result["article_selection"])
    for name, value in artifacts.items(): assert json.loads((f.output / name).read_bytes()) == value
    pins = json.loads((f.output / "source_receipts.json").read_bytes())["files"]
    assert all(pins[row["image"]] == row["image_sha256"] for row in f.pages)
    assert all(pdf.sha(path) == expected for path, expected in pins.items())


@pytest.mark.parametrize("problem", ["inspection_dropout", "region_dropout", "render_dropout", "wrong_source_text", "wrong_image", "image_unpinned", "header_image"])
def test_inspection_dropouts_or_rebound_artifacts_cannot_publish(prepared, problem):
    f = prepared
    if problem == "inspection_dropout": f.report["pages"].pop()
    elif problem == "region_dropout": f.selection["regions"] = f.selection["regions"][1:]
    elif problem == "render_dropout":
        render = json.loads(f.render_path.read_bytes()); render["pages"].pop()
        f.render_path.write_text(json.dumps(render))
        f.report["render_manifest"]["sha256"] = pdf.sha(f.render_path)
        f.report["files"][str(f.render_path)] = pdf.sha(f.render_path)
    elif problem == "wrong_source_text": f.report["source_text"]["sha256"] = "0" * 64
    elif problem == "wrong_image": f.report["pages"][0]["image_sha256"] = "0" * 64
    elif problem == "image_unpinned": f.report["files"].pop(f.pages[0]["image"])
    identity = f.seal()
    if problem == "header_image":
        identity["title_page_image_sha256"] = "0" * 64
        f.identity_path.write_text(json.dumps(identity))
    with pytest.raises(ValueError):
        register(scope(), f.original, pdf.sha(f.original), "https://author.example.org/compound.pdf", f.output,
            fetch=fetched, selection=f.selection, identity_evidence=f.identity_path)
    assert not (f.output / "packet.json").exists()
