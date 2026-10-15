"""Pin original documents for a bounded local extraction/review pass.

Preparation checks bytes and document metadata, never scientific completeness.
This packet deliberately has no field for an earlier extracted protocol or verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

from .validation import EVIDENCE_POLICY, _sources

FORMAT = "curation_prepared_sources/1.0"


def validate_packet(packet: dict, atlas: Path, method_ids: list[str]) -> None:
    if (not isinstance(packet, dict) or set(packet) != {"format_version", "methods"}
            or packet.get("format_version") != FORMAT):
        raise ValueError("Invalid prepared-source packet format")
    methods = packet["methods"]
    if not isinstance(methods, dict) or set(methods) != set(method_ids):
        raise ValueError("Prepared sources must match exactly the selected methods")
    for method, leads in methods.items():
        if not isinstance(leads, list) or not leads:
            raise ValueError(f"No prepared source documents for {method}")
        records = []
        for lead in leads:
            if (not isinstance(lead, dict)
                    or set(lead) != {"source_id", "title", "url", "files"}
                    or not isinstance(lead["files"], list) or not lead["files"]):
                raise ValueError("Prepared sources contain only identity, URL and file leads")
            paths, hashes, roles = [], {}, {}
            for file in lead["files"]:
                if (not isinstance(file, dict) or set(file) != {"path", "sha256", "role"}
                        or not isinstance(file["path"], str)
                        or not Path(file["path"]).is_absolute()):
                    raise ValueError(
                        "Prepared source files require absolute paths, hashes and roles")
                paths.append(file["path"])
                hashes[file["path"]] = file["sha256"]
                roles[file["path"]] = file["role"]
            # Reuse the integrity gate without asserting that any passages were
            # scientifically read. This temporary adapter is never supplied as a
            # scientific register or exported as a source inspection claim.
            records.append({"source_id": lead["source_id"], "source_metadata": {
                "title": lead["title"], "primary_url": lead["url"],
                "inspected_url": lead["url"], "local_evidence": paths,
                "sha256": hashes, "artifact_roles": roles,
                "sections_inspected": ["Not inspected; file-integrity preparation only"],
                "access_limits": [],
            }})
        _sources(records, atlas, (atlas,), evidence_policy=EVIDENCE_POLICY)


def load_packet(path: Path, atlas: Path, method_ids: list[str]) -> dict:
    packet = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_packet(packet, atlas, method_ids)
    return packet
