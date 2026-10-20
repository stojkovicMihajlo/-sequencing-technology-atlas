"""Replay PDF identity from synthetic pinned metadata/text; never run conversion."""

import json
from pathlib import Path

import pytest

from genomics_discovery.curation.cached_documents import _publish_rendition
from genomics_discovery.curation.protocol_benchmark.source_identity import verify_publication
from genomics_discovery.curation.run_state import sha, write
from genomics_discovery.curation.source_acquisition import pdf_registration as pdf
from genomics_discovery.curation.source_acquisition.models import Identity
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet

TITLE = "A primary experimental paper for this synthetic fixture"
DOI = "10.1234/fixture"
URL = "https://example.org/original.pdf"


@pytest.fixture(params=["early", "selected"])
def registered(tmp_path, request):
    original = tmp_path / "article.pdf"
    original.write_bytes(b"%PDF-synthetic bytes; not parsed or converted by this test")
    directory = tmp_path / "registered"
    directory.mkdir()
    case = {"case_id": "fixture", "method_id": "fixture", "title": "Fixture assay", "scope": "Original selected article",
        "variant": "Original", "primary_identity": {"pmid": "123", "doi": DOI, "title": TITLE}}
    identity = {"pmid": "123", "doi": DOI, "title": TITLE}
    if request.param == "early":
        full_text = TITLE + "\nPublication DOI: " + DOI + "\nMethods and the original report."
        decision = {"kind": "whole_document", "basis": "Inspected this synthetic fixture as a single complete article."}
    else:
        full_text = "Prior unrelated article.\n" + TITLE + "\nMethods.\fSecond page.\fThird.\fFourth.\fPublication DOI: " + DOI + "\nTarget article ends here.\nFollowing article."
        decision = {"kind": "exact_text_bounds", "basis": "Inspected exact boundaries of the synthetic target within a compound document.",
            "start": TITLE, "end": "Target article ends here."}
    text_path = directory / "full_pdf_text.txt"
    text_path.write_text(full_text)
    png = tmp_path / "inspected_page.png"
    png.write_bytes(b"Synthetic inspected-page artifact, not an actual scientific image")
    inspection = tmp_path / "inspection.json"
    write(inspection, {"method_id": "fixture", "original_sha256": sha(original), "selection": decision,
        "files": {str(original): sha(original), str(text_path): sha(text_path), str(png): sha(png)}})
    selection = {**decision, "inspection_report": {"path": str(inspection), "sha256": sha(inspection)}}
    selected, prefix, suffix, selection = pdf.select_article_text(full_text, selection)
    write(directory / "article_selection.json", selection)
    for name, text in (("excluded_prefix.txt", prefix), ("excluded_suffix.txt", suffix)):
        (directory / name).write_text(text)
    rendition = directory / "primary.txt"
    _publish_rendition(rendition, selected, original, sha(original), "synthetic fixture", "No real PDF conversion performed.")
    packet = build_packet(**{key: case[key] for key in ("method_id", "title", "scope", "variant")}, sources=[{
        "source_id": "primary_pdf_" + sha(original)[:12], "title": TITLE, "url": URL, "role": "primary_paper", "path": str(rendition)}])
    write(directory / "packet.json", packet)
    metadata = directory / "bibliographic_original.xml"
    metadata.write_text(f'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>{TITLE}</ArticleTitle></Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">{DOI}</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>')
    write(directory / "bibliographic_retrieval.json", {"status": "downloaded", "path": str(metadata), "sha256": sha(metadata),
        "final_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=123"})
    download = tmp_path / "download.json"
    download_record = {"status": "downloaded", "path": str(original), "sha256": sha(original), "final_url": URL}
    write(download, download_record)
    snapshot = directory / "implementation_snapshot/pdf_registration.py"
    snapshot.parent.mkdir()
    snapshot.write_text("# synthetic registration implementation snapshot\n")
    registration = {"status": "registered_for_fresh_source_review", "scientific_resolution": False,
        "scope": {key: case[key] for key in ("method_id", "title", "scope", "variant", "primary_identity")},
        "identity": identity, "original": str(original), "original_sha256": sha(original), "rendition": str(rendition),
        "rendition_sha256": sha(rendition), "public_url": URL, "metadata": str(metadata), "metadata_sha256": sha(metadata),
        "article_selection": selection, "acquisition_binding": None,
        "original_download_binding": {"path": str(download), "sha256": sha(download), "record": download_record},
        "implementation_snapshot": [{"snapshot": str(snapshot), "sha256": sha(snapshot)}],
        "identity_boundary": "Synthetic identity/custody test, not scientific completeness."}
    if request.param == "early":
        registration["first_pages_check"] = pdf.verify_pdf_text(full_text, identity)
    else:
        def witness(quote):
            start = full_text.index(quote)
            return {"quote": quote, "start_char": start, "end_char": start + len(quote), "page": full_text[:start].count("\f") + 1}
        evidence = {"format_version": "selected_article_identity/1.0", "original_sha256": sha(original),
            "inspection_report": selection["inspection_report"], "title_witness": witness(TITLE), "identifier_witness": witness(DOI),
            "identifier_context": witness("Publication DOI: " + DOI), "identifier_role": "article_publication_identifier",
            "basis": "Inspected the synthetic article's own DOI context on page five."}
        evidence_path = tmp_path / "identity_evidence.json"
        write(evidence_path, evidence)
        registration["identity_evidence"] = {"path": str(evidence_path), "sha256": sha(evidence_path), "record": evidence}
        registration["identity_check"] = pdf.verify_selected_pdf_text(full_text, identity, selection, evidence, original_sha256=sha(original))
    write(directory / "registration.json", registration)
    files = {str(path): sha(path) for path in tmp_path.rglob("*") if path.is_file()}
    write(directory / "source_receipts.json", {"files": files})
    return case, directory, original, metadata, png, request.param


def test_replays_original_bibliography_selection_inspection_and_rendition(registered, tmp_path, monkeypatch):
    case, directory, original, metadata, png, route = registered
    monkeypatch.setattr(pdf.subprocess, "run", lambda *args, **kwargs: pytest.fail("Identity replay cannot convert PDF"))
    proof = verify_publication(load_packet(directory / "packet.json"), case, project_root=tmp_path)
    assert proof["primaries"][0]["identity_route"] == ("registered_pdf_early_page" if route == "early" else "registered_pdf_inspected_selected_article")
    assert all(proof["pins"][str(path)] == sha(path) for path in (original, metadata, png, directory / "registration.json"))
    if route == "selected":
        assert proof["primaries"][0]["pdf_identity_check"]["identifier_witness"]["page"] == 5


@pytest.mark.parametrize("target", ["original", "metadata", "inspection", "selection", "receipt"])
def test_changed_pdf_proof_component_is_rejected(registered, tmp_path, target):
    case, directory, original, metadata, png, _ = registered
    path = {"original": original, "metadata": metadata, "inspection": png,
        "selection": directory / "article_selection.json", "receipt": directory / "registration.json"}[target]
    path.write_bytes(path.read_bytes() + b"\nchanged")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        verify_publication(load_packet(directory / "packet.json"), case, project_root=tmp_path)


def test_self_consistent_receipt_identity_cannot_replace_raw_bibliography(registered, tmp_path):
    case, directory, original, metadata, png, _ = registered
    metadata.write_bytes(metadata.read_bytes().replace(b"<PMID>123</PMID>", b"<PMID>999</PMID>"))
    registration = json.loads((directory / "registration.json").read_bytes())
    registration["metadata_sha256"] = sha(metadata)
    write(directory / "registration.json", registration)
    retrieval = json.loads((directory / "bibliographic_retrieval.json").read_bytes())
    retrieval["sha256"] = sha(metadata)
    write(directory / "bibliographic_retrieval.json", retrieval)
    receipts = json.loads((directory / "source_receipts.json").read_bytes())
    for path in (metadata, directory / "registration.json", directory / "bibliographic_retrieval.json"):
        receipts["files"][str(path)] = sha(path)
    write(directory / "source_receipts.json", receipts)
    with pytest.raises(ValueError, match="PMID mismatch"):
        verify_publication(load_packet(directory / "packet.json"), case, project_root=tmp_path)


@pytest.mark.parametrize("registered", ["selected"], indirect=True)
def test_selected_article_witness_is_revalidated_after_receipt_hashes_match(registered, tmp_path):
    case, directory, original, metadata, png, _ = registered
    registration = json.loads((directory / "registration.json").read_bytes())
    evidence_path = Path(registration["identity_evidence"]["path"])
    evidence = json.loads(evidence_path.read_bytes())
    evidence["title_witness"]["page"] = 5
    write(evidence_path, evidence)
    registration["identity_evidence"].update(record=evidence, sha256=sha(evidence_path))
    write(directory / "registration.json", registration)
    receipts = json.loads((directory / "source_receipts.json").read_bytes())
    receipts["files"].update({str(evidence_path): sha(evidence_path), str(directory / "registration.json"): sha(directory / "registration.json")})
    write(directory / "source_receipts.json", receipts)
    with pytest.raises(ValueError, match="selected-article identity witness"):
        verify_publication(load_packet(directory / "packet.json"), case, project_root=tmp_path)


def test_rebound_rendition_cannot_add_unselected_text(registered, tmp_path):
    case, directory, original, metadata, png, _ = registered
    rendition = directory / "primary.txt"
    rendition.write_text(rendition.read_text() + "\nUnselected extra article content.")
    old_packet = json.loads((directory / "packet.json").read_bytes())
    source = old_packet["sources"][0]
    packet = build_packet(**{key: case[key] for key in ("method_id", "title", "scope", "variant")}, sources=[{
        **{key: source[key] for key in ("source_id", "title", "url", "role")}, "path": str(rendition)}])
    write(directory / "packet.json", packet)
    registration = json.loads((directory / "registration.json").read_bytes())
    registration["rendition_sha256"] = sha(rendition)
    write(directory / "registration.json", registration)
    receipts = json.loads((directory / "source_receipts.json").read_bytes())
    for path in (rendition, directory / "packet.json", directory / "registration.json"):
        receipts["files"][str(path)] = sha(path)
    write(directory / "source_receipts.json", receipts)
    with pytest.raises(ValueError, match="exact selected text"):
        verify_publication(load_packet(directory / "packet.json"), case, project_root=tmp_path)
