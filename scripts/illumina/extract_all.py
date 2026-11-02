#!/usr/bin/env python3
"""Fresh Illumina extraction: two checked records, then the remaining source rows.

No old extraction import, semantic merging, or historical-edge generation.
Each native call and independent review is managed by illumina.extraction.run.
"""
import argparse
import asyncio
from collections import Counter
import os
from pathlib import Path

from genomics_discovery.illumina.extraction import read, run, sha, write
from genomics_discovery.illumina.graph import build_graph


def validate_manifest(path):
    manifest = read(path)
    if manifest.get("prior_model_outputs_used") is not False:
        raise ValueError("Fresh extraction requires raw-source-only registration")
    all_ids = []
    for batch in manifest["batches"].values():
        source = Path(batch["sources"])
        if sha(source) != batch["sources_sha256"] or sha(source.parent / "inventory.json") != batch["inventory_sha256"]:
            raise ValueError("Prepared source bundle changed")
        all_ids.extend(c["record_id"] for c in read(source.parent / "inventory.json")["cards"])
    if (len(all_ids) != len(set(all_ids)) or len(manifest["records"]) != len(all_ids)
            or set(all_ids) != {r["record_id"] for r in manifest["records"]}):
        raise ValueError("Manifest/source occurrence coverage mismatch")
    return manifest


async def execute(manifest_path, output, first_records):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native extraction must execute in Slurm")
    manifest = validate_manifest(manifest_path)
    catalogue = Path(manifest["batches"]["catalogue"]["sources"])
    cards = read(catalogue.parent / "inventory.json")["cards"]
    if len(first_records) != 2 or len(set(first_records)) != 2 or set(first_records) - {c["record_id"] for c in cards}:
        raise ValueError("Choose two distinct existing catalogue records for the initial gate")
    output.mkdir(parents=True, exist_ok=False)
    status = {"format_version": "illumina_fresh_execution/1.0", "manifest": str(manifest_path),
        "manifest_sha256": sha(manifest_path), "slurm_job_id": os.environ["SLURM_JOB_ID"],
        "model": "gpt-6.1-sol", "effort": "xhigh", "account_label": os.environ.get("ATLAS_CODEX_ACCOUNT_LABEL"),
        "old_extractions_imported": False, "first_records": list(first_records),
        "status": "initial_two_running", "batches": {}}
    write(output / "RUN.json", status)

    async def batch(name, source, workers, records=()):
        destination = output / name
        state = await run(source, destination, workers=workers, records=records, max_revisions=1)
        graph = build_graph(destination, Path(source).parent / "inventory.json")
        write(destination / "atlas.json", graph)
        publication_errors = [ident for ident, row in state["cards"].items()
            if row["status"] == "supported" and graph["cards"].get(ident, {}).get("admission") != "supported"]
        counts = Counter(state["counts"])
        if publication_errors:
            counts["supported"] -= len(publication_errors)
            counts["publication_error"] += len(publication_errors)
        status["batches"][name] = {"directory": str(destination), "counts": dict(counts),
                                   "publication_errors": publication_errors}
        write(output / "RUN.json", status)
        return state

    initial = await batch("initial_two", catalogue, 2, first_records)
    if (any(row["status"] != "supported" for row in initial["cards"].values())
            or status["batches"]["initial_two"]["publication_errors"]):
        status.update(status="held_after_initial_two", remaining_records_not_launched=True)
        write(output / "RUN.json", status)
        return status
    remaining = [c["record_id"] for c in cards if c["record_id"] not in first_records]
    status["status"] = "remaining_running"
    write(output / "RUN.json", status)
    jobs = [batch("catalogue", catalogue, 4, remaining)] if remaining else []
    jobs.extend([batch("posters", Path(manifest["batches"]["posters"]["sources"]), 4),
                 batch("footer_panels", Path(manifest["batches"]["footer_panels"]["sources"]), 2)])
    await asyncio.gather(*jobs)
    counts = Counter()
    for record in status["batches"].values():
        counts.update(record["counts"])
    status.update(status="completed" if set(counts) <= {"supported"} else "completed_with_unresolved_records",
                  counts=dict(counts))
    write(output / "RUN.json", status)
    return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--first-record", action="append", required=True)
    args = parser.parse_args()
    outcome = asyncio.run(execute(args.manifest.resolve(), args.output.resolve(), args.first_record))
    print(outcome)
    raise SystemExit(0 if outcome["status"] == "completed" else 1)
