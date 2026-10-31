"""Poster-source preparation controls; tiny fake rasters, no native/model jobs."""
import importlib.util
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

spec = importlib.util.spec_from_file_location("illumina_poster_sources", Path(__file__).parents[1] / "scripts/illumina_poster_sources.py")
poster = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = poster
spec.loader.exec_module(poster)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def prepared_inputs(tmp_path, count=3):
    assets = tmp_path / "pdfs"
    assets.mkdir()
    pdf = assets / "source.pdf"
    pdf.write_bytes(b"%PDF-1.7\nfixture")
    rows = []
    for i in range(count):
        member = {"source_method_label": "A" if i else "A/B", "source_listed_citations": [], "catalogue_reference_candidate_pmids": []}
        row = {"row_id": f"source:p1:row{i}", "resource_id": "source", "resource_kind": "rna_poster", "page": 1,
            "source_pdf_sha256": poster.file_hash(pdf), "source_url": "https://example.org/poster.pdf",
            "source_row_caption": "A/B" if i == 0 else "A", "source_label_fragments": ["A/B" if i == 0 else "A"],
            "members": [member], "diagram_region_proposal": [0, i * 30, 90, i * 30 + 25],
            "exact_label_bboxes": [[2, i * 30 + 5, 20, i * 30 + 15]], "grouped_source_row": False,
            "context_scope_warning": "Nearby diagrams are context, not target operations."}
        if i == 0:
            row["members"] = [{"source_method_label": name, "source_listed_citations": [{"citation_fragment_as_listed": f"{name} et al. (2017) Nature1:1-2"}],
                               "catalogue_reference_candidate_pmids": ["123"] if name == "A" else []} for name in ["A", "B"]]
            row["grouped_source_row"] = True
        rows.append(row)
    overlay = tmp_path / "overlay.json"
    write(overlay, {"format_version": "illumina_poster_row_overlay_proposals/1.0", "rows": rows})
    base = tmp_path / "base"
    base.mkdir()
    write(base / "inventory.json", {"format_version": poster.INVENTORY_FORMAT, "cards": []})
    write(base / "sources.json", {"format_version": poster.SOURCES_FORMAT, "inventory_sha256": poster.file_hash(base / "inventory.json"),
          "assets": [{"asset_id": "source", "status": "downloaded", "kind": "rna_poster", "url": "https://example.org/poster.pdf", "sha256": poster.file_hash(pdf)}],
          "publications": {"123": {"pmid": "123", "title": "Metadata only", "identity_status": "pubmed_record_retrieved"}},
          "fulltexts": {}, "publication_batches": [], "external_publications": {}})
    calls = []
    def renderer(pdf, destination, *, page, dpi):
        calls.append((str(pdf), page, dpi))
        Image.new("RGB", (200, 200), "white").save(destination)
        return {"path": str(destination), "sha256": poster.file_hash(destination), "page": page, "dpi": dpi,
            "width_pixels": 200, "height_pixels": 200, "page_width_points": 100., "page_height_points": 100.,
            "renderer": "test fixture", "command": ["fake", str(pdf)]}
    return overlay, base / "sources.json", assets, renderer, calls


def test_all_rows_remain_distinct_and_each_page_rendered_once(tmp_path):
    overlay, source, assets, renderer, calls = prepared_inputs(tmp_path)
    output = tmp_path / "prepared"
    result = poster.prepare(overlay, source, assets, output, renderer=renderer)
    inventory = json.loads((output / "inventory.json").read_text())
    assert len(calls) == 1 and calls[0][2] == 144
    assert len(inventory["cards"]) == 3
    assert len({c["record_id"] for c in inventory["cards"]}) == 3
    assert [c["source_title"] for c in inventory["cards"]] == ["A/B", "A", "A"]
    assert all(c["canonical_technology_id"] is None for c in inventory["cards"])
    assert inventory["cards"][0]["source_method_labels"] == ["A", "B"]
    assert len(result["assets"]) == 3
    assert result["statistics"]["rendered_pdf_pages"] == 1
    assert result["statistics"]["decoded_raster_pages"] == 1
    assert result["inventory_sha256"] == poster.file_hash(output / "inventory.json")
    assert not result["scientific_review"]


def test_crop_provenance_has_pdf_raster_pixels_and_source_overlay_pins(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    result = poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer)
    asset = result["assets"][0]
    original = asset["original"]
    assert original["crop"]["pixel_box"] == (0, 0, 180, 50)
    assert original["crop"]["pdf_point_box"] == [0, 0, 90, 25]
    for key in ["parent_pdf", "raster", "overlay"]:
        assert Path(original[key]["path"]).is_absolute()
        assert poster.file_hash(original[key]["path"]) == original[key]["sha256"]
    assert poster.file_hash(asset["path"]) == asset["sha256"]
    assert "Nearby" in original["context_scope_warning"]


def test_literal_citations_do_not_receive_hint_pmids(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    result = poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer)
    inventory = json.loads((tmp_path / "out/inventory.json").read_text())
    card = inventory["cards"][0]
    assert len(card["references"]) == 2
    assert all(r["pmid"] is None for r in card["references"])
    assert card["candidate_publications"][0]["pmid"] == "123"
    assert card["candidate_publications"][0]["association_status"] == "unverified_candidate"
    assert card["candidate_publications"][0]["reference_ids"] == []
    assert not result["fulltexts"]
    assert result["publications"]["123"]["title"] == "Metadata only"
    assert inventory["cards"][1]["require_paper_matches"] is True
    assert inventory["cards"][1]["references"] == []
    assert "unresolved" in inventory["cards"][1]["missing_evidence"][0]


def test_pixel_transform_uses_outward_rounding_and_page_bounds():
    assert poster.pixel_box([.2, .2, 4.2, 5.2], dpi=144, width=20, height=20, point_width=10, point_height=10) == (0, 0, 9, 11)
    with pytest.raises(ValueError, match="beyond"):
        poster.pixel_box([0, 0, 11, 5], dpi=144, width=20, height=20, point_width=10, point_height=10)


@pytest.mark.parametrize("box", [[0, 0, float("nan"), 20], [-1, 0, 20, 20], [10, 0, 9, 20]])
def test_invalid_source_geometry_rejected(tmp_path, box):
    overlay, source, assets, renderer, calls = prepared_inputs(tmp_path)
    data = json.loads(overlay.read_text())
    data["rows"][0]["diagram_region_proposal"] = box
    write(overlay, data)
    with pytest.raises(ValueError, match="coordinate"):
        poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer)
    assert not calls


def test_target_label_outside_crop_rejected(tmp_path):
    overlay, source, assets, renderer, calls = prepared_inputs(tmp_path)
    data = json.loads(overlay.read_text())
    data["rows"][0]["exact_label_bboxes"] = [[2, 5, 95, 15]]
    write(overlay, data)
    with pytest.raises(ValueError, match="preserve the target"):
        poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer)
    assert not calls


def test_changed_parent_pdf_rejected_before_render(tmp_path):
    overlay, source, assets, renderer, calls = prepared_inputs(tmp_path)
    (assets / "source.pdf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="custody mismatch"):
        poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer)
    assert not calls


def test_changed_parent_during_render_cannot_publish_snapshot(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    def mutator(pdf, target, **kwargs):
        value = renderer(pdf, target, **kwargs)
        pdf.write_bytes(b"mutated during render")
        return value
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="changed during"):
        poster.prepare(overlay, source, assets, output, renderer=mutator)
    assert not (output / "sources.json").exists()
    assert (output / "PREPARATION_FAILED.json").is_file()


def test_resolution_cannot_add_member_or_claim_identity_verified(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    resolution = tmp_path / "resolution.json"
    write(resolution, {"proposals": {"sha256": poster.file_hash(overlay)},
        "resolutions": [{"row_id": "source:p1:row0", "source_method_label": "Invented", "paper_identity_verified": False}]})
    with pytest.raises(ValueError, match="existing source member"):
        poster.prepare(overlay, source, assets, tmp_path / "out", resolutions_path=resolution, renderer=renderer)


def test_resolution_reference_link_is_candidate_specific(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    data = json.loads(overlay.read_text())
    member = data["rows"][0]["members"][0]
    member["source_listed_citations"].append({"citation_fragment_as_listed": "Other et al. (2018) Cell1:3-4"})
    write(overlay, data)
    refs = []
    for i, r in enumerate(member["source_listed_citations"]):
        refs.append({"citation_id": "citation_" + poster.hashlib.sha256(r["citation_fragment_as_listed"].encode()).hexdigest()[:24],
            "candidate_publications": [{"pmid": "123" if i == 0 else "456", "comparison": {"all_observed_fields_agree": True}}]})
    path = tmp_path / "resolution.json"
    write(path, {"proposals": {"sha256": poster.file_hash(overlay)}, "resolutions": [{"row_id": "source:p1:row0", "source_method_label": "A",
          "paper_identity_verified": False, "candidate_pmids": ["123"], "references": refs}]})
    poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer, resolutions_path=path)
    card = json.loads((tmp_path / "out/inventory.json").read_text())["cards"][0]
    candidate = next(c for c in card["candidate_publications"] if c["candidate_basis"].startswith("bibliographic"))
    assert candidate["reference_ids"] == [card["references"][0]["reference_id"]]
    assert all(r["pmid"] is None for r in card["references"])


def test_unpinned_publication_text_is_not_reused(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    data = json.loads(source.read_text())
    data["publications"]["123"]["metadata_source"] = "missing.xml"
    write(source, data)
    with pytest.raises(ValueError, match="Unpinned"):
        poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer)


def test_changed_fulltext_original_rejected(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    paper = source.parent / "article.xml"
    paper.write_bytes(b"original")
    data = json.loads(source.read_text())
    data["fulltexts"]["123"] = {"status": "fulltext_acquired", "path": "article.xml", "sha256": poster.file_hash(paper)}
    write(source, data)
    paper.write_bytes(b"changed")
    with pytest.raises(ValueError, match="custody mismatch"):
        poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer)


def test_production_render_guard_requires_slurm(tmp_path, monkeypatch):
    overlay, source, assets, _, _ = prepared_inputs(tmp_path)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    with pytest.raises(ValueError, match="under Sherlock Slurm"):
        poster.prepare(overlay, source, assets, tmp_path / "out")


def test_overlay_cannot_relabel_acquired_pdf_kind_or_url(tmp_path):
    overlay, source, assets, renderer, calls = prepared_inputs(tmp_path)
    data = json.loads(overlay.read_text())
    data["rows"][0]["resource_kind"] = "different_source"
    write(overlay, data)
    with pytest.raises(ValueError, match="kind/URL/hash"):
        poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer)
    assert not calls


def test_nonfinite_renderer_geometry_cannot_be_published(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    def broken(pdf, path, **kwargs):
        result = renderer(pdf, path, **kwargs)
        result["page_width_points"] = float("nan")
        return result
    with pytest.raises(ValueError, match="positive and finite"):
        poster.prepare(overlay, source, assets, tmp_path / "out", renderer=broken)


def test_real_renderer_command_contract_with_mocked_poppler_only(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("SLURM_JOB_ID", "unit-test-no-actual-process")
    monkeypatch.setattr(poster.shutil, "which", lambda name: name)
    commands = []
    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "pdfinfo":
            return SimpleNamespace(stdout=b"Page    1 size:  100 x 100 pts\nPage    1 rot:   0\nPage    1 MediaBox: 0.00 0.00 100.00 100.00\nPage    1 CropBox: 0.00 0.00 100.00 100.00\n")
        Image.new("RGB", (200, 200), "white").save(command[-1] + ".png")
        return SimpleNamespace(stdout=b"")
    monkeypatch.setattr(poster.subprocess, "run", fake_run)
    output = tmp_path / "raster.png"
    result = poster.render_page(tmp_path / "source.pdf", output, page=1, dpi=144)
    assert len(commands) == 2
    assert commands[1][:7] == ["pdftoppm", "-f", "1", "-l", "1", "-r", "144"]
    assert result["page_width_points"] == 100
    assert result["sha256"] == poster.file_hash(output)


def test_interleaved_source_pages_decode_once_and_restore_declared_row_order(tmp_path):
    overlay, source, assets, renderer, calls = prepared_inputs(tmp_path)
    second = assets / "second.pdf"
    second.write_bytes(b"%PDF-second")
    data = json.loads(overlay.read_text())
    row = data["rows"][1]
    row["resource_id"] = "second"
    row["source_pdf_sha256"] = poster.file_hash(second)
    write(overlay, data)
    base = json.loads(source.read_text())
    base["assets"].append({"asset_id": "second", "status": "downloaded", "kind": "rna_poster",
                          "url": "https://example.org/poster.pdf", "sha256": poster.file_hash(second)})
    write(source, base)
    result = poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer)
    assert len(calls) == 2
    assert result["statistics"]["decoded_raster_pages"] == 2
    inventory = json.loads((tmp_path / "out/inventory.json").read_text())
    assert [c["source_row_id"] for c in inventory["cards"]] == [r["row_id"] for r in data["rows"]]


def add_external_document(source, tmp_path, *, doi="10.1234/real", card_id="old-card", identity="cached_registration_identity_and_custody_verified"):
    text = tmp_path / "external.txt"
    text.write_text("Actual cached full article text")
    data = json.loads(source.read_text())
    data["external_publications"]["old-card:references:0"] = {"card_id": card_id, "doi": doi,
        "content_level": "fulltext", "identity_status": identity, "title": "Actual document",
        "text_path": str(text), "text_sha256": poster.file_hash(text), "status": "available"}
    write(source, data)
    return text


def add_crossref_resolution(overlay, tmp_path, *, doi="10.1234/real", abstract=None):
    row = json.loads(overlay.read_text())["rows"][0]
    fragment = row["members"][0]["source_listed_citations"][0]["citation_fragment_as_listed"]
    cid = "citation_" + poster.hashlib.sha256(fragment.encode()).hexdigest()[:24]
    response = tmp_path / "crossref.json"
    write(response, {"message": {"DOI": doi, "title": ["Actual Crossref title"], "abstract": abstract}})
    resolution = tmp_path / "resolution.json"
    write(resolution, {"proposals": {"sha256": poster.file_hash(overlay)}, "resolutions": [{
        "row_id": row["row_id"], "source_method_label": "A", "paper_identity_verified": False,
        "candidate_pmids": [], "references": [{"citation_id": cid, "crossref_candidates": [{
            "publication": {"doi": doi, "title": "Untrusted proposed title"},
            "comparison": {"all_observed_fields_agree": True},
            "metadata_receipt": {"path": str(response), "sha256": poster.file_hash(response)}}]}]}]})
    return resolution, response


def test_source_card_doi_document_hint_reuses_fulltext_without_assigning_literal_ref(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    text = add_external_document(source, tmp_path)
    data = json.loads(overlay.read_text())
    data["rows"][0]["members"][0]["exact_current_card_label_candidates"] = ["old-card"]
    write(overlay, data)
    result = poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer)
    card = json.loads((tmp_path / "out/inventory.json").read_text())["cards"][0]
    candidates = [r for r in card["references"] if r["role"] == "candidate_publications"]
    assert len(candidates) == 1
    ref = candidates[0]
    assert ref["linked_literal_reference_ids"] == []
    assert ref["association_status"] == "unverified_candidate"
    document = result["external_publications"][ref["reference_id"]]
    assert document["content_level"] == "fulltext"
    assert document["text_path"] == str(text)
    assert document["paper_method_association_verified"] is False
    assert all(r["pmid"] is None for r in card["references"])
    assert "old-card:references:0" in result["external_publications"]


@pytest.mark.parametrize("abstract,level", [(None, "metadata"), ("<jats:p>Actual abstract only.</jats:p>", "abstract")])
def test_crossref_fallback_preserves_actual_content_level_and_literal_reference(tmp_path, abstract, level):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    resolution, _ = add_crossref_resolution(overlay, tmp_path, abstract=abstract)
    result = poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer, resolutions_path=resolution)
    card = json.loads((tmp_path / "out/inventory.json").read_text())["cards"][0]
    candidate = next(r for r in card["references"] if r["role"] == "candidate_publications")
    document = result["external_publications"][candidate["reference_id"]]
    assert document["content_level"] == level
    assert document["title"] == "Actual Crossref title"
    assert candidate["linked_literal_reference_ids"] == [card["references"][0]["reference_id"]]
    assert card["references"][0]["pmid"] is None
    assert not document["paper_method_association_verified"]


def test_different_doi_cannot_select_an_unrelated_cached_fulltext(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    add_external_document(source, tmp_path, doi="10.1234/unrelated")
    resolution, _ = add_crossref_resolution(overlay, tmp_path, doi="10.1234/real")
    result = poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer, resolutions_path=resolution)
    proposed = [s for key, s in result["external_publications"].items() if ":candidate_publications:" in key]
    assert len(proposed) == 1 and proposed[0]["content_level"] == "metadata"
    assert proposed[0]["doi"] == "10.1234/real"


def test_changed_crossref_response_cannot_be_hidden_by_existing_fulltext(tmp_path):
    overlay, source, assets, renderer, _ = prepared_inputs(tmp_path)
    add_external_document(source, tmp_path)
    resolution, response = add_crossref_resolution(overlay, tmp_path)
    response.write_text("changed")
    with pytest.raises(ValueError, match="custody mismatch"):
        poster.prepare(overlay, source, assets, tmp_path / "out", renderer=renderer, resolutions_path=resolution)
