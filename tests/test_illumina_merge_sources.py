"""Snapshot-level checks: reparse identities, preserve listings, import custody."""
import importlib.util
import json
from pathlib import Path

import pytest

from genomics_discovery.curation import illumina_sources as sources

spec = importlib.util.spec_from_file_location(
    "illumina_merge_sources", Path(__file__).parents[1] / "scripts/illumina_merge_sources.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def inputs(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    card = {"record_id": "mda-ims-mda-midas", "source_title": None, "diagram_asset_ids": [], "references": []}
    inventory = {"format_version": sources.FORMAT, "cards": [card], "assets": [], "statistics": {"cards": 1}}
    sources.write(parent / "inventory.json", inventory)
    xml = b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12</PMID>
    <Article><ArticleTitle>Target</ArticleTitle></Article></MedlineCitation><PubmedData>
    <ArticleIdList><ArticleId IdType="pubmed">12</ArticleId></ArticleIdList><ReferenceList>
    <Reference><ArticleIdList><ArticleId IdType="pmc">PMC99</ArticleId></ArticleIdList></Reference>
    </ReferenceList></PubmedData></PubmedArticle></PubmedArticleSet>'''
    (parent / "batch.xml").write_bytes(xml)
    state = {"format_version": sources.ACQUIRED_FORMAT,
             "inventory_sha256": mod.file_hash(parent / "inventory.json"),
             "assets": [], "publications": {"12": {"pmid": "12", "identity_status": "pubmed_record_retrieved",
                "identifiers": {"pubmed": ["12"], "pmc": ["PMC99"]}, "metadata_source": "batch.xml"}},
             "publication_batches": [{"status": "downloaded", "path": "batch.xml", "sha256": sources.sha(xml),
                                      "requested_pmids": ["12"]}], "fulltexts": {}, "statistics": {},
             "status": "completed_with_failures", "scientific_review": False}
    sources.write(parent / "sources.json", state)
    recovery = tmp_path / "recovery"
    recovery.mkdir()
    image = recovery / "mda.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    text = recovery / "article.txt"
    text.write_text("Recovered primary source text.")
    aid = sources.asset_id("https://www.illumina.com/mda.png")
    bundle = {"format_version": "illumina_source_recovery_bundle/1.0", "scientific_review": False,
        "external_publications": {"recovered:12": {"source_id": "reference:recovered:12", "content_level": "fulltext",
            "text_path": str(text), "text_sha256": mod.file_hash(text)}},
        "recovered_card_references": {card["record_id"]: [{"reference_id": "recovered:12", "pmid": "12"}]},
        "card_overlays": {card["record_id"]: {"original_card": card, "recovered_title": "MDA / IMS-MDA / MIDAS",
            "title_basis": "Recovered caption", "scope_note": "Grouped variants", "source_content_restored": False,
            "assets": [{"asset_id": aid, "source_id": "diagram:" + aid, "kind": "method_diagram",
                "url": "https://www.illumina.com/mda.png", "card_ids": [card["record_id"]],
                "path": str(image), "sha256": mod.file_hash(image), "status": "downloaded"}]}}}
    sources.write(recovery / "recovery_bundle.json", bundle)
    return parent / "sources.json", recovery / "recovery_bundle.json"


def test_merge_corrects_own_ids_imports_files_and_preserves_original_listing(tmp_path):
    parent, bundle = inputs(tmp_path)
    before = {path: path.read_bytes() for path in (parent, parent.parent / "inventory.json", bundle)}
    output = tmp_path / "merged"
    result = mod.merge_snapshot(parent, bundle, output)
    inventory = json.loads((output / "inventory.json").read_text())
    card = inventory["cards"][0]
    assert card["source_title"] is None
    assert card["recovered_title"] == "MDA / IMS-MDA / MIDAS"
    assert card["references"] == []
    assert card["recovered_references"][0]["pmid"] == "12"
    assert len(card["diagram_asset_ids"]) == 1
    assert result["publications"]["12"]["identifiers"] == {"pubmed": ["12"]}
    assert result["metadata_identifier_reaudit"]["records_with_corrected_identifiers"] == 1
    assert result["scientific_review"] is False
    paths = [result["external_publications"]["recovered:12"]["text_path"], result["assets"][0]["path"],
             result["publications"]["12"]["metadata_source"]]
    assert all(Path(path).is_relative_to(output) and Path(path).is_file() for path in paths)
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert mod.file_hash(output / "inventory.json") == result["inventory_sha256"]
    with pytest.raises(FileExistsError):
        mod.merge_snapshot(parent, bundle, output)


def test_bad_bundle_custody_is_rejected_before_source_snapshot_publication(tmp_path):
    parent, bundle = inputs(tmp_path)
    (bundle.parent / "article.txt").write_text("Changed source")
    with pytest.raises(ValueError, match="custody"):
        mod.merge_snapshot(parent, bundle, tmp_path / "rejected")
    assert not (tmp_path / "rejected/sources.json").exists()


def test_parent_inventory_pin_is_required(tmp_path):
    parent, bundle = inputs(tmp_path)
    with (parent.parent / "inventory.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="inventory custody"):
        mod.merge_snapshot(parent, bundle, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


def test_missing_original_pubmed_batch_cannot_leave_old_polluted_ids_unchanged(tmp_path):
    parent, bundle = inputs(tmp_path)
    state = json.loads(parent.read_text())
    state["publication_batches"] = []
    parent.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="same retrieved publication"):
        mod.merge_snapshot(parent, bundle, tmp_path / "rejected")
    assert not (tmp_path / "rejected/sources.json").exists()


def test_explicit_prefix_mapping_imports_staged_bytes_without_rewriting_historical_file(tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    text = staged / "paper.txt"
    text.write_text("Source bytes")
    files = mod.SnapshotFiles(tmp_path / "output", [(Path("/original/machine"), staged)], [])
    result = files.records({"text_path": "/original/machine/paper.txt", "text_sha256": mod.file_hash(text)}, tmp_path)
    assert Path(result["text_path"]).is_relative_to(tmp_path / "output")
    assert result["snapshot_file_imports"]["text_path"]["declared_path"] == "/original/machine/paper.txt"
    assert text.read_text() == "Source bytes"


def test_recovered_overlay_cannot_target_a_different_original_card(tmp_path):
    parent, bundle = inputs(tmp_path)
    data = json.loads(bundle.read_text())
    data["card_overlays"]["mda-ims-mda-midas"]["original_card"]["source_title"] = "Invented old title"
    bundle.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="original inventory card"):
        mod.merge_snapshot(parent, bundle, tmp_path / "rejected")
    assert not (tmp_path / "rejected/sources.json").exists()


def cache_inputs(tmp_path, parent):
    root = tmp_path / "cached"
    root.mkdir()
    original = root / "original.xml"
    original.write_text('<article><front><article-meta><article-id pub-id-type="pmid">12</article-id>'
                        '</article-meta></front><body><p>' + "Methods and materials. " * 20 + '</p></body></article>')
    text = root / "article.txt"
    text.write_text("Methods and materials. " * 20)
    blocks = root / "blocks.json"
    blocks.write_text(json.dumps({"source_id": "paper:12", "original_sha256": mod.file_hash(original), "blocks": []}))
    registration = root / "cache_registration.json"
    registration.write_text('{"kind":"cache source registration"}')
    packet = root / "cache_packet.json"
    packet.write_text('{"kind":"historical source packet"}')
    record = {"source_id": "paper:12", "status": "fulltext_acquired", "format": "JATS",
              "identifiers": {"pmid": ["12"]}, "path": "original.xml", "sha256": mod.file_hash(original),
              "text_path": "article.txt", "text_sha256": mod.file_hash(text), "blocks_path": "blocks.json",
              "blocks_sha256": mod.file_hash(blocks), "cache_registration_path": "cache_registration.json",
              "cache_registration_sha256": mod.file_hash(registration),
              "cache_provenance": {"registration": {"path": "/historical/missing/registration.json",
                  "sha256": mod.file_hash(registration)}, "packets": [{"preserved_path": "cache_packet.json",
                  "packet": {"path": "/historical/missing/packet.json", "sha256": mod.file_hash(packet)}}]}}
    ledger = {"format_version": "illumina_cached_source_recovery/1.0", "parent_acquisition": {
        "path": str(parent), "sha256": mod.file_hash(parent)}, "recoveries": {"12": record}}
    path = root / "recoveries.json"
    path.write_text(json.dumps(ledger))
    return path


def test_exact_parent_cached_recovery_imports_all_current_files_and_preserves_old_provenance(tmp_path):
    parent, bundle = inputs(tmp_path)
    cached = cache_inputs(tmp_path, parent)
    before = cached.read_bytes()
    output = tmp_path / "merged"
    result = mod.merge_snapshot(parent, bundle, output, cached_recoveries=cached)
    record = result["fulltexts"]["12"]
    for key in ("path", "text_path", "blocks_path", "cache_registration_path"):
        assert Path(record[key]).is_relative_to(output) and Path(record[key]).is_file()
    assert Path(record["cache_packet_copies"][0]["path"]).is_relative_to(output)
    assert record["cache_provenance"]["registration"]["path"] == "/historical/missing/registration.json"
    assert result["statistics"]["cached_fulltexts_merged"] == 1
    assert result["statistics"]["fulltexts_acquired"] == 1
    assert cached.read_bytes() == before


def test_cache_ledger_for_another_parent_cannot_be_merged(tmp_path):
    parent, bundle = inputs(tmp_path)
    cached = cache_inputs(tmp_path, parent)
    data = json.loads(cached.read_text())
    data["parent_acquisition"]["sha256"] = "0" * 64
    cached.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="parent acquisition"):
        mod.merge_snapshot(parent, bundle, tmp_path / "rejected", cached_recoveries=cached)
    assert not (tmp_path / "rejected/sources.json").exists()


def test_tampered_cached_reading_blocks_cannot_be_imported(tmp_path):
    parent, bundle = inputs(tmp_path)
    cached = cache_inputs(tmp_path, parent)
    (cached.parent / "blocks.json").write_text('{"altered":true}')
    with pytest.raises(ValueError, match="custody"):
        mod.merge_snapshot(parent, bundle, tmp_path / "rejected", cached_recoveries=cached)
    assert not (tmp_path / "rejected/sources.json").exists()


def test_cached_own_article_front_must_agree_even_if_ledger_ids_claim_target(tmp_path):
    parent, bundle = inputs(tmp_path)
    cached = cache_inputs(tmp_path, parent)
    original = cached.parent / "original.xml"
    original.write_text(original.read_text().replace('>12</article-id>', '>99</article-id>'))
    data = json.loads(cached.read_text())
    data["recoveries"]["12"]["sha256"] = mod.file_hash(original)
    blocks = cached.parent / "blocks.json"
    blocks.write_text(json.dumps({"source_id": "paper:12", "original_sha256": mod.file_hash(original)}))
    data["recoveries"]["12"]["blocks_sha256"] = mod.file_hash(blocks)
    cached.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="article front"):
        mod.merge_snapshot(parent, bundle, tmp_path / "rejected", cached_recoveries=cached)
    assert not (tmp_path / "rejected/sources.json").exists()
