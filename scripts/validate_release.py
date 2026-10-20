"""One finite Slurm validation: tests, real copied sources, tools, and fresh plans.

No model calls, historical run rewriting, or scientific acceptance transfers.
"""

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from genomics_discovery.curation.execution_check import check_execution
from genomics_discovery.curation.extraction_batch import BatchConfig, plan
from genomics_discovery.curation.source_relocation import register_relocated_packet
from genomics_discovery.curation.workflow_extraction.sources import catalog, load_packet, read_passage, search


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--old-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get("SLURM_JOB_ID"):
        parser.error("Use a Slurm allocation for the combined validation")
    root, old, output = args.project_root.resolve(), args.old_root, args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"started_at": datetime.now(UTC).isoformat(), "slurm_job_id": os.environ["SLURM_JOB_ID"]}
    completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               "--junitxml=" + str(output / "tests.xml")], check=False)
    tree = ET.parse(output / "tests.xml").getroot()
    cases = tree.findall(".//testcase")
    report["tests"] = {"exit_code": completed.returncode, "total": len(cases),
                       **{kind: sum(c.find(kind) is not None for c in cases)
                          for kind in ("failure", "error", "skipped")}}
    work = root / "technology_atlas/v2/work/deployment_20261018"
    frozen_path = work / "full_deployment_01/plan.json"
    historical_hash = sha(frozen_path)
    if historical_hash != (frozen_path.parent / "plan.sha256").read_text().strip():
        raise ValueError("Historical deployment plan differs from its frozen checksum")
    frozen = json.loads(frozen_path.read_bytes())
    expected_ids = {job["id"] for job in frozen["jobs"] if job["status"] == "ready"}
    rows, ready = [], []
    for job in frozen["jobs"]:
        if job["status"] != "ready":
            continue
        ident = job["id"]
        try:
            packet_path = root / Path(job["packet"]).relative_to(old)
            if frozen["pins"].get(job["packet"]) != sha(packet_path):
                raise ValueError("Historical packet differs from its frozen deployment pin")
            receipt = register_relocated_packet(packet_path, old_root=old, new_root=root,
                                                output=output / "sources" / ident)
            packet = load_packet(Path(receipt["packet"]))
            cat = catalog(packet)
            queries = {q: search(packet, q, limit=2)["total"]
                       for q in ("DNA", "RNA", "ligation", "barcode", "conversion")}
            source = packet["sources"][0]
            p = source["passages"][0]
            if read_passage(packet, source["source_id"], p["passage_id"])["text"] != p["text"]:
                raise ValueError("Source tool returned a different registered passage")
            rows.append({"id": ident, "status": "copied_sources_re_registered",
                         "sources": len(cat["sources"]),
                         "passages": sum(len(s["passages"]) for s in packet["sources"]),
                         "query_hits": queries, "receipt": receipt})
            ready.append({"id": ident, "method_id": packet["method_id"], "status": "ready",
                          "title": packet["title"], "packet": receipt["packet"]})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rows.append({"id": ident, "status": "registration_failed",
                         "error_type": type(exc).__name__, "error": str(exc)})
    (output / "source_registration.json").write_text(json.dumps(rows, indent=2) + "\n")
    # Preserve the old original-document custody pins in a newly verified receipt.
    historical_inventory = root / Path(frozen["inventory"]).relative_to(old)
    if frozen["pins"].get(frozen["inventory"]) != sha(historical_inventory):
        raise ValueError("Historical inventory differs from its frozen deployment pin")
    inventory_record = json.loads(historical_inventory.read_bytes())
    declared_receipt = Path(inventory_record["source_receipts"])
    if not declared_receipt.is_absolute():
        declared_receipt = Path(frozen["inventory"]).parent / declared_receipt
    historical_receipt = root / declared_receipt.relative_to(old)
    if frozen["pins"].get(str(declared_receipt)) != sha(historical_receipt):
        raise ValueError("Historical source receipt differs from its frozen deployment pin")
    originals = json.loads(historical_receipt.read_bytes())["files"]
    files = {str(historical_receipt): sha(historical_receipt)}
    failures = []
    for filename, expected in originals.items():
        try:
            declared = Path(filename)
            if not declared.is_absolute():
                declared = declared_receipt.parent / declared
            path = (root / declared.relative_to(old)).resolve(strict=True)
            if frozen["pins"].get(str(declared)) != expected:
                raise ValueError("Declared file differs from frozen deployment ownership")
            if not path.is_relative_to(root) or sha(path) != expected:
                raise ValueError("Copied declared file differs")
            files[str(path)] = expected
        except (OSError, ValueError) as exc:
            failures.append({"historical_path": filename, "error_type": type(exc).__name__})
    for row in rows:
        if row["status"] == "copied_sources_re_registered":
            path = Path(row["receipt"]["packet"]).parent / "relocation.json"
            files[str(path)] = sha(path)
    receipt_path = output / "source_receipts.json"
    receipt_path.write_text(json.dumps({"files": files}, indent=2) + "\n")
    manifest = {"format_version": "extraction_inventory/1.0", "jobs": ready,
                "source_receipts": str(receipt_path)}
    (output / "inventory.json").write_text(json.dumps(manifest, indent=2) + "\n")
    sample_ids = {"scbs-seq", "icshape", "tagmentation"}
    selected = [job for job in ready if job["id"] in sample_ids]
    smoke_ok = False
    if {job["id"] for job in selected} == sample_ids and len(selected) == 3 and not failures:
        smoke_manifest = {**manifest, "jobs": selected}
        path = output / "smoke_inventory.json"
        path.write_text(json.dumps(smoke_manifest, indent=2) + "\n")
        state = plan(path, output / "smoke_batch", config=BatchConfig(concurrency=1,
                     transport_retries=0, require_source_coverage=True))
        report["smoke_plan"] = {"status": state["status"], "jobs": len(state["jobs"]),
                                "native_calls": 0, "path": str(output / "smoke_batch")}
        smoke_ok = (state["status"] == "planned" and len(state["jobs"]) == 3
                    and all(job["status"] == "queued" for job in state["jobs"].values()))
    readiness = check_execution(workdir=output, packets=[Path(j["packet"]) for j in selected],
                                require_slurm=True)
    report.update(source_outcomes=dict(Counter(row["status"] for row in rows)),
                  sources_checked=sum(row.get("sources", 0) for row in rows),
                  passages_checked=sum(row.get("passages", 0) for row in rows),
                  original_document_pins={"verified": len(files), "failures": failures},
                  historical_plan_unchanged=sha(frozen_path) == historical_hash,
                  execution_readiness=readiness,
                  finished_at=datetime.now(UTC).isoformat(),
                  boundary="Mechanical source/tool/plan verification. No fresh scientific review, "
                           "native extraction, or graph admission is claimed.")
    offline_ok = (completed.returncode == 0 and not failures and bool(expected_ids)
                  and {job["id"] for job in ready} == expected_ids
                  and len(ready) == len(expected_ids)
                  and len(rows) == len(expected_ids)
                  and not any(row["status"] == "registration_failed" for row in rows)
                  and smoke_ok and report["historical_plan_unchanged"])
    report.update(expected_source_jobs=len(expected_ids), smoke_plan_valid=smoke_ok,
                  offline_checks_passed=offline_ok,
                  status=("offline_checks_failed" if not offline_ok else
                          "ready_for_native_attempt" if readiness["ready_for_native_attempt"] else
                          "offline_checks_passed_native_blocked"))
    (output / "validation.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    # 2 is a prerequisite block, distinct from failed offline checks (1).
    raise SystemExit(1 if not offline_ok else 0 if readiness["ready_for_native_attempt"] else 2)


if __name__ == "__main__":
    main()
