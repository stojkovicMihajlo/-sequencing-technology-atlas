"""Inspected identity after an article's opening; default early guard stays strict."""

import copy
import json
import shutil
import subprocess

import pytest

from genomics_discovery.curation.source_acquisition import pdf_registration as pdf
from test_pdf_inspection import make_pdf
from test_pdf_registration import DOI, TITLE, fetched, register, scope


def inspection_data(text, original_sha="a" * 64, report=None):
    report = report or {"path": "inspection.json", "sha256": "b" * 64}
    selection = {"kind": "exact_text_bounds", "basis": "Inspected the target title through its closing publication identifier and final marker.",
                 "start": TITLE, "end": "End of target article.", "inspection_report": report}

    def witness(quote):
        begin = text.index(quote)
        return {"start_char": begin, "end_char": begin + len(quote), "quote": quote,
                "page": text[:begin].count("\f") + 1}

    identifier = witness(DOI)
    begin = text.rfind("Accepted", 0, identifier["start_char"])
    context = text[begin:identifier["end_char"]]
    evidence = {"format_version": "selected_article_identity/1.0", "original_sha256": original_sha,
                "inspection_report": report, "title_witness": witness(TITLE),
                "identifier_witness": identifier, "identifier_context": witness(context),
                "identifier_role": "article_publication_identifier",
                "basis": "Inspected the target article's closing publication information, separately from reference citations."}
    return selection, evidence


def text_fixture():
    return ("Previous article 10.9999/unrelated\n" + TITLE + "\nAbstract text.\f"
            "Methods and results.\fAccepted 1 May 2018\n" + DOI
            + "\nEnd of target article.\nFollowing article content.")


def test_late_identifier_is_explicitly_scoped_not_a_relaxed_early_default():
    text = text_fixture()
    with pytest.raises(ValueError):
        pdf.verify_pdf_text(text, {"title": TITLE, "doi": DOI})
    selection, evidence = inspection_data(text)
    result = pdf.verify_selected_pdf_text(text, {"title": TITLE, "doi": DOI}, selection,
                                           evidence, original_sha256="a" * 64)
    assert result["route"] == "inspected_selected_article"
    assert result["identifier_witness"]["page"] == 3
    selected, before, after, _ = pdf.select_article_text(text, selection)
    assert "Previous article" in before and "Following article" in after
    assert "Previous article" not in selected and "Following article" not in selected


@pytest.mark.parametrize("problem", ["original", "page", "quote", "title", "role", "inspection", "outside", "isolated"])
def test_inconsistent_or_unqualified_identity_witness_is_rejected(problem):
    text = text_fixture()
    selection, evidence = inspection_data(text)
    if problem == "original": evidence["original_sha256"] = "c" * 64
    elif problem == "page": evidence["identifier_witness"]["page"] = 1
    elif problem == "quote": evidence["identifier_context"]["quote"] += " invented"
    elif problem == "title": evidence["title_witness"]["quote"] = "Different title"
    elif problem == "role": evidence["identifier_role"] = "reference_citation"
    elif problem == "inspection": evidence["inspection_report"] = {"path": "other.json", "sha256": "d" * 64}
    elif problem == "outside": evidence["title_witness"]["start_char"] = 0
    elif problem == "isolated": evidence["identifier_context"] = copy.deepcopy(evidence["identifier_witness"])
    with pytest.raises(ValueError):
        pdf.verify_selected_pdf_text(text, {"title": TITLE, "doi": DOI}, selection,
                                     evidence, original_sha256="a" * 64)


def test_whole_document_and_correction_notice_are_not_admitted_by_the_new_route():
    text = text_fixture()
    selection, evidence = inspection_data(text)
    whole = {k: v for k, v in selection.items() if k not in {"start", "end"}}
    whole["kind"] = "whole_document"
    with pytest.raises(ValueError, match="exact article boundaries"):
        pdf.verify_selected_pdf_text(text, {"title": TITLE, "doi": DOI}, whole,
                                     evidence, original_sha256="a" * 64)
    text = text.replace(TITLE, "Correction: " + TITLE)
    selection, evidence = inspection_data(text)
    selection["start"] = "Correction: " + TITLE
    with pytest.raises(ValueError, match="Correction/retraction"):
        pdf.verify_selected_pdf_text(text, {"title": TITLE, "doi": DOI}, selection,
                                     evidence, original_sha256="a" * 64)


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="Real Poppler required")
def test_real_multi_page_registration_pins_inspected_late_identity_and_exclusions(tmp_path):
    original = tmp_path / "compound.pdf"
    make_pdf(original, ["Previous article 10.9999/unrelated\n" + TITLE + "\nAbstract text.",
                        "Methods and results.", "Accepted 1 May 2018\n" + DOI
                        + "\nEnd of target article.\nFollowing article content."])
    text = subprocess.run([shutil.which("pdftotext"), "-enc", "UTF-8", "-eol", "unix", str(original), "-"],
                          capture_output=True, check=True, text=True).stdout
    expected = pdf.sha(original)
    decision, _ = inspection_data(text, expected)
    decision.pop("inspection_report")
    inspection = tmp_path / "inspection.json"
    inspection.write_text(json.dumps({"method_id": "example", "original_sha256": expected,
        "selection": decision, "files": {str(original): expected},
        "authority": "Synthetic generated fixture inspection, no human/visual validation claim."}))
    selection, evidence = inspection_data(text, expected, {"path": str(inspection), "sha256": pdf.sha(inspection)})
    evidence_path = tmp_path / "identity_evidence.json"
    evidence_path.write_text(json.dumps(evidence))
    output = tmp_path / "registered"
    receipt = register(scope(), original, expected, "https://author.example.org/compound.pdf", output,
                       fetch=fetched, selection=selection, identity_evidence=evidence_path)
    assert "first_pages_check" not in receipt
    assert receipt["identity_check"]["identifier_witness"]["page"] == 3
    assert receipt["identity_evidence"]["sha256"] == pdf.sha(evidence_path)
    packet = pdf.load_packet(output / "packet.json")
    body = " ".join(p["text"] for p in packet["sources"][0]["passages"])
    assert "Methods and results" in body
    assert "Previous article" not in body and "Following article" not in body
    pins = json.loads((output / "source_receipts.json").read_bytes())["files"]
    assert pins[str(evidence_path)] == pdf.sha(evidence_path)
    assert pins[str(inspection)] == pdf.sha(inspection)
    assert all(pdf.sha(path) == value for path, value in pins.items())


def test_wrong_identifier_extension_or_conflicting_context_cannot_match_by_substring():
    for replacement in (DOI + "0", DOI + "\n10.9999/another"):
        text = text_fixture().replace(DOI, replacement)
        selection, evidence = inspection_data(text)
        if "another" in replacement:
            context = evidence["identifier_context"]
            context["end_char"] += len("\n10.9999/another")
            context["quote"] = text[context["start_char"]:context["end_char"]]
        with pytest.raises(ValueError, match="conflicting DOI"):
            pdf.verify_selected_pdf_text(text, {"title": TITLE, "doi": DOI}, selection,
                                         evidence, original_sha256="a" * 64)
