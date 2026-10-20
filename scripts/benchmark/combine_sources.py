"""Combine checked source packets without rewriting them or weakening failed repairs."""

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from scripts.benchmark import recover_supplements as recovery
from genomics_discovery.curation.extraction_batch.storage import check_pins, sha, write
from genomics_discovery.curation.protocol_benchmark import source_identity
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet

BOUNDARY = ("Whole-packet source registration union only; source-ready does not mean scientifically complete. "
    "No source/extraction merging, model calls, downloads, conversions, or frozen-record rewriting.")


def phase(directory, cases, pins, expected_phase):
    manifest_path, bindings_path = directory / "annotation_manifest.json", directory / "input_bindings.json"
    manifest = recovery.read(manifest_path)
    report_path = directory / "source_report.json"
    if (manifest["case_design_sha256"] != pins["cases_sha256"] or manifest.get("phase") != expected_phase
            or manifest["source_report"]["path"] != str(report_path)):
        raise ValueError("Source phase differs from the exact case design, phase, or report path")
    additions = {str(report_path): manifest["source_report"]["sha256"],
        str(manifest_path): sha(manifest_path), str(bindings_path): sha(bindings_path)}
    check_pins(additions)
    recovery.bind(pins["files"], recovery.read(bindings_path))
    recovery.bind(pins["files"], additions)
    if expected_phase == "explicit_supplement_recovery_only":
        receipt_path = directory / "source_receipts.json"
        receipt = recovery.read(receipt_path)
        if receipt["format_version"] != "benchmark_supplement_recovery/1.0":
            raise ValueError("Recovered phase has an unsupported source receipt")
        if any(receipt["files"].get(name) != value for name, value in additions.items()):
            raise ValueError("Recovered receipt does not bind its exact manifest/report/input bindings")
        recovery.bind(pins["files"], receipt["files"])
        recovery.bind(pins["files"], {str(receipt_path): sha(receipt_path)})
    check_pins(pins["files"])
    reports = recovery.read(report_path)["cases"]
    if set(reports) != set(cases) or any(reports[mid]["case_id"] != case["case_id"] for mid, case in cases.items()):
        raise ValueError("Source report must retain every exact frozen case disposition")
    rows = {row["method_id"]: row for row in manifest["cases"]}
    if len(rows) != len(manifest["cases"]) or set(rows) - set(cases):
        raise ValueError("Source manifest has duplicate or undeclared methods")
    for mid, row in rows.items():
        if any(row[field] != cases[mid][field] for field in ("case_id", "method_id", "family", "split")):
            raise ValueError("Source manifest case metadata differs from the frozen design")
        if row["source_report"] != str(report_path):
            raise ValueError("Source manifest row does not bind this source report")
    return manifest, reports, rows


def combine(cases_path, priorchecked, recovered, output):
    cases_path, priorchecked, recovered, output = (Path(p).resolve() for p in (cases_path, priorchecked, recovered, output))
    if output.exists() or any(output.is_relative_to(p) for p in (priorchecked, recovered)):
        raise ValueError("Use a new output directory outside both source phases")
    case_list = recovery.read(cases_path)["cases"]
    cases = {case["method_id"]: case for case in case_list}
    if len(cases) != len(case_list) or len({case["case_id"] for case in case_list}) != len(cases):
        raise ValueError("Frozen case and method IDs must be unique")
    files = {str(path): sha(path) for path in (cases_path, Path(__file__).resolve(),
        Path(recovery.__file__).resolve(), Path(source_identity.__file__).resolve())}
    pins = {"cases_sha256": sha(cases_path), "files": files}
    checked_manifest, checked_reports, checked_rows = phase(priorchecked, cases, pins, "registered_source_identity_recheck_only")
    repaired_manifest, repaired_reports, repaired_rows = phase(recovered, cases, pins, "explicit_supplement_recovery_only")
    recipe_binding = repaired_manifest["recovery_recipes"]
    recipe_path = Path(recipe_binding["path"])
    if files.get(str(recipe_path)) != recipe_binding["sha256"]:
        raise ValueError("Recovery recipe lacks its exact phase custody pin")
    recipes = recovery.read(recipe_path)
    if recipes["cases_sha256"] != pins["cases_sha256"]:
        raise ValueError("Recovery recipes differ from the frozen case design")
    required = set(recovery.recipe_index(recipes, cases))
    if set(repaired_manifest["requested_method_ids"]) != required or set(repaired_rows) - required:
        raise ValueError("Recovery manifest differs from its explicitly requested methods")
    reports, annotation = {}, []
    for mid, case in cases.items():
        origin, old_reports, rows = (recovered, repaired_reports, repaired_rows) if mid in required else (priorchecked, checked_reports, checked_rows)
        report = reports[mid] = deepcopy(old_reports[mid])
        report["source_union"] = {"selected_phase": str(origin), "recovery_required": mid in required,
            "fallback_permitted": False, "status": "withheld"}
        row = rows.get(mid)
        if report["status"] != "registered_for_source_first_annotation" or row is None:
            report.update(status="source_acquisition_needed")
            report.setdefault("reason", "Selected source phase did not register an eligible packet; no fallback to partial inputs")
            continue
        try:
            packet_path = Path(row["packet"])
            if (not packet_path.is_absolute() or files.get(str(packet_path)) != row["packet_sha256"]
                    or report["packet"] != str(packet_path) or report["packet_sha256"] != row["packet_sha256"]):
                raise ValueError("Selected packet lacks exact matching manifest/report/custody bindings")
            raw_packet = recovery.read(packet_path)
            if any(files.get(s["original_path"]) != s["original_sha256"] for s in raw_packet["sources"]):
                raise ValueError("Selected source bytes lack their exact source-phase custody pins")
            packet = load_packet(packet_path)
            if (digest(packet) != report["source_packet_sha256"]
                    or any(packet[field] != case[field] for field in ("method_id", "title", "scope", "variant"))):
                raise ValueError("Selected packet differs from its object digest or frozen case scope")
            proof = source_identity.verify_publication(packet, case, packet_path=packet_path, bound_pins=files)
            report.update(publication_verification=proof)
            report["source_union"]["status"] = "registered"
            annotation.append({"case_id": case["case_id"], "method_id": mid, "family": case["family"], "split": case["split"],
                "packet": str(packet_path), "packet_sha256": row["packet_sha256"], "source_report": str(output / "source_report.json")})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report.update(status="source_identity_or_scope_inspection_needed", reason=f"{type(exc).__name__}: {exc}")
    check_pins(files)
    output.mkdir(parents=True, exist_ok=False)
    write(output / "input_bindings.json", files)
    write(output / "source_report.json", {"format_version": "benchmark_source_report/1.0", "cases": reports,
        "counts": dict(Counter(row["status"] for row in reports.values())), "scientific_resolution": False,
        "annotation_ready_is_not_scientific_completeness": True, "boundary": BOUNDARY})
    manifest = {"format_version": "protocol_benchmark_batch/1.0", "phase": "checked_source_packet_union_only",
        "case_design_sha256": sha(cases_path), "cases": annotation, "required_recovery_method_ids": sorted(required),
        "source_phases": [str(priorchecked), str(recovered)], "boundary": BOUNDARY,
        "source_report": {"path": str(output / "source_report.json"), "sha256": sha(output / "source_report.json")}}
    write(output / "annotation_manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("cases", "recovered", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--rechecked", "--priorchecked", dest="priorchecked", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(combine(args.cases, args.priorchecked, args.recovered, args.output), indent=2))


if __name__ == "__main__":
    main()
