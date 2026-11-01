"""Export a new recovery overlay with source-bound PubMed metadata.

Offline only: verify original acquisition receipts, reparse each article's own
identity, and add the same metadata_source/metadata_source_sha256 fields used by
the citation resolver. Historical overlays and scientific assertions stay intact.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from genomics_discovery.illumina.sources import parse_pubmed, sha, write  # noqa: E402


EXTERNAL_MAPS = ("additional_external_sources", "external_publications")
CANONICAL_EXTERNAL_FIELDS = {"doi", "title", "abstract", "identity_status", "text_path", "text_sha256",
                             "metadata_source", "metadata_source_sha256", "derivation", "kind"}


def locate_source(value: str, overlay_root: Path, lookup_roots: list[Path]) -> Path:
    path = Path(value)
    candidates = [path] if path.is_absolute() else [overlay_root / path] + [root / path for root in lookup_roots]
    found = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
    if found is None:
        raise ValueError(f"External source path is unavailable; provide --lookup-root for recorded relative paths: {value}")
    return found


def doi_key(value):
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(value or "").strip().lower())
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        raise ValueError("External source lacks a valid own DOI")
    return doi


def normalize_external(record_id: str, record: dict, overlay_root: Path, output: Path,
                       lookup_roots: list[Path]) -> tuple[dict, dict, tuple[Path, bytes] | None]:
    """Check every external document before downstream source registration."""
    if not isinstance(record, dict):
        raise ValueError(f"External document must be an object: {record_id}")
    result = copy.deepcopy(record)
    embedded = record.get("primary_api_record")
    planned_text = None
    if embedded is not None:
        if not isinstance(embedded, dict):
            raise ValueError(f"External primary API record is not an object: {record_id}")
        receipt = record.get("metadata_receipt", {})
        if not receipt.get("path") or not receipt.get("sha256") or receipt.get("http_status") != 200:
            raise ValueError(f"External API record lacks an original successful receipt: {record_id}")
        source = locate_source(receipt["path"], overlay_root, lookup_roots)
        raw = source.read_bytes()
        if sha(raw) != receipt["sha256"]:
            raise ValueError(f"External API response custody mismatch: {record_id}")
        body = json.loads(raw)
        matches = [(index, item) for index, item in enumerate(body.get("collection", [])) if item == embedded]
        if len(matches) != 1:
            raise ValueError(f"External embedded DOI/title/abstract record differs from its bound API collection: {record_id}")
        for field in ("doi", "title", "abstract"):
            if not isinstance(embedded.get(field), str) or not embedded[field].strip():
                raise ValueError(f"External primary API record lacks required {field}: {record_id}")
        doi, title, abstract = doi_key(embedded["doi"]), embedded["title"], embedded["abstract"]
        if record_id.startswith("preprint:") and doi_key(record_id.removeprefix("preprint:")) != doi:
            raise ValueError(f"External API DOI differs from the declared preprint source ID: {record_id}")
        if record.get("doi") is not None and doi_key(record["doi"]) != doi:
            raise ValueError(f"External top-level DOI differs from the bound API record: {record_id}")
        for field, expected in (("title", title), ("abstract", abstract)):
            if record.get(field) is not None and record[field] != expected:
                raise ValueError(f"External top-level {field} differs from the bound API record: {record_id}")
        if record.get("text_path") or record.get("text_sha256"):
            if not record.get("text_path") or not record.get("text_sha256"):
                raise ValueError(f"External existing text binding is incomplete: {record_id}")
            previous = locate_source(record["text_path"], overlay_root, lookup_roots)
            if sha(previous.read_bytes()) != record["text_sha256"]:
                raise ValueError(f"External existing text custody mismatch: {record_id}")
        text = ("Title: " + title + "\nDOI: " + doi + "\nAuthors: " + str(embedded.get("authors", "")) +
                "\nVersion: " + str(embedded.get("version", "")) + "\nPosted: " + str(embedded.get("date", "")) +
                "\n\nAbstract:\n" + abstract + "\n").encode("utf-8")
        target = output.resolve() / "external" / sha(record_id.encode())[:24] / "title_abstract.txt"
        result.update(doi=doi, title=title, abstract=abstract, kind="text",
            identity_status="primary_api_collection_own_doi_title_abstract_verified",
            text_path=str(target), text_sha256=sha(text), metadata_source=str(source), metadata_source_sha256=sha(raw),
            derivation={"kind": "deterministic_title_abstract_rendition", "input_path": str(source),
                "input_sha256": sha(raw), "collection_record_index": matches[0][0],
                "primary_api_record_sha256": sha(json.dumps(embedded, ensure_ascii=False, sort_keys=True).encode())})
        planned_text = target, text
        check = {"record_id": record_id, "doi": doi, "title": title, "metadata_source": str(source),
                 "metadata_source_sha256": sha(raw), "own_doi_title_abstract_checked": True,
                 "raw_api_record_preserved": result["primary_api_record"] == embedded,
                 "published_field_as_reported": embedded.get("published"),
                 "text_path": str(target), "text_sha256": sha(text)}
    else:
        # Other supported exporters can supply an already normalized record.
        # Do not guess missing identity or source fields from a label or URL.
        required = ("doi", "title", "identity_status", "text_path", "text_sha256")
        missing = [field for field in required if not isinstance(record.get(field), str) or not record[field].strip()]
        if missing:
            raise ValueError(f"External document lacks required canonical fields {missing}: {record_id}")
        doi_key(record["doi"])
        if record["identity_status"].lower() in {"unverified", "unknown", "metadata_unverified"}:
            raise ValueError(f"External canonical document has no established metadata identity: {record_id}")
        text_path = locate_source(record["text_path"], overlay_root, lookup_roots)
        if sha(text_path.read_bytes()) != record["text_sha256"]:
            raise ValueError(f"External canonical text custody mismatch: {record_id}")
        result["text_path"] = str(text_path)
        check = {"record_id": record_id, "doi": record["doi"], "title": record["title"],
                 "existing_canonical_identity_preserved": True, "text_path": str(text_path),
                 "text_sha256": record["text_sha256"], "provider_identity_reverified": False}
    for field in ("doi", "title", "identity_status", "text_path", "text_sha256"):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise ValueError(f"External export lacks required canonical field {field}: {record_id}")
    for key, value in record.items():
        if key not in CANONICAL_EXTERNAL_FIELDS and result.get(key) != value:
            raise AssertionError(f"External source payload changed during normalization: {record_id}/{key}")
    return result, check, planned_text


def bind_overlay(overlay_path: Path, metadata_sources: list[tuple[Path, Path]], output: Path,
                 *, lookup_roots: list[Path] = ()) -> dict:
    original = overlay_path.read_bytes()
    overlay = json.loads(original)
    if not isinstance(overlay.get("additional_publications"), dict):
        raise ValueError("Recovery overlay must contain an additional_publications map")
    verified = {}
    raw_sources = []
    for xml_path, receipt_path in metadata_sources:
        raw, receipt_raw = xml_path.read_bytes(), receipt_path.read_bytes()
        receipt = json.loads(receipt_raw)
        if receipt.get("http_status") != 200 or receipt.get("sha256") != sha(raw):
            raise ValueError(f"PubMed metadata receipt/custody mismatch: {xml_path}")
        publications = parse_pubmed(raw)
        pin = {"metadata_source": str(xml_path.resolve()), "metadata_source_sha256": sha(raw),
               "acquisition_receipt": {"path": str(receipt_path.resolve()), "sha256": sha(receipt_raw)},
               "request_url": receipt.get("request_url")}
        raw_sources.append({**pin, "own_pmids": sorted(publications)})
        for pmid, publication in publications.items():
            if pmid in verified and verified[pmid][0] != publication:
                raise ValueError(f"Original XML sources disagree about publication identity: {pmid}")
            verified.setdefault(pmid, (publication, pin))
    result = copy.deepcopy(overlay)
    checks = []
    for pmid, publication in result["additional_publications"].items():
        if publication.get("pmid") != pmid or pmid not in verified:
            raise ValueError(f"Recovery publication lacks a matching own PubMed record: {pmid}")
        actual, pin = verified[pmid]
        normalize_dois = lambda item: {value.strip().lower() for value in item.get("identifiers", {}).get("doi", [])}
        if (publication.get("title") != actual.get("title") or normalize_dois(publication) != normalize_dois(actual)
                or publication.get("identifiers", {}) != actual.get("identifiers", {})):
            raise ValueError(f"Recovery publication identity differs from original PubMed XML: {pmid}")
        # All previously exported metadata, not just identity, must come from
        # the same parsed record. Additional source-binding fields are allowed.
        for key, value in actual.items():
            if publication.get(key) != value:
                raise ValueError(f"Recovery publication field {key!r} differs from original XML: {pmid}")
        if publication.get("metadata_source_sha256") not in {None, pin["metadata_source_sha256"]}:
            raise ValueError(f"Recovery publication already advertises a conflicting metadata pin: {pmid}")
        publication["metadata_source"] = pin["metadata_source"]
        publication["metadata_source_sha256"] = pin["metadata_source_sha256"]
        checks.append({"pmid": pmid, "title": actual["title"], "identifiers": actual["identifiers"],
                       **pin, "own_pmid_title_doi_and_metadata_fields_match": True})
    external_checks, planned_texts = [], []
    roots = [Path(__file__).resolve().parents[2], *lookup_roots]
    for map_name in EXTERNAL_MAPS:
        documents = result.get(map_name, {})
        if not isinstance(documents, dict):
            raise ValueError(f"External document collection must be a map: {map_name}")
        for record_id, record in documents.items():
            normalized, check, planned_text = normalize_external(record_id, record, overlay_path.parent, output, roots)
            result[map_name][record_id] = normalized
            external_checks.append({"map": map_name, **check})
            if planned_text is not None:
                planned_texts.append(planned_text)
    metadata_maps = {"additional_publications", *EXTERNAL_MAPS}
    before_assertions = {key: value for key, value in overlay.items() if key not in metadata_maps}
    after_assertions = {key: value for key, value in result.items() if key not in metadata_maps}
    if before_assertions != after_assertions:
        raise AssertionError("Source binding must not change curator assertions")
    output.mkdir(parents=True, exist_ok=False)
    for path, raw in planned_texts:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        if sha(path.read_bytes()) != sha(raw):
            raise ValueError(f"Derived external text custody mismatch: {path}")
    destination = output / "recovery_overlay.json"
    write(destination, result)
    receipt = {"format_version": "illumina_recovery_metadata_export_check/1.0", "status": "passed",
        "parent_overlay": {"path": str(overlay_path.resolve()), "sha256": sha(original)},
        "exported_overlay": {"path": str(destination.resolve()), "sha256": sha(destination.read_bytes())},
        "metadata_sources": raw_sources, "publication_bindings": checks, "external_bindings": external_checks,
        "publications_checked": len(checks), "curator_assertions_unchanged": True,
        "external_documents_checked": len(external_checks), "external_documents_normalized_before_export": True,
        "historical_parent_bytes_unchanged": overlay_path.read_bytes() == original,
        "network_requests": 0, "model_calls": 0, "scientific_protocol_validation": False}
    if not receipt["historical_parent_bytes_unchanged"]:
        raise ValueError("Parent recovery overlay changed during export")
    write(output / "metadata_export_check.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--metadata-source", type=Path, nargs=2, action="append", required=True,
                        metavar=("ORIGINAL_PUBMED_XML", "ACQUISITION_RECEIPT_JSON"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lookup-root", type=Path, action="append", default=[])
    args = parser.parse_args()
    result = bind_overlay(args.overlay, args.metadata_source, args.output, lookup_roots=args.lookup_root)
    print(json.dumps({"status": result["status"], "publications_checked": result["publications_checked"],
                      "curator_assertions_unchanged": result["curator_assertions_unchanged"]}))


if __name__ == "__main__":
    main()
