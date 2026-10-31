"""Create a custody-checked source snapshot with explicit recovery overlays.

No network requests or model calls. Original catalogue records and parent
snapshots remain unchanged. Current files are copied into the new snapshot;
historical registrations stay byte-identical even when their internal paths
describe their original machine.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from genomics_discovery.curation.illumina_sources import (  # noqa: E402
    ACQUIRED_FORMAT, FORMAT, parse_pubmed, sha, write,
)
from illumina_cached_sources import identities, xml_article  # noqa: E402


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class SnapshotFiles:
    """Resolve declared relocations, verify bytes, and register copied files."""

    PATHS = {"path": "sha256", "text_path": "text_sha256", "blocks_path": "blocks_sha256",
             "metadata_source": "metadata_source_sha256", "cache_registration_path": "cache_registration_sha256"}

    def __init__(self, output: Path, mappings: list[tuple[Path, Path]], lookup_roots: list[Path]):
        self.output = output.resolve()
        self.mappings = mappings
        self.lookup_roots = lookup_roots
        self.imports = {}

    def locate(self, value: str, descriptor_root: Path) -> Path:
        path = Path(value)
        candidates = []
        # A declared deployment relocation wins over a coincidentally existing
        # old path. Its advertised content hash still has to match.
        if path.is_absolute():
            for old, new in sorted(self.mappings, key=lambda pair: len(pair[0].parts), reverse=True):
                if path.is_relative_to(old):
                    candidates.append(new / path.relative_to(old))
            candidates.append(path)
        else:
            candidates.append(descriptor_root / path)
            candidates.extend(root / path for root in self.lookup_roots)
            # Receipts from a CLI may contain a repo-relative path while its
            # current descriptor is nested deeper. Explicit mappings can also
            # resolve a matching multi-component suffix of the original root.
            for old, new in self.mappings:
                for index in range(1, len(old.parts) - 1):
                    suffix = Path(*old.parts[index:])
                    if path.is_relative_to(suffix):
                        candidates.append(new / path.relative_to(suffix))
        found = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
        if found is None:
            raise FileNotFoundError(f"No source file for declared path {value!r}; provide --path-map or --lookup-root")
        return found

    def import_file(self, value: str, descriptor_root: Path, expected: str | None = None) -> tuple[str, dict]:
        source = self.locate(value, descriptor_root)
        observed = file_hash(source)
        if expected is not None and observed != expected:
            raise ValueError(f"Source custody mismatch: {source}")
        suffix = source.suffix.lower() if len(source.suffix) <= 12 else ""
        destination = self.output / "imports" / observed[:2] / (observed + suffix)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        if file_hash(destination) != observed:
            raise ValueError(f"Imported source custody mismatch: {destination}")
        key = str(destination)
        provenance = {"declared_path": value, "resolved_input_path": str(source), "sha256": observed,
                      "bytes": source.stat().st_size, "imported_path": key}
        self.imports.setdefault(key, {"path": key, "sha256": observed, "bytes": source.stat().st_size,
                                      "origins": []})["origins"].append(provenance)
        return key, provenance

    def records(self, value, descriptor_root: Path):
        if isinstance(value, list):
            return [self.records(item, descriptor_root) for item in value]
        if not isinstance(value, dict):
            return value
        # The durable copied cache registration/packets have explicit current
        # fields. cache_provenance describes historical paths/states and stays
        # unchanged rather than pretending those old paths were relocated.
        result = {key: copy.deepcopy(item) if key == "cache_provenance" else self.records(item, descriptor_root)
                  for key, item in value.items()}
        imported = {}
        for key, hash_key in self.PATHS.items():
            if isinstance(result.get(key), str):
                result[key], imported[key] = self.import_file(result[key], descriptor_root, result.get(hash_key))
                result[hash_key] = imported[key]["sha256"]
        if imported:
            result["snapshot_file_imports"] = imported
        return result


def merge_snapshot(source_snapshot: Path, recovery_bundle: Path, output: Path, *,
                   mappings: list[tuple[Path, Path]] = (), lookup_roots: list[Path] = (),
                   cached_recoveries: Path | None = None) -> dict:
    source_snapshot, recovery_bundle = source_snapshot.resolve(), recovery_bundle.resolve()
    parent_raw, bundle_raw = source_snapshot.read_bytes(), recovery_bundle.read_bytes()
    parent, bundle = json.loads(parent_raw), json.loads(bundle_raw)
    if parent.get("format_version") != ACQUIRED_FORMAT:
        raise ValueError("Unknown source acquisition format")
    if bundle.get("format_version") != "illumina_source_recovery_bundle/1.0":
        raise ValueError("Unknown source recovery bundle format")
    inventory_path = source_snapshot.parent / "inventory.json"
    inventory_raw = inventory_path.read_bytes()
    if sha(inventory_raw) != parent["inventory_sha256"]:
        raise ValueError("Parent source inventory custody mismatch")
    inventory = json.loads(inventory_raw)
    if inventory.get("format_version") != FORMAT:
        raise ValueError("Unknown inventory format")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    files = SnapshotFiles(output, list(mappings), list(lookup_roots))
    state = files.records(copy.deepcopy(parent), source_snapshot.parent)
    recovered = files.records(copy.deepcopy(bundle), recovery_bundle.parent)

    # Reparse the original raw XML. A ReferenceList/ArticleId is never an
    # identifier of the containing publication. Preserve non-returned records.
    publications = {key: value for key, value in state.get("publications", {}).items()
                    if value.get("identity_status") != "pubmed_record_retrieved"}
    reparsed, changed = {}, []
    for batch in state.get("publication_batches", []):
        if batch.get("status") != "downloaded":
            continue
        batch_path = Path(batch["path"])
        found = parse_pubmed(batch_path.read_bytes())
        if set(found) - set(batch["requested_pmids"]):
            raise ValueError("Original PubMed batch contains an unrequested publication")
        for pmid, record in found.items():
            record["metadata_source"] = str(batch_path)
            record["metadata_source_sha256"] = batch["sha256"]
            old_ids = parent.get("publications", {}).get(pmid, {}).get("identifiers", {})
            if old_ids != record["identifiers"]:
                changed.append({"pmid": pmid, "previous_identifiers": old_ids,
                                "own_article_identifiers": record["identifiers"]})
            if pmid in reparsed and reparsed[pmid]["identifiers"] != record["identifiers"]:
                raise ValueError("Repeated PubMed identity has inconsistent own identifiers")
            reparsed[pmid] = record
    expected_returned = {key for key, value in parent.get("publications", {}).items()
                         if value.get("identity_status") == "pubmed_record_retrieved"}
    if set(reparsed) != expected_returned:
        raise ValueError("Original XML does not recover the same retrieved publication identities")
    publications.update(reparsed)
    state["publications"] = publications
    state["metadata_identifier_reaudit"] = {"rule": "own_article_ArticleIdList_only; cited_reference_IDs_excluded",
        "records_reparsed": len(reparsed), "records_with_corrected_identifiers": len(changed), "changes": changed}

    cached_count = 0
    if cached_recoveries:
        cached_recoveries = cached_recoveries.resolve()
        cached_raw = cached_recoveries.read_bytes()
        cached = json.loads(cached_raw)
        if cached.get("format_version") != "illumina_cached_source_recovery/1.0":
            raise ValueError("Unknown cached recovery format")
        if cached.get("parent_acquisition", {}).get("sha256") != sha(parent_raw):
            raise ValueError("Cached recovery parent acquisition does not match supplied source snapshot")
        for pmid, record in cached.get("recoveries", {}).items():
            if (pmid not in reparsed or reparsed[pmid].get("pmid") != pmid
                    or record.get("source_id") != "paper:" + pmid
                    or record.get("status") != "fulltext_acquired"):
                raise ValueError(f"Cached recovery does not identify an existing exact PMID: {pmid}")
            if state.get("fulltexts", {}).get(pmid, {}).get("status") == "fulltext_acquired":
                raise ValueError(f"Cached recovery would overwrite an acquired fulltext: {pmid}")
            current = copy.deepcopy(record)
            for path_key, hash_key in (("path", "sha256"), ("text_path", "text_sha256"),
                                       ("blocks_path", "blocks_sha256"),
                                       ("cache_registration_path", "cache_registration_sha256")):
                if not isinstance(current.get(path_key), str) or not re.fullmatch(r"[a-f0-9]{64}", current.get(hash_key, "")):
                    raise ValueError(f"Cached recovery lacks a pinned {path_key}: {pmid}")
            current["cache_packet_copies"] = []
            for packet in current.get("cache_provenance", {}).get("packets", []):
                if not packet.get("preserved_path") or not packet.get("packet", {}).get("sha256"):
                    raise ValueError(f"Cached recovery packet lacks a durable path or pin: {pmid}")
                current["cache_packet_copies"].append({"path": packet["preserved_path"],
                    "sha256": packet["packet"]["sha256"], "historical_packet_pin": {
                        "recorded_path": packet["packet"]["path"], "sha256": packet["packet"]["sha256"]}})
            imported = files.records(current, cached_recoveries.parent)
            verify_cached_identity(pmid, imported, reparsed[pmid])
            imported["prior_fulltext_attempt"] = state.get("fulltexts", {}).get(pmid)
            state.setdefault("fulltexts", {})[pmid] = imported
            cached_count += 1
        imported_ledger, ledger_pin = files.import_file(str(cached_recoveries), cached_recoveries.parent, sha(cached_raw))
        state["cached_source_recovery"] = {"path": imported_ledger, "sha256": sha(cached_raw),
            "parent_acquisition_sha256": sha(parent_raw), "fulltexts_merged": cached_count,
            "source_import": ledger_pin}

    cards = {card["record_id"]: card for card in inventory["cards"]}
    assets = {asset["asset_id"]: asset for asset in inventory["assets"]}
    acquired_assets = {asset["asset_id"]: asset for asset in state["assets"]}
    attribution = {"bundle_path": str(recovery_bundle), "bundle_sha256": sha(bundle_raw),
                   "kind": "explicit_recovered_source_overlay; original_listing_preserved"}
    for card_id, overlay in recovered.get("card_overlays", {}).items():
        if card_id not in cards or overlay["original_card"] != cards[card_id]:
            raise ValueError(f"Recovered overlay does not match original inventory card: {card_id}")
        card = cards[card_id]
        card["recovered_title"] = overlay["recovered_title"]
        card["source_overlay"] = {**attribution, "title_basis": overlay["title_basis"],
            "scope_note": overlay["scope_note"], "source_content_restored": overlay["source_content_restored"]}
        for asset in overlay.get("assets", []):
            aid = asset["asset_id"]
            if aid in acquired_assets and acquired_assets[aid]["sha256"] != asset["sha256"]:
                raise ValueError(f"Recovered diagram conflicts with existing asset bytes: {aid}")
            assets.setdefault(aid, {key: asset[key] for key in ("asset_id", "source_id", "kind", "url", "card_ids")})
            acquired_assets[aid] = asset
            if aid not in card["diagram_asset_ids"]:
                card["diagram_asset_ids"].append(aid)
    for card_id, refs in recovered.get("recovered_card_references", {}).items():
        if card_id not in cards:
            raise ValueError(f"Unknown recovered-reference card: {card_id}")
        cards[card_id]["recovered_references"] = refs
        cards[card_id].setdefault("source_overlay", attribution)
    inventory["assets"] = list(assets.values())
    inventory["source_recovery_overlay"] = attribution
    # These fields describe acquisition scope, not the number of split methods.
    inventory["original_catalogue_statistics"] = copy.deepcopy(inventory["statistics"])
    inventory["statistics"]["cards_with_diagrams"] = sum(bool(card["diagram_asset_ids"]) for card in cards.values())
    inventory["statistics"]["unique_method_diagrams"] = sum(
        asset["kind"] == "method_diagram" for asset in assets.values())
    inventory["statistics"]["assets_with_recovery"] = len(assets)
    inventory["statistics"]["cards_with_recovered_diagram"] = len(recovered.get("card_overlays", {}))
    state["assets"] = list(acquired_assets.values())
    existing_external = state.setdefault("external_publications", {})
    for ident, record in recovered["external_publications"].items():
        if ident in existing_external and existing_external[ident] != record:
            raise ValueError(f"External reference would overwrite an existing registration: {ident}")
        existing_external[ident] = record
    state["card_overlays"] = recovered.get("card_overlays", {})
    state["recovered_card_references"] = recovered.get("recovered_card_references", {})
    state["scientific_review"] = False
    state["source_snapshot_revision"] = "custody_merge_and_own_article_identity_reparse/1.0"
    state["snapshot_parents"] = {"source_snapshot": {"path": str(source_snapshot), "sha256": sha(parent_raw)},
        "inventory": {"path": str(inventory_path), "sha256": sha(inventory_raw)},
        "recovery_bundle": {"path": str(recovery_bundle), "sha256": sha(bundle_raw)}}
    inventory_output = output / "inventory.json"
    write(inventory_output, inventory)
    state["inventory_sha256"] = file_hash(inventory_output)
    state["statistics"].update(assets_expected=len(assets), assets_downloaded=sum(
        asset.get("status") == "downloaded" for asset in state["assets"]),
        recovered_external_references=len(existing_external),
        recovered_external_fulltexts=sum(record.get("content_level") == "fulltext" for record in existing_external.values()),
        metadata_records_reparsed=len(reparsed), metadata_records_with_corrected_identifiers=len(changed),
        cached_fulltexts_merged=cached_count, fulltexts_acquired=sum(
            record.get("status") == "fulltext_acquired" for record in state.get("fulltexts", {}).values()))
    write(output / "imports.json", list(files.imports.values()))
    state["imports_manifest"] = {"path": str(output / "imports.json"), "sha256": file_hash(output / "imports.json")}
    if source_snapshot.read_bytes() != parent_raw or recovery_bundle.read_bytes() != bundle_raw:
        raise ValueError("A parent source snapshot changed during merge")
    write(output / "sources.json", state)
    return state


def verify_cached_identity(pmid: str, record: dict, publication: dict) -> None:
    """Check a cache's own article identity, never a cited identifier."""
    expected_dois = identities(publication)["doi"]
    def agrees(raw_ids):
        observed = identities(raw_ids)
        if observed["pmid"] and observed["pmid"] != {pmid}:
            return False
        if observed["doi"] and expected_dois and observed["doi"].isdisjoint(expected_dois):
            return False
        return observed["pmid"] == {pmid} or bool(observed["doi"] & expected_dois)
    if not agrees(record.get("identifiers", {})):
        raise ValueError(f"Cached recovery identifiers disagree with original PubMed identity: {pmid}")
    original = Path(record["path"]).read_bytes()
    if record.get("format") == "JATS":
        _, own_ids = xml_article(original)
        if not agrees(own_ids):
            raise ValueError(f"Cached JATS article front disagrees with PubMed identity: {pmid}")
    elif record.get("format") == "registered_pdf_text":
        registration = json.loads(Path(record["cache_registration_path"]).read_bytes())
        if (not original.startswith(b"%PDF-") or not agrees(registration.get("identity", {}))
                or registration.get("original_sha256") != record["sha256"]
                or registration.get("rendition_sha256") != record["text_sha256"]):
            raise ValueError(f"Cached PDF registration does not bind this original and rendition: {pmid}")
    else:
        raise ValueError(f"Unsupported cached fulltext format: {record.get('format')}")
    blocks = json.loads(Path(record["blocks_path"]).read_bytes())
    if blocks.get("source_id") != "paper:" + pmid or blocks.get("original_sha256") != record["sha256"]:
        raise ValueError(f"Cached reading blocks do not identify this original article: {pmid}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--recovery-bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--path-map", action="append", default=[], metavar="OLD_PREFIX=STAGED_PREFIX")
    parser.add_argument("--lookup-root", action="append", default=[], type=Path)
    parser.add_argument("--cached-recoveries", type=Path)
    args = parser.parse_args()
    mappings = []
    for item in args.path_map:
        old, separator, new = item.partition("=")
        if not separator or not Path(old).is_absolute() or not Path(new).is_absolute():
            parser.error("--path-map requires absolute OLD_PREFIX=STAGED_PREFIX")
        mappings.append((Path(old), Path(new)))
    result = merge_snapshot(args.source_snapshot, args.recovery_bundle, args.output,
                            mappings=mappings, lookup_roots=args.lookup_root, cached_recoveries=args.cached_recoveries)
    print(json.dumps(result["statistics"]))


if __name__ == "__main__":
    main()
