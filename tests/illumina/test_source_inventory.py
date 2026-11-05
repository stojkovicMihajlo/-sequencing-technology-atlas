import pytest

from scripts.illumina.source_inventory import inventory


def source(ident, title, *, poster=None, footer=False):
    return {"record_id": ident, "title": title, "status": "supported",
        "batch": "footer_panels" if footer else "posters" if poster else "catalogue",
        "card": {"source_title": title, "source_page_url":
            "https://example.org/for-all-you-seq-" + poster + ".pdf" if poster else "https://example.org/" + ident},
        "candidate": {"method_names": ["INVENTED_METHOD_MUST_NOT_DEFINE_INVENTORY"]}}


def test_duplicates_and_grouped_headings_do_not_become_method_counts():
    rows = [source("card", "A / B"), source("rna", "A", poster="rna"),
            source("single", "A", poster="single-cell"), source("dna", "C", poster="dna")]
    result = inventory(rows)
    assert result["counts"]["source_occurrences"] == 4
    assert result["counts"]["reviewed_unique_method_count"] is None
    by_key = {x["label_key"]: x for x in result["label_review_queue"]}
    assert set(by_key) == {"a", "b", "c"}
    assert by_key["a"]["poster_record_ids"] == ["rna", "single"]
    assert by_key["a"]["grouped_source_record_ids"] == ["card"]
    assert by_key["b"]["coverage"] == "website_heading_only"
    assert by_key["c"]["coverage"] == "poster_heading_only"


def test_legacy_is_comparison_only_and_footer_does_not_inflate_main_coverage():
    rows = [source("card", "A"), source("panel", "A", poster="rna", footer=True)]
    result = inventory(rows, {"technologies": [{"id": "old_a", "name": "A"}, {"id": "old_z", "name": "Z"}]})
    assert result["counts"]["main_heading_keys"] == 1
    item = result["label_review_queue"][0]
    assert item["coverage"] == "website_heading_only"
    assert item["legacy_label_candidates"] == ["old_a"]
    assert item["footer_record_ids"] == ["panel"]


def test_record_identity_errors_fail_instead_of_silently_deduplicating():
    one = source("same", "A")
    with pytest.raises(ValueError, match="Duplicate"):
        inventory([one, one])
    unknown = source("other", "A", poster="unknown")
    with pytest.raises(ValueError, match="Unrecognized"):
        inventory([unknown])


def test_missing_heading_is_recorded_not_silently_treated_as_source_text():
    row = source("missing", "Recovered name")
    row["card"]["source_title"] = None
    item = inventory([row])["occurrences"][0]
    assert item["title"] == "Recovered name"
    assert item["title_basis"] == "archive_title_missing_source_heading"
