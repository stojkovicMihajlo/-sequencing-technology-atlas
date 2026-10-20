"""Revalidate publication identity from original bytes, never rendition prose."""

import json
from pathlib import Path
import re

from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.source_acquisition.html_identity import assess_primary_html
from genomics_discovery.curation.source_acquisition.models import Identity
from genomics_discovery.curation.source_acquisition.runtime import _doi, _identity, _match, _normal
from .pdf_identity import verify_pdf_registration


def verify_publication(packet, case, *, packet_path=None, project_root=None, pins=None, bound_pins=None):
    """Return exact identity proof/pins for every primary; retain observed pins on failure.

    Search only the packet directory and the rendition's two immediate directories
    for originals_ledger.json. A receipt must bind source ID, path and bytes; its
    asserted identity/assessment is ignored and the original is reparsed instead.
    """
    expected = Identity(**{key: case["primary_identity"][key] for key in ("pmid", "doi", "title")
        if case["primary_identity"].get(key)})
    if not (expected.pmid or expected.doi):
        raise ValueError("Primary publication requires independently scoped PMID/DOI")
    pins = {} if pins is None else pins
    records = []
    root = Path(project_root).resolve() if project_root else None

    def pinned(value, expected_sha=None):
        path = Path(value)
        if not path.is_absolute() or root is not None and not path.resolve().is_relative_to(root):
            raise ValueError(f"Publication provenance path is outside its declared project: {path}")
        if expected_sha is not None and (not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)):
            raise ValueError(f"Invalid publication SHA256 pin: {path}")
        if bound_pins is not None:
            if str(path) not in bound_pins or expected_sha is not None and bound_pins[str(path)] != expected_sha:
                raise ValueError(f"Publication provenance is not bound by prior source custody: {path}")
            expected_sha = bound_pins[str(path)]
        actual_sha = sha(path)
        if expected_sha is not None and actual_sha != expected_sha:
            raise ValueError(f"Publication provenance SHA256 mismatch: {path}")
        if str(path) in pins and pins[str(path)] != actual_sha:
            raise ValueError(f"Publication provenance changed: {path}")
        pins[str(path)] = actual_sha
        return path

    primaries = [source for source in packet["sources"] if source["role"] == "primary_paper"]
    if not primaries:
        raise ValueError("Packet has no primary publication")
    for source in primaries:
        if not isinstance(source.get("original_sha256"), str):
            raise ValueError("Primary source lacks a declared SHA256 pin")
        rendition = pinned(source["original_path"], source["original_sha256"])
        record = {"source_id": source["source_id"], "rendition": str(rendition),
            "rendition_sha256": source["original_sha256"], "expected_identity": expected.model_dump(mode="json")}
        if source["source_id"].startswith("primary_pdf_") and (rendition.parent / "registration.json").is_file():
            record.update(verify_pdf_registration(source, case, expected, pinned))
            records.append(record)
            continue
        original = rendition
        if rendition.suffix.lower() != ".xml":
            candidates = [rendition.parent / "originals_ledger.json", rendition.parent.parent / "originals_ledger.json"]
            if packet_path:
                candidates.append(Path(packet_path).parent / "originals_ledger.json")
            matches = []
            for ledger in dict.fromkeys(candidates):
                if root is not None and not ledger.resolve().is_relative_to(root):
                    continue
                if not ledger.is_file():
                    continue
                ledger = pinned(ledger)
                rows = json.loads(ledger.read_bytes())
                if not isinstance(rows, list):
                    raise ValueError(f"Originals ledger must be a list: {ledger}")
                for row in rows:
                    if (isinstance(row, dict) and row.get("source_id") == source["source_id"]
                            and row.get("rendition") == str(rendition)):
                        if row.get("rendition_sha256") != source["original_sha256"]:
                            raise ValueError(f"Originals ledger rendition SHA256 mismatch: {ledger}")
                        matches.append((ledger, row))
            if not matches:
                raise ValueError(f"Primary rendition lacks an exact source-ID/path/hash originals ledger: {rendition}")
            if len({(row.get("original"), row.get("original_sha256")) for _, row in matches}) != 1:
                raise ValueError(f"Conflicting originals ledger records: {rendition}")
            ledger, receipt = matches[0]
            if not receipt.get("original") or not receipt.get("original_sha256"):
                raise ValueError(f"Originals ledger lacks original path/SHA256: {ledger}")
            original = pinned(receipt["original"], receipt["original_sha256"])
            record.update(receipts=[{"path": str(path), "sha256": pins[str(path)]} for path, _ in matches])
        raw = original.read_bytes()
        if original.suffix.lower() == ".xml":
            actual = _identity(raw, "jats")
            _match(actual, expected)
            record["identity_route"] = "reparsed_jats"
        elif original.suffix.lower() in {".html", ".htm"}:
            actual = _identity(raw, "html")
            record["html_assessment"] = assess_primary_html(raw, actual, expected, expected,
                source["url"], normal=_normal, doi=_doi)
            record["identity_route"] = "ledger_bound_reparsed_html"
        else:
            raise ValueError(f"Primary original is not supported HTML/JATS: {original}")
        record.update(original=str(original), original_sha256=pins[str(original)], actual_identity=actual)
        records.append(record)
    for filename, expected_sha in pins.items():
        if sha(Path(filename)) != expected_sha:
            raise ValueError(f"Publication provenance changed during verification: {filename}")
    return {"primaries": records, "pins": dict(pins),
        "boundary": "Original publication identity and exact rendition custody only; not scientific source completeness."}
