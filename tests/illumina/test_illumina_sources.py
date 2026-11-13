import hashlib
import json
from pathlib import Path

import pytest

from genomics_discovery.illumina import sources as mod


def catalogue(tmp_path, rows):
    path = tmp_path / "cards.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def card(ident="one", *, image="http://www.illumina.com/shared.png", **extra):
    return {"record_id": ident, "source_title": "Method", "description_verbatim": "Source description.",
            "source_image_urls": [image], "references": [], "related_publications": [], **extra}


def test_inventory_preserves_empty_grouped_and_duplicate_image_cards(tmp_path):
    path = catalogue(tmp_path, [card(source_title="A/B", references=[{"pmid": "12", "text": "Listed reference"}]),
                                card("two", related_publications=[{"url": "http://www.ncbi.nlm.nih.gov/pubmed/12"}]),
                                {"record_id": "empty"}])
    result = mod.build_inventory(path)
    assert result["statistics"]["cards"] == 3
    assert result["statistics"]["unique_method_diagrams"] == 1
    assert result["statistics"]["unique_pmids"] == 1
    assert result["cards"][2]["source_record_status"] == "incomplete_source_record"
    diagram = next(a for a in result["assets"] if a["kind"] == "method_diagram")
    assert diagram["card_ids"] == ["one", "two"]
    assert diagram["url"].startswith("https:")
    assert result["publications"][0]["reference_roles"] == ["references", "related_publications"]
    assert result["cards"][0]["references"][0]["identity_status"] == "metadata_unverified"
    assert result["cards"][0]["source_id"] == "card:one"


def test_supplied_selector_controls_resource_discovery(tmp_path):
    path = catalogue(tmp_path, [card()])
    selector = tmp_path / "selector.js"
    selector.write_text(mod.RESOURCE_PATHS[0][1])
    result = mod.build_inventory(path, selector)
    assert result["statistics"]["collection_resources"] == 1
    assert sum(r["status"] == "not_found_in_supplied_selector" for r in result["resources"]) == 4
    assert result["selector"]["sha256"] == hashlib.sha256(selector.read_bytes()).hexdigest()


def test_duplicate_record_ids_rejected(tmp_path):
    with pytest.raises(ValueError, match="unique"):
        mod.build_inventory(catalogue(tmp_path, [card(), card()]))


def test_pubmed_markup_abstract_and_identifiers_remain_metadata():
    raw = b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12</PMID><Article>
    <ArticleTitle>A <i>gene</i> method</ArticleTitle><Journal><Title>Journal</Title>
    <JournalIssue><PubDate><Year>2016</Year></PubDate></JournalIssue></Journal>
    <Abstract><AbstractText Label="METHODS">A summary.</AbstractText></Abstract></Article>
    </MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="pmc">PMC12</ArticleId>
    <ArticleId IdType="doi">10.1/example</ArticleId></ArticleIdList></PubmedData>
    </PubmedArticle></PubmedArticleSet>'''
    record = mod.parse_pubmed(raw)["12"]
    assert record["title"] == "A gene method"
    assert record["identifiers"]["pmc"] == ["PMC12"]
    assert record["abstract"][0]["label"] == "METHODS"
    assert record["source_id"] == "metadata:12"
    assert record["origin_date_status"].startswith("not_established")
    assert "not_protocol" in record["scientific_support_status"]
    with pytest.raises(ValueError, match="PubMed"):
        mod.parse_pubmed(b"<html>Captcha</html>")


def receipt(url, path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"request_url": url, "final_url": url, "http_status": 200, "path": str(path),
            "sha256": mod.sha(raw), "bytes": len(raw)}


def test_cited_pubmed_and_pmc_identifiers_never_become_target_article_identifiers():
    raw = b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12</PMID>
    <Article><ArticleTitle>Target without PMC</ArticleTitle></Article></MedlineCitation>
    <PubmedData><ArticleIdList><ArticleId IdType="pubmed">12</ArticleId>
    <ArticleId IdType="doi">10.1/target</ArticleId></ArticleIdList>
    <ReferenceList><Reference><Citation>Cited paper</Citation><ArticleIdList>
    <ArticleId IdType="pubmed">99</ArticleId><ArticleId IdType="pmc">PMC99</ArticleId>
    </ArticleIdList></Reference></ReferenceList></PubmedData></PubmedArticle></PubmedArticleSet>'''
    target = mod.parse_pubmed(raw)["12"]
    assert target["identifiers"] == {"pubmed": ["12"], "doi": ["10.1/target"]}
    assert mod.acquire_pmc_fulltext(target, Path("unused"))["status"] == "no_pmc_identifier_in_pubmed_metadata"


def test_acquisition_media_signature_and_metadata_failures_are_nonpositive(tmp_path, monkeypatch):
    inventory = mod.build_inventory(catalogue(tmp_path, [card(references=[{"pmid": "12"}])]))
    inventory["assets"] = [a for a in inventory["assets"] if a["kind"] == "method_diagram"]
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory))

    def fetch(url, dest):
        if "efetch" in url:
            raise RuntimeError("Unavailable metadata")
        return receipt(url, dest, b"<html>not an image</html>")

    monkeypatch.setattr(mod, "fetch", fetch)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    out = tmp_path / "run"
    result = mod.acquire(path, out, metadata=True)
    assert result["status"] == "completed_with_failures"
    assert result["assets"][0]["status"] == "invalid_media"
    assert result["publication_batches"][0]["status"] == "failed"
    assert result["statistics"]["pmids_retrieved"] == 0
    assert json.loads((out / "sources.json").read_text())["scientific_review"] is False
    with pytest.raises(FileExistsError):
        mod.acquire(path, out)


def test_acquisition_rejects_unrequested_pubmed_record(tmp_path, monkeypatch):
    inventory = mod.build_inventory(catalogue(tmp_path, [card(references=[{"pmid": "12"}])]))
    inventory["assets"] = []
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory))
    raw = b"<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>99</PMID><Article><ArticleTitle>Wrong</ArticleTitle></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"
    monkeypatch.setattr(mod, "fetch", lambda url, dest: receipt(url, dest, raw))
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    result = mod.acquire(path, tmp_path / "run", metadata=True)
    assert result["status"] == "completed_with_failures"
    assert result["publications"] == {}
    assert "unrequested" in result["publication_batches"][0]["error"]


def test_oai_extracts_identity_bound_article_and_preserves_original_response(tmp_path, monkeypatch):
    article = b'<article><front><article-meta><article-id pub-id-type="pmid">12</article-id><article-id pub-id-type="pmc">PMC12</article-id></article-meta></front><body><p>Methods.</p></body></article>'
    response = b'<OAI-PMH><GetRecord><record><metadata>' + article + b'</metadata></record></GetRecord></OAI-PMH>'

    def fetch(url, dest, **options):
        assert url.startswith("https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/")
        assert options['preserve_http_error'] is True
        return receipt(url, dest, response)

    monkeypatch.setattr(mod, "fetch", fetch)
    result = mod.acquire_pmc_fulltext({"pmid": "12", "identifiers": {"pmc": ["PMC12"]}}, tmp_path)
    assert result["status"] == "fulltext_acquired"
    assert b"Methods." in (tmp_path / result["path"]).read_bytes()
    assert "Methods." in (tmp_path / result["text_path"]).read_text()
    assert (tmp_path / result["original_response"]["path"]).read_bytes() == response
    assert "subtree" in result["derivation"]
    assert result["scientific_support_status"] == "fulltext_available_not_content_reviewed"


def test_missing_pmc_and_non_oa_are_explicit(tmp_path, monkeypatch):
    assert mod.acquire_pmc_fulltext({"pmid": "12"}, tmp_path)["status"] == "no_pmc_identifier_in_pubmed_metadata"
    monkeypatch.setattr(mod, "fetch", lambda url, dest, **options: receipt(url, dest,
        b'<OAI-PMH><error code="cannotDisseminateFormat">Full text unavailable</error></OAI-PMH>'))
    result = mod.acquire_pmc_fulltext({"pmid": "12", "identifiers": {"pmc": ["PMC12"]}}, tmp_path)
    assert result["status"] == "fulltext_not_available_via_pmc_oai"
    assert result["reason"] == "Full text unavailable"


def test_http400_oai_protocol_body_is_an_availability_hold_not_an_article(tmp_path, monkeypatch):
    raw=b'<OAI-PMH><error code="cannotDisseminateFormat">The document is unsupported for the given prefix.</error></OAI-PMH>'
    def fetch(url,dest,**options):
        assert options['preserve_http_error']
        result=receipt(url,dest,raw);result['http_status']=400;return result
    monkeypatch.setattr(mod,'fetch',fetch)
    result=mod.acquire_pmc_fulltext({'pmid':'12','identifiers':{'pmc':['PMC12']}},tmp_path)
    assert result['status']=='fulltext_not_available_via_pmc_oai'
    assert result['original_response']['http_status']==400
    assert (tmp_path/result['original_response']['path']).read_bytes()==raw
    assert not (tmp_path/'papers/12/article.xml').exists()


def test_oai_wrong_article_identity_cannot_become_fulltext(tmp_path, monkeypatch):
    response = b'<OAI-PMH><GetRecord><record><metadata><article><front><article-meta><article-id pub-id-type="pmid">99</article-id><article-id pub-id-type="pmcid">PMC99</article-id></article-meta></front><body><p>Wrong paper.</p></body></article></metadata></record></GetRecord></OAI-PMH>'
    monkeypatch.setattr(mod, "fetch", lambda url, dest, **options: receipt(url, dest, response))
    result = mod.acquire_pmc_fulltext({"pmid": "12", "identifiers": {"pmc": ["PMC12"]}}, tmp_path)
    assert result["status"] == "failed"
    assert not (tmp_path / "papers/12/article.xml").exists()


def test_recovery_preserves_prior_bytes_and_rebases_asset_custody(tmp_path, monkeypatch):
    inv = mod.build_inventory(catalogue(tmp_path, [card(references=[{"pmid": "12"}])]))
    inv["assets"] = [a for a in inv["assets"] if a["kind"] == "method_diagram"]
    prior = tmp_path / "prior"
    prior.mkdir()
    mod.write(prior / "inventory.json", inv)
    image = b'\x89PNG\r\n\x1a\n'
    (prior / "image.png").write_bytes(image)
    state = {"format_version": mod.ACQUIRED_FORMAT, "assets": [{"status": "downloaded",
             "path": "image.png", "sha256": mod.sha(image)}],
             "publications": {"12": {"pmid": "12", "identity_status": "pubmed_record_retrieved"}},
             "fulltexts": {}, "publication_batches": []}
    mod.write(prior / "sources.json", state)
    before = (prior / "sources.json").read_bytes()
    monkeypatch.setattr(mod, "acquire_pmc_fulltext", lambda pub, out: {
        "source_id": "paper:" + pub["pmid"], "status": "fulltext_acquired"})
    out = tmp_path / "recovered"
    result = mod.recover_fulltext(prior / "sources.json", out)
    assert result["status"] == "completed"
    assert result["assets"][0]["path"] == str((prior / "image.png").resolve())
    assert (prior / "sources.json").read_bytes() == before
    assert result["parent_acquisition"]["sha256"] == mod.sha(before)
    assert (out / "inventory.json").read_bytes() == (prior / "inventory.json").read_bytes()


def test_recovery_rejects_changed_prior_asset(tmp_path):
    inv = mod.build_inventory(catalogue(tmp_path, [card()]))
    prior = tmp_path / "prior"
    prior.mkdir()
    mod.write(prior / "inventory.json", inv)
    (prior / "image.png").write_bytes(b"changed")
    mod.write(prior / "sources.json", {"format_version": mod.ACQUIRED_FORMAT,
        "assets": [{"path": "image.png", "sha256": mod.sha(b"original")}], "publications": {}, "fulltexts": {}})
    with pytest.raises(ValueError, match="custody"):
        mod.recover_fulltext(prior / "sources.json", tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_jats_reading_preserves_paragraph_boundaries_and_does_not_duplicate_figures():
    text, blocks = mod.jats_text(b'<article><front><article-meta><title-group><article-title>Title</article-title></title-group></article-meta></front><body><sec><title>Methods</title><p>First <italic>reaction</italic>.</p><p>Second reaction.</p><fig><caption><p>Caption.</p></caption></fig></sec></body></article>')
    assert "First reaction.\n\n[b00004] Second reaction." in text
    assert text.count("Caption.") == 1
    assert blocks[-1]["locator"].endswith("fig[1]")


def test_fulltext_requires_metadata_before_creating_output(tmp_path):
    out = tmp_path / "run"
    with pytest.raises(ValueError, match="requires"):
        mod.acquire(tmp_path / "missing.json", out, fulltext=True)
    assert not out.exists()


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:secret@example.com/x", "javascript:alert(1)"])
def test_unsafe_url_forms_rejected(url):
    with pytest.raises(ValueError):
        mod.https_url(url)
