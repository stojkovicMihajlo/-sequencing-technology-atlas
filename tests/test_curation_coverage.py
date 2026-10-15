"""Frozen scope and omission checks complement emitted-module review."""

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest
import test_curation_harness as fixtures
from test_curation_prepared_sources import setup_case

from genomics_discovery.curation import harness
from genomics_discovery.curation.codex_worker import WorkerResult
from genomics_discovery.curation.coverage import SCOPE_FORMAT, validate_checklist
from genomics_discovery.curation.prompts import build_prompt
from genomics_discovery.curation.scaffolds import make_checklist_scaffold
from genomics_discovery.curation.validation import EVIDENCE_POLICY, validate_review


def scoped_case(tmp_path, **kwargs):
    run, atlas, packet, source, _ = setup_case(tmp_path)
    # Continuation source catalogues intentionally require runs within the atlas.
    run = atlas / "work/scoped-run"
    scope = {"implementation": "Published measurement implementation, version 1",
             "coverage": "Architecture for an attributed signal, not a historical execution",
             "required_outputs": ["An attributed signal record"],
             "included_branches": ["Primary readout"], "excluded_branches": ["Optional export"]}
    scope_path = atlas / "review_scope_input.json"
    harness.write(scope_path, {"format_version": SCOPE_FORMAT, "methods": {"method-0": scope}})
    harness.initialize(run, atlas, source_only=True, prepared_sources=packet,
                       review_scope=scope_path, max_attempts=1, max_revisions=1, **kwargs)
    return run, atlas, source, scope


def checklist(task, *, gap_blocking=None):
    document = make_checklist_scaffold(task)
    for source in document["sources"]:
        source["source_metadata"]["inspected_url"] = source["source_metadata"]["primary_url"]
        source["source_metadata"]["sections_inspected"] = ["Methods"]
    refs = [{"source_id": document["sources"][0]["source_id"], "locator": "Methods"}]
    document["items"] = [
        {"item_id": "signal", "requirement": "Preserve the reported signal record",
         "scope_reason": "Required readout", "source_refs": refs},
        {"item_id": "identity", "requirement": "CHECKLIST_ONLY_IDENTITY_REQUIREMENT",
         "scope_reason": "The declared output requires attribution", "source_refs": refs}]
    if gap_blocking is not None:
        document["remaining_gaps"] = [{"gap_id": "dependency", "affected_field": "readout",
            "detail": "An interpretation dependency has not been inspected",
            "scope_reason": "Output interpretation" if gap_blocking else "Recipe detail only",
            "blocking_for_declared_scope": gap_blocking,
            "requested_evidence": "Inspect the named source dependency"}]
    return document


class CoverageWorker(fixtures.FakeWorker):
    def __init__(self, *, missing=False, gap_blocking=None, gap_outcome="resolved",
                 cancel_checklist=False, invalid_checklist=False):
        super().__init__()
        self.missing, self.gap_blocking, self.gap_outcome = missing, gap_blocking, gap_outcome
        self.cancel_checklist, self.invalid_checklist = cancel_checklist, invalid_checklist

    async def run(self, prompt, attempt):
        mid = attempt.parent.name
        task = harness.read(attempt.parent / "input.json")
        if attempt.name.endswith("_source_checklist"):
            self.calls.append((mid, "source_checklist", attempt, prompt))
            document = checklist(task, gap_blocking=self.gap_blocking)
            if self.invalid_checklist:
                document["items"] = []
            harness.write(attempt / "checklist.json", document)
            result = WorkerResult(0, "checklist-session", {"input_tokens": 5}, 0, None)
            harness.write(attempt / "result.json", asdict(result))
            events = [{"type": "thread.started", "thread_id": result.thread_id},
                      {"type": "turn.started"}, {"type": "turn.completed", "usage": result.usage}]
            (attempt / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
            (attempt / "final.txt").write_text("Provisional source checklist complete.")
            if self.cancel_checklist:
                raise asyncio.CancelledError
            return result
        result = await super().run(prompt, attempt)
        if attempt.name.endswith("_extractor"):
            path = attempt / "extraction.json"
            document = harness.read(path)
            document["review_scope"] = task["review_scope"]
            # Use prepared original bytes, not the synthetic fixture's source copy.
            source = deepcopy(checklist(task)["sources"][0])
            meta = source["source_metadata"]
            source_path = meta["local_evidence"][0]
            meta["local_evidence"] = source_path
            meta["sha256"] = meta["sha256"][source_path]
            document["sources"] = [source]
            for module in document["profiles"][0]["modules"]:
                module["source_refs"] = [{"source_id": source["source_id"], "locator": "Methods"}]
            harness.write(path, document)
        else:
            path = attempt / "review.json"
            document = harness.read(path)
            coverage = deepcopy(harness.read(attempt / "review_scaffold.json")[
                "source_to_record_check"])
            for item in coverage["item_checks"]:
                item.update(outcome="missing" if self.missing and item["item_id"] == "identity"
                            else "represented", observations=["Checked original Methods"],
                            scope_reason="Required declared output",
                            record_paths=["/profiles/0/modules/0"],
                            source_refs_checked=[{"source_id": "original", "locator": "Methods"}])
            for gap in coverage["gap_checks"]:
                gap.update(outcome=self.gap_outcome, observations=["Dependency reassessed"],
                           scope_reason="Frozen output scope checked",
                           source_refs_checked=[{"source_id": "original", "locator": "Methods"}],
                           record_paths=["/profiles/0/modules/0"])
                if self.gap_outcome == "unresolved":
                    document["remaining_gaps"] = checklist(task, gap_blocking=self.gap_blocking)[
                        "remaining_gaps"]
            document["source_to_record_check"] = coverage
            harness.write(path, document)
        return result


@pytest.mark.asyncio
async def test_checklist_precedes_extraction_and_is_withheld_until_review(tmp_path):
    run, atlas, _, scope = scoped_case(tmp_path)
    worker = CoverageWorker()
    summary = await harness.execute(run, worker=worker)
    assert summary["counts"] == {"accepted": 1}
    assert [call[1] for call in worker.calls] == [
        "source_checklist", "extractor", "independent_verifier"]
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert job["source_checklist_sha256"] == harness.sha(Path(job["source_checklist"]))
    assert "CHECKLIST_ONLY_IDENTITY_REQUIREMENT" not in worker.calls[1][3]
    assert job["source_checklist"] not in worker.calls[1][3]
    assert job["source_checklist"] in worker.calls[2][3]
    assert harness.read(Path(job["extraction"]))["review_scope"] == scope
    assert harness.check_artifact(run, "method-0", Path(job["source_checklist"]),
                                  "source_checklist")["valid"]
    exported = harness.export_accepted(run)
    assert exported["profiles"] == 1
    verification = harness.read(Path(exported["output"]))["profiles"][0]["verification"]
    assert verification["review_scope"] == scope
    assert verification["source_checklist_sha256"] == job["source_checklist_sha256"]
    harness.check_frozen(run, harness.read(run / "state.json"), harness.read(run / "config.json"))


@pytest.mark.asyncio
async def test_omitted_requirement_blocks_acceptance_despite_all_module_checks(tmp_path):
    run, _, _, _ = scoped_case(tmp_path)
    worker = CoverageWorker(missing=True)
    summary = await harness.execute(run, worker=worker)
    assert summary["counts"] == {"failed": 1}
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    assert "source requirement missing: identity" in str(job["last_error"])
    assert harness.export_accepted(run)["profiles"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("blocking,outcome,expected", [
    (True, "unresolved", "failed"), (False, "unresolved", "accepted"),
    (True, "outside_declared_scope", "accepted")])
async def test_unresolved_source_gap_respects_frozen_output_consequence(
    tmp_path, blocking, outcome, expected
):
    run, _, _, _ = scoped_case(tmp_path)
    result = await harness.execute(run, worker=CoverageWorker(
        gap_blocking=blocking, gap_outcome=outcome))
    assert result["counts"] == {expected: 1}


@pytest.mark.asyncio
async def test_completed_checklist_recovers_after_cancellation_without_rereading(tmp_path):
    run, _, _, _ = scoped_case(tmp_path)
    with pytest.raises(asyncio.CancelledError):
        await harness.execute(run, worker=CoverageWorker(cancel_checklist=True))
    recovered = harness.recover_completed(run)
    assert recovered["recovered_artifacts"][0]["role"] == "source_checklist"
    worker = CoverageWorker()
    assert (await harness.execute(run, worker=worker))["counts"] == {"accepted": 1}
    assert [call[1] for call in worker.calls] == ["extractor", "independent_verifier"]


@pytest.mark.asyncio
async def test_checklist_format_failure_has_bounded_budget_and_no_extraction(tmp_path):
    run, _, _, _ = scoped_case(tmp_path)
    worker = CoverageWorker(invalid_checklist=True)
    assert (await harness.execute(run, worker=worker))["counts"] == {"failed": 1}
    assert [call[1] for call in worker.calls] == ["source_checklist"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["scope", "checklist", "source"])
async def test_frozen_scope_checklist_and_source_tampering_prevent_export(tmp_path, mutation):
    run, _, source, _ = scoped_case(tmp_path)
    await harness.execute(run, worker=CoverageWorker())
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    path = {"scope": run / "review_scope.json", "checklist": Path(job["source_checklist"]),
            "source": source}[mutation]
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="changed|sha256 mismatch"):
        harness.export_accepted(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["scope", "item", "pointer", "hash", "gap", "gap_source"])
async def test_review_rejects_scope_omission_bad_pointers_and_unassessed_gaps(tmp_path, mutation):
    run, atlas, _, scope = scoped_case(tmp_path)
    await harness.execute(run, worker=CoverageWorker(gap_blocking=True))
    job = harness.read(run / "state.json")["jobs"]["method-0"]
    extraction, review = Path(job["extraction"]), Path(job["review"])
    doc = harness.read(review)
    coverage = doc["source_to_record_check"]
    if mutation == "scope":
        draft = harness.read(extraction)
        draft["review_scope"]["included_branches"] = []
        harness.write(extraction, draft)
    elif mutation == "item":
        coverage["item_checks"].pop()
    elif mutation == "pointer":
        coverage["item_checks"][0]["record_paths"] = ["/profiles/0/modules/99"]
    elif mutation == "hash":
        coverage["checklist_sha256"] = "0" * 64
    elif mutation == "gap":
        coverage["gap_checks"] = []
    else:
        coverage["gap_checks"][0]["source_refs_checked"] = []
    harness.write(review, doc)
    errors = validate_review(review, extraction, "method-0", (atlas, run),
        review_scopes={"method-0": scope}, source_checklist=Path(job["source_checklist"]),
        evidence_policy=EVIDENCE_POLICY)
    assert errors


def test_checklist_prompt_does_not_receive_seed_or_prior_graph_context(tmp_path):
    run, _, _, _ = scoped_case(tmp_path)
    task = harness.read(run / "jobs/method-0/input.json")
    task["seed"] = {"secret": "OLD_DRAFT_SECRET"}
    task["previous_profile"] = "OLD_PROFILE_SECRET"
    prompt = build_prompt("source_checklist", run / "jobs/method-0/001_source_checklist", task,
        {}, extraction=Path("OLD_EXTRACTION_SECRET"),
        prior_attempts=(Path("OLD_REVIEW_SECRET"),))
    assert all(secret not in prompt for secret in (
        "OLD_DRAFT_SECRET", "OLD_PROFILE_SECRET", "OLD_EXTRACTION_SECRET", "OLD_REVIEW_SECRET"))
    assert "instruction-level" in prompt


@pytest.mark.parametrize("value", ["true", 1, None])
def test_checklist_gap_control_flag_requires_boolean(tmp_path, value):
    run, atlas, _, scope = scoped_case(tmp_path)
    task = harness.read(run / "jobs/method-0/input.json")
    doc = checklist(task, gap_blocking=True)
    doc["remaining_gaps"][0]["blocking_for_declared_scope"] = value
    path = run / "jobs/method-0/test/checklist.json"
    harness.write(path, doc)
    assert validate_checklist(path, "method-0", (atlas, run), scope,
                              evidence_policy=EVIDENCE_POLICY)


def test_legacy_mode_has_no_checklist_or_frozen_scope(tmp_path):
    run, _ = fixtures.make_run.__wrapped__(tmp_path)()
    assert "review_scope_sha256" not in harness.read(run / "config.json")
    assert "review_scope" not in harness.read(run / "jobs/method-0/input.json")


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["drop", "narrow", "preserve"])
async def test_continuation_keeps_structured_scope_and_rebuilds_independent_checklist(
    tmp_path, change
):
    run, atlas, _, scope = scoped_case(tmp_path)
    await harness.execute(run, worker=CoverageWorker())
    destination = run.with_name("continuation")
    scope_path = atlas / "next_scope.json"
    next_scope = deepcopy(scope)
    if change == "narrow":
        next_scope["included_branches"] = []
    harness.write(scope_path, {"format_version": SCOPE_FORMAT,
                               "methods": {"method-0": next_scope}})
    options = dict(method_ids=["method-0"], source_only=True,
                   prepared_sources=atlas / "prepared.json", continue_from=run,
                   review_scope=None if change == "drop" else scope_path, max_attempts=1)
    if change != "preserve":
        with pytest.raises(ValueError, match="scope"):
            harness.initialize(destination, atlas, **options)
        assert not destination.exists()
        return
    harness.initialize(destination, atlas, **options)
    worker = CoverageWorker()
    assert (await harness.execute(destination, worker=worker))["counts"] == {"accepted": 1}
    assert [call[1] for call in worker.calls] == [
        "source_checklist", "extractor", "independent_verifier"]
    assert str(run / "jobs/method-0/002_extractor") not in worker.calls[0][3]
    assert str(run / "jobs/method-0/003_independent_verifier") not in worker.calls[0][3]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_sources", [False, True])
async def test_opt_in_checklist_reuse_requires_identical_sources_and_keeps_fresh_review(
    tmp_path, changed_sources
):
    run, atlas, _, _ = scoped_case(tmp_path)
    await harness.execute(run, worker=CoverageWorker())
    packet_path = atlas / "prepared.json"
    if changed_sources:
        packet = harness.read(packet_path)
        extra = atlas / "papers/additional.txt"
        extra.write_text("Synthetic additional source, not a scientific finding.")
        packet["methods"]["method-0"].append({"source_id": "extra-source",
            "title": "Additional document", "url": "https://example.org/additional",
            "files": [{"path": str(extra), "sha256": harness.sha(extra),
                       "role": "source_document"}]})
        harness.write(packet_path, packet)
    destination = run.with_name("reuse-trial")
    harness.initialize(destination, atlas, method_ids=["method-0"], source_only=True,
                       prepared_sources=packet_path, continue_from=run,
                       review_scope=run / "review_scope.json", reuse_source_checklist=True)
    job = harness.read(destination / "state.json")["jobs"]["method-0"]
    if not changed_sources:
        original = harness.read(run / "state.json")["jobs"]["method-0"]
        assert job["source_checklist_sha256"] == original["source_checklist_sha256"]
        assert job["seed"]["source_checklist"]["scope"].endswith(
            "not executed again in the destination run")
    else:
        assert not job.get("source_checklist")
    worker = CoverageWorker()
    assert (await harness.execute(destination, worker=worker))["counts"] == {"accepted": 1}
    assert [c[1] for c in worker.calls] == (
        ["source_checklist", "extractor", "independent_verifier"] if changed_sources else
        ["extractor", "independent_verifier"])
    assert "independent source-to-record checklist" not in worker.calls[-2][3].lower()
    assert harness.export_accepted(destination)["profiles"] == 1
    if not changed_sources:
        original_path = Path(job["seed"]["source_checklist"]["path"])
        original_path.write_text(original_path.read_text() + " ")
        with pytest.raises(ValueError, match="Pinned continuation artifact changed"):
            harness.export_accepted(destination)
