"""Scientific identity/custody checks for exceptional source recoveries."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "illumina_external_sources", Path(__file__).parents[1] / "scripts/illumina_external_sources.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@pytest.mark.parametrize("url, expected", [
    ("http://onlinelibrary.wiley.com/doi/10.1111/2041-210X.12435/abstract", "10.1111/2041-210x.12435"),
    ("http://biorxiv.org/content/early/2014/03/05/003236", "10.1101/003236"),
    ("https://www.illumina.com/ http:/pubs.acs.org/doi/abs/10.1021/ja00365a069", "10.1021/ja00365a069"),
    ("https://www.illumina.com", None),
])
def test_normalize_doi_leads_without_pretending_identity_is_verified(url, expected):
    assert mod.doi_from_reference({"url": url}) == expected


def test_bound_cache_must_match_registered_bytes(tmp_path):
    directory = tmp_path / "paper/registration_v3"
    directory.mkdir(parents=True)
    record = {"identity": {"doi": "10.1111/example", "title": "Example"},
              "status": "registered_for_fresh_source_review", "article_selection": {"kind": "whole_article"}}
    for key in ("original", "rendition", "metadata"):
        path = directory / key
        path.write_text(key)
        record[key], record[key + "_sha256"] = str(path), mod.sha(path.read_bytes())
    registration = directory / "registration.json"
    registration.write_text(json.dumps(record))
    assert mod.cached_registration("10.1111/example", [tmp_path])[0] == registration
    Path(record["rendition"]).write_text("corrupted")
    with pytest.raises(ValueError, match="custody"):
        mod.cached_registration("10.1111/example", [tmp_path])


def test_html_rendition_preserves_paragraphs_and_excludes_navigation():
    parser = mod.PMCArticleHTML()
    parser.feed('<nav>Exclude navigation</nav><meta name="citation_pmid" content="12">'
                '<section class="body main-article-body"><h2>Methods</h2>'
                '<section><p>First <i>reaction</i>.</p><p>Second reaction.</p></section></section>'
                '<footer>Exclude footer</footer>')
    assert parser.citations["citation_pmid"] == ["12"]
    assert parser.text() == "Methods\n\nFirst reaction.\n\nSecond reaction.\n"


def test_prior_public_html_requires_exact_custody_and_article_identity(tmp_path):
    source = tmp_path / "original.html"
    html = '<meta name="citation_pmid" content="99"><meta name="citation_title" content="Wrong">'
    html += '<section class="main-article-body"><p>' + "Methods. " * 150 + '</p></section>'
    source.write_text(html)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"request_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12/",
        "http_status": 200, "path": str(source), "sha256": mod.sha(source.read_bytes())}))
    with pytest.raises(ValueError, match="PMID"):
        mod.register_existing_pmc_html({"pmid": "12", "title": "Target", "identifiers": {"pmc": ["PMC12"]}},
                                       receipt, tmp_path / "new")
    assert not (tmp_path / "new/public_html/PMC12/article.txt").exists()


def test_recovered_card_overlay_never_rewrites_empty_original(tmp_path):
    png = tmp_path / "diagram.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    receipt = tmp_path / "receipt.json"
    url = "https://www.illumina.com/mda.png"
    receipt.write_text(json.dumps({"card_id": "mda-ims-mda-midas", "url": url,
        "path": str(png), "sha256": mod.sha(png.read_bytes()), "bytes": png.stat().st_size,
        "discovery_basis": "Explicit public recovery"}))
    inventory = {"cards": [{"record_id": "mda-ims-mda-midas", "source_title": None, "references": []}]}
    before = copy.deepcopy(inventory)
    result = mod.recovery_bundle(inventory, {"external_publications": {}, "recovered_card_references": {}}, receipt)
    overlay = result["card_overlays"]["mda-ims-mda-midas"]
    assert inventory == before
    assert overlay["original_title"] is None
    assert overlay["original_card"]["references"] == []
    assert overlay["assets"][0]["source_id"] == "diagram:" + mod.asset_id(url)
    assert overlay["source_content_restored"] is False


def test_empty_homepage_references_are_not_publications(tmp_path):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"cards": [{"record_id": "one", "references": [{
        "reference_id": "one:references:1", "role": "references", "pmid": None,
        "source_reference": {"url": "https://www.illumina.com", "text": ""}}]}]}))
    result = mod.run(inventory, tmp_path / "out", [], network=False, include_mda=False)
    record = result["external_publications"]["one:references:1"]
    assert record["status"] == "malformed_homepage_link_not_publication"
    assert record["content_level"] == "unavailable"
    assert result["scientific_review"] is False
