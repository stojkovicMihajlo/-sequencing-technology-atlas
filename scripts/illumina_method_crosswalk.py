#!/usr/bin/env python3
"""Account for original atlas entities versus Illumina cards without merging them.

Existing record-ID associations are historical curation, not new independent
validation. Exact source-label matches are lexical candidates, not scientific
identity. Grouped cards, duplicate cards and outside-catalogue methods remain
distinct. No fuzzy matching, ancestry edges, protocol or applicability claims.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re


def normalize(label):
    table = str.maketrans({"’": "'", "′": "'", "‘": "'", "–": "-", "—": "-", "−": "-"})
    label = " ".join(str(label).translate(table).split()).strip(" \t\r\n()[]{}.,:;")
    return re.sub(r"\s*-\s*", "-", label).casefold()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(path):
    raw = path.read_bytes()
    return json.loads(raw), {"path": str(path.resolve()), "sha256": sha(raw)}


def labels(card):
    title = card.get("source_title") or ""
    return list(dict.fromkeys(s.strip() for s in [title, *title.split("/")] if s.strip()))


def unique(rows, field):
    ids = [r[field] for r in rows]
    if not rows or len(ids) != len(set(ids)):
        raise ValueError("Expected nonempty unique " + field)
    return {r[field]: r for r in rows}


def compare(atlas, legacy, inventory):
    methods = unique(atlas.get("methods", atlas.get("technologies", [])), "id")
    prior = unique(legacy.get("technologies", legacy.get("methods", [])), "id")
    cards = unique(inventory["cards"], "record_id")
    label_index, title_index = defaultdict(list), defaultdict(list)
    for rid, card in cards.items():
        title = card.get("source_title") or ""
        if title:
            title_index[normalize(title)].append(rid)
        for label in labels(card):
            label_index[normalize(label)].append({"card_id": rid, "source_label": label,
                "basis": "exact_source_title" if label == title else "exact_source_slash_member"})
    crosswalk, unresolved = [], []
    for mid, method in methods.items():
        own_labels = list(dict.fromkeys([method["name"], *method.get("aliases", [])]))
        bindings = list(dict.fromkeys(prior.get(mid, {}).get("record_ids", [])))
        missing_cards = sorted(set(bindings) - set(cards))
        if missing_cards:
            raise ValueError("Historical binding references absent cards: " + repr(missing_cards))
        matches = []
        for label in own_labels:
            for hit in label_index.get(normalize(label), []):
                matches.append({"method_label": label, **hit})
        lexical_cards = sorted(set(m["card_id"] for m in matches))
        exact_bound = sorted(set(bindings) & set(lexical_cards))
        binding_only = sorted(set(bindings) - set(lexical_cards))
        row = {"method_id": mid, "name": method["name"], "declared_aliases": method.get("aliases", []),
               "outside_catalogue_as_recorded": bool(method.get("outside_catalogue")),
               "origin_paper_ids_as_recorded": method.get("origin_paper_ids", []),
               "historical_card_bindings": bindings, "exact_lexical_candidates": matches,
               "exact_bound_card_ids": exact_bound, "binding_only_card_ids": binding_only,
               "unbound_exact_candidate_ids": sorted(set(lexical_cards) - set(bindings)),
               "coverage_status": ("historically_bound_to_catalogue" if bindings else
                                   "outside_catalogue_record" if method.get("outside_catalogue") else
                                   "unresolved_no_historical_card_binding"),
               "scientific_identity_verified_by_this_comparison": False}
        crosswalk.append(row)
        if binding_only:
            unresolved.append({"method_id": mid, "kind": "historical_binding_without_exact_label",
                "card_ids": binding_only, "meaning": "Existing association needs source-scope review; may denote context/variant rather than a literally named member."})
        if len(lexical_cards) > 1:
            unresolved.append({"method_id": mid, "kind": "exact_label_multiple_cards", "card_ids": lexical_cards,
                "meaning": "Duplicate, overlapping or ambiguous source cards; no identity merge."})
        if not bindings:
            unresolved.append({"method_id": mid, "kind": row["coverage_status"], "card_ids": lexical_cards,
                "meaning": "Preserve original entity; this comparison does not create a catalogue assignment."})
    card_rows = []
    for rid, card in cards.items():
        old_ids = [r["method_id"] for r in crosswalk if rid in r["historical_card_bindings"]]
        lexical_ids = [r["method_id"] for r in crosswalk if any(m["card_id"] == rid for m in r["exact_lexical_candidates"])]
        named = []
        for label in labels(card):
            named.append({"source_label": label, "method_id_candidates": [mid for mid, method in methods.items()
                if normalize(label) in {normalize(s) for s in [method["name"], *method.get("aliases", [])]}]})
        card_rows.append({"card_id": rid, "source_title": card.get("source_title"), "source_labels": labels(card),
            "historical_method_ids": old_ids, "exact_lexical_method_candidates": lexical_ids,
            "label_candidates": named, "source_record_status": card.get("source_record_status"),
            "scope_requires_independent_review": len(old_ids) > 1 or len(labels(card)) > 1,
            "diagram_shared_with_card_ids": [], "scientific_applicability_verified": False})
    image_index = defaultdict(set)
    for rid, card in cards.items():
        for aid in card.get("diagram_asset_ids", []):
            image_index[aid].add(rid)
    for row in card_rows:
        row["diagram_shared_with_card_ids"] = sorted(set().union(*(image_index[a] for a in cards[row["card_id"]].get("diagram_asset_ids", []))) - {row["card_id"]})
    duplicate_titles = [{"normalized_source_title": title, "card_ids": ids} for title, ids in title_index.items() if len(ids) > 1]
    return {"format_version": "illumina_method_scope_crosswalk/1.0", "scientific_review": False,
        "statistics": {"original_entities": len(methods), "historical_graph_entities": len(prior), "current_cards": len(cards),
            "historically_bound_entities": sum(bool(r["historical_card_bindings"]) for r in crosswalk),
            "outside_catalogue_entities": sum(r["outside_catalogue_as_recorded"] for r in crosswalk),
            "entities_with_exact_label_candidates": sum(bool(r["exact_lexical_candidates"]) for r in crosswalk),
            "entities_with_binding_only_cases": sum(bool(r["binding_only_card_ids"]) for r in crosswalk),
            "cards_with_historical_entities": sum(bool(r["historical_method_ids"]) for r in card_rows),
            "cards_with_multiple_historical_entities": sum(len(r["historical_method_ids"]) > 1 for r in card_rows),
            "duplicate_title_sets": len(duplicate_titles), "unresolved_lexical_cases": len(unresolved)},
        "original_only_entity_ids": sorted(set(methods) - set(prior)),
        "prior_only_entity_ids": sorted(set(prior) - set(methods)),
        "methods": crosswalk, "cards": card_rows, "duplicate_source_titles": duplicate_titles,
        "unresolved_lexical_cases": unresolved,
        "limits": ["Existing record-ID associations are preserved as historical curation, not independently verified.",
                   "Exact names/declared aliases identify lexical candidates only; slash labels are not necessarily synonyms.",
                   "Identical diagrams/cards do not establish identical methods or per-operation applicability.",
                   "Outside-catalogue entities and poster-only candidates remain separate coverage obligations.",
                   "This comparison establishes no experimental compatibility or evolutionary edges."]}


def write_csv(path, rows, fields):
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(row.get(k), ensure_ascii=False) if isinstance(row.get(k), (list, dict)) else row.get(k) for k in fields})


def run(atlas_path, legacy_path, inventory_path, output):
    atlas, atlas_pin = load(atlas_path)
    legacy, legacy_pin = load(legacy_path)
    inventory, inventory_pin = load(inventory_path)
    result = compare(atlas, legacy, inventory)
    result["inputs"] = {"original_atlas": atlas_pin, "historical_graph": legacy_pin, "current_inventory": inventory_pin}
    output.mkdir(parents=True, exist_ok=False)
    (output / "crosswalk.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    write_csv(output / "METHOD_COVERAGE.csv", result["methods"], ["method_id", "name", "coverage_status",
        "outside_catalogue_as_recorded", "historical_card_bindings", "exact_bound_card_ids", "binding_only_card_ids", "unbound_exact_candidate_ids"])
    write_csv(output / "CARD_COVERAGE.csv", result["cards"], ["card_id", "source_title", "source_labels", "historical_method_ids",
        "exact_lexical_method_candidates", "scope_requires_independent_review", "diagram_shared_with_card_ids"])
    write_csv(output / "UNRESOLVED_LEXICAL_CASES.csv", result["unresolved_lexical_cases"], ["method_id", "kind", "card_ids", "meaning"])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atlas", type=Path, required=True)
    parser.add_argument("--legacy-graph", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = run(args.atlas, args.legacy_graph, args.inventory, args.output)
    print(json.dumps(result["statistics"], indent=2))


if __name__ == "__main__":
    main()
