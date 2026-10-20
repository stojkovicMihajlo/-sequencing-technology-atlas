"""Account for every atlas method and stage identity-checked sources for extraction.

This is source preparation, not a claim that a source set is complete. Existing
explicitly registered packets take precedence. Otherwise only a single attributed
origin with matching JATS publication identity is staged automatically. Missing
documents, ambiguous scope and invalid packets remain visible jobs, not omissions.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from xml.etree.ElementTree import ParseError

from .document_manifest import inspect_jats
from .cached_documents import primary_html, supplements
from .run_state import sha, write
from .workflow_extraction.sources import build_packet, load_packet


def _methods(atlas: dict) -> list[dict]:
    if atlas.get("format_version") == "integrated_technology_graph/1.0":
        rows = [n["record"] for n in atlas["nodes"] if n["kind"] == "technology"]
    else:
        rows = atlas.get("methods", atlas.get("technologies", []))
    ids = [row["id"] for row in rows]
    if not rows or len(set(ids)) != len(ids):
        raise ValueError("Atlas requires nonempty, unique method IDs")
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", mid) for mid in ids):
        raise ValueError("Method IDs must be safe, stable batch identifiers")
    return rows


def _registered(atlas: dict, overrides: dict, base: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for row in atlas.get("workflows", {}).values():
        if row.get("atlas_method_id") and row.get("packet_path"):
            path = Path(row["packet_path"])
            result.setdefault(row["atlas_method_id"], []).append(
                (path if path.is_absolute() else base / path).resolve())
    # Explicit choices resolve multiple variants; never choose by newest mtime.
    for mid, path in overrides.items():
        result[mid] = [Path(path)]
    return {mid: sorted(set(paths)) for mid, paths in result.items()}


def prepare(atlas_path: Path, cache: Path, output: Path, *, packets: dict | None = None) -> dict:
    """Write a full manifest, per-method source packets and actionable source leads.

    `packets` is an explicit canonical-method-ID -> packet-path mapping. It does
    not infer method identity from names, filenames or prior model descriptions.
    No network requests or model calls occur here.
    """
    atlas_path, cache, output = atlas_path.resolve(), cache.resolve(), output.resolve()
    atlas = json.loads(atlas_path.read_bytes())
    methods = _methods(atlas)
    overrides = {mid: str(Path(path).resolve()) for mid, path in (packets or {}).items()}
    unknown = set(overrides) - {row["id"] for row in methods}
    if unknown:
        raise ValueError(f"Packet mappings reference absent methods: {sorted(unknown)}")
    registered = _registered(atlas, overrides, atlas_path.parent)
    output.mkdir(parents=True, exist_ok=False)
    jobs, report = [], []
    for method in methods:
        mid, title = method["id"], method["name"]
        record = {"id": mid, "title": title,
                  "origin_paper_ids": method.get("origin_paper_ids", []),
                  "source_basis": None, "source_leads": [], "problems": []}
        packet = None
        paths = registered.get(mid, [])
        if paths:
            if len(paths) != 1:
                record["problems"].append("Multiple registered variants; select an explicit packet")
                record["registered_candidates"] = [str(path) for path in paths]
            else:
                try:
                    packet = load_packet(paths[0])
                    record.update(source_basis="explicit_registered_packet",
                                  registered_packet=str(paths[0]),
                                  registered_packet_sha256=sha(paths[0]))
                except (OSError, ValueError, KeyError, ParseError) as exc:
                    record["problems"].append(f"Registered packet failed validation: {exc}")
        else:
            origins = record["origin_paper_ids"]
            if len(origins) != 1:
                record["problems"].append(
                    "Select a source-defined implementation: zero or multiple origin papers")
            elif not re.fullmatch(r"pmid:\d+", origins[0]):
                record["problems"].append("Origin lacks a PMID cache binding; register an explicit packet")
            else:
                pmid = origins[0].split(":", 1)[1]
                folder = cache / pmid
                primary = folder / "article.xml"
                record["source_leads"].append({
                    "paper_id": origins[0], "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "expected_jats": str(primary),
                    "available_files": [str(p) for p in sorted(folder.glob("*")) if p.is_file()],
                })
                if not primary.exists() and not (folder / "article.html").exists():
                    record["problems"].append(
                        "No identity-checkable JATS/HTML source; retrieve full text or register a verified text rendition")
                else:
                    options = dict(
                        method_id=mid, title=title,
                        scope=(f"Describe the general {title} assay implementation in PMID {pmid}, "
                               "from its stated starting material to its intended molecular/data readout. "
                               "Preserve source-defined alternatives separately and distinguish optional "
                               "evaluation experiments. Check applicable supplements and cited procedural "
                               "dependencies; a missing required source is a source acquisition task. "
                               "Do not reconstruct every historical experiment or assume omitted chemistry."),
                        variant=f"Implementation attributed by atlas to PMID {pmid}; verify scope against source")
                    renditions = output / "renditions" / mid
                    # Try independently identity-checkable formats. A stale/broken XML
                    # cache must not hide an available, matching publication HTML.
                    for kind, original in (("jats", primary), ("html", folder / "article.html")):
                        if not original.is_file():
                            continue
                        try:
                            if kind == "jats":
                                structure = inspect_jats(original.read_bytes())
                                ids = structure["identity"]["identifiers"].get("pmid", [])
                                if ids != [pmid]:
                                    raise ValueError(f"JATS PMID mismatch: expected {pmid}, found {ids}")
                                document_title = structure["identity"]["title"]
                                if not document_title:
                                    raise ValueError("JATS publication title is missing")
                                source = {"source_id": f"pmid_{pmid}", "title": document_title,
                                          "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                                          "role": "primary_paper", "path": str(original)}
                                receipt = None
                            else:
                                source, receipt = primary_html(folder, pmid, renditions / "primary.txt")
                            packet = build_packet(**options, sources=[source])
                            record["source_basis"] = f"atlas_origin_and_exact_{kind}_identity"
                            if kind == "jats":
                                record["document_structure"] = structure
                            if receipt is not None:
                                record.setdefault("rendition_receipts", []).append(receipt)
                            break
                        except (OSError, ValueError, KeyError, ParseError) as exc:
                            record.setdefault("primary_format_failures", []).append(
                                {"format": kind, "path": str(original), "error": str(exc)})
                    if packet is None:
                        errors = "; ".join(f["error"] for f in record.get("primary_format_failures", []))
                        record["problems"].append(f"Primary source failed preparation: {errors}")
                    else:
                        # Source readiness is not scientific completeness: a missing,
                        # malformed or foreign supplement register is a visible lead,
                        # never a reason to hide a readable primary from source review.
                        try:
                            extra, receipts, leads = supplements(folder, pmid, renditions)
                        except (OSError, ValueError, KeyError, TypeError) as exc:
                            extra, receipts = [], []
                            leads = [{"status": "supplement_register_invalid",
                                      "register": str(folder / "supplements" / "acquisition.json"),
                                      "error": str(exc)}]
                        record.setdefault("rendition_receipts", []).extend(receipts)
                        record["supplement_leads"] = leads
                        for source in extra:
                            try:
                                # A bad/oversized supplement must not invalidate the
                                # primary or other independently readable supplements.
                                part = build_packet(**options, sources=[source])
                                packet["sources"].extend(part["sources"])
                            except (OSError, ValueError, KeyError, ParseError) as exc:
                                leads.append({"status": "supplement_packet_registration_failed",
                                              "source": source, "error": str(exc)})
        job = {"id": mid, "method_id": mid, "title": title}
        if packet is not None:
            destination = output / "packets" / mid / "packet.json"
            write(destination, packet)
            # Canonical atlas identity and packet implementation ID can differ;
            # carry the latter to the extraction controller, with this mapping logged.
            job.update(status="ready", packet=str(destination.relative_to(output)),
                       method_id=packet["method_id"])
            record.update(status="ready_for_source_inspection", packet=str(destination),
                          packet_method_id=packet["method_id"], source_count=len(packet["sources"]),
                          packet_sha256=sha(destination))
        else:
            reason = "; ".join(record["problems"])
            job.update(status="missing_sources", reason=reason,
                       next_evidence=(f"For {title} ({mid}), resolve: {reason}. "
                                      f"Publication leads: {', '.join(record['origin_paper_ids']) or 'not registered'}. "
                                      "Register the chosen source-defined implementation and its required documents."))
            record["status"] = "needs_source_preparation"
        jobs.append(job)
        report.append(record)
    # Preserve originals beyond the text files registered inside source packets.
    document_pins = {str(atlas_path): sha(atlas_path)}
    for row in report:
        for receipt in row.get("rendition_receipts", []):
            for field in ("original", "rendition", "register"):
                if field in receipt:
                    document_pins[receipt[field]] = receipt[field + "_sha256"]
        for lead in row.get("supplement_leads", []):
            if lead.get("original") and lead.get("original_sha256"):
                document_pins[lead["original"]] = lead["original_sha256"]
    write(output / "source_receipts.json", {"files": document_pins})
    manifest = {"format_version": "extraction_inventory/1.0", "jobs": jobs,
                "source_receipts": "source_receipts.json"}
    write(output / "inventory.json", manifest)
    result = {"format_version": "atlas_source_readiness/1.0",
              "atlas": str(atlas_path), "atlas_sha256": sha(atlas_path),
              "cache": str(cache), "explicit_packet_overrides": overrides,
              "counts": dict(Counter(row["status"] for row in report)),
              "methods": report,
              "boundary": "Ready means documents can be inspected, not complete evidence, correct "
                          "atlas attribution, an accepted protocol, or reviewed historical edges. "
                          "Source-first review must decide applicability and required dependencies."}
    write(output / "readiness.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("atlas", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--packets", type=Path, help="Explicit JSON method-ID to packet-path mapping")
    args = parser.parse_args()
    overrides = json.loads(args.packets.read_bytes()) if args.packets else None
    if overrides:
        overrides = {mid: str((args.packets.parent / path).resolve()) for mid, path in overrides.items()}
    result = prepare(args.atlas, args.cache, args.output, packets=overrides)
    print(json.dumps({"counts": result["counts"], "inventory": str(args.output / "inventory.json")}))


if __name__ == "__main__":
    main()
