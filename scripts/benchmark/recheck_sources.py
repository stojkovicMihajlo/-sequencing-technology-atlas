"""Recheck registered source identity from pinned prior bytes; no new retrieval."""

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from genomics_discovery.curation.protocol_benchmark import pdf_identity, source_identity
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet

BOUNDARY = ("Data repair: revalidate publication identity/custody of previously registered packets only. "
    "No new retrieval, native calls, packet rewriting or scientific source-completeness claim.")


def read(path):
    return json.loads(path.read_bytes())


def bind(pins, additions):
    if any(path in pins and pins[path] != value for path, value in additions.items()):
        raise ValueError("Conflicting prior source SHA256 pins")
    pins.update(additions)


def verify(pins):
    for name, expected in pins.items():
        if not Path(name).is_absolute() or sha(Path(name)) != expected:
            raise ValueError(f"Prior source custody changed: {name}")


def recheck(cases_path, prior_sources, output, *, case_ids=()):
    cases_path, prior_sources, output = (Path(path).resolve() for path in (cases_path, prior_sources, output))
    if output.exists() or output.is_relative_to(prior_sources):
        raise ValueError("Use a new output directory outside the prior source phase")
    cases = read(cases_path)["cases"]
    ids, mids = [case["case_id"] for case in cases], [case["method_id"] for case in cases]
    if len(set(ids)) != len(ids) or len(set(mids)) != len(mids) or set(case_ids) - set(ids):
        raise ValueError("Case/method IDs must be unique and selected cases declared")
    old_manifest_path = prior_sources / "annotation_manifest.json"
    old_report_path = prior_sources / "source_report.json"
    old_bindings_path = prior_sources / "input_bindings.json"
    receipt_path = prior_sources / "acquisition/source_receipts.json"
    old_manifest, receipt = read(old_manifest_path), read(receipt_path)
    if old_manifest["case_design_sha256"] != sha(cases_path):
        raise ValueError("Recheck cases differ from the original frozen case design")
    if Path(old_manifest["source_report"]["path"]) != old_report_path:
        raise ValueError("Prior manifest does not bind this exact source report path")
    if receipt["format_version"] != "public_source_acquisition/1.0":
        raise ValueError("Unsupported source acquisition receipt")
    pins = dict(read(old_bindings_path))
    bind(pins, receipt["files"])
    bind(pins, {str(old_report_path): old_manifest["source_report"]["sha256"]})
    code = (Path(__file__).resolve(), Path(source_identity.__file__).resolve(), Path(pdf_identity.__file__).resolve(),
        Path(pdf_identity.pdf.__file__).resolve(),
        Path(source_identity._identity.__code__.co_filename).resolve(),
        Path(source_identity.assess_primary_html.__code__.co_filename).resolve())
    bind(pins, {str(path): sha(path) for path in (cases_path, old_manifest_path, old_bindings_path, receipt_path, *code)})
    verify(pins)
    reports = deepcopy(read(old_report_path)["cases"])
    annotation_cases = []
    for case in cases:
        mid = case["method_id"]
        report = reports.setdefault(mid, {"case_id": case["case_id"], "status": "source_acquisition_needed"})
        previous_status = report["status"]
        report["identity_recheck"] = {"prior_status": previous_status, "phase": "registered_source_identity_recheck_only"}
        # A failed required acquisition cannot be promoted from a retained partial packet.
        if previous_status not in {"registered_for_source_first_annotation", "source_identity_or_scope_inspection_needed"}:
            report["identity_recheck"]["status"] = "pending_prior_source_gate"
            continue
        chosen_name = report.get("packet") if previous_status == "registered_for_source_first_annotation" else report.get("retained_packet")
        if not chosen_name:
            report.update(status="source_acquisition_needed", reason="No previously registered packet available for recheck")
            continue
        chosen = Path(chosen_name)
        try:
            if previous_status == "registered_for_source_first_annotation":
                expected = report["packet_sha256"]
                bind(pins, {str(chosen): expected})
                origin = "prior_source_report"
            else:
                expected = receipt["files"].get(str(chosen))
                if expected is None:
                    raise ValueError("Retained packet lacks an exact original source_receipts SHA256 pin")
                origin = "prior_source_receipts"
            if not chosen.is_absolute() or sha(chosen) != expected:
                raise ValueError("Previously registered packet byte hash differs")
            packet = load_packet(chosen)
            if report.get("source_packet_sha256") and report["source_packet_sha256"] != digest(packet):
                raise ValueError("Prior source report packet object digest differs")
            if report.get("case_id") != case["case_id"] or any(packet[field] != case[field]
                    for field in ("method_id", "title", "scope", "variant")):
                raise ValueError("Previously registered packet differs from the frozen case scope")
            proof = source_identity.verify_publication(packet, case, packet_path=chosen, bound_pins=pins)
            if any(pins.get(path) != expected_sha for path, expected_sha in proof["pins"].items()):
                raise ValueError("Publication proof uses bytes not bound by prior source custody")
            report.update(status="registered_for_source_first_annotation", packet=str(chosen), packet_sha256=expected,
                source_packet_sha256=digest(packet), publication_verification=proof)
            report["identity_recheck"].update(status="verified", packet_pin_origin=origin,
                packet_sha256=expected, prior_reason=report.pop("reason", None))
            if not case_ids or case["case_id"] in case_ids:
                annotation_cases.append({"case_id": case["case_id"], "method_id": mid,
                    "family": case["family"], "split": case["split"], "packet": str(chosen),
                    "packet_sha256": expected, "source_report": str(output / "source_report.json")})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report.update(status="source_identity_or_scope_inspection_needed", reason=str(exc), retained_packet=str(chosen))
            report["identity_recheck"].update(status="withheld", error=f"{type(exc).__name__}: {exc}")
    verify(pins)
    output.mkdir(parents=True, exist_ok=False)
    write(output / "input_bindings.json", pins)
    write(output / "source_report.json", {"format_version": "benchmark_source_report/1.0", "cases": reports,
        "counts": dict(Counter(row["status"] for row in reports.values())), "boundary": BOUNDARY,
        "annotation_ready_is_not_scientific_completeness": True,
        "prior_source_report": old_manifest["source_report"], "prior_source_receipts": {"path": str(receipt_path), "sha256": sha(receipt_path)}})
    manifest = {"format_version": "protocol_benchmark_batch/1.0", "created_at": now(),
        "case_design_sha256": sha(cases_path), "cases": annotation_cases,
        "source_report": {"path": str(output / "source_report.json"), "sha256": sha(output / "source_report.json")},
        "selected_case_ids": list(case_ids), "phase": "registered_source_identity_recheck_only", "boundary": BOUNDARY}
    write(output / "annotation_manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--prior-sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args(argv)
    print(json.dumps(recheck(args.cases, args.prior_sources, args.output, case_ids=args.case), indent=2))


if __name__ == "__main__":
    main()
