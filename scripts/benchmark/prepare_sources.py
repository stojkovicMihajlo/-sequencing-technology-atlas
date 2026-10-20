"""Prepare fresh benchmark source scopes through existing acquisition tools.

Native public-source finding runs only in Slurm. It never reads atlas extractions
or reference answers. Existing historical packets and source bytes are immutable.
Readiness here means registered input, not scientifically complete evidence.
"""

import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.source_acquisition.models import Identity
from genomics_discovery.curation.source_acquisition.runtime import _match, acquire
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import build_packet, load_packet


MIGRATION = Path("technology_atlas/v2/work/migration_to_xiaojie_20261019/validation_48532750/sources")
SPECIAL = {
    "scbs-seq": "technology_atlas/v2/work/qiu_experiments_20261019/source_repair_19/batch/inputs/scbs-seq/source_packet.json",
}


def read(path):
    return json.loads(Path(path).read_bytes())


def verify(pins):
    for filename, expected in pins.items():
        if sha(Path(filename)) != expected:
            raise ValueError(f"Source preparation input changed: {filename}")


def verify_publication(packet, case):
    expected = Identity(**{k: case["primary_identity"][k]
        for k in ("pmid", "doi") if case["primary_identity"].get(k)})
    primaries = [s for s in packet["sources"] if s["role"] == "primary_paper"]
    if not primaries:
        raise ValueError("Packet has no primary publication")
    for source in primaries:
        _match(source["document_identity"], expected)


def existing_packet(case, root):
    """Specific known locations, never recursive discovery or path rewriting."""
    mid = case["method_id"]
    leads = ([SPECIAL[mid]] if mid in SPECIAL else [])
    leads += [str(MIGRATION / mid / "packet.json"), *case.get("existing_packet_leads", [])]
    failures = []
    for value in dict.fromkeys(leads):
        path = Path(value)
        path = path if path.is_absolute() else root / path
        if not path.is_file():
            continue
        try:
            packet = load_packet(path)
            # Publication identity is not an arbitrary historical method label.
            verify_publication(packet, case)
            return path, packet, failures
        except (OSError, ValueError, KeyError) as exc:
            failures.append({"path": str(path), "error": str(exc)})
    return None, None, failures


async def prepare(args):
    if not os.environ.get("SLURM_JOB_ID"):
        raise RuntimeError("Benchmark source preparation requires a Sherlock Slurm allocation")
    root, output = args.project_root.resolve(), args.output.resolve()
    if not output.is_relative_to(root):
        raise ValueError("Benchmark results must stay in this project")
    output.mkdir(parents=True, exist_ok=False)
    cases_path = args.cases.resolve()
    document = read(cases_path)
    cases = document["cases"]
    if len({c["case_id"] for c in cases}) != len(cases) or len({c["method_id"] for c in cases}) != len(cases):
        raise ValueError("Case and method IDs must be unique")
    pins = {str(cases_path): sha(cases_path), str(Path(__file__).resolve()): sha(Path(__file__).resolve())}
    extra = {}
    if getattr(args, "additional_leads", None):
        extra_path = args.additional_leads.resolve()
        updates = read(extra_path)
        if updates["base_cases_sha256"] != sha(cases_path):
            raise ValueError("Additional source leads do not bind this case design")
        extra = updates["methods"]
        if set(extra) - {case["method_id"] for case in cases}:
            raise ValueError("Additional source leads refer to an unknown case")
        pins[str(extra_path)] = sha(extra_path)
    worklist, scopes, retained, reports = [], {}, {}, {}
    for case in cases:
        mid = case["method_id"]
        options = {key: case[key] for key in ("method_id", "title", "scope", "variant")}
        source_path, packet, failures = existing_packet(case, root)
        row = {"id": mid, "title": case["title"], "origin_paper_ids": [],
               "source_packet": None, "tasks": [], "retained_unknowns": [],
               "source_leads": [*case.get("source_leads", []), *extra.get(mid, [])]}
        # Exact PMID/DOI jointly identify the publication. Manuscript and published
        # titles may differ (SPLiT-seq); preserve titles in the design without a
        # fuzzy title match or treating an alias as a different publication.
        expected = {k: case["primary_identity"][k] for k in ("pmid", "doi")
                    if case["primary_identity"].get(k)}
        if expected.get("pmid"):
            row["origin_paper_ids"] = ["pmid:" + str(expected["pmid"])]
        scopes[mid] = {**options, "primary_identity": expected}
        report = {"case_id": case["case_id"], "family": case["family"], "split": case["split"],
                  "invalid_existing_leads": failures, "previous_packet": None,
                  "boundary": "Registration and exact-file identity only; original scientific scope must be checked by source-first curators."}
        if packet is not None:
            # Build a NEW packet with declared benchmark scope. Do not edit old paths.
            pins[str(source_path)] = sha(source_path)
            pins.update({s["original_path"]: s["original_sha256"] for s in packet["sources"]})
            descriptors = [{**{k: s[k] for k in ("source_id", "title", "url", "role")},
                            "path": s["original_path"]} for s in packet["sources"]]
            fresh = build_packet(**options, sources=descriptors,
                **{k: packet[k] for k in ("format_version", "rendition_version") if k in packet})
            path = output / "initial_packets" / mid / "packet.json"
            write(path, fresh)
            row["source_packet"] = {"path": str(path), "sha256": sha(path),
                "document_sha256": digest(fresh), **options}
            retained[mid] = path
            report["previous_packet"] = {"path": str(source_path), "sha256": sha(source_path)}
            report["registered_sources"] = [{"id": s["source_id"], "role": s["role"],
                "url": s["url"], "passages": len(s["passages"])} for s in fresh["sources"]]
        # These four source scopes already have independently checked supplements.
        # All others receive a new public-source search before annotation.
        sufficient_existing = (packet is not None and mid in {"atac-seq", "scbs-seq", "drop-seq", "dnase-seq"}
                               and any(s["role"] == "supplement" for s in packet["sources"]))
        if not sufficient_existing:
            row["tasks"] = [{"id": mid + "_source_scope", "kind": "acquire_document",
                "description": "Locate original full Methods, supplements and explicitly delegated procedural sources for the frozen publication/variant. Verify publication identity and supplement associations. Distinguish newer revisions; do not infer scientific details or use atlas extractions as evidence.",
                "origin": {"path": str(cases_path), "sha256": sha(cases_path)},
                "next_evidence": "Public publisher/PMC/author/institutional original documents", "blocking": True}]
            worklist.append(row)
            report["source_search_required"] = True
        else:
            report["source_search_required"] = False
        reports[mid] = report
    verify(pins)
    write(output / "input_bindings.json", pins)
    acquisition = None
    if worklist:
        path = output / "worklist.json"
        write(path, {"format_version": "source_worklist/1.0", "inputs": [], "methods": worklist,
                     "boundary": "Benchmark source collection only; no candidate or reference answers supplied."})
        selected_scopes = {row["id"]: scopes[row["id"]] for row in worklist}
        acquisition = await acquire(path, output / "acquisition", scopes=selected_scopes,
            concurrency=args.concurrency, worker=CodexWorker(model="gpt-6.1-sol", effort="xhigh",
                timeout_seconds=None, web_search=True, network_access=True))
    verify(pins)
    annotation_cases = []
    for case in cases:
        mid = case["method_id"]
        found = (acquisition or {}).get("methods", {}).get(mid, {})
        chosen = Path(found["packet"]) if found.get("packet") else retained.get(mid)
        report = reports[mid]
        report["acquisition_status"] = found.get("status", "not_requested")
        if report["source_search_required"] and not found.get("packet"):
            report.update(status="source_acquisition_needed",
                retained_packet=str(chosen) if chosen else None,
                reason="Required source-search task did not produce a newly registered packet; retained partial input is not silently promoted.")
            continue
        if chosen is None:
            report["status"] = "source_acquisition_needed"
            continue
        packet = load_packet(chosen)
        try:
            for key in ("method_id", "title", "scope", "variant"):
                if packet[key] != case[key]:
                    raise ValueError(f"Final packet {key} differs from declared benchmark scope")
            verify_publication(packet, case)
        except (ValueError, KeyError, TypeError) as exc:
            report.update(status="source_identity_or_scope_inspection_needed", reason=str(exc),
                          retained_packet=str(chosen))
            continue
        report.update(status="registered_for_source_first_annotation", packet=str(chosen), packet_sha256=sha(chosen),
                      source_packet_sha256=digest(packet), sources=len(packet["sources"]),
                      supplement_count=sum(s["role"] == "supplement" for s in packet["sources"]))
        annotation_cases.append({"case_id": case["case_id"], "method_id": mid,
            "family": case["family"], "split": case["split"], "packet": str(chosen),
            "packet_sha256": sha(chosen), "source_report": str(output / "source_report.json")})
    write(output / "source_report.json", {"format_version": "benchmark_source_report/1.0", "cases": reports,
          "counts": dict(Counter(r["status"] for r in reports.values())),
          "annotation_ready_is_not_scientific_completeness": True})
    manifest = {"format_version": "protocol_benchmark_batch/1.0", "created_at": now(),
        "case_design_sha256": sha(cases_path), "cases": annotation_cases,
        "source_report": {"path": str(output / "source_report.json"), "sha256": sha(output / "source_report.json")},
        "model": "gpt-6.1-sol", "effort": "xhigh",
        "boundary": "Fixed-source model-reviewed reference candidates; not human gold or blind harness performance."}
    write(output / "annotation_manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--additional-leads", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(prepare(args)), indent=2))
