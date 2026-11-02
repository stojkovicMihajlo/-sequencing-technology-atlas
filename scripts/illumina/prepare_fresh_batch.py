#!/usr/bin/env python3
"""Register a NEW extraction batch from raw sources, never prior model results.

Catalogue cards, main poster rows and footer panels retain separate identities.
Collection-page candidates are secondary reading material, not primary evidence
or automatic method equivalence. Run preparation on Sherlock under Slurm.
"""
import argparse
from collections import defaultdict
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess

from poster_sources import Custody, row_record_id
from genomics_discovery.illumina.extraction import card_sources, read, sha, write


def collection_candidates(audits, poster_cards):
    """Return explicit source-page leads; no fuzzy matching or canonical merging."""
    candidates = defaultdict(set)
    poster_labels = defaultdict(set)
    for card in poster_cards:
        for label in card.get("source_method_labels", []):
            poster_labels[label.strip()].add(card["record_id"])
    for kind, audit in audits.items():
        for figure in audit["figures"]:
            page = figure.get("page", figure.get("pdf_page"))
            rows = figure.get("actual_drawing_poster_row_ids")
            if rows is None:
                rows = [figure.get("poster_row_candidate") or figure.get("poster_row_id")]
            for row in filter(None, rows):
                candidates[row_record_id(row)].add((kind, page))
            for ident in figure.get("card_label_candidates", []):
                candidates[ident].add((kind, page))
    # Equal literal labels provide reading candidates only. No identity is asserted.
    return candidates, poster_labels


def prepare(source_root, annotations, output):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Source preparation must run inside a Slurm allocation")
    output.mkdir(parents=True, exist_ok=False)
    custody = Custody()
    bundles = {}
    parents = {}
    for name, old in (("catalogue", "sources_06"), ("posters", "poster_sources_06")):
        base = source_root / old
        inventory = custody.absolute_records(read(base / "inventory.json"), base)
        acquired = custody.absolute_records(read(base / "sources.json"), base)
        bundles[name] = (inventory, acquired)
        parents[name] = {"inventory": str(base / "inventory.json"),
            "inventory_sha256": sha(base / "inventory.json"), "sources": str(base / "sources.json"),
            "sources_sha256": sha(base / "sources.json")}
    audits = {kind: read(annotations / (kind + "_collection.json")) for kind in ("dna", "rna")}
    leads, labels = collection_candidates(audits, bundles["posters"][0]["cards"])
    documents = {}
    collection_dir = output / "collection_pages"
    collection_dir.mkdir()
    for kind, asset_id in (("dna", "asset_409e5c0403ff18301d168974"),
                           ("rna", "asset_4a5d25b11e0887cf7958bda3")):
        asset = next(a for a in bundles["catalogue"][1]["assets"] if a["asset_id"] == asset_id)
        original, pin = custody.verify(Path(asset["path"]), asset["sha256"])
        for figure in audits[kind]["figures"]:
            expected = figure.get("source_pdf_sha256") or audits[kind].get("source_pdf_sha256")
            if expected != pin:
                raise ValueError("Collection annotation refers to a different PDF edition")
        command = ["pdftotext", "-layout", str(original), "-"]
        pages = subprocess.run(command, check=True, capture_output=True).stdout.decode().split("\f")
        page_numbers = {f.get("page", f.get("pdf_page")) for f in audits[kind]["figures"]}
        for page in sorted(page_numbers):
            text = pages[page - 1]
            if not text.strip():
                raise ValueError("Collection page text is empty")
            path = collection_dir / f"{kind}_{page:03d}.txt"
            path.write_text(text)
            documents[kind, page] = {"source_id": f"collection:{kind}:page:{page}",
                "content_level": "catalogue", "title": f"Illumina {kind.upper()} collection, PDF page {page}",
                "url": asset["url"], "text_path": str(path), "text_sha256": sha(path),
                "original_pdf": {"path": str(original), "sha256": pin}, "page": page,
                "conversion": command, "annotation": {"path": str(annotations / (kind + "_collection.json")),
                    "sha256": sha(annotations / (kind + "_collection.json"))},
                "association_status": "candidate_secondary_catalogue_page; verify target and scope before use"}
    for name, (inventory, acquired) in bundles.items():
        for card in inventory["cards"]:
            matches = set(leads[card["record_id"]])
            if name == "catalogue":
                for poster_id in labels[card.get("source_title", "").strip()]:
                    matches.update(leads[poster_id])
            card["catalogue_documents"] = [copy.deepcopy(documents[key]) for key in sorted(matches)]
        inventory["fresh_extraction"] = {"prior_model_outputs_used": False,
            "reason": "User requested a separate complete Illumina extraction before development tracing"}
    # Footer/product/readout panels were inventoried previously, never extracted.
    overlay = read(annotations / "footer_panels.json")
    footer_cards, footer_assets = [], []
    originals = {a["sha256"]: a for a in bundles["catalogue"][1]["assets"]}
    for row in overlay["rows"]:
        ident = row_record_id(row["row_id"])
        crop = annotations / "panels" / Path(row["image_provenance"]["crop_path"]).name
        custody.verify(crop, row["image_provenance"]["crop_sha256"])
        original = originals[row["source_pdf_sha256"]]
        asset_id = "footer_crop_" + hashlib.sha256(row["row_id"].encode()).hexdigest()[:24]
        footer_assets.append({"asset_id": asset_id, "source_id": "diagram:" + ident,
            "status": "downloaded", "path": str(crop), "sha256": sha(crop), "url": row["source_url"],
            "original_pdf": {"path": original["path"], "sha256": original["sha256"]},
            "source_region_pdf_points": row["diagram_region_proposal"],
            "annotation": {"path": str(annotations / "footer_panels.json"),
                           "sha256": sha(annotations / "footer_panels.json")}})
        footer_cards.append({"record_id": ident, "source_id": "card:" + ident,
            "source_title": row["source_row_caption"], "description_verbatim": row["source_row_caption"],
            "source_page_url": row["source_url"], "source_category": "Illumina poster footer",
            "diagram_asset_ids": [asset_id], "references": [], "require_paper_matches": False,
            "source_row_id": row["row_id"], "source_method_labels": [m["source_method_label"] for m in row["members"]],
            "scope_kind": "source_depiction_row_not_canonical_technology",
            "source_scope_note": row["scope_note"], "panel_classification": row["panel_classification"]})
    bundles["footer_panels"] = ({"cards": footer_cards}, {"assets": footer_assets, "publications": {}, "fulltexts": {}})
    manifest = {"format_version": "illumina_fresh_batch/1.0", "prior_model_outputs_used": False,
        "parents_raw_sources_only": parents, "annotation_pins": {p.name: sha(p) for p in annotations.glob("*.json")},
        "batches": {}, "records": []}
    ids = set()
    for name, (inventory, acquired) in bundles.items():
        folder = output / name
        folder.mkdir()
        for card in inventory["cards"]:
            if card["record_id"] in ids:
                raise ValueError("Repeated source record ID across batches")
            ids.add(card["record_id"])
            registry, images = card_sources(card, acquired, folder)
            if not images:
                raise ValueError("Source occurrence has no usable diagram: " + card["record_id"])
            manifest["records"].append({"record_id": card["record_id"], "title": card.get("source_title"),
                "batch": name, "status": "pending", "catalogue_page_candidates": len(card.get("catalogue_documents", [])),
                "source_count": len(registry)})
        write(folder / "inventory.json", inventory)
        acquired["inventory_sha256"] = sha(folder / "inventory.json")
        write(folder / "sources.json", acquired)
        manifest["batches"][name] = {"sources": str(folder / "sources.json"), "sources_sha256": sha(folder / "sources.json"),
            "inventory_sha256": sha(folder / "inventory.json"), "records": len(inventory["cards"])}
    manifest["source_occurrences"] = len(ids)
    manifest["count_warning"] = "Source occurrences include repeated depictions and grouped methods; not a unique technology count"
    write(output / "manifest.json", manifest)
    with (output / "CHECKLIST.csv").open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest["records"][0]))
        writer.writeheader(); writer.writerows(manifest["records"])
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.source_root.resolve(), args.annotations.resolve(), args.output.resolve())
    print(json.dumps({"source_occurrences": result["source_occurrences"], "batches": result["batches"]}, indent=2))
