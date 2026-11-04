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
    assert len(result["artifacts"]) == 6
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


def test_coherent_export_mutation_cannot_inherit_native_review(tmp_path):
    run = history(tmp_path)
    analysis = json.loads((run / "analysis.json").read_text())
    graph = json.loads((run / "graph.json").read_text())
    analysis["claims"][0]["seed_design_change"] = "Unreviewed replacement of the original change description."
    graph["links"][0]["claim"] = analysis["claims"][0]
    write(run / "analysis.json", analysis)
    write(run / "graph.json", graph)
    result = tracking.export([], [run], tmp_path / "tracker")
    assert not any(r["published_edge"] for r in result["change_claims"])
    assert result["change_claims"][0]["analysis_integrity_status"] == "export_content_mismatch"
    assert "differs from the reviewed native" in result["history_runs"][0]["tracker_limitation"]


def test_legitimate_export_reserialization_retains_native_review_binding(tmp_path):
    run = history(tmp_path)
    path = run / "analysis.json"
    path.write_text(json.dumps(json.loads(path.read_text()), separators=(",", ":"), sort_keys=True))
    before = {p: p.read_bytes() for p in run.rglob("*.json")}
    result = tracking.export([], [run], tmp_path / "tracker")
    row = result["change_claims"][0]
    assert row["analysis_file_sha256"] != row["review_bound_analysis_sha256"]
    assert row["analysis_integrity_status"] == "matches_reviewed_native"
    assert row["published_ancestry_edge"]
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_missing_native_bytes_do_not_establish_publication_binding(tmp_path):
    run = history(tmp_path)
    (run / "attempts/002_analyse/artifact.json").unlink()
    result = tracking.export([], [run], tmp_path / "tracker")
    assert not any(r["published_edge"] for r in result["change_claims"])
    assert result["change_claims"][0]["analysis_integrity_status"] == "not_established"
    assert "native analysis bytes unavailable" in result["history_runs"][0]["tracker_limitation"]


def test_exact_native_copy_supports_unavailable_recorded_remote_path(tmp_path):
    run = history(tmp_path)
    native = run / "attempts/002_analyse/artifact.json"
    copy_path = run / "native_analysis.json"
    copy_path.write_bytes(native.read_bytes())
    native.unlink()
    result = tracking.export([], [run], tmp_path / "tracker")
    row = result["change_claims"][0]
    assert row["published_ancestry_edge"]
    assert row["native_analysis_path"] == str(copy_path.resolve())
    assert row["native_analysis_sha256"] == row["review_bound_analysis_sha256"]


def test_modified_available_native_cannot_be_hidden_by_a_valid_backup(tmp_path):
    run = history(tmp_path)
    native = run / "attempts/002_analyse/artifact.json"
    (run / "native_analysis.json").write_bytes(native.read_bytes())
    native.write_text(native.read_text() + "\n")
    result = tracking.export([], [run], tmp_path / "tracker")
    assert not any(r["published_edge"] for r in result["change_claims"])
    assert result["change_claims"][0]["analysis_integrity_status"] == "native_hash_mismatch"


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


def test_version3_tracking_retains_context_and_prevents_outside_history(tmp_path):
    run = history(tmp_path)
    analysis = json.loads((run / 'analysis.json').read_text())
    analysis['format_version'] = 'citation_analysis/3'
    for c in analysis['claims']:
        c.update(citation_context='The citation paragraph for this reference.', claim_scope='seed_workflow')
    pin = write(run / 'analysis.json', analysis)
    review = json.loads((run / 'review.json').read_text())
    review['analysis_sha256'] = pin
    for f, c in zip(review['findings'], analysis['claims']):
        f['evidence'] += c['predecessor_evidence']
    write(run / 'review.json', review)
    state = json.loads((run / 'state.json').read_text()); state['final_analysis_sha256'] = pin
    write(Path(state['stages'][0]['path']) / 'artifact.json', analysis)
    state['stages'][0]['artifact_sha256'] = pin
    write(run / 'state.json', state)
    graph = json.loads((run / 'graph.json').read_text())
    graph.update(format_version='citation_lineage_pilot/3', analysis_sha256=pin)
    for item in graph['links'] + graph['withheld']:
        i = int(item['claim']['id']); item['claim'] = analysis['claims'][i]; item['review'] = review['findings'][i]
    write(run / 'graph.json', graph)
    result = tracking.export([], [run], tmp_path / 'v3')
    assert result['change_claims'][0]['published_ancestry_edge']
    assert result['change_claims'][0]['citation_context'] == analysis['claims'][0]['citation_context']
    analysis['claims'][0].update(claim_scope='supporting_experiment', seed_operation_ids=[])
    pin = write(run / 'analysis.json', analysis)
    write(Path(state['stages'][0]['path']) / 'artifact.json', analysis)
    state['stages'][0]['artifact_sha256'] = pin
    state['final_analysis_sha256'] = pin; write(run / 'state.json', state)
    review['analysis_sha256'] = pin; write(run / 'review.json', review)
    graph['analysis_sha256'] = pin; graph['links'][0]['claim'] = analysis['claims'][0]
    write(run / 'graph.json', graph)
    result = tracking.export([], [run], tmp_path / 'invalid_scope')
    assert not result['change_claims'][0]['published_ancestry_edge']
