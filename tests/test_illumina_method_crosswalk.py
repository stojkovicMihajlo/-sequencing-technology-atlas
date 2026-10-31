"""Lexical coverage must preserve grouped methods, duplicate cards and unknowns."""
import importlib.util
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location("illumina_method_crosswalk", Path(__file__).parents[1] / "scripts/illumina_method_crosswalk.py")
crosswalk = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = crosswalk
spec.loader.exec_module(crosswalk)


def methods():
    return {"methods": [{"id": "a", "name": "A-seq", "aliases": []},
                        {"id": "b", "name": "B-seq", "aliases": []},
                        {"id": "x", "name": "Foundation", "aliases": [], "outside_catalogue": True}]}


def test_grouped_card_retains_two_distinct_methods():
    prior = {"technologies": [{"id": "a", "record_ids": ["a-b"]}, {"id": "b", "record_ids": ["a-b"]}]}
    result = crosswalk.compare(methods(), prior, {"cards": [{"record_id": "a-b", "source_title": "A-seq / B-seq"}]})
    assert result["statistics"]["original_entities"] == 3
    assert result["statistics"]["historically_bound_entities"] == 2
    assert result["cards"][0]["historical_method_ids"] == ["a", "b"]
    assert result["cards"][0]["scope_requires_independent_review"]
    assert not result["cards"][0]["scientific_applicability_verified"]
    assert result["methods"][2]["coverage_status"] == "outside_catalogue_record"


def test_duplicate_cards_not_merged_by_exact_name_or_shared_diagram():
    prior = {"technologies": [{"id": "a", "record_ids": ["one"]}]}
    cards = [{"record_id": r, "source_title": "A-seq", "diagram_asset_ids": ["same_image"]} for r in ["one", "two"]]
    result = crosswalk.compare(methods(), prior, {"cards": cards})
    assert len(result["duplicate_source_titles"]) == 1
    assert result["methods"][0]["unbound_exact_candidate_ids"] == ["two"]
    assert result["cards"][0]["diagram_shared_with_card_ids"] == ["two"]
    assert result["cards"][1]["historical_method_ids"] == []


def test_historical_association_not_literal_member_stays_unresolved():
    prior = {"technologies": [{"id": "a", "record_ids": ["different"]}]}
    result = crosswalk.compare(methods(), prior, {"cards": [{"record_id": "different", "source_title": "Other assay"}]})
    assert result["methods"][0]["binding_only_card_ids"] == ["different"]
    assert result["statistics"]["entities_with_binding_only_cases"] == 1


def test_prime_is_not_dropped_and_numeral_not_deleted():
    assert crosswalk.normalize("3′-Seq") == "3'-seq"
    assert crosswalk.normalize("3′-Seq") != crosswalk.normalize("3Seq")


def test_absent_historical_card_rejected():
    with pytest.raises(ValueError, match="absent cards"):
        crosswalk.compare(methods(), {"technologies": [{"id": "a", "record_ids": ["missing"]}]},
                          {"cards": [{"record_id": "other", "source_title": "A-seq"}]})


def test_empty_card_preserved_without_name_invention():
    result = crosswalk.compare(methods(), {"technologies": [{"id": "a", "record_ids": ["empty"]}]},
                               {"cards": [{"record_id": "empty", "source_title": None}]})
    assert result["cards"][0]["source_labels"] == []
    assert result["methods"][0]["binding_only_card_ids"] == ["empty"]
