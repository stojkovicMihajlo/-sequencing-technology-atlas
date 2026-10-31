#!/usr/bin/env python3
"""Preserve Illumina PDF text/word geometry and propose method-label coverage.

This is a lexical source index, not scientific extraction or visual validation.
Exact catalogue aliases yield candidates; unmatched labels and row regions remain
explicitly unverified. Every extracted page is retained, including pages with no
recognized labels. No model calls, OCR, canonical merges or historical edges.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
from xml.etree import ElementTree as ET


PDF_KINDS = {"rna_poster", "dna_poster", "single_cell_poster",
             "rna_methods_collection", "dna_methods_collection"}
_TRANSLATE = str.maketrans({"’": "'", "′": "'", "‘": "'", "–": "-", "—": "-", "−": "-"})
_METHOD_TOKEN = re.compile(
    r"(?<![\w])(?:[A-Za-z0-9][A-Za-z0-9&+'′’]*(?:-[A-Za-z0-9&+'′’]+)*-?[Ss][Ee][Qq][Ss]?"
    r"|[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+|[A-Z][A-Z0-9]{2,9})(?![\w])")
_NON_METHOD = {"DNA", "RNA", "PCR", "NGS", "CDNA", "MRNA", "SNP", "SNPS", "FFPE",
               "UMIS", "SEQ", "HTTP", "HTTPS", "WWW", "USA", "FOR", "ONLY", "RESEARCH", "USE"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def normalize(value):
    """Normalize typography without dropping apostrophes, numbers or '-seq'."""
    text = " ".join(value.translate(_TRANSLATE).split()).strip(" \t\r\n()[]{}.,:;")
    return re.sub(r"\s*-\s*", "-", text).casefold()


def alias_index(cards, extra_aliases=None):
    index = defaultdict(set)
    ids = {c["record_id"] for c in cards}
    for card in cards:
        title = card.get("source_title") or ""
        # A slash enumerates source names; it does not assert equivalence.
        for name in [title, *title.split("/")]:
            if name.strip():
                index[normalize(name)].add(card["record_id"])
    for ident, names in (extra_aliases or {}).items():
        if ident not in ids or not isinstance(names, list):
            raise ValueError("Extra aliases require existing card IDs and lists of source labels")
        for name in names:
            if not isinstance(name, str) or not normalize(name):
                raise ValueError("Extra aliases must be nonempty source labels")
            index[normalize(name)].add(ident)
    return {name: sorted(matches) for name, matches in index.items()}


def sanitize_bbox_xml(raw):
    """Replace only XML-1.0-forbidden controls with a visible replacement marker.

    Raw Poppler output is authoritative and remains preserved separately. This
    does not repair labels, entities, markup or invalid UTF-8. Every replacement
    records original byte/character location, line and code point. Damaged word
    text must remain visibly damaged rather than joining letters into an alias.
    """
    text = raw.decode("utf-8")
    bad = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]|&#(?:x[0-9a-fA-F]+|[0-9]+);")
    events, pieces, previous = [], [], 0
    for match in bad.finditer(text):
        token = match.group()
        point = ord(token) if len(token) == 1 else int(token[3:-1], 16) if token.startswith("&#x") else int(token[2:-1])
        allowed = point in {9, 10, 13} or 0x20 <= point <= 0xD7FF or 0xE000 <= point <= 0xFFFD or 0x10000 <= point <= 0x10FFFF
        if allowed:
            continue
        index = match.start()
        events.append({"character_offset": index, "byte_offset": len(text[:index].encode("utf-8")),
                       "line": text.count("\n", 0, index) + 1,
                       "column_zero_based": index - text.rfind("\n", 0, index) - 1,
                       "original_codepoint": f"U+{point:04X}", "original_token": token,
                       "replacement": "U+FFFD", "kind": "literal_control" if len(token) == 1 else "invalid_numeric_character_reference"})
        pieces.extend([text[previous:index], "\ufffd"])
        previous = match.end()
    pieces.append(text[previous:])
    sanitized = "".join(pieces).encode("utf-8")
    receipt = {"format_version": "poppler_bbox_xml_sanitation/1.0", "raw_sha256": hashlib.sha256(raw).hexdigest(),
               "sanitized_sha256": hashlib.sha256(sanitized).hexdigest(), "replacements": len(events), "events": events,
               "policy": "Only XML1.0-forbidden controls are replaced with visible U+FFFD; no scientific label completion or normalization."}
    return sanitized, receipt


def parse_bbox(raw, *, sanitation_receipt=None):
    sanitized, receipt = sanitize_bbox_xml(raw)
    if sanitation_receipt is not None:
        sanitation_receipt.update(receipt)
    root = ET.fromstring(sanitized)
    pages = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "page":
            continue
        width, height = float(element.attrib["width"]), float(element.attrib["height"])
        if not all(math.isfinite(x) and x > 0 for x in (width, height)):
            raise ValueError("Invalid PDF page dimensions")
        words = []
        for word in element.iter():
            if word.tag.rsplit("}", 1)[-1] != "word":
                continue
            text = "".join(word.itertext()).strip()
            box = [float(word.attrib[key]) for key in ("xMin", "yMin", "xMax", "yMax")]
            if not all(math.isfinite(x) for x in box) or box[0] > box[2] or box[1] > box[3]:
                raise ValueError("Invalid PDF word geometry")
            if text:
                words.append({"word_id": f"w{len(words) + 1:05d}", "text": text, "bbox": box})
                if "\ufffd" in text:
                    words[-1]["text_integrity"] = "contains_visible_replacement_marker_requires_visual_review"
        pages.append({"page": len(pages) + 1, "width": width, "height": height,
                      "coordinate_system": "pdftotext bbox page points, top-left origin",
                      "words": words})
    if not pages:
        raise ValueError("No pages found in pdftotext bbox output")
    return pages


def line_runs(page):
    """Geometry-based text runs; proposal boundaries are never scientific facts."""
    lines = []
    for word in sorted(page["words"], key=lambda w: (w["bbox"][1], w["bbox"][0])):
        x1, y1, x2, y2 = word["bbox"]
        center, height = (y1 + y2) / 2, max(y2 - y1, 1)
        closest = None
        distance = float("inf")
        for line in lines:
            delta = abs(center - line["center"])
            if delta <= 0.45 * min(height, line["height"]) and delta < distance:
                closest, distance = line, delta
        if closest is None:
            lines.append({"center": center, "height": height, "words": [word]})
        else:
            closest["words"].append(word)
    runs = []
    for line in lines:
        current = []
        for word in sorted(line["words"], key=lambda w: w["bbox"][0]):
            gap = word["bbox"][0] - current[-1]["bbox"][2] if current else 0
            if current and gap > max(18, line["height"] * 3):
                runs.append(current)
                current = []
            current.append(word)
        if current:
            runs.append(current)
    return sorted(runs, key=lambda ws: (min(w["bbox"][1] for w in ws), ws[0]["bbox"][0]))


def union_box(words):
    return [min(w["bbox"][0] for w in words), min(w["bbox"][1] for w in words),
            max(w["bbox"][2] for w in words), max(w["bbox"][3] for w in words)]


def occurrences(page, aliases, cards):
    found = []
    for words in line_runs(page):
        text = " ".join(w["text"] for w in words)
        normalized = normalize(text)
        damaged = any("\ufffd" in w["text"] for w in words)
        labels = {}
        if not damaged:
            for alias, matches in aliases.items():
                if re.search(r"(?<![\w&+'-])" + re.escape(alias) + r"(?![\w&+'-])", normalized):
                    labels[alias] = (matches, "catalogue_alias")
        for match in _METHOD_TOKEN.finditer(text):
            label = match.group()
            if label.upper() not in _NON_METHOD:
                key = normalize(label)
                labels.setdefault(key, ([] if damaged else aliases.get(key, []), "damaged_text_token" if damaged else "method_like_token"))
        bibliographic = bool(re.search(r"\b(?:19|20)\d{2}\b", text) and
                             re.search(r"et\s+al|\b(?:Nature|Methods|Genome|Cell|Res|Sci|Proc)\b", text, re.I))
        for label, (matches, basis) in labels.items():
            exact_run = normalize(text) == label
            appearance = "reference_mention" if bibliographic else (
                "candidate_label" if exact_run else "text_mention")
            slug_hints = [c["record_id"] for c in cards if not matches and
                          re.search(r"(?:^|-)" + re.escape(label) + r"(?:-|$)",
                                    c["record_id"].casefold())]
            found.append({"page": page["page"], "label": label, "source_run_text": text,
                          "source_word_ids": [w["word_id"] for w in words], "bbox": union_box(words),
                          "generation_basis": basis, "appearance": appearance,
                          "catalogue_candidates": matches, "record_slug_hints_not_matches": slug_hints,
                          "matching": "ambiguous_exact_alias" if len(matches) > 1 else (
                              "exact_alias_candidate" if matches else "damaged_text_not_eligible_for_exact_matching" if damaged else "no_exact_catalogue_alias"),
                          "verification": "not_visually_reviewed",
                          "workflow_row_verified": False})
    return found


def propose_rows(page, found):
    """Propose column/row windows around standalone labels for visual review."""
    labels = [x for x in found if x["appearance"] == "candidate_label"]
    columns = []
    tolerance = page["width"] * 0.025
    for label in sorted(labels, key=lambda x: x["bbox"][0]):
        left = label["bbox"][0]
        column = next((c for c in columns if abs(c["left"] - left) <= tolerance), None)
        if column is None:
            column = {"left": left, "members": []}
            columns.append(column)
        column["members"].append(label)
    columns.sort(key=lambda c: c["left"])
    for ci, column in enumerate(columns):
        right = columns[ci + 1]["left"] if ci + 1 < len(columns) else page["width"]
        members = sorted(column["members"], key=lambda x: x["bbox"][1])
        for i, label in enumerate(members):
            center = (label["bbox"][1] + label["bbox"][3]) / 2
            above = ((members[i - 1]["bbox"][1] + members[i - 1]["bbox"][3]) / 2
                     if i else max(0, label["bbox"][1] - page["height"] * 0.04))
            below = ((members[i + 1]["bbox"][1] + members[i + 1]["bbox"][3]) / 2
                     if i + 1 < len(members) else min(page["height"], label["bbox"][3] + page["height"] * 0.04))
            label["row_region_proposal"] = [max(0, column["left"] - 5), (above + center) / 2,
                                             right, (center + below) / 2]
            label["row_region_basis"] = "Standalone-label x clustering and neighboring-label y midpoints; unverified"


def csv_write(path, rows):
    fields = ["occurrence_id", "resource_id", "resource_kind", "page", "label", "appearance", "matching",
              "catalogue_candidates", "record_slug_hints_not_matches", "bbox",
              "row_region_proposal", "verification", "workflow_row_verified", "source_run_text"]
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(row[k], ensure_ascii=False) if isinstance(row.get(k), (list, dict))
                             else row.get(k, "") for k in fields})


def build_index(sources_path, inventory_path, output, *, aliases_path=None, executable=None):
    sources_path, inventory_path, output = map(Path, (sources_path, inventory_path, output))
    sources, inventory = json.loads(sources_path.read_bytes()), json.loads(inventory_path.read_bytes())
    extra = json.loads(Path(aliases_path).read_bytes()) if aliases_path else None
    aliases = alias_index(inventory["cards"], extra)
    executable = executable or shutil.which("pdftotext")
    if not executable:
        raise ValueError("pdftotext is required; no OCR or model fallback is performed")
    output.mkdir(parents=True, exist_ok=False)
    resources, all_occurrences = [], []
    for asset in sources.get("assets", []):
        if asset.get("kind") not in PDF_KINDS:
            continue
        receipt = {"resource_id": asset["asset_id"], "kind": asset["kind"],
                   "url": asset["url"], "acquisition_status": asset["status"],
                   "index_status": "not_indexed", "workflow_review_status": "not_reviewed"}
        resources.append(receipt)
        if asset["status"] != "downloaded":
            receipt["reason"] = asset.get("error", "Resource was not acquired")
            continue
        pdf = (sources_path.parent / asset["path"]).resolve()
        before = sha(pdf)
        if before != asset["sha256"]:
            raise ValueError("Acquired PDF changed: " + str(pdf))
        directory = output / asset["asset_id"]
        directory.mkdir()
        bbox = directory / "words.xhtml"
        text = directory / "layout.txt"
        try:
            subprocess.run([str(executable), "-bbox", "-enc", "UTF-8", str(pdf), str(bbox)],
                           check=True, capture_output=True, timeout=180)
            subprocess.run([str(executable), "-layout", "-enc", "UTF-8", str(pdf), str(text)],
                           check=True, capture_output=True, timeout=180)
            raw_bbox = bbox.read_bytes()
            sanitation = {}
            pages = parse_bbox(raw_bbox, sanitation_receipt=sanitation)
            write(directory / "xml_sanitation.json", sanitation)
            if sanitation["replacements"]:
                sanitized, _ = sanitize_bbox_xml(raw_bbox)
                (directory / "words.sanitized.xhtml").write_bytes(sanitized)
            for page in pages:
                found = occurrences(page, aliases, inventory["cards"])
                propose_rows(page, found)
                for index, item in enumerate(found, 1):
                    item.update(resource_id=asset["asset_id"], resource_kind=asset["kind"],
                                occurrence_id=f"{asset['asset_id']}:p{page['page']}:n{index}")
                all_occurrences.extend(found)
                write(directory / f"page_{page['page']:04d}.json", page)
            if sha(pdf) != before:
                raise ValueError("PDF changed during indexing")
            receipt.update(index_status="text_and_geometry_indexed", source_path=str(pdf),
                           source_sha256=before, bbox_sha256=sha(bbox), text_sha256=sha(text),
                           pages=len(pages), words=sum(len(p["words"]) for p in pages),
                           page_records=str(directory.relative_to(output)),
                           xml_sanitation={"path": str((directory / "xml_sanitation.json").relative_to(output)),
                                           "sha256": sha(directory / "xml_sanitation.json"),
                                           "replacements": sanitation["replacements"],
                                           "sanitized_bbox_path": str((directory / "words.sanitized.xhtml").relative_to(output)) if sanitation["replacements"] else None,
                                           "sanitized_bbox_sha256": sanitation["sanitized_sha256"]},
                           empty_text_pages=[p["page"] for p in pages if not p["words"]])
        except (OSError, ValueError, subprocess.SubprocessError, ET.ParseError) as exc:
            receipt.update(index_status="failed", error=f"{type(exc).__name__}: {exc}")
    write(output / "occurrences.json", all_occurrences)
    csv_write(output / "COVERAGE.csv", all_occurrences)
    unmatched = [x for x in all_occurrences if not x["catalogue_candidates"]]
    csv_write(output / "UNMATCHED_LABELS.csv", unmatched)
    poster_unmatched = [x for x in unmatched if x["resource_kind"].endswith("_poster")]
    csv_write(output / "POSTER_ONLY_CANDIDATES.csv", poster_unmatched)
    card_coverage = [{"record_id": card["record_id"], "source_title": card.get("source_title"),
                      "occurrence_ids": [x["occurrence_id"] for x in all_occurrences
                                         if card["record_id"] in x["catalogue_candidates"]],
                      "slug_hint_occurrence_ids": [x["occurrence_id"] for x in all_occurrences
                          if card["record_id"] in x["record_slug_hints_not_matches"]],
                      "verification": "not_visually_reviewed"}
                     for card in inventory["cards"]]
    write(output / "catalogue_coverage.json", card_coverage)
    represented_kinds = {a.get("kind") for a in sources.get("assets", [])}
    manifest = {"format_version": "illumina_pdf_label_index/1.0",
                "sources_sha256": sha(sources_path), "inventory_sha256": sha(inventory_path),
                "extra_aliases_sha256": sha(aliases_path) if aliases_path else None,
                "resources": resources, "missing_resource_kinds": sorted(PDF_KINDS - represented_kinds),
                "occurrences": len(all_occurrences), "unmatched_occurrences": len(unmatched),
                "poster_unmatched_occurrences": len(poster_unmatched),
                "catalogue_records_with_exact_alias_candidates": sum(bool(c["occurrence_ids"]) for c in card_coverage),
                "verified_workflow_rows": 0,
                "scope": "Lexical/geometric candidates, not a completed method inventory or scientific review.",
                "limitations": ["Bibliography and body mentions can resemble row labels.",
                    "Unmatched acronyms can be reagents or other non-method text.",
                    "Wrapped or graphical labels may not be recovered by text extraction.",
                    "Row windows are proposals requiring inspection of the original PDF.",
                    "Every extracted page and word is retained; absence of a lexical match does not establish absence of a method.",
                    "No OCR guesses, semantic aliases, canonical assignments or historical edges are produced."]}
    write(output / "resources.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--aliases", type=Path, help="Optional source-attested card-ID to label-list mapping")
    parser.add_argument("--pdftotext", help="Exact Poppler pdftotext executable")
    args = parser.parse_args(argv)
    result = build_index(args.sources, args.inventory, args.output,
                         aliases_path=args.aliases, executable=args.pdftotext)
    print(json.dumps({"resources": len(result["resources"]), "occurrences": result["occurrences"],
                      "unmatched_occurrences": result["unmatched_occurrences"], "verified_workflow_rows": 0}))
    return 1 if result["missing_resource_kinds"] or any(
        r["index_status"] != "text_and_geometry_indexed" for r in result["resources"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
