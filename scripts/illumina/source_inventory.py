"""Reconcile source occurrences before method identity review; no model calls.

Reads original card/poster metadata in an archived run, never its extracted
claims. Literal heading matches nominate review, not scientific identity.
The older technology atlas is optional comparison material, not the census seed.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
from pathlib import Path

from scripts.illumina.method_crosswalk import normalize


POSTERS = {
    "for-all-you-seq-rna.pdf": "rna_poster",
    "for-all-you-seq-dna.pdf": "dna_poster",
    "for-all-you-seq-single-cell.pdf": "single_cell_poster",
}


def classify(record):
    card = record["card"]
    if record["batch"] in {"catalogue", "initial_two"}:
        return "website", "method_card"
    filename = card.get("source_page_url", "").split("?")[0].rsplit("/", 1)[-1]
    if filename not in POSTERS or record["batch"] not in {"posters", "footer_panels"}:
        raise ValueError("Unrecognized source class: " + record["record_id"])
    return POSTERS[filename], "main_row" if record["batch"] == "posters" else "footer_panel"


def inventory(records, legacy=None):
    occurrences, index = [], defaultdict(list)
    for record in records:
        card = record["card"]
        resource, kind = classify(record)
        title = card.get("source_title") or record["title"]
        # Slashes nominate heading members; they do not establish aliases or
        # distinct assays. Never use a prior model's method_names to define scope.
        supplied = card.get("source_method_labels")
        labels = supplied or [x.strip() for x in title.split("/") if x.strip()]
        if not labels:
            raise ValueError("Source heading has no labels: " + record["record_id"])
        row = {"record_id": record["record_id"], "resource": resource, "kind": kind,
               "title": title, "url": card["source_page_url"],
               "title_basis": "source_heading" if card.get("source_title") else "archive_title_missing_source_heading",
               "labels": list(dict.fromkeys(labels)),
               "label_basis": "registered_source_labels" if supplied else "heading_slash_segments",
               "categories": card.get("source_category_path", []),
               "archived_extraction_status": record["status"]}
        occurrences.append(row)
        for key in dict.fromkeys(normalize(label) for label in labels):
            index[key].append(row)
    ids = [r["record_id"] for r in occurrences]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate source occurrence ID")
    old = defaultdict(set)
    for method in (legacy or {}).get("technologies", []):
        for label in [method["name"], *method.get("aliases", [])]:
            old[normalize(label)].add(method["id"])
    labels = []
    for key, matches in sorted(index.items()):
        main = [r for r in matches if r["kind"] != "footer_panel"]
        web = [r for r in main if r["resource"] == "website"]
        posters = [r for r in main if r["resource"] != "website"]
        coverage = ("website_and_poster" if web and posters else "website_heading_only" if web
                    else "poster_heading_only" if posters else "footer_only")
        labels.append({"label_key": key,
            "literal_labels": sorted({s for r in matches for s in r["labels"] if normalize(s) == key}),
            "coverage": coverage, "website_record_ids": [r["record_id"] for r in web],
            "poster_record_ids": [r["record_id"] for r in posters],
            "footer_record_ids": [r["record_id"] for r in matches if r["kind"] == "footer_panel"],
            "resources": sorted({r["resource"] for r in main}),
            "grouped_source_record_ids": [r["record_id"] for r in matches if len(r["labels"]) > 1],
            "legacy_label_candidates": sorted(old[key]),
            "identity_status": "unreviewed_heading_match_not_method_identity"})
    method_labels = [r for r in labels if r["coverage"] != "footer_only"]
    resources = {}
    for resource in ["website", *POSTERS.values()]:
        own = [r for r in occurrences if r["resource"] == resource]
        resources[resource] = dict(Counter(r["kind"] for r in own))
    totals = {"source_occurrences": len(occurrences), "resources": resources,
        "website_grouped_headings": sum(r["resource"] == "website" and len(r["labels"]) > 1 for r in occurrences),
        "main_heading_keys": len(method_labels),
        "heading_coverage": dict(Counter(r["coverage"] for r in method_labels)),
        "reviewed_unique_method_count": None,
        "legacy_entries_for_comparison_only": len((legacy or {}).get("technologies", [])),
        "boundary": "Counts describe this archived source snapshot. Heading keys are retrieval leads, not reviewed methods. Guides must be checked through their separate panel-coverage ledger; paper counts never define technologies."}
    return {"format_version": "illumina_source_inventory/1", "counts": totals,
            "occurrences": occurrences, "label_review_queue": labels}


def csv_table(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--legacy", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    with gzip.open(args.archive, "rt") as stream:
        records = [json.loads(line) for line in stream if line.strip()]
    result = inventory(records, json.loads(args.legacy.read_text()) if args.legacy else None)
    result["inputs"] = {name: {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for name, path in [("archive", args.archive), ("legacy", args.legacy)] if path}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "inventory.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    csv_table(args.output / "source_occurrences.csv", result["occurrences"])
    csv_table(args.output / "label_review_queue.csv", result["label_review_queue"])
    print(json.dumps(result["counts"], indent=2))


if __name__ == "__main__":
    main()
