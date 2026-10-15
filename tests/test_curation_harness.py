"""Offline end-to-end tests of the persistent curation queue and review boundary."""

import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pytest

from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.harness import (
    check_artifact,
    execute,
    export_accepted,
    initialize,
    progress,
    read,
    recover_completed,
    sha,
    summarize,
    write,
)
from genomics_discovery.curation.validation import canonical_hash


@pytest.fixture
def make_run(tmp_path):
    serial = 0

    def create(count=1, **kwargs):
        nonlocal serial
        serial += 1
        atlas = tmp_path / f"atlas-{serial}"
        run = tmp_path / f"run-{serial}"
        methods = [{"id": f"method-{i}", "name": f"Method {i}", "paper_ids": ["pmid:1"]}
                   for i in range(count)]
        write(atlas / "output/knowledge_graph.json", {
            "revision_id": "test-revision", "methods": methods,
            "papers": [{"paper_id": "pmid:1", "title": "Primary source lead"}]})
        work = atlas / "work/component_curation_20261008"
        write(work / "curation_manifest.json", {
            "rows": [{"method_id": m["id"], "status": "pending_detailed_review"} for m in methods]})
        prompts = work / "run01/prompts"
        prompts.mkdir(parents=True)
        (prompts / "shared_extraction_v1.txt").write_text(
            "Describe concrete source-scoped modules.")
        (prompts / "evidence_recovery_clarification_20261008.txt").write_text(
            "Recover evidence before declaring a missing source unrecoverable.")
        (prompts / "output_contract_v1.txt").write_text(
            "Use protocol_component_extraction/1.0.\nStart with reading notes LEGACY ROUTING")
        kwargs.setdefault("max_attempts", 1)
        initialize(run, atlas, **kwargs)
        return run, atlas

    return create


def extraction_document(mid, directory):
    source = directory / "source.txt"
    source.write_text("Synthetic source; section 1 describes a measurement.")
    refs = [{"source_id": f"{mid}-source", "locator": "Section 1"}]
    module = {
        "module_id": "m1", "operation_description": "Observe a signal",
        "purpose": "Record an observable", "boundary_rationale": "Signal becomes data",
        "input_state": {"description": "Prepared specimen", "scope": "Fixture"},
        "output_state": {"description": "Signal record", "scope": "Fixture"},
        "steps": ["Record the reported signal."], "materials": [], "equipment": [],
        "information_effect": "Retains identity", "function_tags": ["detection"],
        "source_refs": refs, "evidence_scope": "Synthetic source", "unknowns": [],
    }
    meta = {key: f"Specified {key}" for key in (
        "name", "record_kind", "implementation_version", "native_specimen", "measurement_target",
        "attribution_unit", "architecture_coverage", "recipe_coverage")}
    meta.update({key: f"Specified {key}" for key in (
        "directly_recorded_evidence", "instrument_output", "quantity_inferred")})
    meta["key_interfaces"] = ["Signal identity retained"]
    return {
        "format_version": "protocol_component_extraction/1.0", "scope": "Synthetic fixture",
        "prompt_provenance": {"prompt_path": str(directory / "prompt.txt")},
        "sources": [{"source_id": f"{mid}-source", "source_metadata": {
            "title": "Synthetic source", "primary_url": "https://example.org/paper",
            "inspected_url": "https://example.org/paper", "local_evidence": str(source),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "artifact_roles": {str(source): "source_document"},
            "sections_inspected": ["Section 1"], "access_limits": [],
        }}],
        "profiles": [{"profile_id": f"{mid}-profile", "method_ids": [mid],
                      "profile_metadata": meta, "modules": [module],
                      "topology": {"default_order": ["m1"], "connections": []},
                      "function_coverage": [{"function": "detection", "status": "present",
                                             "module_ids": ["m1"], "reason": "Signal recorded"}],
                      "unknowns": [],
                      "status": "source_scoped_protocol_extraction_not_execution_record"}],
    }


def review_document(extraction, outcome):
    profile = extraction["profiles"][0]
    accepted = outcome in {"accepted", "accepted_with_explicit_gaps"}
    return {
        "format_version": "protocol_component_review/1.0", "profile_id": profile["profile_id"],
        "profile_sha256": canonical_hash(profile), "reviewer_role": "independent_verifier",
        "outcome": outcome, "errors": [] if accepted else [{"finding": "Revise unsupported claim"}],
        "remaining_gaps": [],
        "scientific_scope": {"coverage": "Synthetic source Section 1 inspected",
                             "limits": "Synthetic fixture only"},
        "module_checks": [{"module_id": m["module_id"], "module_sha256": canonical_hash(m),
                           "outcome": outcome, "observations": ["Source passage checked"],
                           "source_refs_checked": m["source_refs"]} for m in profile["modules"]],
        "source_checks": [{"source_id": s["source_id"], "reading_scope": ["Section 1"], "files": [{
            "path": s["source_metadata"]["local_evidence"],
            "sha256": s["source_metadata"]["sha256"]}]} for s in extraction["sources"]],
        "topology_check": {"outcome": "accepted", "observations": ["Endpoints checked"]},
        "coverage_check": {"outcome": "accepted", "observations": ["Functions checked"]},
    }


class FakeWorker:
    def __init__(self, outcomes=("accepted_with_explicit_gaps",), *, bad_role=None, delay=0):
        self.outcomes, self.bad_role, self.delay = outcomes, bad_role, delay
        self.calls = []
        self.review_counts = Counter()
        self.active, self.max_active = Counter(), Counter()
        self.peak = 0
        self.overlapped = False

    async def run(self, prompt, attempt):
        attempt.mkdir(parents=True, exist_ok=True)
        (attempt / "prompt.txt").write_text(prompt)
        role, mid = attempt.name.split("_", 1)[1], attempt.parent.name
        self.calls.append((mid, role, attempt, prompt))
        self.active[role] += 1
        self.max_active[role] = max(self.max_active[role], self.active[role])
        self.peak = max(self.peak, sum(self.active.values()))
        self.overlapped |= all(self.active[r] for r in ("extractor", "independent_verifier"))
        try:
            await asyncio.sleep(self.delay * (1 + int(mid.split("-")[-1]) % 3))
            if role == "extractor":
                document = extraction_document(mid, attempt)
                if self.bad_role == role:
                    document["profiles"][0]["modules"][0]["source_refs"] = []
                write(attempt / "extraction.json", document)
            else:
                state = read(attempt.parents[2] / "state.json")
                extraction = read(Path(state["jobs"][mid]["extraction"]))
                count = self.review_counts[mid]
                self.review_counts[mid] += 1
                outcome = self.outcomes[min(count, len(self.outcomes) - 1)]
                document = review_document(extraction, outcome)
                if self.bad_role == role:
                    document["module_checks"] = []
                write(attempt / "review.json", document)
            result = WorkerResult(0, f"fake-session-{len(self.calls)}",
                                  {"input_tokens": 5}, 0, None)
            write(attempt / "result.json", asdict(result))
            events = [{"type": "thread.started", "thread_id": result.thread_id},
                      {"type": "turn.started"},
                      {"type": "turn.completed", "usage": result.usage}]
            (attempt / "events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events))
            (attempt / "final.txt").write_text("Synthetic completed attempt.")
            return result
        finally:
            self.active[role] -= 1


class UnvalidatedCritiqueWorker(FakeWorker):
    """Completed concrete negative critique with an invalid scope declaration."""

    def __init__(self, *, interrupt=False):
        super().__init__(("needs_revision", "accepted_with_explicit_gaps"))
        self.interrupt = interrupt

    async def run(self, prompt, attempt):
        result = await super().run(prompt, attempt)
        if attempt.name.endswith("independent_verifier") and self.review_counts[
                attempt.parent.name] == 1:
            document = read(attempt / "review.json")
            document["errors"] = [{"module_id": "m1", "field": "source register",
                                   "finding": "Reopen the source and correct its registration."}]
            document["source_checks"] = []
            document.pop("scientific_scope")
            write(attempt / "review.json", document)
            write(attempt / "validation.json", {"errors": ["Missing scientific_scope"]})
            if self.interrupt:
                raise asyncio.CancelledError
        return result


@pytest.mark.asyncio
async def test_invalid_negative_critique_spends_revision_and_requires_fresh_review(make_run):
    run, _ = make_run(max_revisions=1, incremental_reviews=True)
    worker = UnvalidatedCritiqueWorker()
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert [role for _, role, _, _ in worker.calls] == [
        "extractor", "independent_verifier", "extractor", "independent_verifier"]
    job = read(run / "state.json")["jobs"]["method-0"]
    assert job["revision"] == 1
    assert len(job["provisional_critiques"]) == 1
    rejected = Path(job["attempts"][1]["directory"])
    assert read(rejected / "validation.json")["errors"]
    assert Path(job["review"]).parent != rejected
    assert "review_plan" not in job["attempts"][-1]
    correction_prompt = worker.calls[2][3]
    assert "unvalidated_review_findings" in correction_prompt
    assert "reopen the original sources" in correction_prompt
    assert "source_checks" not in job["feedback"]["unvalidated_review_findings"]
    assert export_accepted(run)["profiles"] == 1


@pytest.mark.asyncio
async def test_completed_invalid_negative_critique_recovers_only_as_leads(make_run):
    run, _ = make_run(max_revisions=1)
    with pytest.raises(asyncio.CancelledError):
        await execute(run, worker=UnvalidatedCritiqueWorker(interrupt=True))
    state = read(run / "state.json")
    job = state["jobs"]["method-0"]
    attempt = job["attempts"][-1]
    attempt["artifact_sha256"] = sha(Path(attempt["directory"]) / "review.json")
    write(run / "state.json", state)
    recovered = recover_completed(run)
    assert recovered["counts"] == {"needs_revision": 1}
    assert recovered["recovered_artifacts"][0]["role"] == "provisional_critique"
    assert export_accepted(run)["profiles"] == 0
    assert recover_completed(run)["recovered_artifacts"] == []
    receipt = read(Path(read(run / "state.json")["jobs"]["method-0"][
        "provisional_critiques"][0]["record"]))
    assert receipt["integrity_errors"]
    assert receipt["scope"].startswith("Unvalidated correction leads only")
    worker = FakeWorker()
    assert (await execute(run, worker=worker))["counts"] == {"accepted": 1}
    assert [r for _, r, _, _ in worker.calls] == ["extractor", "independent_verifier"]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["accepted", "stale", "wrong_role", "no_findings",
                                   "unknown_module", "no_pin", "changed_output", "timeout",
                                   "malformed", "no_budget"])
async def test_provisional_critique_cannot_bypass_identity_transport_or_budget(make_run, damage):
    run, _ = make_run(max_revisions=1)
    with pytest.raises(asyncio.CancelledError):
        await execute(run, worker=UnvalidatedCritiqueWorker(interrupt=True))
    state = read(run / "state.json")
    job = state["jobs"]["method-0"]
    attempt = job["attempts"][-1]
    directory = Path(attempt["directory"])
    output = directory / "review.json"
    document = read(output)
    if damage == "accepted":
        document.update(outcome="accepted", errors=[])
    elif damage == "stale":
        document["profile_sha256"] = "0" * 64
    elif damage == "wrong_role":
        document["reviewer_role"] = "extractor"
    elif damage == "no_findings":
        document["errors"] = []
    elif damage == "unknown_module":
        document["errors"][0]["module_id"] = "not-in-current-profile"
    elif damage == "timeout":
        result = read(directory / "result.json")
        result["error"] = "Worker exceeded its time budget"
        write(directory / "result.json", result)
        (directory / "events.jsonl").write_text('{"type":"turn.started"}\n')
    elif damage == "no_budget":
        job["revision"] = 1
        attempt["revision"] = 1
    write(output, document)
    attempt["artifact_sha256"] = sha(output)
    if damage == "no_pin":
        attempt.pop("artifact_sha256")
    elif damage == "changed_output":
        output.write_text(output.read_text() + " ")
    elif damage == "malformed":
        output.write_text("not json")
        attempt["artifact_sha256"] = sha(output)
    write(run / "state.json", state)
    assert recover_completed(run)["recovered_artifacts"] == []
    assert not read(run / "state.json")["jobs"]["method-0"].get("provisional_critiques")


@pytest.mark.asyncio
async def test_provisional_receipt_and_rejected_archive_remain_frozen(make_run):
    run, _ = make_run(max_revisions=1)
    await execute(run, worker=UnvalidatedCritiqueWorker())
    job = read(run / "state.json")["jobs"]["method-0"]
    receipt = Path(job["provisional_critiques"][0]["record"])
    archive = Path(read(receipt)["attempt"]) / "review.json"
    archive.write_text(archive.read_text() + " ")
    with pytest.raises(ValueError, match="Provisional critique archive changed"):
        recover_completed(run)


@pytest.mark.asyncio
async def test_new_correction_status_does_not_report_previous_validation_failure(make_run):
    class InspectStatus(UnvalidatedCritiqueWorker):
        async def run(self, prompt, attempt):
            if attempt.name == "003_extractor":
                job = read(attempt.parents[2] / "state.json")["jobs"]["method-0"]
                assert job["status"] == "extracting"
                assert "last_error" not in job
                assert job["feedback"]["integrity_errors"]
            return await super().run(prompt, attempt)

    run, _ = make_run(max_revisions=1)
    assert (await execute(run, worker=InspectStatus()))["counts"] == {"accepted": 1}


@pytest.mark.asyncio
async def test_followup_reopens_with_feedback_and_preserves_accepted_artifacts(make_run):
    from genomics_discovery.curation.followup import apply_followup_review

    run, _ = make_run(count=2)
    await execute(run, worker=FakeWorker())
    original = read(run / "state.json")["jobs"]["method-0"]
    extraction, old_review = Path(original["extraction"]), Path(original["review"])
    prior_hashes = sha(extraction), sha(old_review)
    followup = run / "jobs/method-0/followup_audit/review.json"
    correction = review_document(read(extraction), "needs_revision")
    correction["errors"] = [{"finding": "QC observation does not demonstrate a decision"}]
    write(followup, correction)
    applied = apply_followup_review(run, "method-0", followup)
    assert applied["status"] == "needs_revision" and applied["revision"] == 1
    assert (sha(extraction), sha(old_review)) == prior_hashes
    state = read(run / "state.json")
    assert state["jobs"]["method-0"]["feedback"] == correction
    assert state["jobs"]["method-1"]["status"] == "accepted"
    with pytest.raises(ValueError, match="already applied"):
        apply_followup_review(run, "method-0", followup)
    class CorrectingWorker(FakeWorker):
        async def run(self, prompt, attempt):
            result = await super().run(prompt, attempt)
            if attempt.name.endswith("_extractor"):
                path = attempt / "extraction.json"
                document = read(path)
                document["profiles"][0]["modules"][0]["information_effect"] = (
                    "Records a QC observation without asserting a control decision")
                write(path, document)
            return result

    worker = CorrectingWorker()
    await execute(run, worker=worker)
    assert [c[:2] for c in worker.calls] == [
        ("method-0", "extractor"), ("method-0", "independent_verifier")]
    assert "QC observation does not demonstrate a decision" in worker.calls[0][3]
    # The new native review does not erase why the previous acceptance was reopened.
    assert read(run / "state.json")["jobs"]["method-0"]["followup_reviews"][0][
        "previous_review"] == str(old_review)
    export_accepted(run)  # Resolved old-profile audit no longer blocks corrected evidence.


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["acceptance", "stale", "empty", "locked"])
async def test_followup_rejects_invalid_reopen_without_state_change(make_run, case):
    from genomics_discovery.curation.followup import apply_followup_review
    from genomics_discovery.curation.harness import run_lock

    run, _ = make_run()
    await execute(run, worker=FakeWorker())
    job = read(run / "state.json")["jobs"]["method-0"]
    followup = run / "jobs/method-0/followup/review.json"
    correction = review_document(read(Path(job["extraction"])), "needs_revision")
    if case == "acceptance":
        correction = review_document(read(Path(job["extraction"])), "accepted")
    elif case == "stale":
        correction["profile_sha256"] = "0" * 64
    elif case == "empty":
        correction["errors"] = []
    write(followup, correction)
    before = (run / "state.json").read_bytes()
    if case == "locked":
        with run_lock(run), pytest.raises(ValueError):
            apply_followup_review(run, "method-0", followup)
    else:
        with pytest.raises(ValueError):
            apply_followup_review(run, "method-0", followup)
    assert (run / "state.json").read_bytes() == before


@pytest.mark.asyncio
async def test_followup_preserves_budget_cap_and_actionable_audit(make_run):
    from genomics_discovery.curation.followup import apply_followup_review

    run, _ = make_run(max_revisions=0)
    await execute(run, worker=FakeWorker())
    job = read(run / "state.json")["jobs"]["method-0"]
    followup = run / "jobs/method-0/followup/review.json"
    correction = review_document(read(Path(job["extraction"])), "needs_revision")
    write(followup, correction)
    applied = apply_followup_review(run, "method-0", followup)
    assert applied["status"] == "research_needed" and applied["revision"] == 0
    assert read(run / "state.json")["jobs"]["method-0"]["feedback"] == correction


@pytest.mark.asyncio
async def test_unapplied_current_followup_blocks_export_and_appears_in_inspection(make_run):
    from genomics_discovery.curation.inspection import inspect_run

    run, _ = make_run()
    await execute(run, worker=FakeWorker())
    job = read(run / "state.json")["jobs"]["method-0"]
    followup = run / "jobs/method-0/followup_audit_20261009/review.json"
    correction = review_document(read(Path(job["extraction"])), "needs_revision")
    write(followup, correction)
    with pytest.raises(ValueError, match="follow-up audit blocks export"):
        export_accepted(run)
    inspected = inspect_run(run)
    assert inspected["method_counts"]["methods_with_followup_holds"] == 1
    assert inspected["jobs"]["method-0"]["followup_audit_holds"][0]["review"] == str(followup)
    # An unexplained wrong profile hash cannot silently remove the export hold.
    correction["profile_sha256"] = "0" * 64
    write(followup, correction)
    assert inspect_run(run)["method_counts"]["methods_with_followup_holds"] == 1
    with pytest.raises(ValueError, match="follow-up audit blocks export"):
        export_accepted(run)


@pytest.mark.asyncio
async def test_incomplete_designated_audit_fails_closed(make_run):
    run, _ = make_run()
    await execute(run, worker=FakeWorker())
    followup = run / "jobs/method-0/followup_audit_20261009/review.json"
    write(followup, {"outcome": "needs_revision"})
    with pytest.raises(ValueError, match="follow-up audit blocks export"):
        export_accepted(run)


@pytest.mark.asyncio
async def test_extraction_independent_review_and_export(make_run):
    run, _ = make_run()
    worker = FakeWorker()
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert result["attempts"] == 2
    assert result["reported_usage"] == {"input_tokens": 10}
    assert [c[1] for c in worker.calls] == ["extractor", "independent_verifier"]
    assert worker.calls[0][2] != worker.calls[1][2]
    assert "LEGACY ROUTING" not in worker.calls[0][3]
    assert "Do not edit its artifact" in worker.calls[1][3]
    exported = export_accepted(run)
    assert exported["profiles"] == exported["modules"] == 1
    bundle = read(Path(exported["output"]))
    profile = bundle["profiles"][0]
    verification = profile.pop("verification")
    assert verification["verified_profile_sha256"] == canonical_hash(profile)
    assert Path(bundle["promotion_provenance"][0]["review"]).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["before_invoke", "after_accepted"])
@pytest.mark.parametrize("error_type", [BrokenPipeError, ValueError])
@pytest.mark.parametrize("failure_site", ["write", "flush"])
async def test_closed_progress_stdout_does_not_cancel_jobs_or_lose_artifacts(
    make_run, monkeypatch, failure_stage, error_type, failure_site
):
    run, _ = make_run(count=3)

    class LostOutput:
        failed = failure_stage == "before_invoke"

        def write(self, value):
            if '"status": "accepted"' in value:
                self.failed = True
            if self.failed and failure_site == "write":
                raise error_type("Progress output closed")
            return len(value)

        def flush(self):
            if self.failed:
                raise error_type("Progress output closed")

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "stdout", LostOutput())
        result = await execute(run, worker=FakeWorker(delay=.002))
    assert result["counts"] == {"accepted": 3}
    assert result["attempts"] == 6
    events = [json.loads(line) for line in (run / "progress.jsonl").read_text().splitlines()]
    assert len(events) == 9
    assert sum(event["status"] == "accepted" for event in events) == 3
    assert all(event["timestamp"] for event in events)
    jobs = read(run / "state.json")["jobs"]
    for job in jobs.values():
        assert sha(Path(job["extraction"])) == job["extraction_sha256"]
        assert sha(Path(job["review"])) == job["review_sha256"]
    assert export_accepted(run)["profiles"] == 3


def test_durable_progress_disk_failure_is_not_swallowed(tmp_path, monkeypatch):
    def disk_failure(_):
        raise OSError("Progress disk synchronization failed")

    monkeypatch.setattr(os, "fsync", disk_failure)
    with pytest.raises(OSError, match="disk synchronization failed"):
        progress(tmp_path, {"method": "method-0", "status": "extracting"})


@pytest.mark.asyncio
async def test_evolution_pilot_keeps_pending_edge_separate_from_accepted_protocol(make_run):
    run, _ = make_run(count=2, with_evolution=True, source_only=True)

    class EvolutionWorker(FakeWorker):
        async def run(self, prompt, attempt):
            result = await super().run(prompt, attempt)
            role = attempt.name.split("_", 1)[1]
            output = attempt / ("extraction.json" if role == "extractor" else "review.json")
            doc = read(output)
            fields = {k: {"text": None, "evidence_status": "pending", "evidence_ids": [],
                           "reason": "Unresolved source comparison"} for k in
                      ("before", "after", "design_move", "bottleneck", "tradeoff")}
            claim = {"claim_id": "method-0:comparison", "source_method_id": "method-1",
                     "target_method_id": "method-0", "relationship_kind": "comparison",
                     "relation": "compares", "change_operator": "compare",
                     "status": "awaiting_evidence", "module_ids": ["m1"], "evidence": [],
                     **fields}
            if role == "extractor":
                doc["evolution_claims"] = [claim]
            else:
                doc["evolution_checks"] = [{"claim_id": claim["claim_id"],
                    "claim_sha256": canonical_hash(claim), "outcome": "awaiting_evidence",
                    "relationship_kind": "comparison", "relation": "compares",
                    "change_operator": "compare", "module_ids_checked": ["m1"],
                    "evidence_ids_checked": [], "field_checks": fields,
                    "rationale": "No evidence to establish a comparison"}]
            write(output, doc)
            assert check_artifact(run, "method-0", output, role)["valid"]
            assert "EVOLUTION ASSERTIONS" in prompt and "curation check" in prompt
            return result

    result = await execute(run, worker=EvolutionWorker(), max_jobs=1)
    assert result["counts"] == {"accepted": 1, "pending": 1}
    exported = read(Path(export_accepted(run)["output"]))
    disposition = exported["evolution_records"][0]["disposition"]
    assert disposition["supported_claim_ids"] == []
    assert disposition["pending_claim_ids"] == ["method-0:comparison"]
    assert disposition["requires_separate_graph_import_review"] is True


@pytest.mark.asyncio
async def test_evolution_pilot_requires_explicit_evaluation(make_run):
    run, _ = make_run(with_evolution=True)
    result = await execute(run, worker=FakeWorker())
    assert result["counts"] == {"failed": 1}
    assert "evolution_claims" in str(result["jobs"]["method-0"]["last_error"])


@pytest.mark.asyncio
async def test_frozen_evolution_registry_blocks_model_call_on_change(make_run):
    run, _ = make_run(with_evolution=True)
    write(run / "method_registry.json", {"methods": [{"id": "invented-method"}]})
    worker = FakeWorker()
    with pytest.raises(ValueError, match="method registry changed"):
        await execute(run, worker=worker)
    assert not worker.calls


@pytest.mark.asyncio
async def test_source_handoff_preserves_cancelled_downloads_for_both_roles(make_run):
    run, _ = make_run(max_attempts=2)
    state = read(run / "state.json")
    prior = run / "jobs/method-0/001_extractor"
    prior.mkdir(parents=True)
    source = prior / "recovered_source.txt"
    source.write_text("Recovered paper contents must stay on disk, not in every prompt.")
    write(prior / "reading_ledger.json", {"local_source": str(source)})
    write(prior / "result.json", asdict(WorkerResult(130, None, {}, 0, "Cancelled")))
    state["jobs"]["method-0"]["attempts"].append({
        "role": "extractor", "directory": str(prior), "revision": 0, "epoch": 0})
    write(run / "state.json", state)
    index = run / "shared_sources/index.json"
    write(index, {"documents": [{"path": str(source), "sha256": sha(source)}]})
    original_index = index.read_bytes()
    worker = FakeWorker()
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert [c[1] for c in worker.calls] == ["extractor", "independent_verifier"]
    attempts = read(run / "state.json")["jobs"]["method-0"]["attempts"]
    for call, recorded in zip(worker.calls, attempts[1:], strict=True):
        _, _, directory, prompt = call
        snapshot = directory / "shared_sources.json"
        assert str(snapshot) in prompt
        assert str(prior) in prompt
        assert "SOURCE HANDOFF" in prompt
        assert source.read_text() not in prompt
        assert snapshot.read_bytes() == original_index
        assert recorded["evidence_index_sha256"] == sha(snapshot)
        assert str(prior) in recorded["prior_attempt_directories"]
    assert str(worker.calls[0][2]) in worker.calls[1][3]
    # Updating the shared lookup later must not rewrite archived worker inputs.
    write(index, {"documents": []})
    assert all((c[2] / "shared_sources.json").read_bytes() == original_index
               for c in worker.calls)
    assert source.is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["[]", "{broken json"])
async def test_invalid_shared_source_index_blocks_model_call(make_run, content):
    run, _ = make_run()
    index = run / "shared_sources/index.json"
    index.parent.mkdir(parents=True)
    index.write_text(content)
    worker = FakeWorker()
    result = await execute(run, worker=worker)
    assert result["counts"] == {"failed": 1}
    assert worker.calls == []
    assert result["attempts"] == 0
    assert result["jobs"]["method-0"]["last_error"]


@pytest.mark.asyncio
async def test_bounded_revision_receives_feedback_then_accepts(make_run):
    run, _ = make_run(max_revisions=1)
    worker = FakeWorker(("needs_revision", "accepted"))
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert result["jobs"]["method-0"]["revision"] == 1
    assert [c[1] for c in worker.calls] == ["extractor", "independent_verifier"] * 2
    revision_prompt = worker.calls[2][3]
    assert "REVISION:" in revision_prompt and "Revise unsupported claim" in revision_prompt
    assert str(worker.calls[0][2] / "extraction.json") in revision_prompt
    assert (worker.calls[0][2] / "extraction.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["unresolved_source_gap", "needs_revision"])
async def test_nonaccepting_review_without_errors_stops_without_revision_or_retry(
    make_run, outcome
):
    run, _ = make_run(max_revisions=2, max_attempts=3)

    class GapOnlyWorker(FakeWorker):
        async def run(self, prompt, attempt):
            result = await super().run(prompt, attempt)
            if attempt.name.endswith("_independent_verifier"):
                path = attempt / "review.json"
                document = read(path)
                document["errors"] = []
                document["remaining_gaps"] = [{
                    "finding": "The supporting source is unavailable",
                    "next_evidence": "An accessible copy of the cited source",
                }]
                write(path, document)
            return result

    worker = GapOnlyWorker((outcome,))
    result = await execute(run, worker=worker)
    assert result["counts"] == {"research_needed": 1}
    assert result["attempts"] == 2
    assert [c[1] for c in worker.calls] == ["extractor", "independent_verifier"]
    job = read(run / "state.json")["jobs"]["method-0"]
    assert job["revision"] == 0
    assert job["feedback"] == read(Path(job["review"]))
    assert job["review_sha256"] == sha(Path(job["review"]))
    assert export_accepted(run)["profiles"] == 0

    # Neither ordinary resume nor a failed-attempt retry should reopen a gap.
    resumed = FakeWorker()
    await execute(run, worker=resumed)
    retried = await execute(run, worker=resumed, retry_failed=True)
    assert retried["counts"] == {"research_needed": 1}
    assert retried["attempts"] == 2
    assert resumed.calls == []


@pytest.mark.asyncio
async def test_source_gap_with_errors_receives_bounded_correction(make_run):
    run, _ = make_run(max_revisions=1)

    class MixedReviewWorker(FakeWorker):
        async def run(self, prompt, attempt):
            result = await super().run(prompt, attempt)
            if attempt.name.endswith("_independent_verifier"):
                path = attempt / "review.json"
                document = read(path)
                if document["outcome"] == "unresolved_source_gap":
                    document["remaining_gaps"] = [{
                        "finding": "A separate supporting source is unavailable"}]
                    write(path, document)
            return result

    worker = MixedReviewWorker(("unresolved_source_gap", "accepted"))
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert result["jobs"]["method-0"]["revision"] == 1
    assert [c[1] for c in worker.calls] == ["extractor", "independent_verifier"] * 2
    revision_prompt = worker.calls[2][3]
    assert "Revise unsupported claim" in revision_prompt
    assert "A separate supporting source is unavailable" in revision_prompt
    assert str(worker.calls[0][2] / "extraction.json") in revision_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["needs_revision", "unresolved_source_gap"])
@pytest.mark.parametrize("max_revisions", [0, 1])
async def test_terminal_review_preserves_all_feedback(make_run, outcome, max_revisions):
    run, _ = make_run(max_revisions=max_revisions)

    class DistinctReviewWorker(FakeWorker):
        async def run(self, prompt, attempt):
            result = await super().run(prompt, attempt)
            if attempt.name.endswith("_independent_verifier"):
                path = attempt / "review.json"
                document = read(path)
                document["errors"] = [{"finding": f"Correct the claim from {attempt.name}"}]
                document["remaining_gaps"] = [{
                    "finding": f"Source gap remains at {attempt.name}"}]
                write(path, document)
            return result

    worker = DistinctReviewWorker((outcome,))
    result = await execute(run, worker=worker)
    assert result["counts"] == {"research_needed": 1}
    assert result["attempts"] == 2 * (max_revisions + 1)
    job = read(run / "state.json")["jobs"]["method-0"]
    assert job["revision"] == max_revisions
    final_review = worker.calls[-1][2] / "review.json"
    assert job["review"] == str(final_review)
    assert job["feedback"] == read(final_review)
    assert job["feedback"]["errors"] and job["feedback"]["remaining_gaps"]


@pytest.mark.asyncio
async def test_revision_schema_retry_preserves_scientific_feedback_and_saved_draft(make_run):
    run, _ = make_run(max_revisions=1, max_attempts=2)

    class InvalidFirstRevision(FakeWorker):
        async def run(self, prompt, attempt):
            self.bad_role = "extractor" if attempt.name == "003_extractor" else None
            return await super().run(prompt, attempt)

    worker = InvalidFirstRevision(("needs_revision", "accepted"))
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    retry_prompt = worker.calls[3][3]
    assert "Revise unsupported claim" in retry_prompt
    assert f"REVISION: Read the previous extraction at {worker.calls[2][2] / 'extraction.json'}" \
        in retry_prompt
    assert "integrity_errors" in retry_prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["extractor", "independent_verifier"])
async def test_explicit_retry_preserves_validation_feedback_across_epochs(make_run, role):
    run, _ = make_run(max_attempts=1)
    failed = FakeWorker(bad_role=role)
    await execute(run, worker=failed)
    previous = failed.calls[-1][2]
    errors = read(previous / "validation.json")["errors"]
    worker = FakeWorker()
    result = await execute(run, worker=worker, retry_failed=True)
    assert result["counts"] == {"accepted": 1}
    retry_prompt = worker.calls[0][3]
    assert "PRIOR FEEDBACK:" in retry_prompt
    feedback = json.loads(retry_prompt.split("PRIOR FEEDBACK:\n", 1)[1])
    assert feedback["integrity_errors"] == errors
    assert feedback["previous_attempt"] == str(previous)
    assert feedback["repair_artifact"] == str(previous / (
        "extraction.json" if role == "extractor" else "review.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("archived_validation", [False, True])
async def test_cancelled_retry_does_not_hide_prior_validation_feedback(
    make_run, archived_validation
):
    run, _ = make_run(max_attempts=3)
    previous = run / "jobs/method-0/001_extractor"
    await FakeWorker(bad_role="extractor").run("Invalid draft", previous)
    write(previous / "validation.json", {"errors": ["Synthetic field-specific repair"]})
    interrupted = previous.with_name("002_extractor")
    write(interrupted / "result.json", {"error": "Codex attempt cancelled"})
    if archived_validation:
        write(interrupted / "validation.json", {"errors": ["Codex attempt cancelled"]})
    state = read(run / "state.json")
    state["jobs"]["method-0"].update(status="extracting", attempts=[
        {"role": "extractor", "directory": str(p), "revision": 0} for p in
        (previous, interrupted)])
    write(run / "state.json", state)
    worker = FakeWorker()
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert "Synthetic field-specific repair" in worker.calls[0][3]


@pytest.mark.parametrize("timeout", [float("inf"), float("nan")])
def test_initialize_rejects_nonfinite_attempt_timeout(make_run, timeout):
    with pytest.raises(ValueError, match="timeout"):
        make_run(timeout=timeout)


async def test_uncapped_run_keeps_explicit_discovery_policy_and_independent_review(make_run):
    run, _ = make_run(timeout=None, allow_source_discovery=True)
    config = read(run / "config.json")
    assert config["timeout_seconds"] is None
    assert config["allow_source_discovery"] is True
    assert read(run / "jobs/method-0/input.json")["allow_source_discovery"] is True
    result = await execute(run, worker=FakeWorker())
    assert result["counts"] == {"accepted": 1}
    assert result["attempts"] == 2


def test_source_only_initialization_preserves_identity_and_omits_prior_answers(make_run):
    legacy, atlas = make_run()
    graph = read(atlas / "output/knowledge_graph.json")
    graph["methods"][0].update(
        aliases=["Alternative name"], original_categories=["measurement"], year=2001,
        origin_paper_ids=["pmid:1"], workflow={"previous_answer": "workflow"},
        mechanism="Previous mechanism", bottlenecks=["Previous gaps"],
        verification={"outcome": "accepted"})
    write(atlas / "output/knowledge_graph.json", graph)
    run = legacy.with_name("source-only-run")
    initialize(run, atlas, source_only=True)
    task = read(run / "jobs/method-0/input.json")
    assert task["source_only"] is True
    for key in ("id", "name", "aliases", "original_categories", "year", "origin_paper_ids",
                "paper_ids"):
        assert task[key] == graph["methods"][0][key]
    assert not set(task) & {"workflow", "mechanism", "bottlenecks", "verification"}
    assert task["paper_leads"] == graph["papers"]
    assert task["read_only_source_roots"] == [str(atlas / "papers")]
    assert read(run / "config.json")["source_only"] is True
    assert read(legacy / "config.json")["source_only"] is False


def test_source_only_paper_leads_do_not_leak_nested_review_answers(make_run):
    legacy, atlas = make_run()
    graph = read(atlas / "output/knowledge_graph.json")
    paper = graph["papers"][0]
    paper.update(
        contribution_summary="FORBIDDEN top-level conclusion",
        reviews=[{
            "paper_id": "pmid:1", "doi": "10.example/original",
            "local_full_text_path": "/cache/original.pdf", "sha256": "source-hash",
            "contribution_summary": "FORBIDDEN previous design interpretation",
            "review_scope": "FORBIDDEN inherited review scope",
            "limitations_of_review": ["FORBIDDEN prior gap"],
            "sections_read": ["FORBIDDEN inherited reading"],
        }],
        acquisition={
            "pmcid": "PMC1", "full_text_url": "https://example.org/original",
            "supplement_links": ["supplement.pdf"],
            "attempts": [{"summary": "FORBIDDEN previous worker text"}],
            "review_status": "FORBIDDEN inherited acceptance",
        },
    )
    write(atlas / "output/knowledge_graph.json", graph)
    fresh = legacy.with_name("fresh-source-only")
    initialize(fresh, atlas, source_only=True)
    task = read(fresh / "jobs/method-0/input.json")
    assert "FORBIDDEN" not in json.dumps(task)
    lead = task["paper_leads"][0]
    assert lead["cached_source_leads"][0]["local_full_text_path"] == "/cache/original.pdf"
    assert lead["cached_source_leads"][0]["sha256"] == "source-hash"
    assert lead["acquisition"]["supplement_links"] == ["supplement.pdf"]
    assert lead["acquisition"]["pmcid"] == "PMC1"
    regular = legacy.with_name("regular-input")
    initialize(regular, atlas)
    assert read(regular / "jobs/method-0/input.json")["paper_leads"] == [paper]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["needs_revision", "unresolved_source_gap"])
async def test_rejection_exhaustion_stays_research_needed(make_run, outcome):
    run, _ = make_run(max_revisions=1)
    result = await execute(run, worker=FakeWorker((outcome,)))
    assert result["counts"] == {"research_needed": 1}
    assert result["attempts"] == 4
    assert export_accepted(run)["profiles"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["extractor", "independent_verifier"])
async def test_successful_process_with_bad_artifact_is_never_accepted(make_run, role):
    run, _ = make_run()
    result = await execute(run, worker=FakeWorker(bad_role=role))
    assert result["counts"] == {"failed": 1}
    assert result["jobs"]["method-0"]["last_error"]
    assert export_accepted(run)["profiles"] == 0


@pytest.mark.asyncio
async def test_resume_skips_accepted_and_max_jobs_leaves_others_pending(make_run):
    run, _ = make_run(count=3)
    first = FakeWorker()
    result = await execute(run, worker=first, max_jobs=1)
    assert result["counts"] == {"accepted": 1, "pending": 2}
    second = FakeWorker()
    result = await execute(run, worker=second)
    assert result["counts"] == {"accepted": 3}
    assert {c[0] for c in second.calls} == {"method-1", "method-2"}
    empty = FakeWorker()
    assert (await execute(run, worker=empty))["attempts"] == 6
    assert empty.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("file", ["config.json", "contracts.json"])
async def test_frozen_run_documents_cannot_be_changed(make_run, file):
    run, _ = make_run()
    document = read(run / file)
    document["tampered"] = True
    write(run / file, document)
    worker = FakeWorker()
    with pytest.raises(ValueError, match="changed"):
        await execute(run, worker=worker)
    assert worker.calls == []


@pytest.mark.asyncio
async def test_frozen_method_input_blocks_worker_invocation(make_run):
    run, _ = make_run()
    task = run / "jobs/method-0/input.json"
    document = read(task)
    document["name"] = "Changed assignment"
    write(task, document)
    worker = FakeWorker()
    with pytest.raises(ValueError, match="Frozen method input"):
        await execute(run, worker=worker)
    assert worker.calls == []


@pytest.mark.asyncio
async def test_more_than_four_jobs_run_with_reserved_verifier_capacity(make_run):
    run, _ = make_run(count=18, workers=8, reviewers=2)
    worker = FakeWorker(delay=0.003)
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 18}
    assert worker.peak > 4
    assert worker.peak <= 8
    assert worker.max_active["extractor"] == 6
    assert worker.max_active["independent_verifier"] == 2
    assert worker.overlapped
    assert len({call[2] for call in worker.calls}) == 36


@pytest.mark.asyncio
async def test_accepted_source_tampering_blocks_export(make_run):
    run, _ = make_run()
    await execute(run, worker=FakeWorker())
    job = read(run / "state.json")["jobs"]["method-0"]
    extraction = read(Path(job["extraction"]))
    source = Path(extraction["sources"][0]["source_metadata"]["local_evidence"])
    source.write_text("Tampered source")
    with pytest.raises(ValueError, match="Invalid acceptance"):
        export_accepted(run)
    assert not (run / "accepted_profiles.json").exists()


@pytest.mark.asyncio
async def test_failed_run_requires_explicit_retry(make_run):
    run, _ = make_run()
    await execute(run, worker=FakeWorker(bad_role="extractor"))
    worker = FakeWorker()
    assert (await execute(run, worker=worker))["counts"] == {"failed": 1}
    assert worker.calls == []
    assert (await execute(run, worker=worker, retry_failed=True))["counts"] == {"accepted": 1}
    assert summarize(run)["attempts"] == 3


@pytest.mark.asyncio
async def test_invalid_max_jobs_rejected_without_work(make_run):
    run, _ = make_run()
    worker = FakeWorker()
    with pytest.raises(ValueError, match="max_jobs"):
        await execute(run, worker=worker, max_jobs=0)
    assert worker.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact", ["config", "input", "extraction", "review"])
async def test_frozen_records_also_checked_during_export(make_run, artifact):
    run, _ = make_run()
    await execute(run, worker=FakeWorker())
    job = read(run / "state.json")["jobs"]["method-0"]
    paths = {"config": run / "config.json", "input": run / "jobs/method-0/input.json",
             "extraction": Path(job["extraction"]), "review": Path(job["review"])}
    target = paths[artifact]
    value = read(target)
    value["tampered_but_still_structurally_valid"] = True
    write(target, value)
    with pytest.raises(ValueError, match="changed"):
        export_accepted(run)
    assert not (run / "accepted_profiles.json").exists()


@pytest.mark.asyncio
async def test_interrupted_review_resumes_same_extraction_within_budget(make_run):
    run, _ = make_run(max_attempts=2)
    entered_review = asyncio.Event()

    class BlockingReviewer(FakeWorker):
        async def run(self, prompt, attempt):
            if "independent_verifier" in attempt.name:
                attempt.mkdir(parents=True, exist_ok=True)
                (attempt / "prompt.txt").write_text(prompt)
                entered_review.set()
                await asyncio.Event().wait()
            return await super().run(prompt, attempt)

    task = asyncio.create_task(execute(run, worker=BlockingReviewer()))
    await asyncio.wait_for(entered_review.wait(), 2)
    with pytest.raises(ValueError, match="active orchestrator"):
        await execute(run, worker=FakeWorker())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    interrupted = read(run / "state.json")["jobs"]["method-0"]
    assert interrupted["status"] == "verifying"
    extraction = interrupted["extraction"]
    worker = FakeWorker()
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert [c[1] for c in worker.calls] == ["independent_verifier"]
    assert result["jobs"]["method-0"]["extraction"] == extraction
    assert result["attempts"] == 3


@pytest.mark.asyncio
async def test_retry_cap_does_not_mutate_unselected_failed_jobs(make_run):
    run, _ = make_run(count=2)
    await execute(run, worker=FakeWorker(bad_role="extractor"))
    assert summarize(run)["counts"] == {"failed": 2}
    result = await execute(run, worker=FakeWorker(), retry_failed=True, max_jobs=1)
    assert result["counts"] == {"accepted": 1, "failed": 1}


@pytest.mark.asyncio
async def test_persisted_attempt_budget_requires_explicit_retry(make_run):
    run, _ = make_run(max_attempts=1)
    await execute(run, worker=FakeWorker(bad_role="independent_verifier"))
    state = read(run / "state.json")
    job = state["jobs"]["method-0"]
    # Model the last checkpoint before recording terminal failure. The failed
    # verifier attempt is already archived and must still consume its budget.
    job["status"] = "verifying"
    write(run / "state.json", state)
    worker = FakeWorker()
    result = await execute(run, worker=worker)
    assert result["counts"] == {"failed": 1}
    assert worker.calls == []
    result = await execute(run, worker=worker, retry_failed=True)
    assert result["counts"] == {"accepted": 1}
    assert [c[1] for c in worker.calls] == ["independent_verifier"]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["extractor", "independent_verifier"])
async def test_explicit_retry_covers_interruption_before_failure_checkpoint(make_run, role):
    run, _ = make_run(count=2, max_attempts=1)
    await execute(run, worker=FakeWorker(bad_role=role))
    state = read(run / "state.json")
    for job in state["jobs"].values():
        job["status"] = "extracting" if role == "extractor" else "verifying"
    write(run / "state.json", state)
    unselected = state["jobs"]["method-1"]
    worker = FakeWorker()
    result = await execute(run, worker=worker, max_jobs=1, retry_failed=True)
    assert result["jobs"]["method-0"]["status"] == "accepted"
    after = read(run / "state.json")["jobs"]
    assert after["method-0"]["epoch"] == 1
    assert after["method-1"] == unselected
    assert worker.calls[0][1] == role


@pytest.mark.asyncio
async def test_live_orphan_worker_refuses_duplicate_launch(make_run):
    run, _ = make_run()
    attempt = run / "jobs/method-0/001_extractor"
    write(attempt / "started.json", {"pid": os.getpid()})
    state = read(run / "state.json")
    state["jobs"]["method-0"].update(status="extracting", attempts=[{
        "role": "extractor", "directory": str(attempt), "revision": 0}])
    write(run / "state.json", state)
    worker = FakeWorker()
    with pytest.raises(ValueError, match="may still be running"):
        await execute(run, worker=worker)
    assert worker.calls == []


@pytest.mark.asyncio
async def test_resuming_capped_pilot_never_selects_new_methods(make_run):
    run, _ = make_run(count=12)
    await execute(run, worker=FakeWorker(), max_jobs=10)
    worker = FakeWorker()
    result = await execute(run, worker=worker, max_jobs=10, retry_failed=True)
    assert result["counts"] == {"accepted": 10, "pending": 2}
    assert worker.calls == []
    state = read(run / "state.json")
    assert state["invocations"][-1]["cohort_method_ids"] == [f"method-{i}" for i in range(10)]


async def completed_extraction(run, *, revision=0, legacy_error=False):
    """Create a real-shaped finished archive without asking the scheduler to accept it."""
    directory = run / "jobs/method-0/001_extractor"
    await FakeWorker().run("Fixture extraction prompt", directory)
    result = read(directory / "result.json")
    if legacy_error:
        result["error"] = "Codex emitted error; see events.jsonl"
        write(directory / "result.json", result)
        events = (directory / "events.jsonl").read_text().splitlines()
        events.insert(2, json.dumps({"type": "error", "message": "Retrying connection"}))
        (directory / "events.jsonl").write_text("\n".join(events) + "\n")
    write(directory / "validation.json", {"errors": [result["error"]] if legacy_error else []})
    state = read(run / "state.json")
    state["jobs"]["method-0"].update(status="failed", attempts=[{
        "role": "extractor", "directory": str(directory), "revision": revision,
        "finished_at": "2026-10-08T00:00:00+00:00"}])
    write(run / "state.json", state)
    return directory


@pytest.mark.asyncio
async def test_recovery_reuses_completed_first_attempt_after_cancelled_retry(make_run):
    run, _ = make_run()
    directory = await completed_extraction(run, legacy_error=True)
    originals = {name: (directory / name).read_bytes()
                 for name in ("result.json", "validation.json", "extraction.json")}
    cancelled = directory.with_name("002_extractor")
    write(cancelled / "result.json", {"exit_code": -15, "error": "Codex attempt cancelled"})
    state = read(run / "state.json")
    state["jobs"]["method-0"]["attempts"].append({
        "role": "extractor", "directory": str(cancelled), "revision": 0})
    write(run / "state.json", state)

    recovered = recover_completed(run, max_jobs=1)
    assert recovered["counts"] == {"extracted": 1}
    assert recovered["attempts"] == 2
    assert len(recovered["recovered_artifacts"]) == 1
    record = recovered["recovered_artifacts"][0]
    assert record["archive_sha256"]["extraction.json"] == sha(directory / "extraction.json")
    assert record["artifact_hash_previously_checkpointed"] is False
    assert "original output hash was not recorded" in record["artifact_hash_scope"]
    assert list((run / "recoveries").glob("*.json"))
    assert recover_completed(run)["recovered_artifacts"] == []
    worker = FakeWorker()
    assert (await execute(run, worker=worker, max_jobs=1))["counts"] == {"accepted": 1}
    assert [call[1] for call in worker.calls] == ["independent_verifier"]
    assert all((directory / name).read_bytes() == original for name, original in originals.items())


@pytest.mark.asyncio
async def test_execute_recovers_before_consuming_another_extraction_attempt(make_run):
    run, _ = make_run(max_attempts=1)
    directory = await completed_extraction(run, legacy_error=True)
    worker = FakeWorker()
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert [call[1] for call in worker.calls] == ["independent_verifier"]
    assert result["jobs"]["method-0"]["extraction"] == str(directory / "extraction.json")


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["source", "artifact_hash", "incomplete", "exit_code", "final"])
async def test_recovery_rejects_tampered_or_incomplete_work(make_run, damage):
    run, _ = make_run()
    directory = await completed_extraction(run)
    state = read(run / "state.json")
    if damage == "source":
        (directory / "source.txt").write_text("Changed source bytes")
    elif damage == "artifact_hash":
        state["jobs"]["method-0"]["attempts"][0]["artifact_sha256"] = "0" * 64
        write(run / "state.json", state)
    elif damage == "incomplete":
        with (directory / "events.jsonl").open("a") as handle:
            handle.write(json.dumps({"type": "turn.started"}) + "\n")
    elif damage == "exit_code":
        result = read(directory / "result.json")
        result["exit_code"] = 124
        write(directory / "result.json", result)
    else:
        (directory / "final.txt").write_text("")
    result = recover_completed(run)
    assert result["counts"] == {"failed": 1}
    assert result["recovered_artifacts"] == []
    assert not (run / "recoveries").exists()


@pytest.mark.asyncio
async def test_recovery_never_reuses_a_previous_scientific_revision(make_run):
    run, _ = make_run()
    await completed_extraction(run)
    state = read(run / "state.json")
    state["jobs"]["method-0"]["revision"] = 1
    write(run / "state.json", state)
    assert recover_completed(run)["recovered_artifacts"] == []


@pytest.mark.asyncio
async def test_recovery_recognizes_completed_review_without_new_worker(make_run):
    run, _ = make_run()
    await execute(run, worker=FakeWorker())
    state = read(run / "state.json")
    state["jobs"]["method-0"]["status"] = "verifying"
    state["jobs"]["method-0"]["review"] = None
    state["jobs"]["method-0"].pop("review_sha256")
    write(run / "state.json", state)
    worker = FakeWorker()
    result = await execute(run, worker=worker)
    assert result["counts"] == {"accepted": 1}
    assert worker.calls == []
    assert result["jobs"]["method-0"]["recoveries"] == 1


@pytest.mark.asyncio
async def test_newer_recovered_extraction_invalidates_older_completed_review(make_run):
    run, _ = make_run(max_attempts=2)
    await execute(run, worker=FakeWorker())
    state = read(run / "state.json")
    job = state["jobs"]["method-0"]
    directory = run / "jobs/method-0/003_extractor"
    await FakeWorker().run("A subsequent extraction", directory)
    document = read(directory / "extraction.json")
    document["profiles"][0]["profile_metadata"]["name"] = "Changed profile"
    write(directory / "extraction.json", document)
    job["attempts"].append({"role": "extractor", "directory": str(directory), "revision": 0})
    job["status"] = "extracting"
    write(run / "state.json", state)
    result = recover_completed(run)
    assert result["counts"] == {"extracted": 1}
    assert result["jobs"]["method-0"]["review"] is None
    worker = FakeWorker()
    assert (await execute(run, worker=worker))["counts"] == {"accepted": 1}
    assert [call[1] for call in worker.calls] == ["independent_verifier"]


@pytest.mark.asyncio
async def test_recovery_audit_records_are_frozen(make_run):
    run, _ = make_run()
    await completed_extraction(run)
    recover_completed(run)
    record = next((run / "recoveries").glob("*.json"))
    record.write_text("{}")
    with pytest.raises(ValueError, match="Recovery audit record changed"):
        recover_completed(run)


@pytest.mark.asyncio
async def test_recovery_refuses_incomplete_live_attempt_even_with_completed_prior(make_run):
    run, _ = make_run()
    directory = await completed_extraction(run)
    active = directory.with_name("002_extractor")
    write(active / "started.json", {"pid": os.getpid()})
    state = read(run / "state.json")
    state["jobs"]["method-0"]["attempts"].append({
        "role": "extractor", "directory": str(active), "revision": 0})
    write(run / "state.json", state)
    before = (run / "state.json").read_bytes()
    with pytest.raises(ValueError, match="may still be running"):
        recover_completed(run)
    assert (run / "state.json").read_bytes() == before


@pytest.mark.asyncio
async def test_status_distinguishes_saved_drafts_from_acceptance_and_cumulative_usage(make_run):
    run, _ = make_run()
    await completed_extraction(run, legacy_error=True)
    status = summarize(run)
    assert status["counts"] == {"failed": 1}
    assert status["methods_with_saved_drafts"] == {"extraction": 1}
    assert status["attempt_counts"] == {"archived": 1, "without_result": 0}
    assert status["reported_usage"] == {"input_tokens": 5}
    assert "Cumulative" in status["usage_scope"]
    latest = status["jobs"]["method-0"]["latest_attempt"]
    assert latest["archived_error"] == "Codex emitted error; see events.jsonl"
    assert latest["result_archived"]


@pytest.mark.asyncio
async def test_failed_output_hash_is_pinned_before_recovery(make_run):
    run, _ = make_run()
    worker = FakeWorker(bad_role="extractor")
    await execute(run, worker=worker)
    state = read(run / "state.json")
    attempt = state["jobs"]["method-0"]["attempts"][0]
    output = Path(attempt["directory"]) / "extraction.json"
    assert attempt["artifact_sha256"] == sha(output)
    # Even if an edit fixes validation, it is no longer the archived output.
    document = read(output)
    document["profiles"][0]["modules"][0]["source_refs"] = [
        {"source_id": "method-0-source", "locator": "Section 1"}]
    write(output, document)
    assert recover_completed(run)["recovered_artifacts"] == []
