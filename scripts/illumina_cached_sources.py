#!/usr/bin/env python3
"""Reuse exact-identity, hash-bound article caches; never infer identity from title.

This is offline source recovery, not scientific review. Pass only explicit cache
roots. Historical paths/packets remain untouched; new copies and a recovery ledger
are written beneath a new output directory. macOS dataless files are not opened.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET

SF_DATALESS = 0x40000000  # Darwin sys/stat.h; absent from Python's stat module.
FORMAT = "illumina_cached_source_recovery/1.0"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def normalize_doi(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else ""


def identities(value: dict) -> dict[str, set[str]]:
    ids = value.get("identifiers", value)
    out = {"pmid": set(), "doi": set()}
    for key, kind in (("pmid", "pmid"), ("pubmed", "pmid"), ("doi", "doi")):
        items = ids.get(key, [])
        if not isinstance(items, list):
            items = [items]
        for item in items:
            text = normalize_doi(item) if kind == "doi" else str(item or "").strip()
            if text and (kind == "doi" or text.isdecimal()):
                out[kind].add(text)
    return out


def file_state(path: Path) -> str:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(info.st_mode):
        return "symlink_not_followed"
    if getattr(info, "st_flags", 0) & SF_DATALESS:
        return "dataless_not_opened"
    return "materialized" if stat.S_ISREG(info.st_mode) else "not_regular"


def read_materialized(path: Path, expected: str | None = None) -> bytes:
    state = file_state(path)
    if state != "materialized":
        raise ValueError(state + ": " + str(path))
    raw = path.read_bytes()
    if expected and digest(raw) != expected:
        raise ValueError("sha256_mismatch: " + str(path))
    return raw


def resolve_recorded(recorded: str, origin: Path, workspace: Path) -> Path:
    """Resolve explicit historical root suffix; never search by basename."""
    path = Path(recorded)
    if path.is_absolute() and file_state(path) != "missing":
        return path
    marker = "technology_atlas/v2/work/"
    if marker in str(path):
        return workspace / marker / str(path).split(marker, 1)[1]
    return path if path.is_absolute() else origin.parent / path


def match_targets(ids: dict, targets: dict) -> list[str]:
    matches = []
    for pmid, expected in targets.items():
        if ids["pmid"] and ids["pmid"] != expected["pmid"]:
            continue
        if ids["doi"] and expected["doi"] and ids["doi"].isdisjoint(expected["doi"]):
            continue
        if ids["pmid"] & expected["pmid"] or ids["doi"] & expected["doi"]:
            matches.append(pmid)
    return matches


def xml_article(raw: bytes) -> tuple[ET.Element, dict]:
    root = ET.fromstring(raw)
    articles = [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "article"]
    if len(articles) != 1:
        raise ValueError("not_single_article_jats")
    article = articles[0]
    front = next((e for e in article if e.tag.rsplit("}", 1)[-1] == "front"), None)
    meta = next((e for e in front if e.tag.rsplit("}", 1)[-1] == "article-meta"), None) if front is not None else None
    if meta is None:
        raise ValueError("jats_missing_article_metadata")
    ids = {}
    for item in meta:
        if item.tag.rsplit("}", 1)[-1] == "article-id":
            ids.setdefault(item.get("pub-id-type"), []).append("".join(item.itertext()).strip())
    body = next((e for e in article if e.tag.rsplit("}", 1)[-1] == "body"), None)
    if body is None or len(" ".join(body.itertext()).strip()) < 200:
        raise ValueError("jats_no_article_body")
    return article, ids


def article_blocks(article: ET.Element) -> tuple[str, list[dict]]:
    blocks = []
    def visit(node, location):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"article-title", "title", "p", "table-wrap", "fig", "ref"}:
            text = " ".join("".join(node.itertext()).split())
            if text:
                blocks.append({"block_id": f"b{len(blocks)+1:05d}", "kind": tag,
                               "locator": location, "text": text})
            return
        counts = Counter()
        for child in node:
            child_tag = child.tag.rsplit("}", 1)[-1]
            counts[child_tag] += 1
            visit(child, f"{location}/{child_tag}[{counts[child_tag]}]")
    visit(article, "/article")
    return "\n\n".join(f"[{b['block_id']}] {b['text']}" for b in blocks) + "\n", blocks


def metadata_files(roots: list[Path]):
    for root in roots:
        if not root.is_dir():
            continue
        for directory, names, files in os.walk(root, followlinks=False):
            names[:] = sorted(n for n in names if not Path(directory, n).is_symlink())
            for name in sorted(files):
                if name in {"packet.json", "registration.json", "acquisition.json"}:
                    yield Path(directory, name)


def recover(sources_path: Path, roots: list[Path], output: Path, workspace: Path) -> dict:
    raw_sources = read_materialized(sources_path)
    acquired = json.loads(raw_sources)
    targets = {}
    for pmid, full in acquired.get("fulltexts", {}).items():
        if full.get("status") == "fulltext_acquired":
            continue
        pub = acquired.get("publications", {}).get(pmid, {})
        ids = identities(pub)
        if pub.get("pmid") not in {None, pmid}:
            raise ValueError("publication_map_pmid_conflict: " + pmid)
        # Older receipts accidentally included ArticleId from ReferenceList.
        # The record's own top-level PMID is authoritative; never use cited IDs.
        ids["pmid"] = {pmid}
        if len(ids["doi"]) > 1:
            ids["doi"] = set()  # Ambiguous legacy DOI lists cannot drive a match.
        targets[pmid] = ids
    output.mkdir(parents=True, exist_ok=False)
    ledger = {"format_version": FORMAT, "scientific_review": False,
              "parent_acquisition": {"path": str(sources_path.resolve()), "sha256": digest(raw_sources)},
              "cache_roots": [str(p.resolve()) for p in roots], "targets": sorted(targets),
              "recoveries": {}, "candidates": [], "issues": [], "statistics": {}}
    for root in roots:
        if not root.is_dir():
            ledger["issues"].append({"path": str(root), "reason": "cache_root_missing"})
    records, packets = [], []
    for path in metadata_files(roots):
        try:
            raw = read_materialized(path)
            if len(raw) > 30_000_000:
                raise ValueError("metadata_file_too_large")
            data = json.loads(raw)
            pin = {"path": str(path.resolve()), "sha256": digest(raw)}
            if path.name == "registration.json" and data.get("identity") and data.get("rendition_sha256"):
                records.append({"kind": "registered_pdf_text", "origin": path, "record": data,
                                "pin": pin, "ids": identities(data["identity"])})
            elif path.name == "acquisition.json":
                for source in data.get("documents", []):
                    if source.get("role") == "primary_paper" and source.get("format") == "jats" and source.get("path") and source.get("original_sha256"):
                        records.append({"kind": "JATS", "origin": path, "record": source, "pin": pin,
                                        "ids": identities(source.get("host_identity", source.get("identity", {})))})
            elif path.name == "packet.json":
                for source in data.get("sources", []):
                    if source.get("role") != "primary_paper":
                        continue
                    item = {"source_id": source.get("source_id"), "packet": pin,
                            "original_sha256": source.get("original_sha256"),
                            "passages_sha256": digest(json.dumps(source.get("passages", []), sort_keys=True, ensure_ascii=False).encode())}
                    packets.append(item)
                    ids = identities(source.get("document_identity", {}))
                    if source.get("original_path") and source.get("original_sha256") and match_targets(ids, targets):
                        records.append({"kind": "JATS", "origin": path, "record": source, "pin": pin, "ids": ids})
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            ledger["issues"].append({"path": str(path), "reason": str(exc).split(": ", 1)[0]})
    seen = set()
    for item in records:
        expected_matches = match_targets(item["ids"], targets)
        if not expected_matches:
            continue
        record, origin = item["record"], item["origin"]
        key = (item["kind"], record.get("original_sha256"), record.get("rendition_sha256"))
        if key in seen:
            continue
        seen.add(key)
        candidate = {"kind": item["kind"], "target_pmids": expected_matches,
                     "registration": item["pin"], "status": "candidate", "url": record.get("public_url", record.get("url")),
                     "packets": [p for p in packets if p["original_sha256"] in {record.get("original_sha256"), record.get("rendition_sha256")} ]}
        ledger["candidates"].append(candidate)
        try:
            original = resolve_recorded(record.get("original", record.get("path", record.get("original_path", ""))), origin, workspace)
            original_hash = record.get("original_sha256")
            if not isinstance(original_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", original_hash):
                raise ValueError("original_sha256_missing_or_invalid")
            candidate["original"] = {"recorded_path": record.get("original", record.get("path", record.get("original_path"))),
                                     "resolved_path": str(original), "sha256": original_hash, "state": file_state(original)}
            if item["kind"] == "registered_pdf_text":
                cached_text = resolve_recorded(record["rendition"], origin, workspace)
                candidate["rendition"] = {"recorded_path": record["rendition"], "resolved_path": str(cached_text),
                                          "sha256": record["rendition_sha256"], "state": file_state(cached_text)}
                if file_state(cached_text) == "materialized":
                    read_materialized(cached_text, record["rendition_sha256"])
                    candidate["rendition"]["verified"] = True
            # Never promote a rendition whose authoritative original cannot be verified.
            original_raw = read_materialized(original, original_hash)
            candidate["original"]["verified"] = True
            if item["kind"] == "JATS":
                article, article_ids = xml_article(original_raw)
                actual_matches = match_targets(identities(article_ids), targets)
                matches = sorted(set(actual_matches) & set(expected_matches))
                if len(matches) != 1:
                    raise ValueError("article_front_identity_not_unique_or_mismatched")
                text, blocks = article_blocks(article)
                rendition = "cached_jats_reading_blocks/1.0; original XML preserved"
                media_type, suffix = "application/xml", ".xml"
            else:
                if not original_raw.startswith(b"%PDF-"):
                    raise ValueError("registered_original_not_pdf")
                check = record.get("first_pages_check", {})
                if check.get("full_title_match") is not True or check.get("printed_identifier") not in {"doi", "pmid"}:
                    raise ValueError("pdf_identity_not_previously_verified")
                text_path = resolve_recorded(record["rendition"], origin, workspace)
                text_raw = read_materialized(text_path, record["rendition_sha256"])
                text = text_raw.decode("utf-8")
                # DOI in a bibliography is insufficient: inspect only first PDF page.
                first_page = text.split("\f", 1)[0]
                first_page = re.sub(r"\[Rendition provenance, not publication content\].*?(?:\n\n|\Z)", "", first_page, flags=re.S)
                expected_ids = item["ids"][check["printed_identifier"]]
                if not any((v.lower() in first_page.lower()) for v in expected_ids):
                    raise ValueError("pdf_first_page_identifier_not_in_rendition")
                if len(text.strip()) < 1500 or "\f" not in text:
                    raise ValueError("pdf_body_rendition_not_established")
                matches = expected_matches
                if len(matches) != 1:
                    raise ValueError("registered_identity_ambiguous")
                blocks = [{"block_id": f"b{i+1:05d}", "kind": "pdf_page", "locator": f"PDF text page {i+1}", "text": t.strip()}
                          for i, t in enumerate(text.split("\f")) if t.strip()]
                article_ids = {"pmid": matches[0], "doi": next(iter(item["ids"]["doi"]), "")}
                rendition = "hash_verified_registered_pdf_text; no OCR or layout equivalence"
                media_type, suffix = "application/pdf", ".pdf"
            pmid = matches[0]
            if pmid in ledger["recoveries"]:
                candidate["status"] = "verified_alternate_preserved_in_ledger"
                continue
            directory = output / "papers" / pmid
            directory.mkdir(parents=True)
            original_copy = directory / ("original" + suffix)
            original_copy.write_bytes(original_raw)
            text_path = directory / "article.txt"
            text_path.write_text(text, encoding="utf-8")
            blocks_path = directory / "blocks.json"
            blocks_path.write_text(json.dumps({"format_version": "cached_reading_blocks/1.0", "source_id": "paper:" + pmid,
                                               "original_sha256": digest(original_raw), "blocks": blocks}, ensure_ascii=False, indent=2) + "\n")
            # Copied registration remains auditable even when scratch origin expires.
            (directory / "cache_registration.json").write_bytes(read_materialized(origin, item["pin"]["sha256"]))
            for index, packet in enumerate(candidate["packets"], 1):
                copied_packet = directory / f"cache_packet_{index:04d}.json"
                pin = packet["packet"]
                copied_packet.write_bytes(read_materialized(Path(pin["path"]), pin["sha256"]))
                packet["preserved_path"] = str(copied_packet.relative_to(output))
            candidate["status"] = "verified_cache_recovered_not_content_reviewed"
            recovered = {"source_id": "paper:" + pmid, "status": "fulltext_acquired", "format": item["kind"],
                         "path": str(original_copy.relative_to(output)), "sha256": digest(original_raw), "bytes": len(original_raw),
                         "text_path": str(text_path.relative_to(output)), "text_sha256": digest(text.encode("utf-8")),
                         "blocks_path": str(blocks_path.relative_to(output)), "blocks_sha256": digest(blocks_path.read_bytes()), "block_count": len(blocks),
                         "cache_registration_path": str((directory / "cache_registration.json").relative_to(output)),
                         "cache_registration_sha256": item["pin"]["sha256"],
                         "url": candidate["url"], "identifiers": article_ids, "rendition": rendition,
                         "media_type": media_type, "cache_provenance": candidate,
                         "scientific_support_status": "fulltext_available_not_content_reviewed"}
            ledger["recoveries"][pmid] = recovered
        except (ValueError, OSError, ET.ParseError, KeyError, UnicodeError) as exc:
            candidate["status"] = "not_recovered"
            candidate["reason"] = str(exc).split(": ", 1)[0]
    ledger["statistics"] = {"targets": len(targets), "metadata_records": len(records), "packets": len(packets),
                            "candidates": len(ledger["candidates"]), "recovered": len(ledger["recoveries"]),
                            "remaining": len(targets) - len(ledger["recoveries"]),
                            "candidate_gap_types": dict(Counter(c.get("reason") for c in ledger["candidates"] if c.get("reason"))),
                            "metadata_gap_types": dict(Counter(i["reason"] for i in ledger["issues"]))}
    ledger["unresolved_targets"] = [{"pmid": p, "title": acquired.get("publications", {}).get(p, {}).get("title"),
                                       "prior_status": acquired["fulltexts"][p].get("status"),
                                       "candidate_count": sum(p in c["target_pmids"] for c in ledger["candidates"])}
                                      for p in sorted(set(targets) - set(ledger["recoveries"]))]
    if digest(read_materialized(sources_path)) != digest(raw_sources):
        raise ValueError("parent_acquisition_changed")
    (output / "recoveries.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    return ledger


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--root", type=Path, action="append", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = recover(args.sources, args.root, args.output, args.workspace)
    print(json.dumps(result["statistics"], indent=2))


if __name__ == "__main__":
    main()
