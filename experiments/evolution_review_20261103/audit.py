"""Inspect every archived source record without new inference or changing verdicts.

Run from the repository root with .venv/bin/python and PYTHONPATH=src.
Field coverage is not semantic correctness. Full source bodies are not in this
archive, so this audit deliberately does not pretend to recheck quote entailment.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path

from genomics_discovery.illumina.models import DiagramExtraction, DiagramReview, digest


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(row, status, reason):
    c, review = row.get("candidate") or {}, row.get("review") or {}
    errors = []
    if c:
        try:
            candidate = DiagramExtraction.model_validate(c)
            if review:
                parsed_review = DiagramReview.model_validate(review)
                if parsed_review.candidate_sha256 != digest(candidate):
                    errors.append("review_candidate_digest_mismatch")
        except ValueError as error:
            errors.append(str(error))
    states = {s["id"]: s for s in c.get("states", [])}
    ops = {o["id"]: o for o in c.get("operations", [])}
    if len(states) != len(c.get("states", [])) or len(ops) != len(c.get("operations", [])):
        errors.append("duplicate_local_ids")
    expected = Counter()
    for op in ops.values():
        for field, suffix in (("inputs", "input"), ("outputs", "output")):
            for ident in op[field]:
                if ident not in states:
                    errors.append("unknown_state:" + ident)
                    continue
                source, target = (ident, op["id"]) if field == "inputs" else (op["id"], ident)
                expected[source, target, states[ident]["kind"] + "_" + suffix] += 1
    actual = Counter((f["source"], f["target"], f["kind"]) for f in c.get("flows", [])
                     if f["kind"] != "precedes")
    if actual != expected:
        errors.append("operation_IO_disagrees_with_flows")
    for flow in c.get("flows", []):
        if flow["kind"] == "precedes" and (flow["source"] not in ops or flow["target"] not in ops):
            errors.append("invalid_precedes_endpoint")
    for scope in c.get("method_scopes", []):
        if set(scope["operation_ids"]) - ops.keys():
            errors.append("unknown_method_scope_operation")

    levels = Counter()
    for match in c.get("paper_matches", []):
        if match["assessment"] == "content_checked" and match["relationship"] in {
                "supports_architecture", "supports_variant"}:
            levels[row["sources"].get(match["source_id"], {}).get("content_level", "unknown")] += 1
    corrections = c.get("corrections", [])
    targets = [item["target"] for item in review.get("checks", [])]
    return {
        "record_id": row["record_id"], "title": row["title"], "batch": row["batch"],
        "status": status, "reason": reason or "", "candidate_present": bool(c),
        "candidate_sha256": digest(DiagramExtraction.model_validate(c)) if c and not errors else None,
        "operations": len(ops), "states": len(states), "flows": len(c.get("flows", [])),
        "method_names": c.get("method_names", []),
        "method_scopes": c.get("method_scopes", []),
        "unknowns": c.get("unknowns", []),
        "unknown_count": len(c.get("unknowns", [])), "correction_count": len(corrections),
        "correction_target_ids": [x["target_id"] for x in corrections],
        "mechanism_empty_operation_ids": [o["id"] for o in ops.values() if not o.get("mechanism")],
        "information_effect_empty_operation_ids": [o["id"] for o in ops.values() if not o.get("information_effect")],
        "architecture_paper_levels": dict(levels),
        "review_format": review.get("format_version", "absent"),
        "separate_label_checks": sum(t.startswith(("state_label:", "operation_label:")) for t in targets),
        "structural_errors": errors,
        "identity_resolution": "not_established_by_source_record",
        "historical_relation_review": "not_performed_in_this_batch",
        "per_claim_detail_adequacy": "requires_the_specific_comparison_question",
        "next_action": ("resolve_record_hold_before_relationship_admission" if status != "supported" else
                        "classify_panel_before_assay_use" if row["batch"] == "footer_panels" else
                        "resolve_method_identity_then_follow_methodological_citations"),
    }


def audit(directory, output):
    archive = directory / "records.jsonl.gz"
    receipt = json.loads((directory / "AUDIT_STATUS.json").read_text())["raw_audit_receipts"][archive.name]
    if sha(archive) != receipt["sha256"] or archive.stat().st_size != receipt["bytes"]:
        raise ValueError("Source archive differs from its recorded receipt")
    publication = json.loads((directory / "GRAPH_PUBLICATION.json").read_text())
    with gzip.open(archive, "rt") as stream:
        rows = [json.loads(line) for line in stream]
    by_id = {r["record_id"]: r for r in rows}
    if len(by_id) != len(rows):
        raise ValueError("Duplicate source occurrence")
    overrides = {}
    for item in publication["new_admission_failures"]:
        rid = item["record_id"]
        if rid not in by_id or rid in overrides or by_id[rid]["status"] != item["baseline_status"]:
            raise ValueError("Unmatched or inconsistent admission override")
        overrides[rid] = item
    records = [inspect(r, overrides.get(r["record_id"], {}).get("current_status", r["status"]),
                       overrides.get(r["record_id"], {}).get("reason", r.get("reason"))) for r in rows]
    counts = Counter(r["status"] for r in records)
    if dict(counts) != publication["counts"] or len(records) != publication["selected_source_occurrences"]:
        raise ValueError("Record accounting disagrees with publication")
    supported = [r for r in records if r["status"] == "supported"]
    assays = [r for r in supported if r["batch"] != "footer_panels"]
    summary = {
        "source_occurrences": len(records), "current_status_counts": dict(counts),
        "supported_nonfooter_occurrences": len(assays),
        "supported_footer_panels": len(supported) - len(assays),
        "unique_method_count": None,
        "supported_totals": {k: sum(r[k] for r in supported) for k in
                             ("operations", "states", "flows", "unknown_count", "correction_count")},
        "supported_with_unknowns": sum(r["unknown_count"] > 0 for r in supported),
        "supported_with_corrections": sum(r["correction_count"] > 0 for r in supported),
        "supported_mechanism_empty_operations": sum(len(r["mechanism_empty_operation_ids"]) for r in supported),
        "supported_information_effect_empty_operations": sum(len(r["information_effect_empty_operation_ids"]) for r in supported),
        "supported_nonfooter_without_architecture_fulltext_match": sum(not r["architecture_paper_levels"].get("fulltext") for r in assays),
        "record_structural_errors": {r["record_id"]: r["structural_errors"] for r in records if r["structural_errors"]},
        "review_formats": dict(Counter(r["review_format"] for r in records)),
        "separate_label_checks": sum(r["separate_label_checks"] for r in records),
        "native_model_calls": 0,
        "input_pins": {name: sha(directory / name) for name in
                       ("records.jsonl.gz", "AUDIT_STATUS.json", "GRAPH_PUBLICATION.json")},
        "boundary": "Exhaustive archived-record structure and field coverage audit, not a fresh scientific rereview. "
                    "Empty fields are triage signals, not proven errors. Historical claims require separate evidence review.",
    }
    if output.exists():
        raise FileExistsError("Preserve prior audit outputs; choose a fresh directory")
    output.mkdir(parents=True)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output / "records.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    columns = ["record_id", "title", "batch", "status", "operations", "states", "flows",
               "unknown_count", "correction_count", "review_format", "separate_label_checks", "next_action", "reason"]
    with (output / "CHECKLIST.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.audit, args.output), indent=2))
