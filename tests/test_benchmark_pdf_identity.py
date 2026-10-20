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


@pytest.fixture(params=["early", "selected", "regions"])
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
    elif request.param == "selected":
        full_text = "Prior unrelated article.\n" + TITLE + "\nMethods.\fSecond page.\fThird.\fFourth.\fPublication DOI: " + DOI + "\nTarget article ends here.\nFollowing article."
        decision = {"kind": "exact_text_bounds", "basis": "Inspected exact boundaries of the synthetic target within a compound document.",
            "start": TITLE, "end": "Target article ends here."}
    else:
        full_text = ("Prior unrelated article.\nTarget opening material.\n" + "Selected first-column material. " * 140
            + "Target first-column ending.\nInterleaved prior-article tail and DOI: 10.9999/foreign\n" + TITLE
            + "\nMethods.\fSecond page.\fThird.\fFourth.\fPublication DOI: " + DOI
            + "\nTarget article ends here.\nFollowing article.")
        decision = {"kind": "exact_text_regions", "basis": "Inspected disjoint target regions against the synthetic mixed first page.",
            "regions": [{"start": "Target opening material.", "end": "Target first-column ending."},
                        {"start": TITLE, "end": "Target article ends here."}]}
    text_path = directory / "full_pdf_text.txt"
    text_path.write_text(full_text)
    png = tmp_path / "inspected_page.png"
    png.write_bytes(b"Synthetic inspected-page artifact, not an actual scientific image")
    inspection_record = {"method_id": "fixture", "original_sha256": sha(original), "selection": decision,
        "files": {str(original): sha(original), str(text_path): sha(text_path), str(png): sha(png)}}
    if request.param == "regions":
        decision["source_text_sha256"] = sha(text_path)
        pages = []
        for number, text in enumerate(full_text.split("\f"), 1):
            image = png if number == 1 else tmp_path / f"inspected_page_{number}.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nSynthetic fixture, not a real rendered image: " + str(number).encode())
            pages.append({"page": number, "image": str(image), "image_sha256": sha(image), "text": text})
        render = tmp_path / "render_manifest.json"
        write(render, {"original": str(original), "original_sha256": sha(original), "pages": pages})
        inspection_record.update(format_version="article_content_inspection/1.1", decision="select", selected_reading_order="coherent",
            source_text={"path": str(text_path), "sha256": sha(text_path)},
            render_manifest={"path": str(render), "sha256": sha(render)},
            pages=[{"page": row["page"], "image_sha256": row["image_sha256"], "relationship": "mixed_articles" if row["page"] == 1 else "target_article",
                "reading_order": "interleaved" if row["page"] == 1 else "coherent", "observations": "Synthetic pinned-page inspection fixture only."} for row in pages])
        inspection_record["files"].update({str(render): sha(render), **{row["image"]: row["image_sha256"] for row in pages}})
    inspection = tmp_path / "inspection.json"
    write(inspection, inspection_record)
    selection = {**decision, "inspection_report": {"path": str(inspection), "sha256": sha(inspection)}}
    selected, prefix, suffix, selection = pdf.select_article_text(full_text, selection)
    write(directory / "article_selection.json", selection)
    for name, text in (("excluded_prefix.txt", prefix), ("excluded_suffix.txt", suffix)):
        (directory / name).write_text(text)
    if request.param == "regions":
        for name, record in pdf.selected_region_artifacts(full_text, selection).items():
            write(directory / name, record)
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
        if request.param == "regions":
            evidence.update(format_version="selected_article_identity/1.1", title_role="visually_inspected_article_header",
                title_page_image_sha256=sha(png))
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
    if route in {"selected", "regions"}:
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


def repin_fixture(directory):
    """Adversarially self-consistent byte receipts do not establish interpretation."""
    receipts = json.loads((directory / "source_receipts.json").read_bytes())
    receipts["files"] = {path: sha(Path(path)) for path in receipts["files"]}
    write(directory / "source_receipts.json", receipts)


@pytest.mark.parametrize("registered", ["regions"], indirect=True)
def test_region_replay_preserves_original_offsets_complete_exclusions_and_header_page(registered, tmp_path):
    case, directory, _, _, png, _ = registered
    proof = verify_publication(load_packet(directory / "packet.json"), case, project_root=tmp_path)
    primary = proof["primaries"][0]
    text = (directory / "full_pdf_text.txt").read_text()
    selected = (directory / "primary.txt").read_text().partition("\n\n")[2]
    regions = json.loads((directory / "source_text_map.json").read_bytes())["regions"]
    excluded = json.loads((directory / "excluded_regions.json").read_bytes())["regions"]
    assert primary["pdf_identity_check"]["title_role"] == "visually_inspected_article_header"
    assert primary["pdf_identity_check"]["title_page_image_sha256"] == sha(png)
    assert primary["pdf_identity_check"]["title_witness"]["start_char"] > 3000
    assert "Interleaved prior-article tail" not in selected
    assert any("Interleaved prior-article tail" in row["text"] for row in excluded)
    for row in regions:
        assert selected[row["selected_start_char"]:row["selected_end_char"]] == text[row["start_char"]:row["end_char"]]
    intervals = sorted((row["start_char"], row["end_char"]) for row in regions + excluded)
    assert intervals[0][0] == 0 and intervals[-1][1] == len(text)
    assert all(left[1] == right[0] for left, right in zip(intervals, intervals[1:]))
    assert all(proof["pins"][str(directory / name)] == sha(directory / name)
        for name in ("excluded_regions.json", "source_text_map.json"))


@pytest.mark.parametrize("registered", ["regions"], indirect=True)
@pytest.mark.parametrize("artifact", ["excluded_regions.json", "source_text_map.json"])
def test_repinned_region_map_or_interior_exclusion_cannot_replace_source_reconstruction(registered, tmp_path, artifact):
    case, directory, *_ = registered
    path = directory / artifact
    record = json.loads(path.read_bytes())
    if artifact == "excluded_regions.json":
        record["regions"][1]["text"] = "Invented exclusion in an otherwise hash-consistent receipt."
    else:
        record["regions"][1]["selected_start_char"] -= 1
    write(path, record)
    repin_fixture(directory)
    with pytest.raises(ValueError, match="map or exclusions differ"):
        verify_publication(load_packet(directory / "packet.json"), case, project_root=tmp_path)


@pytest.mark.parametrize("registered", ["regions"], indirect=True)
@pytest.mark.parametrize("artifact", ["excluded_regions.json", "source_text_map.json"])
def test_region_artifacts_must_be_bound_and_present(registered, tmp_path, artifact):
    case, directory, *_ = registered
    receipts = json.loads((directory / "source_receipts.json").read_bytes())
    receipts["files"].pop(str(directory / artifact))
    write(directory / "source_receipts.json", receipts)
    with pytest.raises(ValueError, match="not bound by its source receipt"):
        verify_publication(load_packet(directory / "packet.json"), case, project_root=tmp_path)


@pytest.mark.parametrize("registered", ["regions"], indirect=True)
def test_region_identity_context_cannot_cross_an_excluded_source_interval(registered, tmp_path):
    case, directory, *_ = registered
    registration = json.loads((directory / "registration.json").read_bytes())
    evidence_path = Path(registration["identity_evidence"]["path"])
    evidence = json.loads(evidence_path.read_bytes())
    text = (directory / "full_pdf_text.txt").read_text()
    start, end = text.index("Interleaved prior-article tail"), evidence["identifier_witness"]["end_char"]
    evidence["identifier_context"] = {"start_char": start, "end_char": end, "quote": text[start:end], "page": 1}
    write(evidence_path, evidence)
    registration["identity_evidence"].update(record=evidence, sha256=sha(evidence_path))
    write(directory / "registration.json", registration)
    repin_fixture(directory)
    with pytest.raises(ValueError, match="selected-article identity witness"):
        verify_publication(load_packet(directory / "packet.json"), case, project_root=tmp_path)


@pytest.mark.parametrize("registered", ["regions"], indirect=True)
@pytest.mark.parametrize("problem", ["rendered_text", "foreign_page", "missing_page", "wrong_header_image"])
def test_region_replay_revalidates_rendered_source_and_inspection_relationships(registered, tmp_path, problem):
    case, directory, original, *_ = registered
    registration = json.loads((directory / "registration.json").read_bytes())
    selection = registration["article_selection"]
    inspection_path = Path(selection["inspection_report"]["path"])
    inspection = json.loads(inspection_path.read_bytes())
    evidence_path = Path(registration["identity_evidence"]["path"])
    evidence = json.loads(evidence_path.read_bytes())
    if problem == "rendered_text":
        render_path = Path(inspection["render_manifest"]["path"])
        render = json.loads(render_path.read_bytes())
        render["pages"][1]["text"] = "Different article page text."
        write(render_path, render)
        inspection["render_manifest"]["sha256"] = inspection["files"][str(render_path)] = sha(render_path)
    elif problem == "foreign_page":
        inspection["pages"][1]["relationship"] = "other_article"
    elif problem == "missing_page":
        inspection["pages"].pop()
    else:
        evidence["title_page_image_sha256"] = inspection["pages"][1]["image_sha256"]
    write(inspection_path, inspection)
    report = {"path": str(inspection_path), "sha256": sha(inspection_path)}
    selection["inspection_report"] = evidence["inspection_report"] = report
    write(directory / "article_selection.json", selection)
    write(evidence_path, evidence)
    registration["identity_evidence"].update(record=evidence, sha256=sha(evidence_path))
    registration["identity_check"] = pdf.verify_selected_pdf_text((directory / "full_pdf_text.txt").read_text(),
        registration["identity"], selection, evidence, original_sha256=sha(original))
    write(directory / "registration.json", registration)
    repin_fixture(directory)
    with pytest.raises(ValueError, match="Rendered source page|foreign or unreadable|Every original source page|first selected source-page image"):
        verify_publication(load_packet(directory / "packet.json"), case, project_root=tmp_path)
