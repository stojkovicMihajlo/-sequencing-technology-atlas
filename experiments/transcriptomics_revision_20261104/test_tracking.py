"""Progress displays cannot turn acceptance of a disposition into ancestry."""
import copy
import csv
import hashlib
import json
from pathlib import Path

import pytest

from experiments.transcriptomics_revision_20261104 import tracking


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def history(tmp_path):
    run = tmp_path / "history"
    claims, findings = [], []
    for i, relation in enumerate(("component_adoption", "comparison", "background")):
        claims.append({"id": str(i), "reference_id": "R1", "contributor": "Earlier method",
            "contributor_kind": "assay", "relation": relation, "seed_operation_ids": ["o_rt"],
            "earlier_demonstration": "Earlier demonstration; not asserted to be an exact prior implementation.",
            "seed_design_change": "Adopt a supported component in this measured context.",
            "limitation_addressed": "Reported limitation", "reported_gain": "No isolated causal gain established",
            "tradeoffs_and_transfer_limits": "Different substrate", "catalogue_candidates": ["candidate-only"],
            "seed_evidence": [{"source_id": "seed", "quote": "Exact source statement, with punctuation."}],
            "predecessor_evidence": [{"source_id": "R1", "quote": "Earlier paper supporting passage."}],
            "unresolved": ["Transfer limit remains qualified"]})
        findings.append({"claim_id": str(i), "decision": "accept", "rationale": "Scoped support",
            "evidence": claims[-1]["seed_evidence"],
            "unresolved_assessments": [{"issue_index": 0, "blocks_relation": False, "rationale": "Outside the narrow claim"}]})
    analysis = {"seed_method": "Example assay", "claims": claims, "reference_dispositions": {"R1": "Reviewed"}, "summary": "Example"}
    native = run / "attempts/002_analyse/artifact.json"
    pin = write(native, analysis)
    review = {"analysis_sha256": pin, "findings": findings, "omitted_candidates": [], "summary": "Example review"}
    review_path = run / "attempts/003_review/artifact.json"
    review_pin = write(review_path, review)
    state = {"status": "completed", "final_analysis_sha256": pin, "stages": [
        {"role": "analyse", "status": "validated", "path": str(native.parent), "artifact_sha256": pin},
        {"role": "review", "status": "validated", "path": str(review_path.parent), "artifact_sha256": review_pin}]}
    write(run / "state.json", state)
    write(run / "analysis.json", analysis); write(run / "review.json", review)
    write(run / "inputs/seed_binding.json", {"record_id": "example", "parent_artifact_pins": {"extraction.json": "seedpin"}})
    graph = {"format_version": "citation_lineage_pilot/2", "analysis_sha256": pin, "nodes": [],
        "links": [{"id": c["id"], "kind": c["relation"], "claim": c, "review": r} for c, r in zip(claims[:2], findings[:2])],
        "withheld": [{"claim": claims[2], "review": findings[2], "reason": "no_methodological_link"}]}
    write(run / "graph.json", graph)
    return run


def test_published_comparison_and_accepted_background_are_not_ancestry(tmp_path):
    run = history(tmp_path)
    result = tracking.export([], [run], tmp_path / "tracker")
    rows = result["change_claims"]
    assert len(rows) == 3  # Three claims for one reference are not collapsed.
    assert [r["published_edge"] for r in rows] == [True, True, False]
    assert [r["published_ancestry_edge"] for r in rows] == [True, False, False]
    assert rows[2]["review_decision"] == "accept"
    assert rows[2]["publication_status"] == "withheld"
    assert rows[2]["withheld_reason"] == "no_methodological_link"
    assert all(r["change_categories"] == [] and r["taxonomy_version"] is None for r in rows)
    assert rows[0]["earlier_demonstration"].startswith("Earlier demonstration;")
    assert "before" not in rows[0]
    assert rows[0]["catalogue_candidates"] == ["candidate-only"]
    with (tmp_path / "tracker/changes.csv").open() as f:
        csv_rows = list(csv.DictReader(f))
    assert json.loads(csv_rows[0]["seed_evidence"]) == rows[0]["seed_evidence"]
    assert len(result["artifacts"]) == 5
    assert result["history_runs"][0]["published_ancestry_edges"] == 1


@pytest.mark.parametrize("failure", ["stale_review", "different_claim", "different_relation", "missing_claim", "duplicate_claim", "blocking_issue", "omitted_candidate"])
def test_inconsistent_or_blocked_publication_never_becomes_positive_edge(tmp_path, failure):
    run = history(tmp_path)
    graph = json.loads((run / "graph.json").read_text())
    review = json.loads((run / "review.json").read_text())
    if failure == "stale_review": review["analysis_sha256"] = "different"
    elif failure == "different_claim": graph["links"][0]["claim"]["reported_gain"] = "Invented benefit"
    elif failure == "different_relation": graph["links"][0]["kind"] = "comparison"
    elif failure == "missing_claim": graph["links"].pop()
    elif failure == "duplicate_claim": graph["links"].append(copy.deepcopy(graph["links"][0]))
    elif failure == "blocking_issue":
        review["findings"][0]["unresolved_assessments"][0]["blocks_relation"] = True
        graph["links"][0]["review"] = review["findings"][0]
    elif failure == "omitted_candidate": review["omitted_candidates"] = ["Unexamined methodological reference"]
    write(run / "graph.json", graph); write(run / "review.json", review)
    result = tracking.export([], [run], tmp_path / "tracker")
    assert not result["change_claims"][0]["published_edge"]
    assert not result["change_claims"][0]["published_ancestry_edge"]


def test_running_revision_does_not_inherit_the_previous_review(tmp_path):
    run = history(tmp_path)
    state = json.loads((run / "state.json").read_text())
    changed = json.loads((run / "analysis.json").read_text())
    changed["claims"][0]["seed_design_change"] = "Revised wording with random assignment"
    native = run / "attempts/004_analyse/artifact.json"
    pin = write(native, changed)
    state.update(status="review", stages=state["stages"] + [
        {"role": "analyse", "status": "validated", "path": str(native.parent), "artifact_sha256": pin},
        {"role": "review", "status": "running", "path": str(run / "attempts/005_review")}])
    write(run / "state.json", state)
    result = tracking.export([], [run], tmp_path / "tracker")
    assert result["change_claims"][0]["design_change"] == changed["claims"][0]["seed_design_change"]
    assert all(r["review_decision"] == "pending_or_unbound" and not r["published_edge"] for r in result["change_claims"])


def test_unchanged_running_nomination_has_no_fabricated_claims(tmp_path):
    run = tmp_path / "history"
    write(run / "state.json", {"status": "nominate", "stages": []})
    result = tracking.export([], [run], tmp_path / "tracker")
    assert result["change_claims"] == []
    assert result["history_runs"][0]["status"] == "nominate"
    assert (tmp_path / "tracker/changes.csv").read_text().startswith("claim_key,")


def test_methods_keep_failed_rows_hash_mismatch_and_distinct_implementations(tmp_path):
    batches = []
    for i in range(2):
        batch = tmp_path / f"batch{i}"; batches.append(batch)
        attempt = batch / "cards/method/attempt_001"
        pin = write(attempt / "extraction.json", {"different": i})
        write(attempt / "graph.json", {"graph": "recorded"})
        write(batch / "state.json", {"format_version": "illumina_batch/1.0", "cards": {
            "method": {"title": "Same display name", "status": "supported", "attempt_directory": str(attempt),
                       "artifact_pins": {"extraction.json": pin if i == 0 else "wrong"}},
            "failed": {"title": "Failure visible", "status": "failed", "reason": "Source missing"}}})
    before = {p: p.read_bytes() for batch in batches for p in batch.rglob("*.json")}
    result = tracking.export(batches, [], tmp_path / "tracker")
    assert len(result["methods"]) == 4
    supported = [r for r in result["methods"] if r["record_id"] == "method"]
    assert supported[0]["method_key"] != supported[1]["method_key"]
    assert [r["integrity_status"] for r in supported] == ["matches_recorded_hash", "hash_mismatch"]
    assert len([r for r in result["methods"] if r["extraction_status"] == "failed"]) == 2
    assert all(p.read_bytes() == data for p, data in before.items())
    with pytest.raises(FileExistsError): tracking.export(batches, [], tmp_path / "tracker")
