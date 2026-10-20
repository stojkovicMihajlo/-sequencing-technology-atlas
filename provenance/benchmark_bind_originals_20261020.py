"""Bind documentary source ancestry without changing registered source bytes.

This host source-curation step runs in Slurm. It reuses source_originals and
creates a new override phase; it does not extract protocols or call models.
"""

import json
import os
from pathlib import Path

from genomics_discovery.curation.extraction_batch.storage import check_pins, sha, write
from genomics_discovery.curation.source_originals import bind_originals, load_originals
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from scripts.benchmark.recover_supplements import bind


def read(path):
    return json.loads(Path(path).read_bytes())


def main():
    if not os.environ.get("SLURM_JOB_ID"):
        raise RuntimeError("Original-source association replay requires Slurm")
    project, runtime = Path(os.environ["ATLAS_PROJECT_ROOT"]), Path(os.environ["ATLAS_REPOSITORY"])
    phase = project / "technology_atlas/v2/work/benchmark_20261020"
    base = phase / "source_overlay_live_01"
    report_path = base / "source_report.json"
    if sha(report_path) != "7bfc66db94d82eab27eb01e8c742b70c0c2397f5385285158f8a11678806d799":
        raise ValueError("Source report differs from the inspected nine-case phase")
    report = read(report_path)
    inputs = read(base / "input_bindings.json")
    check_pins(inputs)
    annotation_path = runtime / "provenance/benchmark_legacy_source_associations_20261020.json"
    annotations = read(annotation_path)
    common = {**inputs, str(report_path): sha(report_path),
        str(annotation_path): sha(annotation_path), str(Path(__file__).resolve()): sha(__file__)}
    output = Path(os.environ.get("ATLAS_SOURCE_CLOSURE_PHASE", str(phase / "original_source_closure_01"))).resolve()
    if not output.is_relative_to(phase):
        raise ValueError("Source closure must remain in the benchmark experiment")
    output.mkdir()
    registrations, summaries = [], []
    for mid in ("atac-seq", "drop-seq", "spatial-transcriptomics"):
        directory = output / mid
        directory.mkdir()
        packet_path = Path(report["cases"][mid]["packet"])
        packet = load_packet(packet_path)
        files, rows = dict(common), []
        for item in annotations["records"]:
            if item["method_id"] != mid:
                continue
            original, rendition = project / item["original"], project / item["rendition"]
            declared = {str(original): item["original_sha256"], str(rendition): item["rendition_sha256"],
                **{str(project / path): value for path, value in item["documentary_inputs"].items()}}
            check_pins(declared)
            with original.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    raise ValueError("Documentary PDF ancestor has no PDF signature")
            matches = [s for s in packet["sources"] if s["original_path"] == str(rendition)
                       and s["original_sha256"] == item["rendition_sha256"]]
            if len(matches) != 1:
                raise ValueError("Documentary association must select one exact registered source")
            rows.append({"source_id": matches[0]["source_id"], "original": str(original),
                "original_sha256": item["original_sha256"], "rendition": str(rendition),
                "rendition_sha256": item["rendition_sha256"],
                "boundary": item["transformation_limits"], "documentary_inputs": declared})
            bind(files, declared)
        if mid == "spatial-transcriptomics":
            original = phase / "manual_sources_01/spatial/original_compound.pdf"
            expected = "39d88dc581bc348d28d7355273c6b66c9cecf1c62c0c1a3a26604bd042825731"
            check_pins({str(original): expected})
            if len(packet["sources"]) != 2 or {s["role"] for s in packet["sources"]} != {"primary_paper", "supplement"}:
                raise ValueError("Spatial association differs from the inspected compound selection")
            for source in packet["sources"]:
                rows.append({"source_id": source["source_id"], "original": str(original),
                    "original_sha256": expected, "rendition": source["original_path"],
                    "rendition_sha256": source["original_sha256"],
                    "boundary": "Original association follows the prior bound main/SI selection and visual source-curation records. No new transcription or scientific interpretation."})
        ledger, bindings = directory / "originals_ledger.json", directory / "original_bindings.json"
        write(ledger, rows)
        write(bindings, bind_originals(packet_path, ledger))
        bound = load_originals(bindings)
        bind(files, bound["input_files"])
        bind(files, {str(bindings): sha(bindings)})
        check_pins(files)
        custody = directory / "custody.json"
        write(custody, {"format_version": "benchmark_manual_registration/1.0", "files": files,
            "boundary": "New explicit original associations; registered packets, texts and old receipts unchanged. Association and byte custody do not establish transcription or scientific fidelity."})
        descriptor = {"path": str(bindings), "sha256": sha(bindings)}
        registrations.append({"method_id": mid, "packet": str(packet_path), "packet_sha256": sha(packet_path),
            "custody": {"path": str(custody), "sha256": sha(custody)}, "original_bindings": descriptor})
        summaries.append({"method_id": mid, "bound_source_ids": sorted(bound["bindings"]),
                          "unmapped_source_ids": bound["unmapped_source_ids"]})
    check_pins(common)
    cases = runtime / "benchmarks/protocol_graph_v1/cases.json"
    write(output / "overrides.json", {"format_version": "benchmark_source_overrides/1.0",
        "cases_sha256": sha(cases), "registrations": registrations, "curation_errors": [],
        "boundary": "Source ancestry/access only. Exact case scopes, packet bytes and scientific requirements unchanged."})
    write(output / "receipt.json", {"original_associations": summaries,
        "boundary": "Actual host custody replay; no model call, source conversion or new protocol fact."})
    print(json.dumps(summaries))


if __name__ == "__main__":
    main()
