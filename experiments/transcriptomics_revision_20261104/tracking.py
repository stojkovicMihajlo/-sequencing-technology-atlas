"""Read existing extraction/history outputs into an atlas progress snapshot.

No model calls, semantic classification, identity merging or graph admission.
Change categories remain unassigned; scientific wording and evidence are copied.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from genomics_discovery.illumina.relationships import RELATIONS


class Snapshot:
    def __init__(self):
        self.artifacts = {}
        self.documents = {}

    def read(self, path, *, optional=False):
        path = Path(path).resolve()
        key = str(path)
        if key in self.documents:
            return self.documents[key]
        if optional and not path.exists():
            return None
        raw = path.read_bytes()
        doc = json.loads(raw)
        self.artifacts[key] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        self.documents[key] = doc
        return doc

    def pin(self, path):
        return self.artifacts[str(Path(path).resolve())]["sha256"]


def method_rows(batch, snapshot):
    batch = Path(batch).resolve()
    state = snapshot.read(batch / "state.json")
    if state.get("format_version") != "illumina_batch/1.0":
        raise ValueError("Expected an extraction batch state.json")
    result = []
    for record_id, entry in state["cards"].items():
        attempt = Path(entry["attempt_directory"]) if entry.get("attempt_directory") else None
        extraction_path = attempt / "extraction.json" if attempt else None
        extraction = snapshot.read(extraction_path, optional=True) if attempt else None
        pin = snapshot.pin(extraction_path) if extraction is not None else None
        expected = entry.get("artifact_pins", {}).get("extraction.json")
        integrity = "matches_recorded_hash" if pin and pin == expected else "not_established"
        if pin and expected and pin != expected:
            integrity = "hash_mismatch"
        graph = snapshot.read(attempt / "graph.json", optional=True) if attempt else None
        identity_pin = pin or expected
        result.append({
            "method_key": f"{record_id}@{identity_pin}" if identity_pin else f"{batch}::{record_id}",
            "record_id": record_id, "method_name": entry.get("title", record_id),
            "batch_path": str(batch), "attempt_directory": str(attempt) if attempt else None,
            "extraction_status": entry["status"], "integrity_status": integrity,
            "graph_file_present": graph is not None,
            "operations": entry.get("operations"), "states": entry.get("states"),
            "flows": entry.get("flows"), "revision": entry.get("revision"),
            "reason": entry.get("reason") or entry.get("error") or "",
            "failure_category": entry.get("failure_category"),
            "research_questions": entry.get("research_questions", []),
            "extraction_path": str(extraction_path) if extraction is not None else None,
            "extraction_sha256": pin, "expected_extraction_sha256": expected,
            "source_state_path": str(batch / "state.json"),
        })
    return result


def history_rows(run, snapshot):
    run = Path(run).resolve()
    state = snapshot.read(run / "state.json")
    completed = state.get("status") == "completed"
    graph = snapshot.read(run / "graph.json", optional=True) if completed else None
    analysis_path = run / "analysis.json"
    review_path = run / "review.json"
    analysis_pin = state.get("final_analysis_sha256")
    if not completed:
        stages = state.get("stages", [])
        available = [(i, x) for i, x in enumerate(stages)
                     if x["role"] == "analyse" and x["status"] == "validated"]
        if not available:
            return [], {"run_path": str(run), "status": state.get("status"), "claim_count": 0}
        index, author = available[-1]
        analysis_path = Path(author["path"]) / "artifact.json"
        analysis_pin = author.get("artifact_sha256")
        reviews = [x for x in stages[index + 1:]
                   if x["role"] == "review" and x["status"] == "validated"]
        review_path = Path(reviews[-1]["path"]) / "artifact.json" if reviews else None
    analysis = snapshot.read(analysis_path, optional=True)
    review = snapshot.read(review_path, optional=True) if review_path else None
    sources_path = run / "inputs/sources.json"
    sources = snapshot.read(sources_path, optional=True)
    binding = snapshot.read(run / "inputs/seed_binding.json", optional=True) or {}
    if not binding and graph:
        seed = next((x for x in graph["nodes"] if x["id"] == "seed"), {})
        binding = seed.get("bindings", {})
    summary = {"run_path": str(run), "status": state.get("status"),
               "error": state.get("error"), "record_id": binding.get("record_id"),
               "claim_count": len(analysis["claims"]) if analysis else 0}
    if analysis is None:
        summary["tracker_limitation"] = "Final or validated analysis artifact unavailable at its recorded path"
        return [], summary
    claims = {x["id"]: x for x in analysis["claims"]}
    if len(claims) != len(analysis["claims"]):
        raise ValueError("Duplicate claim IDs in history analysis")
    review_matches = bool(review and analysis_pin and review.get("analysis_sha256") == analysis_pin)
    if not completed and snapshot.pin(analysis_path) != analysis_pin:
        review_matches = False
    findings = {x["claim_id"]: x for x in review.get("findings", [])} if review_matches else {}
    review_complete = review_matches and len(findings) == len(review["findings"]) and set(findings) == set(claims)
    published = {x["id"]: x for x in graph.get("links", [])} if graph else {}
    withheld = {x["claim"]["id"]: x for x in graph.get("withheld", [])} if graph else {}
    graph_consistent = bool(
        graph and graph.get("format_version") == "citation_lineage_pilot/2"
        and graph.get("analysis_sha256") == analysis_pin and review_complete
        and len(published) == len(graph["links"]) and len(withheld) == len(graph["withheld"])
        and not set(published) & set(withheld) and set(published) | set(withheld) == set(claims)
        and all(x.get("kind") == claims[k]["relation"] for k, x in published.items())
        and all(x["claim"] == claims[k] and x["review"] == findings[k]
                for k, x in {**published, **withheld}.items())
    )
    if completed and not graph_consistent:
        summary["tracker_limitation"] = "Completed publication/analysis/review binding is missing or inconsistent"
    rows = []
    seed_pin = binding.get("parent_artifact_pins", {}).get("extraction.json")
    for claim_id, claim in claims.items():
        finding = findings.get(claim_id, {})
        relation = claim["relation"]
        layer = RELATIONS.get(relation, {}).get("layer", "unknown")
        assessments = finding.get("unresolved_assessments", [])
        assessed = sorted(x["issue_index"] for x in assessments) == list(range(len(claim.get("unresolved", []))))
        safe = (graph_consistent and claim_id in published and layer not in {"excluded", "unknown"}
                and finding.get("decision") == "accept" and not review.get("omitted_candidates")
                and assessed and not any(x["blocks_relation"] for x in assessments)
                and bool(claim.get("seed_evidence")) and bool(claim.get("predecessor_evidence")))
        status = "published_overlay" if safe else (
            "withheld" if graph_consistent and claim_id in withheld else "not_published")
        rows.append({
            "claim_key": f"{run}::{analysis_pin}::{claim_id}", "claim_id": claim_id,
            "claim_granularity": "claim_may_contain_multiple_changes",
            "method_key": f"{binding.get('record_id')}@{seed_pin}" if seed_pin else None,
            "seed_method": analysis["seed_method"], "record_id": binding.get("record_id"),
            "contributor": claim["contributor"], "contributor_kind": claim["contributor_kind"],
            "reference_id": claim["reference_id"],
            "relation": relation, "relationship_layer": layer,
            "published_edge": safe, "published_ancestry_edge": safe and layer == "history",
            "publication_status": status,
            "withheld_reason": withheld.get(claim_id, {}).get("reason"),
            "review_decision": finding.get("decision", "pending_or_unbound"),
            "review_rationale": finding.get("rationale"),
            "review_evidence": finding.get("evidence", []),
            "earlier_demonstration": claim["earlier_demonstration"],
            "design_change": claim["seed_design_change"],
            "limitation_addressed": claim["limitation_addressed"],
            "reported_gain": claim["reported_gain"],
            "tradeoffs_and_transfer_limits": claim["tradeoffs_and_transfer_limits"],
            "affected_operation_ids": claim["seed_operation_ids"],
            "catalogue_candidates": claim["catalogue_candidates"],
            "seed_evidence": claim["seed_evidence"], "predecessor_evidence": claim["predecessor_evidence"],
            "unresolved": claim["unresolved"], "uncertainty_assessments": assessments,
            "change_categories": [], "classification_status": "unassigned", "taxonomy_version": None,
            "history_run": str(run), "analysis_path": str(analysis_path),
            "analysis_file_sha256": snapshot.pin(analysis_path), "review_bound_analysis_sha256": analysis_pin,
            "review_path": str(review_path) if review is not None else None,
            "sources_path": str(sources_path) if sources is not None else None,
            "sources_sha256": snapshot.pin(sources_path) if sources is not None else None,
            "graph_path": str(run / "graph.json") if graph else None,
        })
    summary.update(published_edges=sum(x["published_edge"] for x in rows),
                   published_ancestry_edges=sum(x["published_ancestry_edge"] for x in rows))
    return rows, summary


def _csv(path, rows, fields):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(row[k], ensure_ascii=False) if isinstance(row[k], (list, dict))
                             else row[k] for k in fields})


def export(extraction_batches, history_runs, output):
    snapshot = Snapshot()
    methods, changes, histories = [], [], []
    for batch in dict.fromkeys(Path(x).resolve() for x in extraction_batches):
        methods.extend(method_rows(batch, snapshot))
    for run in dict.fromkeys(Path(x).resolve() for x in history_runs):
        rows, summary = history_rows(run, snapshot)
        changes.extend(rows); histories.append(summary)
    document = {"format_version": "atlas_progress_snapshot/1", "created_at": datetime.now(timezone.utc).isoformat(),
                "methods": methods, "change_claims": changes, "history_runs": histories,
                "artifacts": snapshot.artifacts,
                "boundary": "Read-only snapshot, not new scientific review or canonical admission. Method keys bind records to extraction bytes, not semantic identity. Earlier demonstration is not necessarily an explicit before-state. One row is an existing claim, which may contain multiple changes. Background and unsupported dispositions remain visible but are not published edges. Change classification remains unassigned."}
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    (output / "ledger.json").write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    _csv(output / "methods.csv", methods, list(methods[0]) if methods else ["method_key", "record_id", "extraction_status"])
    _csv(output / "changes.csv", changes, list(changes[0]) if changes else ["claim_key", "relation", "publication_status", "classification_status"])
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extraction-batch", action="append", type=Path, default=[])
    parser.add_argument("--history-run", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.extraction_batch and not args.history_run:
        parser.error("Supply an extraction batch or history run")
    result = export(args.extraction_batch, args.history_run, args.output)
    print(json.dumps({"methods": len(result["methods"]), "claims": len(result["change_claims"]),
                      "published_edges": sum(x["published_edge"] for x in result["change_claims"])}))


if __name__ == "__main__":
    main()
