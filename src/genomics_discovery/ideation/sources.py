"""Preserve captured evidence and its custody records across agent handoffs.

Alternative titles/content levels are declarations, not promoted scientific
facts. A receipt remains a receipt; citation verification still checks its
captured files, publication identity and scientific support separately.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path


_DECLARATION_FIELDS = ("path", "captured_at", "title", "content_level", "provenance_status")


def _read(path):
    return json.loads(Path(path).read_text())


def _append_unique(target, values):
    for value in values:
        if value not in target:
            target.append(deepcopy(value))


def _declaration(entry):
    return {key: entry[key] for key in _DECLARATION_FIELDS if key in entry}


def _mark_metadata_variants(entry):
    alternatives = entry.get("capture_declarations", [])
    if any(any(row.get(key, entry.get(key)) != entry.get(key)
               for key in ("title", "content_level")) for row in alternatives):
        entry["metadata_status"] = (
            "differing_capture_declarations; first declaration retained without scientific preference")


def _receipt_matches(receipt, entry, *, require_binding=False):
    """Reject conflicting custody, without certifying a receipt's provenance."""
    pin = receipt.get("text_sha256")
    if (require_binding or pin is not None) and pin != entry["content_sha256"]:
        raise ValueError("Source origin receipt has a different captured-text hash")
    urls = {receipt[key] for key in ("request_url", "final_url") if receipt.get(key)}
    if urls and entry.get("url") and entry["url"] not in urls:
        raise ValueError("Source origin receipt has a different capture URL")


def _with_sidecars(registry_path):
    registry = _read(registry_path)
    if registry.get("format_version") != "ideation_sources/1":
        raise ValueError("Unknown literature registry format")
    entries = registry["sources"]
    for ident, entry in entries.items():
        if hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest() != entry["content_sha256"]:
            raise ValueError("Captured source changed before handoff: " + ident)
        for receipt in entry.get("origin_receipts", []):
            _receipt_matches(receipt, entry)
    for filename, field in (("origin_receipts.json", "origin_receipts"),
                            ("capture_declarations.json", "capture_declarations")):
        sidecar = Path(registry_path).parent / filename
        if not sidecar.exists():
            continue
        for ident, records in _read(sidecar).items():
            if ident not in entries:
                raise ValueError("Source sidecar refers to an unregistered source: " + ident)
            if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
                raise ValueError("Source sidecar records must be objects in a list")
            entry = entries[ident]
            if field == "origin_receipts":
                for receipt in records:
                    _receipt_matches(receipt, entry, require_binding=True)
            elif records and field not in entry:
                entry[field] = [_declaration(entry)]
            _append_unique(entry.setdefault(field, []), records)
            _mark_metadata_variants(entry)
    return entries


def merge_sources(paths):
    """Join registries and adjacent sidecars without rewriting input artifacts.

    The caller explicitly supplies each input registry; a missing one is an error,
    not an empty source bundle. Identical URL/content identities retain their
    original address and all distinct capture declarations and origin receipts.
    """
    merged = {}
    for path in paths:
        for ident, entry in _with_sidecars(path).items():
            if ident not in merged:
                merged[ident] = entry
                continue
            existing = merged[ident]
            if existing == entry:
                continue
            if any(existing.get(key) != entry.get(key) for key in ("url", "content_sha256")):
                raise ValueError("Conflicting source registration: " + ident)
            declarations = existing.setdefault("capture_declarations", [_declaration(existing)])
            _append_unique(declarations, [_declaration(entry), *entry.get("capture_declarations", [])])
            captures = existing.setdefault("additional_captures", [])
            capture = {key: entry[key] for key in ("path", "captured_at") if key in entry}
            if capture.get("path") != existing["path"]:
                _append_unique(captures, [capture])
            _append_unique(captures, entry.get("additional_captures", []))
            _append_unique(existing.setdefault("origin_receipts", []), entry.get("origin_receipts", []))
            _mark_metadata_variants(existing)
    return {"format_version": "ideation_sources/1", "sources": merged}
