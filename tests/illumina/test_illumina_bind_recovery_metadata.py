import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "illumina_bind_recovery_metadata", Path(__file__).parents[2] / "scripts/illumina/bind_recovery_metadata.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def inputs(tmp_path):
    xml = tmp_path / "original.xml"
    xml.write_text('''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12</PMID><Article>
    <ArticleTitle>Actual primary paper</ArticleTitle></Article></MedlineCitation><PubmedData>
    <ArticleIdList><ArticleId IdType="pubmed">12</ArticleId><ArticleId IdType="doi">10.1234/actual</ArticleId>
    </ArticleIdList><ReferenceList><Reference><ArticleIdList><ArticleId IdType="pubmed">99</ArticleId>
    </ArticleIdList></Reference></ReferenceList></PubmedData></PubmedArticle></PubmedArticleSet>''')
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"http_status": 200, "path": str(xml), "sha256": mod.sha(xml.read_bytes()),
        "request_url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=12&retmode=xml"}))
    overlay = tmp_path / "overlay.json"
    overlay.write_text(json.dumps({"additional_publications": mod.parse_pubmed(xml.read_bytes()),
        "citation_resolutions": [{"literal_poster_reference": "Original reference", "candidate_pmid": "12"}],
        "automatic_canonical_graph_assignment": False}))
    return overlay, xml, receipt


def test_source_binding_exports_new_overlay_without_changing_parent_or_assertions(tmp_path):
    overlay, xml, acquisition = inputs(tmp_path)
    original = overlay.read_bytes()
    before = json.loads(original)
    output = tmp_path / "new"
    checked = mod.bind_overlay(overlay, [(xml, acquisition)], output)
    after = json.loads((output / "recovery_overlay.json").read_bytes())
    publication = after["additional_publications"]["12"]
    assert publication["metadata_source"] == str(xml.resolve())
    assert publication["metadata_source_sha256"] == mod.sha(xml.read_bytes())
    assert publication["identifiers"] == {"pubmed": ["12"], "doi": ["10.1234/actual"]}
    assert after["citation_resolutions"] == before["citation_resolutions"]
    assert overlay.read_bytes() == original
    assert checked["publications_checked"] == 1
    assert checked["curator_assertions_unchanged"] is True
    assert checked["exported_overlay"]["sha256"] == mod.sha((output / "recovery_overlay.json").read_bytes())
    with pytest.raises(FileExistsError):
        mod.bind_overlay(overlay, [(xml, acquisition)], output)


@pytest.mark.parametrize("field, value", [("title", "Unsupported different title"),
    ("identifiers", {"pubmed": ["12"], "doi": ["10.1234/different"]}),
    ("abstract", [{"text": "Invented abstract"}])])
def test_identity_or_metadata_mismatch_cannot_be_exported(tmp_path, field, value):
    overlay, xml, acquisition = inputs(tmp_path)
    data = json.loads(overlay.read_text())
    data["additional_publications"]["12"][field] = value
    overlay.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="differs"):
        mod.bind_overlay(overlay, [(xml, acquisition)], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_changed_original_xml_fails_its_original_acquisition_pin(tmp_path):
    overlay, xml, acquisition = inputs(tmp_path)
    xml.write_text(xml.read_text().replace("Actual primary paper", "Changed source"))
    with pytest.raises(ValueError, match="custody mismatch"):
        mod.bind_overlay(overlay, [(xml, acquisition)], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_cited_pmid_cannot_be_bound_as_the_own_article_record(tmp_path):
    overlay, xml, acquisition = inputs(tmp_path)
    data = json.loads(overlay.read_text())
    paper = data["additional_publications"].pop("12")
    paper["pmid"] = "99"
    data["additional_publications"]["99"] = paper
    overlay.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="own PubMed record"):
        mod.bind_overlay(overlay, [(xml, acquisition)], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def external_input(overlay, root):
    api_record = {"doi": "10.1101/045989", "title": "A source-bound preprint",
                  "abstract": "An actual API abstract describing two methods.", "authors": "Author One;Author Two;",
                  "version": "1", "date": "2016-03-27", "published": "NA"}
    api = root / "api.json"
    api.write_text(json.dumps({"messages": [{"status": "ok"}], "collection": [api_record]}))
    source_id = "preprint:10.1101/045989"
    source = {"source_id": source_id, "content_level": "abstract", "primary_api_record": api_record,
              "metadata_receipt": {"path": str(api), "sha256": mod.sha(api.read_bytes()), "http_status": 200,
                  "request_url": "https://api.biorxiv.org/details/biorxiv/10.1101/045989"}}
    data = json.loads(overlay.read_text())
    data["additional_external_sources"] = {source_id: source}
    overlay.write_text(json.dumps(data))
    return source_id, api, source


def test_api_external_export_has_canonical_fields_verified_text_and_unchanged_raw_wrapper(tmp_path):
    overlay, xml, acquisition = inputs(tmp_path)
    source_id, api, original_source = external_input(overlay, tmp_path)
    before = overlay.read_bytes()
    output = tmp_path / "new"
    checked = mod.bind_overlay(overlay, [(xml, acquisition)], output)
    result = json.loads((output / "recovery_overlay.json").read_text())["additional_external_sources"][source_id]
    for field in ("doi", "title", "identity_status", "text_path", "text_sha256"):
        assert isinstance(result[field], str) and result[field]
    assert result["primary_api_record"] == original_source["primary_api_record"]
    assert result["metadata_receipt"] == original_source["metadata_receipt"]
    assert result["primary_api_record"]["published"] == "NA"
    text = Path(result["text_path"])
    assert text.is_relative_to(output) and mod.sha(text.read_bytes()) == result["text_sha256"]
    assert original_source["primary_api_record"]["abstract"] in text.read_text()
    assert result["metadata_source_sha256"] == mod.sha(api.read_bytes())
    assert checked["external_documents_checked"] == 1
    assert checked["external_bindings"][0]["own_doi_title_abstract_checked"] is True
    assert overlay.read_bytes() == before


@pytest.mark.parametrize("field, changed", [("doi", "10.1101/999999"), ("title", "Wrong title"),
                                            ("abstract", "Invented abstract")])
def test_external_top_level_identity_or_abstract_tampering_is_rejected(tmp_path, field, changed):
    overlay, xml, acquisition = inputs(tmp_path)
    source_id, _, _ = external_input(overlay, tmp_path)
    data = json.loads(overlay.read_text())
    data["additional_external_sources"][source_id][field] = changed
    overlay.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="differs"):
        mod.bind_overlay(overlay, [(xml, acquisition)], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("field, changed", [("doi", "10.1101/999999"), ("title", "Wrong title"),
                                            ("abstract", "Invented abstract")])
def test_external_embedded_record_must_equal_the_bound_actual_api_record(tmp_path, field, changed):
    overlay, xml, acquisition = inputs(tmp_path)
    source_id, _, _ = external_input(overlay, tmp_path)
    data = json.loads(overlay.read_text())
    data["additional_external_sources"][source_id]["primary_api_record"][field] = changed
    overlay.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="bound API collection"):
        mod.bind_overlay(overlay, [(xml, acquisition)], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_external_api_response_bytes_must_match_the_original_receipt(tmp_path):
    overlay, xml, acquisition = inputs(tmp_path)
    _, api, _ = external_input(overlay, tmp_path)
    api.write_text(api.read_text() + " ")
    with pytest.raises(ValueError, match="API response custody"):
        mod.bind_overlay(overlay, [(xml, acquisition)], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize("field", ["doi", "title", "abstract"])
def test_missing_own_api_identity_fields_cannot_be_filled_from_guesses(tmp_path, field):
    overlay, xml, acquisition = inputs(tmp_path)
    source_id, api, _ = external_input(overlay, tmp_path)
    data = json.loads(overlay.read_text())
    record = data["additional_external_sources"][source_id]
    record["primary_api_record"].pop(field)
    api.write_text(json.dumps({"collection": [record["primary_api_record"]]}))
    record["metadata_receipt"]["sha256"] = mod.sha(api.read_bytes())
    overlay.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="lacks required"):
        mod.bind_overlay(overlay, [(xml, acquisition)], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_every_external_document_is_checked_before_any_new_export_is_published(tmp_path):
    overlay, xml, acquisition = inputs(tmp_path)
    external_input(overlay, tmp_path)
    data = json.loads(overlay.read_text())
    data["external_publications"] = {"reference:incomplete": {"source_id": "reference:incomplete"}}
    overlay.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="required canonical fields"):
        mod.bind_overlay(overlay, [(xml, acquisition)], tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()
