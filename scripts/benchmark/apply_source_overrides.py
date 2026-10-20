"""Overlay explicitly registered whole source packets into a new benchmark phase.

This command reads existing bytes only. A requested override that fails validation
is withheld; it never falls back to the previous packet.
"""

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re

from scripts.benchmark import combine_sources
from genomics_discovery.curation.extraction_batch.storage import check_pins, sha, write
from genomics_discovery.curation.protocol_benchmark import source_identity
from genomics_discovery.curation.source_originals import load_originals
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet

read, bind = combine_sources.recovery.read, combine_sources.recovery.bind
BOUNDARY = ("Explicit custody-checked whole-packet source overlay only. No model calls, downloads, "
    "protocol extraction, packet rewriting, or claim of scientific source completeness.")


def _packet(path, expected_sha, case, files):
    path = Path(path)
    if not path.is_absolute() or files.get(str(path)) != expected_sha:
        raise ValueError("Selected packet lacks its exact absolute-path custody pin")
    check_pins({str(path): expected_sha})
    raw = read(path)
    if any(files.get(source["original_path"]) != source["original_sha256"] for source in raw["sources"]):
        raise ValueError("Every selected original source needs its exact custody pin")
    packet = load_packet(path)
    if any(packet[field] != case[field] for field in ("method_id", "title", "scope", "variant")):
        raise ValueError("Selected packet differs from frozen method identity or case scope")
    proof = source_identity.verify_publication(packet, case, packet_path=path, bound_pins=files)
    return packet, proof


def _original_bindings(declared, packet_path, packet, files):
    if (not isinstance(declared, dict) or set(declared) != {"path", "sha256"}
            or not isinstance(declared["path"], str) or not Path(declared["path"]).is_absolute()
            or not isinstance(declared["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", declared["sha256"])):
        raise ValueError("Original bindings require an absolute path and file-byte SHA256")
    path = Path(declared["path"]).resolve()
    check_pins({str(path): declared["sha256"]})
    bound = load_originals(path)
    if (Path(bound["packet"]).resolve() != packet_path.resolve()
            or bound["source_packet_sha256"] != digest(packet)):
        raise ValueError("Original bindings belong to another selected packet")
    bind(files, {str(path): declared["sha256"], **bound["input_files"]})
    return {"path": str(path), "sha256": declared["sha256"]}


def apply_overrides(cases_path, base_sources, overrides_path, output):
    cases_path, base_sources, overrides_path, output = (Path(path).resolve() for path in
        (cases_path, base_sources, overrides_path, output))
    if output.exists() or output.is_relative_to(base_sources):
        raise ValueError("Use a new output directory outside the base source phase")
    case_list = read(cases_path)["cases"]
    cases = {case["method_id"]: case for case in case_list}
    if len(cases) != len(case_list) or len({case["case_id"] for case in case_list}) != len(cases):
        raise ValueError("Frozen method and case IDs must be unique")
    overrides = read(overrides_path)
    if (overrides.get("format_version") != "benchmark_source_overrides/1.0"
            or overrides.get("cases_sha256") != sha(cases_path)):
        raise ValueError("Overrides must bind the exact frozen case design and supported format")
    registrations = overrides.get("registrations")
    if not isinstance(registrations, list):
        raise ValueError("Overrides require a registrations list")
    requested = {}
    for registration in registrations:
        mid = registration.get("method_id") if isinstance(registration, dict) else None
        if mid not in cases or mid in requested:
            raise ValueError("Override methods must be distinct declared case methods")
        requested[mid] = registration
    root = Path(__file__).resolve().parents[2]
    code = [Path(__file__).resolve(), Path(combine_sources.__file__).resolve(),
        Path(combine_sources.recovery.__file__).resolve(), *root.joinpath("src/genomics_discovery").rglob("*.py")]
    files = {str(path): sha(path) for path in (cases_path, overrides_path, *code)}
    pins = {"cases_sha256": sha(cases_path), "files": files}
    _, previous, rows = combine_sources.phase(base_sources, cases, pins, "checked_source_packet_union_only")
    reports, annotation = {}, []
    for mid, case in cases.items():
        report = reports[mid] = deepcopy(previous[mid])
        registration = requested.get(mid)
        if registration is not None:
            report["previous_disposition"] = deepcopy(previous[mid])
            for key in ("packet", "packet_sha256", "source_packet_sha256", "publication_verification", "original_bindings"):
                report.pop(key, None)
            report["source_override"] = {"registration": deepcopy(registration),
                "fallback_permitted": False, "status": "withheld"}
        elif report.get("status") != "registered_for_source_first_annotation":
            continue
        try:
            if registration is not None:
                custody_binding = registration["custody"]
                custody_path = Path(custody_binding["path"])
                if not custody_path.is_absolute():
                    raise ValueError("Manual custody receipt requires an absolute path")
                # Preserve inspected receipt bytes even when its declared pin fails.
                observed_receipt = {str(custody_path): sha(custody_path)}
                bind(files, observed_receipt)
                check_pins({str(custody_path): custody_binding["sha256"]})
                custody = read(custody_path)
                if not isinstance(custody, dict) or custody.get("format_version") != "benchmark_manual_registration/1.0":
                    raise ValueError("Unsupported manual source registration custody format")
                if not isinstance(custody.get("files"), dict):
                    raise ValueError("Manual source registration requires an explicit file-pin map")
                selected_files = {}
                bind(selected_files, custody["files"])
                check_pins(selected_files)
                packet_path, expected_sha = Path(registration["packet"]), registration["packet_sha256"]
            else:
                row = rows[mid]
                packet_path, expected_sha, selected_files = Path(row["packet"]), row["packet_sha256"], files
                if report.get("packet") != str(packet_path) or report.get("packet_sha256") != expected_sha:
                    raise ValueError("Base packet manifest and report bindings disagree")
            packet, proof = _packet(packet_path, expected_sha, case, selected_files)
            if registration is None and digest(packet) != report.get("source_packet_sha256"):
                raise ValueError("Base packet canonical digest differs from its report")
            selected = registration if registration is not None else rows[mid]
            originals = (_original_bindings(selected["original_bindings"], packet_path, packet, selected_files)
                         if "original_bindings" in selected else None)
            bind(files, selected_files)
            report.update(status="registered_for_source_first_annotation", packet=str(packet_path),
                packet_sha256=expected_sha, source_packet_sha256=digest(packet), sources=len(packet["sources"]),
                supplement_count=sum(source["role"] == "supplement" for source in packet["sources"]),
                publication_verification=proof)
            if originals is not None:
                report["original_bindings"] = originals
            else:
                report.pop("original_bindings", None)
            if registration is not None:
                report.pop("reason", None)
                report["source_override"]["status"] = "registered"
            annotation.append({**{field: case[field] for field in ("case_id", "method_id", "family", "split")},
                "packet": str(packet_path), "packet_sha256": expected_sha,
                "source_report": str(output / "source_report.json"),
                **({"original_bindings": originals} if originals is not None else {})})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report.update(status="source_identity_or_scope_inspection_needed", reason=f"{type(exc).__name__}: {exc}")
    check_pins(files)
    output.mkdir(parents=True, exist_ok=False)
    write(output / "input_bindings.json", files)
    write(output / "source_report.json", {"format_version": "benchmark_source_report/1.0", "cases": reports,
        "counts": dict(Counter(row["status"] for row in reports.values())), "scientific_resolution": False,
        "annotation_ready_is_not_scientific_completeness": True, "boundary": BOUNDARY})
    manifest = {"format_version": "protocol_benchmark_batch/1.0", "phase": "explicit_source_overrides_only",
        "case_design_sha256": sha(cases_path), "cases": annotation, "base_sources": str(base_sources),
        "source_overrides": {"path": str(overrides_path), "sha256": sha(overrides_path)},
        "requested_override_method_ids": sorted(requested), "boundary": BOUNDARY,
        "source_report": {"path": str(output / "source_report.json"), "sha256": sha(output / "source_report.json")}}
    write(output / "annotation_manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cases", "base-sources", "overrides", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(apply_overrides(args.cases, args.base_sources, args.overrides, args.output), indent=2))


if __name__ == "__main__":
    main()
