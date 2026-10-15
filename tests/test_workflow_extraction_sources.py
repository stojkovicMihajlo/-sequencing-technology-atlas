"""Source packet checks establish provenance without asserting source completeness."""

import json
import sys

import pytest

from genomics_discovery.curation.workflow_extraction.sources import (
    build_packet,
    catalog,
    load_packet,
    read_batch,
    read_passage,
    search,
    validate_packet,
)


def packet_for(path, **kwargs):
    return build_packet(method_id="example", title="Example", scope="General procedure",
                        variant="version1", sources=[{"source_id": "original", "title": "Paper",
                        "url": "https://example.org/paper", "role": "primary_paper",
                        "path": str(path)}], **kwargs)


@pytest.fixture
def unequal_sources(tmp_path):
    primary, guide = tmp_path / "primary.txt", tmp_path / "guide.txt"
    primary.write_text("Index sequence associates the sample.\n\nIndex has an unknown setting.")
    guide.write_text("\n\n".join(["Index " * (10 + n) for n in range(7)]))
    return build_packet(
        method_id="example", title="Example", scope="General procedure", variant="version1",
        sources=[{"source_id": ident, "title": ident, "url": "https://example.org/" + ident,
                  "role": role, "path": str(path)} for ident, role, path in (
                      ("primary", "primary_paper", primary),
                      ("guide", "manufacturer_protocol", guide))],
    )


def test_source_scoped_search_exposes_primary_matches_hidden_by_long_dependency(unequal_sources):
    packet = unequal_sources
    global_result = search(packet, "index")
    assert global_result["total"] == 9 and global_result["truncated"] is True
    assert global_result["source_scope"] == ["primary", "guide"]
    assert {hit["source_id"] for hit in global_result["hits"]} == {"guide"}
    before = json.dumps(packet, sort_keys=True)
    scoped = search(packet, "index", source_id="primary")
    assert scoped["source_scope"] == ["primary"] and scoped["total"] == 2
    assert scoped["truncated"] is False and scoped["limit"] == 6
    assert {hit["source_id"] for hit in scoped["hits"]} == {"primary"}
    assert len(search(packet, "index", limit=1, source_id="primary")["hits"]) == 1
    assert json.dumps(packet, sort_keys=True) == before
    miss = search(packet, "absent_word", source_id="primary")
    assert miss["total"] == 0 and miss["source_scope"] == ["primary"]
    assert "no search hit proves" in miss["boundary"]


@pytest.mark.parametrize("kwargs", [{"source_id": "missing"}, {"limit": True},
                                    {"limit": 1.5}, {"limit": 21}])
def test_scoped_search_refuses_unknown_source_and_invalid_limits(unequal_sources, kwargs):
    with pytest.raises(ValueError):
        search(unequal_sources, "index", **kwargs)


def test_source_search_rejects_query_without_lexical_terms(unequal_sources):
    with pytest.raises(ValueError, match="lexical terms"):
        search(unequal_sources, "???")


def test_source_search_cli_passes_source_scope_and_result_boundary(
    unequal_sources, tmp_path, monkeypatch, capsys,
):
    from genomics_discovery.curation.workflow_extraction.__main__ import main

    saved = tmp_path / "packet.json"
    saved.write_text(json.dumps(unequal_sources))
    monkeypatch.setattr(sys, "argv", ["workflow_extraction", "search", str(saved), "index",
                                     "--source-id", "primary", "--limit", "1"])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["source_scope"] == ["primary"]
    assert result["total"] == 2 and result["limit"] == 1 and result["truncated"] is True
    assert result["hits"][0]["source_id"] == "primary"


def test_jats_preserves_identity_locators_and_all_branches(tmp_path):
    source = tmp_path / "article.xml"
    source.write_text('''<article><front><article-meta><article-id pub-id-type="doi">10.x/test
    </article-id><title-group><article-title>Actual paper</article-title></title-group>
    </article-meta></front><body><sec id="method"><title>Methods</title>
    <p>Take an aliquot. Keep the remaining library.</p><sec id="qc"><title>QC</title>
    <p>Quantify the aliquot.</p></sec></sec></body></article>''')
    packet = packet_for(source)
    registered = packet["sources"][0]
    assert registered["document_identity"]["title"] == "Actual paper"
    assert len(registered["passages"]) == 2
    assert "method/p1" in registered["passages"][0]["locator"]
    assert "qc/p1" in registered["passages"][1]["locator"]
    hit = search(packet, "quantify")["hits"][0]
    assert read_passage(packet, hit["source_id"], hit["passage_id"])["text"] == (
        "Quantify the aliquot.")
    assert catalog(packet)["sources"][0]["passage_count"] == 2


def test_changed_source_and_forged_rendition_are_rejected(tmp_path):
    source = tmp_path / "paper.txt"
    source.write_text("Recover tagged fragments.")
    packet = packet_for(source)
    packet["sources"][0]["passages"][0]["text"] = "Invent an additional reaction."
    with pytest.raises(ValueError, match="Stored rendition"):
        validate_packet(packet)
    packet = packet_for(source)
    saved = tmp_path / "packet.json"
    saved.write_text(json.dumps(packet))
    source.write_text("Changed original source.")
    with pytest.raises(ValueError, match="Source changed"):
        load_packet(saved)


def test_long_text_is_bounded_without_discarding_content(tmp_path):
    source = tmp_path / "protocol.txt"
    text = "material transformation " * 700
    source.write_text(text)
    packet = packet_for(source)
    passages = packet["sources"][0]["passages"]
    assert all(len(p["text"]) <= 4000 for p in passages)
    assert "".join(p["text"] for p in passages) == " ".join(text.split())
    with pytest.raises(ValueError, match="Unknown registered"):
        read_passage(packet, "other", "p00001")


def test_source_roles_duplicates_and_binary_renditions_cannot_silently_pass(tmp_path):
    source = tmp_path / "paper.txt"
    source.write_text("Source.")
    packet = packet_for(source)
    packet["sources"].append(dict(packet["sources"][0]))
    with pytest.raises(ValueError, match="unique"):
        validate_packet(packet)
    source.write_bytes(b"%PDF-1.7 fake text")
    with pytest.raises(ValueError, match="text rendition"):
        packet_for(source)


def test_table_cells_and_figure_titles_preserve_context(tmp_path):
    source = tmp_path / "article.xml"
    source.write_text('''<article><front><article-meta><title-group>
    <article-title>Paper</article-title></title-group></article-meta></front><body>
    <sec id="method"><title>Methods</title><table-wrap id="T1"><label>Table 1</label>
    <table><tr><th>Reagent</th><th>Volume</th></tr><tr><td>Buffer</td><td>10 µl</td>
    </tr></table></table-wrap><fig id="F1"><label>Figure 1</label><caption>
    <title>Critical branch</title><p>Withdraw an aliquot.</p></caption></fig>
    <fig id="F2"><caption><title>Another necessary operation</title></caption></fig>
    </sec></body></article>''')
    passages = packet_for(source)["sources"][0]["passages"]
    assert len(passages) == 3
    assert "Reagent | Volume" in passages[0]["text"]
    assert "Buffer | 10 µl" in passages[0]["text"]
    assert "ReagentVolume" not in passages[0]["text"]
    assert "Critical branch" in passages[1]["text"]
    assert "Figure 1" in passages[1]["text"] and "F1" in passages[1]["locator"]
    assert "Another necessary operation" in passages[2]["text"]


def test_methods_catalogue_uses_document_ancestry_without_changing_evidence(tmp_path):
    source = tmp_path / "article.xml"
    source.write_text('''<article><front><article-meta><title-group>
    <article-title>Paper</article-title></title-group></article-meta></front><body>
    <sec id="background"><title>Background</title><p>General context.</p></sec>
    <sec id="methods"><title>Materials and methods</title><p>Prepare input.</p>
    <sec id="qc"><title>Quality control</title><p>Check an aliquot.</p>
    <p>Keep the remaining sample.</p></sec></sec>
    <sec id="results"><title>Results</title><p>Observed output.</p></sec>
    </body></article>''')
    packet = packet_for(source)
    before = json.dumps(packet, sort_keys=True)
    all_sections = catalog(packet)["sources"][0]
    focused = catalog(packet, focus="methods")["sources"][0]
    assert all_sections["passage_count"] == focused["passage_count"] == 5
    assert all_sections["listed_passage_count"] == 5 and focused["listed_passage_count"] == 3
    assert list(focused["sections"]) == [
        "methods — Materials and methods", "qc — Materials and methods > Quality control"]
    assert list(focused["sections"].values()) == [["p00002"], ["p00003", "p00004"]]
    assert focused["method_sections"] == list(focused["sections"])
    assert focused["focus_basis"] == "structural section headings"
    assert read_passage(packet, "original", "p00005")["text"] == "Observed output."
    assert json.dumps(packet, sort_keys=True) == before


def test_methods_catalogue_retains_unstructured_or_ambiguous_context(tmp_path):
    text = tmp_path / "supplement.txt"
    text.write_text("Protocol branch.\n\nA related observation.")
    packet = packet_for(text)
    focused = catalog(packet, focus="methods")["sources"][0]
    assert focused["sections"] == {"document": ["p00001", "p00002"]}
    assert focused["listed_passage_count"] == focused["passage_count"] == 2
    source = tmp_path / "ambiguous.xml"
    source.write_text('''<article><front><article-meta><title-group>
    <article-title>Paper</article-title></title-group></article-meta></front><body>
    <sec><title>Background</title><p>Context.</p></sec>
    <sec><title>Methods</title><p>Prepare input.</p></sec></body></article>''')
    focused = catalog(packet_for(source), focus="methods")["sources"][0]
    assert focused["listed_passage_count"] == focused["passage_count"] == 2
    assert focused["method_sections"] == []
    assert "all groups retained" in focused["focus_basis"]
    with pytest.raises(ValueError, match="Catalogue focus"):
        catalog(packet, focus="fictional")


def test_read_batch_preserves_order_text_locators_and_per_document_hashes(tmp_path):
    first, second = tmp_path / "first.txt", tmp_path / "second.txt"
    first.write_text("First source passage.\n\nSecond source passage.")
    second.write_text("A separately registered supplement.")
    packet = build_packet(method_id="example", title="Example", scope="General procedure",
                          variant="version1", sources=[{
                              "source_id": sid, "title": sid, "url": "https://example.org/" + sid,
                              "role": role, "path": str(path),
                          } for sid, role, path in (("first", "primary_paper", first),
                                                    ("second", "supplement", second))])
    before = json.dumps(packet, sort_keys=True)
    refs = [{"source_id": sid, "passage_id": pid} for sid, pid in (
        ("second", "p00001"), ("first", "p00002"), ("first", "p00001"))]
    result = read_batch(packet, refs)
    assert result["passages"] == [read_passage(packet, ref["source_id"], ref["passage_id"])
                                  for ref in refs]
    assert result["passages"][0]["role"] == "supplement"
    assert result["passages"][0]["original_sha256"] != result["passages"][1]["original_sha256"]
    assert result["passages"][1]["text"] == "Second source passage."
    assert json.dumps(packet, sort_keys=True) == before
    assert catalog(packet, focus="methods")["sources"][1]["focus_basis"] == (
        "registered supplement/protocol role")


@pytest.mark.parametrize("refs", [[], [{"source_id": "original", "passage_id": "p00001"}] * 2,
                                  [{"source_id": "original", "passage_id": f"p{i:05d}"}
                                   for i in range(1, 10)],
                                  [{"source_id": "original", "passage_id": "missing"}],
                                  [{"source_id": "original", "passage_id": "p00001",
                                    "invented_scope": True}]])
def test_read_batch_rejects_empty_duplicate_oversized_or_invalid_selection(tmp_path, refs):
    source = tmp_path / "paper.txt"
    source.write_text("A source passage.")
    with pytest.raises(ValueError):
        read_batch(packet_for(source), refs)


def test_structural_catalogue_cannot_use_changed_original_bytes(tmp_path):
    source = tmp_path / "article.xml"
    source.write_text('''<article><front><article-meta><title-group>
    <article-title>Paper</article-title></title-group></article-meta></front><body>
    <sec id="methods"><title>Methods</title><p>Prepare input.</p></sec></body></article>''')
    packet = packet_for(source)
    source.write_text(source.read_text().replace("Methods", "Discussion"))
    with pytest.raises(ValueError, match="Source changed"):
        catalog(packet, focus="methods")
