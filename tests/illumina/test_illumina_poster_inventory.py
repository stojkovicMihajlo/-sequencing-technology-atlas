"""PDF indexing/source controls; lexical candidates are never verified methods."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("illumina_poster_inventory", Path(__file__).resolve().parents[2]
                                            / "scripts/illumina" / "poster_inventory.py")
poster = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(poster)


def page(words):
    return {"page": 1, "width": 1000, "height": 1000, "words": [
        {"word_id": f"w{i}", "text": text, "bbox": box} for i, (text, box) in enumerate(words)]}


def test_exact_alias_ambiguity_and_distinct_apostrophes():
    cards = [{"record_id": "first", "source_title": "Nuc-Seq"},
             {"record_id": "second", "source_title": "nuc-seq/SNES"},
             {"record_id": "three_prime", "source_title": "3'-Seq"},
             {"record_id": "three", "source_title": "3Seq"}]
    aliases = poster.alias_index(cards)
    assert aliases["nuc-seq"] == ["first", "second"]
    assert aliases["3'-seq"] == ["three_prime"]
    assert aliases["3seq"] == ["three"]
    assert poster.normalize("3′-Seq") == "3'-seq"


def test_partial_method_tokens_are_not_exact_aliases():
    cards = [{"record_id": "rad", "source_title": "RAD"},
             {"record_id": "ims", "source_title": "IMS-MDA"}]
    rows = poster.occurrences(page([("RAD-Seq", [10, 10, 80, 20]),
                                    ("IMS-MDA", [10, 40, 80, 50])]), poster.alias_index(cards), cards)
    assert not next(x for x in rows if x["label"] == "rad-seq")["catalogue_candidates"]
    assert next(x for x in rows if x["label"] == "ims-mda")["catalogue_candidates"] == ["ims"]


def test_mda_recovery_is_a_slug_hint_not_an_assignment():
    cards = [{"record_id": "mda-ims-mda-midas", "source_title": None}]
    rows = poster.occurrences(page([("MDA", [10, 10, 60, 20]),
                                    ("MIDAS", [10, 50, 60, 60])]), poster.alias_index(cards), cards)
    assert {x["label"] for x in rows} == {"mda", "midas"}
    assert all(not x["catalogue_candidates"] for x in rows)
    assert all(x["record_slug_hints_not_matches"] == ["mda-ims-mda-midas"] for x in rows)
    assert all(x["verification"] == "not_visually_reviewed" for x in rows)


def test_bbox_text_is_preserved_with_namespaced_xhtml_and_empty_pages():
    raw = b'''<html xmlns="http://www.w3.org/1999/xhtml"><body><doc>
    <page width="100" height="200"><word xMin="1" yMin="2" xMax="20" yMax="12">A&amp;B-Seq</word></page>
    <page width="100" height="200"></page></doc></body></html>'''
    pages = poster.parse_bbox(raw)
    assert len(pages) == 2 and not pages[1]["words"]
    assert pages[0]["words"][0] == {"word_id": "w00001", "text": "A&B-Seq", "bbox": [1, 2, 20, 12]}


@pytest.mark.parametrize("bad", [b"\x02", b"\x07", b"&#x02;", b"&#2;"])
def test_poppler_illegal_xml_control_preserved_as_visible_damage_with_receipt(bad):
    raw = b'<doc><page width="100" height="200">\n<word xMin="1" yMin="2" xMax="30" yMax="12">A' + bad + b'-Seq</word></page></doc>'
    receipt = {}
    pages = poster.parse_bbox(raw, sanitation_receipt=receipt)
    assert pages[0]["words"][0]["text"] == "A\ufffd-Seq"
    assert "text_integrity" in pages[0]["words"][0]
    assert receipt["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["replacements"] == 1
    assert receipt["events"][0]["original_codepoint"] == ("U+0007" if bad == b"\x07" else "U+0002")
    assert receipt["events"][0]["line"] == 2
    rows = poster.occurrences(pages[0], poster.alias_index([{"record_id": "a", "source_title": "A-Seq"}]), [{"record_id": "a", "source_title": "A-Seq"}])
    assert not any(r["catalogue_candidates"] for r in rows)


def test_valid_entities_and_scientific_label_characters_remain_exact():
    raw = '<doc><page width="100" height="200"><word xMin="1" yMin="2" xMax="20" yMax="12">3′-Seq&amp;Mg&#178;</word></page></doc>'.encode()
    sanitized, receipt = poster.sanitize_bbox_xml(raw)
    assert sanitized == raw
    assert receipt["replacements"] == 0
    assert poster.parse_bbox(raw)[0]["words"][0]["text"] == "3′-Seq&Mg²"


def test_alias_after_damaged_character_is_not_exact_candidate():
    raw = b'<doc><page width="100" height="200"><word xMin="1" yMin="2" xMax="30" yMax="12">prefix\x07B-Seq</word></page></doc>'
    cards = [{"record_id": "b", "source_title": "B-Seq"}]
    found = poster.occurrences(poster.parse_bbox(raw)[0], poster.alias_index(cards), cards)
    assert found and all(not row["catalogue_candidates"] for row in found)
    assert all(row["matching"] == "damaged_text_not_eligible_for_exact_matching" for row in found)


def test_noncontrol_broken_xml_is_not_repaired():
    with pytest.raises(poster.ET.ParseError):
        poster.parse_bbox(b'<doc><page width="100" height="200"><word>A&B</word></page></doc>')


@pytest.mark.parametrize("attributes", ['width="nan" height="100"', 'width="0" height="100"'])
def test_invalid_page_geometry_rejected(attributes):
    with pytest.raises(ValueError):
        poster.parse_bbox(f"<doc><page {attributes}/></doc>".encode())


def test_column_row_regions_remain_proposals():
    cards = [{"record_id": "a", "source_title": "A-Seq"}, {"record_id": "b", "source_title": "B-Seq"}]
    p = page([("A-Seq", [10, 100, 60, 115]), ("B-Seq", [10, 200, 60, 215]),
              ("A-Seq", [510, 100, 560, 115])])
    rows = poster.occurrences(p, poster.alias_index(cards), cards)
    poster.propose_rows(p, rows)
    assert len(rows) == 3
    for row in rows:
        box = row["row_region_proposal"]
        assert box[0] < box[2] and box[1] < box[3]
        assert row["workflow_row_verified"] is False
    assert rows[0]["row_region_proposal"][2] == 510


def test_reference_mentions_are_not_silently_workflow_rows():
    cards = [{"record_id": "a", "source_title": "A-Seq"}]
    p = page([("A-Seq", [10, 100, 50, 110]), ("Smith", [55, 100, 85, 110]),
              ("et", [90, 100, 100, 110]), ("al.", [105, 100, 120, 110]),
              ("(2015)", [125, 100, 160, 110])])
    rows = poster.occurrences(p, poster.alias_index(cards), cards)
    row = next(x for x in rows if x["label"] == "a-seq")
    assert row["appearance"] == "reference_mention"
    assert not row["workflow_row_verified"]


def test_pdf_hash_mismatch_fails_before_running_converter(tmp_path):
    pdf = tmp_path / "poster.pdf"
    pdf.write_bytes(b"%PDF-test")
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps({"assets": [{"asset_id": "poster", "kind": "rna_poster",
        "status": "downloaded", "url": "https://example.org/poster.pdf", "path": "poster.pdf",
        "sha256": "0" * 64}]}))
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"cards": []}))
    with pytest.raises(ValueError, match="PDF changed"):
        poster.build_index(sources, inventory, tmp_path / "output", executable="not-invoked")


def test_index_pipeline_preserves_pages_and_does_not_promote_candidates(tmp_path):
    pdf = tmp_path / "poster.pdf"
    pdf.write_bytes(b"%PDF-test")
    tool = tmp_path / "fake_pdftotext"
    tool.write_text("#!/bin/sh\ncase \"$1\" in\n"
        "-bbox) cat > \"$5\" <<'EOF'\n<doc><page width=\"100\" height=\"100\">"
        "<word xMin=\"1\" yMin=\"5\" xMax=\"30\" yMax=\"15\">MDA</word></page>"
        "<page width=\"100\" height=\"100\"></page></doc>\nEOF\n;;\n"
        "-layout) echo 'MDA' > \"$5\";;\nesac\n")
    tool.chmod(0o700)
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps({"assets": [{"asset_id": "poster", "kind": "rna_poster",
        "status": "downloaded", "url": "https://example.org/poster.pdf", "path": "poster.pdf",
        "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}]}))
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"cards": [{"record_id": "mda-ims-mda-midas", "source_title": None}]}))
    result = poster.build_index(sources, inventory, tmp_path / "output", executable=str(tool))
    assert result["verified_workflow_rows"] == 0
    resource = result["resources"][0]
    assert resource["index_status"] == "text_and_geometry_indexed"
    assert resource["pages"] == 2 and resource["empty_text_pages"] == [2]
    assert result["unmatched_occurrences"] == 1
    assert (tmp_path / "output" / "poster" / "page_0002.json").is_file()
    coverage = json.loads((tmp_path / "output" / "catalogue_coverage.json").read_text())
    assert not coverage[0]["occurrence_ids"] and coverage[0]["slug_hint_occurrence_ids"]
