"""Schedule bounded extraction/review stages, recover attempts and export accepted records.

Run setup, durable state and review gates live in their own modules.
Imported aliases retain the existing harness API for callers and archived runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

from .codex_worker import DEFAULT_EFFORT as DEFAULT_EFFORT
from .codex_worker import DEFAULT_MODEL as DEFAULT_MODEL
from .codex_worker import CodexWorker, assess_archived_attempt
from .prompts import build_prompt

# Compatibility exports; new callers can import their owning module directly.
from .review_gate import _source_checklist_path as _source_checklist_path
from .review_gate import review_policy as review_policy
from .review_gate import validate_job_review as validate_job_review
from .review_gate import validation_options as validation_options
from .run_setup import _source_only_paper_lead as _source_only_paper_lead
from .run_setup import initialize as initialize
from .run_state import _pid_alive as _pid_alive
from .run_state import check_frozen as check_frozen
from .run_state import now as now
from .run_state import progress as progress
from .run_state import read as read
from .run_state import run_lock as run_lock
from .run_state import sha as sha
from .run_state import summarize as summarize
from .run_state import write as write
from .scaffolds import make_checklist_scaffold, make_extraction_scaffold, make_fresh_review_scaffold
from .validation import EVIDENCE_POLICY as EVIDENCE_POLICY
from .validation import (
    _profile_sources,
    canonical_hash,
    function_coverage_advice,
    validate_extraction,
)
from .validation import validate_review as validate_review

ACCEPTED = {"accepted", "accepted_with_explicit_gaps"}
TERMINAL = {"accepted", "research_needed", "failed"}
ARTIFACTS = {"extractor": "extraction.json", "independent_verifier": "review.json",
             "source_checklist": "checklist.json"}


def _check_prior_workers(state: dict) -> None:
    for job in state["jobs"].values():
        for attempt in job["attempts"]:
            directory = Path(attempt["directory"])
            started = directory / "started.json"
            if started.exists() and not (directory / "result.json").exists():
                pid = read(started).get("pid")
                if _pid_alive(pid):
                    raise ValueError(f"Prior worker PID {pid} may still be running; "
                                     "refusing a duplicate attempt")


def _cohort(state: dict, max_jobs: int | None) -> list[tuple[str, dict]]:
    if max_jobs is not None and max_jobs < 1:
        raise ValueError("max_jobs must be positive")
    # The cap applies before terminal jobs are filtered, so resuming a pilot
    # cannot admit new methods merely because earlier methods were accepted.
    return list(state["jobs"].items())[:max_jobs]


def _apply_review(job: dict, review: Path, config: dict) -> None:
    job["review"] = str(review)
    job["review_sha256"] = sha(review)
    verdict = read(review)
    # Preserve the matched pair before a correction replaces job.extraction.
    # Legacy reviews without a recorded policy remain usable as feedback but
    # cannot supply reusable checks under an assumed policy.
    attempt = next((a for a in job["attempts"] if Path(a["directory"]) == review.parent), {})
    if job.get("extraction"):
        job["review_baseline"] = {
            "extraction": {"path": job["extraction"], "sha256": job["extraction_sha256"]},
            "review": {"path": str(review), "sha256": sha(review)},
            "policy": attempt.get("review_policy")}
    job.pop("last_error", None)
    if verdict["outcome"] in ACCEPTED:
        job["status"] = "accepted"
    else:
        # Retain the complete review even when no correction can be queued.
        job["feedback"] = verdict
        # Validated error prose is the correction contract. Gaps alone do not
        # justify repeating extraction, regardless of the nonaccepting label.
        if not verdict["errors"] or job["revision"] >= config["max_revisions"]:
            job["status"] = "research_needed"
        else:
            job["revision"] += 1
            job["status"] = "needs_revision"


def _timeout_critique_transport(directory: Path, attempt: dict, job: dict,
                                config: dict) -> dict | None:
    """Identify a stopped host-timed-out draft; never establish review completion."""
    from .codex_worker import _MAX_EVENT_BYTES, _EventState
    from .inspection import _process

    result, started, command = (read(directory / name) for name in
                                ("result.json", "started.json", "command.json"))
    timeout = config["timeout_seconds"]
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0:
        return None
    error = f"Codex exceeded the {timeout:g}-second attempt timeout"
    if (job.get("status") != "failed"
            or not isinstance(result, dict) or type(result.get("exit_code")) is not int
            or result["exit_code"] != 124 or result.get("error") != error
            or job.get("last_error") != [error]
            or not isinstance(result.get("thread_id"), str) or not result["thread_id"]
            or not isinstance(result.get("usage"), dict)
            or type(result.get("elapsed_seconds")) not in {int, float}
            or not math.isfinite(result["elapsed_seconds"])
            or result["elapsed_seconds"] < timeout
            or not isinstance(attempt.get("finished_at"), str)
            or not isinstance(started, dict) or started.get("spawned") is not True
            or type(started.get("pid")) is not int or not 0 < started["pid"] < 2**31
            or not isinstance(started.get("process_start_identity"), dict)
            or not started["process_start_identity"]
            or not isinstance(command, dict)
            or started.get("cwd") != str(directory) or command.get("cwd") != str(directory)):
        return None
    argv = command.get("argv")
    if not isinstance(argv, list) or not argv or not all(
            isinstance(value, str) and value for value in argv):
        return None
    # Reuse the native worker's pure argument builder; no process is started.
    expected = CodexWorker(argv[0], model=config["model"], effort=config["effort"],
        web_search=config.get("prepared_sources_sha256") is None,
        network_access=config.get("prepared_sources_sha256") is None)._command(directory)
    if argv != expected or (attempt.get("prompt_sha256") is not None
                            and attempt["prompt_sha256"] != sha(directory / "prompt.txt")):
        return None
    finished = datetime.fromisoformat(attempt["finished_at"])
    begin = datetime.fromisoformat(started["started_at"])
    if (finished.tzinfo is None or begin.tzinfo is None or finished < begin
            or _process(started)["status"] != "dead"):
        return None
    state = _EventState()
    with (directory / "events.jsonl").open("rb") as handle:
        while raw := handle.readline(_MAX_EVENT_BYTES + 1):
            if len(raw) > _MAX_EVENT_BYTES:
                return None
            state.record(raw)
    if (state.invalid_lines or state.failed_turn or state.unresolved_error
            or state.thread_id != result["thread_id"] or state.usage != result["usage"]):
        return None
    return {"policy": "terminal-timeout-correction-leads/1.0",
            "kind": "terminal_timeout_saved_draft", "exit_code": 124,
            "host_finished_at": attempt["finished_at"], "thread_id": state.thread_id,
            "completed_turns": state.completed_turns, "unfinished_turn": state.turn_in_progress,
            "final_response_present": bool((directory / "final.txt").stat().st_size),
            "worker_observed_dead": True, "review_transport_complete": False,
            "certified_review": False, "reusable_checks": False}


def _read_provisional_critique(run: Path, config: dict, job: dict, method_id: str,
                              extraction: Path, implementation: dict, *,
                              allow_timeout_correction_leads: bool = False) -> tuple | None:
    """A rejected negative critique may request correction, never certify a record.

    Transport, byte identity and candidate identity must be known. The critique's
    scientific assertions remain unvalidated leads. Its checks cannot be reused;
    correction spends the existing revision budget and requires a fresh review.
    """
    attempt = next((a for a in reversed(job["attempts"])
                    if a.get("role") == "independent_verifier"
                    and a.get("revision") == job["revision"]), None)
    if attempt is None:
        return None
    directory = Path(attempt["directory"]).resolve()
    if directory.parent != run / "jobs" / method_id:
        return None
    output = directory / "review.json"
    names = ("review.json", "result.json", "events.jsonl", "final.txt", "validation.json")
    try:
        hashes = {name: sha(directory / name) for name in names}
        if (attempt.get("artifact_sha256") != hashes["review.json"]
                or sha(extraction) != job.get("extraction_sha256")):
            return None
        transport = None
        if not assess_archived_attempt(directory).complete:
            if not allow_timeout_correction_leads:
                return None
            extra_names = ("started.json", "command.json", "prompt.txt")
            names += extra_names
            if any((directory / name).resolve().parent != directory for name in names):
                return None
            hashes.update({name: sha(directory / name) for name in extra_names})
            transport = _timeout_critique_transport(directory, attempt, job, config)
            if transport is None:
                return None
        options = validation_options(run, config)
        if validate_extraction(extraction, method_id, (Path(config["atlas"]), run), **options):
            return None
        profile = read(extraction)["profiles"][0]
        critique = read(output)
        if (not isinstance(critique, dict)
                or critique.get("format_version") != "protocol_component_review/1.0"
                or critique.get("reviewer_role") != "independent_verifier"
                or critique.get("profile_id") != profile["profile_id"]
                or critique.get("profile_sha256") != canonical_hash(profile)
                or critique.get("outcome") not in {"needs_revision", "unresolved_source_gap"}):
            return None
        findings = critique.get("errors")
        modules = {m["module_id"] for m in profile["modules"]}
        if (not isinstance(findings, list) or not findings
                or not all(isinstance(f, dict) and isinstance(f.get("finding"), str)
                           and f["finding"].strip() for f in findings)
                or not any(f.get("module_id") in modules for f in findings)):
            return None
        errors = validate_job_review(run, config, job, output, extraction, method_id)
        if not errors and transport is None:  # Completed valid reviews use the ordinary path.
            return None
        if hashes != {name: sha(directory / name) for name in names}:
            return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    record = {"recorded_at": now(), "method_id": method_id,
              "attempt": str(directory), "revision": job["revision"],
              "archive_sha256": hashes,
              "extraction": {"path": str(extraction), "sha256": sha(extraction)},
              "integrity_errors": errors, "implementation_hashes": implementation,
              "scope": "Unvalidated correction leads only; not an accepted review, "
                       "not reusable checks, not evidence for graph promotion"}
    if transport is not None:
        record["transport_provenance"] = transport
    return record, critique


def _queue_provisional_revision(run: Path, config: dict, job: dict, method_id: str,
                                extraction: Path, implementation: dict) -> dict | None:
    if job["revision"] >= config["max_revisions"]:
        return None
    found = _read_provisional_critique(run, config, job, method_id, extraction, implementation)
    if found is None:
        return None
    record, critique = found
    record["next_revision"] = job["revision"] + 1
    output = Path(record["attempt"]) / "review.json"
    hashes = record["archive_sha256"]
    errors = record["integrity_errors"]
    folder = run / "provisional_critiques"
    folder.mkdir(exist_ok=True)
    receipt = folder / f"{method_id}-{len(job.get('provisional_critiques', [])) + 1:03d}.json"
    with receipt.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    job.setdefault("provisional_critiques", []).append({"record": str(receipt),
                                                        "sha256": sha(receipt)})
    job["feedback"] = {
        "prior_feedback": job.get("feedback"), "integrity_errors": errors,
        "unvalidated_review": {"path": str(output), "sha256": hashes["review.json"]},
        "unvalidated_review_findings": {key: critique.get(key, []) for key in
                                         ("errors", "remaining_gaps", "sources")},
        "instruction": "This completed critique failed integrity validation. Treat its "
                       "findings as leads, reopen the original sources before acting, and "
                       "repair the source registration and scientifically justified fields "
                       "in a NEW extraction. Preserve the frozen scope and source identities. "
                       "Do not assume these findings or any reviewer checks are certified. "
                       "Explain source-backed disagreement. A fresh full review is required."}
    job["revision"] += 1
    job["status"] = "needs_revision"
    job["force_full_review_revision"] = job["revision"]
    return record


def _recover_completed(run: Path, state: dict, config: dict, cohort: list,
                       implementation: dict) -> list[dict]:
    """Revalidate completed archives only; caller holds the orchestrator lock."""
    roots = (Path(config["atlas"]), run)
    validation_kwargs = validation_options(run, config)
    recovered = []

    def candidate(mid, job, role, extraction=None):
        artifact_name = ARTIFACTS[role]
        for attempt in reversed(job["attempts"]):
            if attempt["role"] != role or attempt["revision"] != job["revision"]:
                continue
            directory = Path(attempt["directory"]).resolve()
            if directory.parent != run / "jobs" / mid:
                raise ValueError("Attempt directory outside its frozen job: " + mid)
            output = directory / artifact_name
            names = ["result.json", "events.jsonl", "final.txt", artifact_name]
            names += [n for n in ("validation.json", "prompt.txt", "command.json", "started.json",
                                 "review_plan.json", "review_scaffold.json",
                                 "extraction_scaffold.json", "checklist_scaffold.json")
                      if (directory / n).exists()]
            if not all((directory / name).is_file() for name in names):
                continue
            hashes = {name: sha(directory / name) for name in names}
            if attempt.get("artifact_sha256", hashes[artifact_name]) != hashes[artifact_name]:
                continue
            assessment = assess_archived_attempt(directory)
            if not assessment.complete:
                continue
            if "native_archive_sha256" in attempt:
                from .accepted_transport import native_archive_hashes
                if native_archive_hashes(directory) != attempt["native_archive_sha256"]:
                    continue
            if role == "source_checklist":
                from .coverage import validate_checklist
                errors = validate_checklist(output, mid, roots,
                    validation_kwargs["review_scopes"][mid],
                    evidence_policy=config.get("evidence_policy"),
                    prepared_sources=(validation_kwargs.get("prepared_sources")
                        if config.get("independent_source_renditions") else None))
            else:
                errors = (validate_extraction(output, mid, roots, **validation_kwargs)
                          if role == "extractor" else
                          validate_job_review(run, config, job, output, extraction, mid))
            if errors:
                continue
            if hashes != {name: sha(directory / name) for name in names}:
                raise ValueError("Attempt archives changed during recovery: " + mid)
            return attempt, output, hashes
        return None

    def record(mid, job, role, found, previous_status):
        attempt, output, hashes = found
        native_hashes = None
        if attempt.get("worker_transport") == "codex-exec-json/1.0":
            from .accepted_transport import native_archive_hashes
            native_hashes = native_archive_hashes(Path(attempt["directory"]))
            if ("native_archive_sha256" in attempt
                    and attempt["native_archive_sha256"] != native_hashes):
                raise ValueError("Native archive differs from original host completion hashes")
        record = {"recovered_at": now(), "method_id": mid, "role": role,
                  "revision": attempt["revision"], "attempt": attempt["directory"],
                  "artifact": str(output), "archive_sha256": hashes,
                  "previous_status": previous_status, "status": job["status"],
                  "artifact_hash_previously_checkpointed": "artifact_sha256" in attempt,
                  "artifact_hash_scope": (
                      "Matches hash recorded when the attempt completed"
                      if "artifact_sha256" in attempt else
                      "Legacy archive: original output hash was not recorded; "
                      "this recovery establishes the artifact hash baseline"),
                  "implementation_hashes": implementation, "validation_errors": [],
                  "scope": "Completed transport and structural/source integrity; "
                           "scientific acceptance requires an independent accepted review"}
        folder = run / "recoveries"
        folder.mkdir(exist_ok=True)
        number = len(list(folder.glob("*.json"))) + 1
        while (path := folder / f"{number:06d}.json").exists():
            number += 1
        with path.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        job.setdefault("recoveries", []).append({"record": str(path), "sha256": sha(path)})
        attempt["artifact_sha256"] = hashes[output.name]
        if native_hashes is not None and "native_archive_sha256" not in attempt:
            attempt["native_archive_sha256"] = native_hashes
            attempt["native_archive_pin_origin"] = "completed_archive_recovery"
        recovered.append(record)
        state["updated_at"] = now()
        write(run / "state.json", state)

    for mid, job in cohort:
        if job["status"] in {"accepted", "research_needed"}:
            continue
        if (config.get("review_scope_sha256") is not None
                and config.get("source_checklist_policy", "independent") == "independent"
                and not job.get("source_checklist")):
            found = candidate(mid, job, "source_checklist")
            if not found:
                continue
            previous_status = job["status"]
            job.update(source_checklist=str(found[1]), source_checklist_sha256=sha(found[1]),
                       status="pending")
            _source_checklist_path(run, config, job, mid)
            record(mid, job, "source_checklist", found, previous_status)
        found = candidate(mid, job, "extractor")
        if found:
            _, extraction, _ = found
            if (job.get("extraction") != str(extraction)
                    or job["status"] in {"needs_revision", "extracting"}):
                previous_status = job["status"]
                job.update(extraction=str(extraction), extraction_sha256=sha(extraction),
                           review=None, status="extracted")
                job.pop("review_sha256", None)
                job.pop("last_error", None)
                record(mid, job, "extractor", found, previous_status)
        elif (job.get("extraction") and job["status"] not in {"needs_revision", "extracting"}
              and any(a["role"] == "extractor" and a["revision"] == job["revision"]
                      and Path(a["directory"]) == Path(job["extraction"]).parent
                      for a in job["attempts"])):
            extraction = Path(job["extraction"])
        else:
            continue
        review = candidate(mid, job, "independent_verifier", extraction)
        if review:
            previous_status = job["status"]
            _apply_review(job, review[1], config)
            record(mid, job, "independent_verifier", review, previous_status)
        elif provisional := _queue_provisional_revision(
                run, config, job, mid, extraction, implementation):
            recovered.append({**provisional, "role": "provisional_critique",
                              "status": job["status"]})
            state["updated_at"] = now()
            write(run / "state.json", state)
    return recovered


def recover_completed(run: Path, *, max_jobs: int | None = None) -> dict:
    """Recover validated completed work without launching any worker process."""
    run = run.resolve()
    with run_lock(run):
        state, config = read(run / "state.json"), read(run / "config.json")
        check_frozen(run, state, config)
        cohort = _cohort(state, max_jobs)
        _check_prior_workers(state)
        implementation = {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")}
        recovered = _recover_completed(run, state, config, cohort, implementation)
        state["updated_at"] = now()
        write(run / "state.json", state)
    return {**summarize(run), "recovered_artifacts": recovered}


async def execute(run: Path, *, worker=None, max_jobs: int | None = None,
                  retry_failed: bool = False) -> dict:
    run = run.resolve()
    with run_lock(run):
        state, config = read(run / "state.json"), read(run / "config.json")
        check_frozen(run, state, config)
        if config.get("regression_only"):
            raise ValueError(
                "Regression trials use their bounded trial runner, not the atlas queue")
        cohort = _cohort(state, max_jobs)
        _check_prior_workers(state)
        contracts = read(run / "contracts.json")
        roots = (Path(config["atlas"]), run)
        validation_kwargs = validation_options(run, config)
        extractor_slots = asyncio.Semaphore(config["workers"] - config["reviewers"])
        reviewer_slots = asyncio.Semaphore(config["reviewers"])
        discover_sources = (config.get("allow_source_discovery", False)
                            or config.get("prepared_sources_sha256") is None)
        worker = worker or CodexWorker(model=config["model"], effort=config["effort"],
                                      timeout_seconds=config["timeout_seconds"],
                                      web_search=discover_sources,
                                      network_access=discover_sources)
        implementation = {p.name: sha(p) for p in Path(__file__).parent.glob("*.py")}
        _recover_completed(run, state, config, cohort, implementation)
        state.setdefault("invocations", []).append({"started_at": now(),
            "implementation_hashes": implementation, "max_jobs": max_jobs,
            "retry_failed": retry_failed, "cohort_method_ids": [mid for mid, _ in cohort]})
        infrastructure_failures = 0

        class InfrastructurePause(Exception):
            pass

        def checkpoint():
            state["updated_at"] = now()
            write(run / "state.json", state)

        async def invoke(mid, job, role, extraction=None, feedback=None):
            nonlocal infrastructure_failures
            semaphore = extractor_slots if role == "extractor" else reviewer_slots
            history = [a for a in job["attempts"] if a["role"] == role
                       and a["revision"] == job["revision"]]
            prior = [a for a in history if a.get("epoch", 0) == job.get("epoch", 0)]
            scientific_feedback = feedback
            artifact_name = ARTIFACTS[role]
            failure_history = []
            for previous in history:
                validation = Path(previous["directory"]) / "validation.json"
                if validation.is_file() and (errors := read(validation).get("errors")):
                    failure_history.append({"previous_attempt": previous["directory"],
                                            "integrity_errors": errors})

            def repair_feedback(errors, directory):
                artifact = directory / artifact_name
                failure = {"previous_attempt": str(directory), "integrity_errors": errors}
                if not failure_history or failure_history[-1] != failure:
                    failure_history.append(failure)
                details = {"prior_feedback": scientific_feedback, "integrity_errors": errors,
                           "previous_attempt": str(directory),
                           "attempt_failure_history": list(failure_history)}
                if artifact.is_file():
                    details.update(repair_artifact=str(artifact), repair_instruction=(
                        "Read the saved artifact and repair the reported errors in a NEW output. "
                        "Preserve correct source-scoped content and original scientific feedback. "
                        "Reuse verified source files; additional research is needed only for "
                        "unresolved scientific findings or evidence gaps. Historical failures "
                        "are context; recheck which still affect the current draft."))
                    if role == "independent_verifier":
                        try:
                            previous_review = read(artifact)
                            details["unvalidated_review_findings"] = {
                                key: previous_review.get(key, [])
                                for key in ("errors", "remaining_gaps", "sources")}
                            details["finding_instruction"] = (
                                "Reassess these new findings as well as the pinned baseline's "
                                "findings. This draft failed integrity checks, so verify its "
                                "source records and scientific assertions before relying on it. "
                                "Explain whether each finding remains open or was resolved.")
                        except (ValueError, OSError, AttributeError):
                            pass  # Preserve the raw artifact path for format repair.
                return details

            # A renewed attempt budget or an intervening cancellation must not
            # erase the last actionable validation report or saved draft.
            if failure_history:
                failure = failure_history[-1]
                feedback = repair_feedback(failure["integrity_errors"],
                                           Path(failure["previous_attempt"]))
            if role == "extractor":
                extraction = next((Path(a["directory"]) / artifact_name
                                   for a in reversed(history)
                                   if (Path(a["directory"]) / artifact_name).is_file()), extraction)
            for retry in range(len(prior), config["max_attempts"]):
                async with semaphore:
                    if infrastructure_failures >= 3:
                        raise InfrastructurePause("Three consecutive worker process failures; "
                                                  "remaining jobs left resumable")
                    attempt = run / "jobs" / mid / f"{len(job['attempts']) + 1:03d}_{role}"
                    task_path = run / "jobs" / mid / "input.json"
                    if sha(task_path) != job["input_sha256"]:
                        raise ValueError("Frozen method input changed: " + mid)
                    task = read(task_path)
                    shared_index = run / "shared_sources" / "index.json"
                    index_bytes = shared_index.read_bytes() if shared_index.is_file() else None
                    if index_bytes is not None and not isinstance(json.loads(index_bytes), dict):
                        raise ValueError("Shared source index must be a JSON object")
                    evidence_index = (attempt / "shared_sources.json"
                                      if index_bytes is not None else shared_index)
                    previous_attempts = tuple(Path(a["directory"]) for a in job["attempts"]
                                              if a["role"] != "source_checklist")
                    attempt.mkdir(parents=True, exist_ok=True)
                    policy = review_policy(config, job, implementation,
                                           hashlib.sha256(index_bytes).hexdigest()
                                           if index_bytes is not None else None)
                    plan_path = None
                    if (role == "independent_verifier" and config.get("incremental_reviews")
                            and (baseline := job.get("review_baseline"))):
                        from .incremental import build_review_plan, make_review_scaffold
                        for pin in (baseline["extraction"], baseline["review"]):
                            if sha(Path(pin["path"])) != pin["sha256"]:
                                raise ValueError("Pinned review baseline changed")
                        baseline_errors = validate_job_review(
                            run, config, job, Path(baseline["review"]["path"]),
                            Path(baseline["extraction"]["path"]), mid)
                        if baseline_errors:
                            raise ValueError("Invalid review baseline: " + str(baseline_errors))
                        plan = build_review_plan(
                            Path(baseline["extraction"]["path"]),
                            Path(baseline["review"]["path"]), extraction, mid, roots,
                            policy=policy, baseline_policy=(None if job.get(
                                "force_full_review_revision") == job["revision"]
                                else baseline.get("policy")),
                            **validation_kwargs)
                        plan_path = attempt / "review_plan.json"
                        write(plan_path, plan)
                        write(attempt / "review_scaffold.json", make_review_scaffold(plan))
                    scaffold_path = None
                    if role == "source_checklist":
                        scaffold_path = attempt / "checklist_scaffold.json"
                        write(scaffold_path, make_checklist_scaffold(task))
                    elif role == "extractor" and extraction is None:
                        scaffold_path = attempt / "extraction_scaffold.json"
                        write(scaffold_path, make_extraction_scaffold(task, attempt / "prompt.txt"))
                    elif role == "independent_verifier" and plan_path is None:
                        scaffold_path = attempt / "review_scaffold.json"
                        write(scaffold_path, make_fresh_review_scaffold(
                            extraction, mid, roots, evidence_policy=config.get("evidence_policy"),
                            source_checklist=_source_checklist_path(run, config, job, mid),
                            **({"source_checklist_policy": "reviewer_source_audit",
                                "prepared_sources": validation_kwargs["prepared_sources"]}
                               if config.get("source_checklist_policy") ==
                               "reviewer_source_audit" else {}),
                            require_connection_reviews=bool(config.get("connection_review_policy")),
                            require_scientific_detail_reviews=bool(config.get("scientific_detail_policy"))))
                    prompt = build_prompt(role, attempt, task, contracts,
                                          extraction=extraction, feedback=feedback,
                                          evidence_index=evidence_index,
                                          prior_attempts=previous_attempts,
                                          review_plan=plan_path,
                                          source_checklist=(Path(job["source_checklist"])
                                              if job.get("source_checklist") else None),
                                          runtime_timeout_seconds=config["timeout_seconds"])
                    if role == "extractor" and scaffold_path is not None:
                        # The prompt can describe the existing starting file;
                        # bind its actual bytes once composition is complete.
                        write(scaffold_path, make_extraction_scaffold(
                            task, attempt / "prompt.txt", prompt))
                    job["attempts"].append({"role": role, "directory": str(attempt),
                                            "started_at": now(), "revision": job["revision"],
                                            "epoch": job.get("epoch", 0),
                                            "implementation_hashes": implementation,
                                            "worker_transport": ("codex-exec-json/1.0"
                                                if type(worker) is CodexWorker
                                                else "injected-worker/1.0"),
                                            "review_policy": policy,
                                            "evidence_index": str(evidence_index),
                                            "prior_attempt_directories": [
                                                str(p) for p in previous_attempts]})
                    if scaffold_path is not None:
                        job["attempts"][-1]["artifact_scaffold"] = {
                            "path": str(scaffold_path), "sha256": sha(scaffold_path)}
                    if plan_path is not None:
                        job["attempts"][-1]["review_plan"] = {
                            "path": str(plan_path), "sha256": sha(plan_path)}
                    job["status"] = ({"extractor": "extracting",
                                      "independent_verifier": "verifying",
                                      "source_checklist": "preparing_checklist"}[role])
                    # Archived validation/feedback preserve prior failures. The
                    # live status must not label a new attempt with an old error.
                    job.pop("last_error", None)
                    checkpoint()
                    if index_bytes is not None:
                        attempt.mkdir(parents=True, exist_ok=True)
                        with evidence_index.open("xb") as handle:
                            handle.write(index_bytes)
                        job["attempts"][-1]["evidence_index_sha256"] = sha(evidence_index)
                        checkpoint()
                    progress(run, {"method": mid, "status": job["status"],
                                   "attempt": len(job["attempts"])})
                    result = await worker.run(prompt, attempt)
                    job["attempts"][-1]["finished_at"] = now()
                    output = attempt / artifact_name
                    if output.is_file():
                        # Pin even rejected outputs before any future recovery can
                        # reconsider them under corrected transport/validation code.
                        job["attempts"][-1]["artifact_sha256"] = sha(output)
                    errors = ([result.error or f"process exit {result.exit_code}"]
                              if result.error or result.exit_code else [])
                    if errors:
                        infrastructure_failures += 1
                    else:
                        infrastructure_failures = 0
                    if not errors:
                        if role == "source_checklist":
                            from .coverage import validate_checklist
                            errors = validate_checklist(output, mid, roots,
                                validation_kwargs["review_scopes"][mid],
                                evidence_policy=config.get("evidence_policy"),
                                prepared_sources=(validation_kwargs.get("prepared_sources")
                                    if config.get("independent_source_renditions") else None))
                        else:
                            errors = (validate_extraction(output, mid, roots, **validation_kwargs)
                                      if role == "extractor" else validate_job_review(
                                          run, config, job, output, extraction, mid))
                    write(attempt / "validation.json", {"errors": errors,
                          "scope": "Structural/source integrity only; not scientific validation"})
                    if not errors:
                        if type(worker) is CodexWorker:
                            from .accepted_transport import native_archive_hashes
                            job["attempts"][-1]["native_archive_sha256"] = (
                                native_archive_hashes(attempt))
                            job["attempts"][-1]["native_archive_pin_origin"] = "worker_return"
                        return output
                    if plan_path is not None and not result.error and not result.exit_code:
                        # A failed reuse contract cannot be repaired by repeatedly
                        # asserting applicability. The next bounded attempt checks
                        # the whole record and retains all earlier findings.
                        job["force_full_review_revision"] = job["revision"]
                    job["last_error"] = errors
                    checkpoint()
                    feedback = repair_feedback(errors, attempt)
                    if role == "extractor" and output.is_file():
                        extraction = output
                if retry + 1 < config["max_attempts"]:
                    await asyncio.sleep(min(2 ** retry, 10))
            return None

        async def process(mid, job):
            try:
                if config.get("review_scope_sha256") is not None:
                    if (config.get("source_checklist_policy", "independent") == "independent"
                            and not job.get("source_checklist")):
                        checklist = await invoke(mid, job, "source_checklist")
                        if checklist is None:
                            job["status"] = "failed"
                            checkpoint()
                            return
                        job.update(source_checklist=str(checklist),
                                   source_checklist_sha256=sha(checklist), status="pending")
                        checkpoint()
                    _source_checklist_path(run, config, job, mid)
                extraction = Path(job["extraction"]) if job.get("extraction") else None
                if extraction and validate_extraction(extraction, mid, roots, **validation_kwargs):
                    raise ValueError("Previously checkpointed extraction was changed: " + mid)
                while True:
                    if extraction is None or job["status"] in {"needs_revision", "extracting"}:
                        candidate, feedback = extraction, job.get("feedback")
                        if candidate is None and (seed := job.get("seed")):
                            from .continuation import validate_seed
                            validate_seed(seed)
                            candidate = Path(seed["extraction"]["path"])
                            feedback = seed["feedback"]
                        extraction = await invoke(mid, job, "extractor", candidate, feedback)
                        if extraction is None:
                            job["status"] = "failed"
                            break
                        job["extraction"] = str(extraction)
                        job["extraction_sha256"] = sha(extraction)
                        job["status"] = "extracted"
                        checkpoint()
                    review = await invoke(mid, job, "independent_verifier", extraction)
                    if review is None:
                        if _queue_provisional_revision(
                                run, config, job, mid, extraction, implementation):
                            checkpoint()
                            continue
                        job["status"] = "failed"
                        break
                    _apply_review(job, review, config)
                    if job["status"] in {"accepted", "research_needed"}:
                        break
                    checkpoint()
            except asyncio.CancelledError:
                # The worker kills its own process group; this checkpoint resumes its stage.
                job["interrupted_at"] = now()
                checkpoint()
                raise
            except InfrastructurePause as exc:
                job["last_error"] = [str(exc)]
                if job["status"] == "verifying":
                    job["status"] = "extracted"
                elif job["status"] == "extracting":
                    job["status"] = "needs_revision" if job.get("extraction") else "pending"
                elif job["status"] == "preparing_checklist":
                    job["status"] = "pending"
            except Exception as exc:
                job["status"] = "failed"
                job["last_error"] = [f"{type(exc).__name__}: {exc}"]
            checkpoint()
            progress(run, {"method": mid, "status": job["status"]})

        selected = []
        for mid, job in cohort:
            if job["status"] not in TERMINAL or (retry_failed and job["status"] == "failed"):
                selected.append((mid, job))
        for _, job in selected:
            if job["status"] == "failed":
                last_role = job["attempts"][-1]["role"] if job["attempts"] else "extractor"
                job["status"] = "extracting" if last_role == "extractor" else "extracted"
                job["epoch"] = job.get("epoch", 0) + 1
            elif retry_failed:
                # An interruption can leave the final allowed attempt archived
                # before its terminal failure checkpoint. Explicit retry grants
                # the same bounded budget here as for an already-failed job.
                role = ("extractor" if not job.get("extraction") or job["status"] in
                        {"needs_revision", "extracting"} else "independent_verifier")
                if (config.get("review_scope_sha256") is not None
                        and config.get("source_checklist_policy", "independent") == "independent"
                        and not job.get("source_checklist")):
                    role = "source_checklist"
                consumed = sum(a["role"] == role and a["revision"] == job["revision"]
                               and a.get("epoch", 0) == job.get("epoch", 0)
                               for a in job["attempts"])
                if consumed >= config["max_attempts"]:
                    job["epoch"] = job.get("epoch", 0) + 1
        tasks = [asyncio.create_task(process(mid, job)) for mid, job in selected]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        checkpoint()
    return summarize(run)


def reviewed_source_registrations(artifact: dict, verdict: dict) -> list[tuple[str, dict]]:
    """Select source definitions from an already validated current review.

    Reviewer registers, reading checks, provenance and retrieval diagnostics alone
    are not scientific references. Include additional sources only when the typed
    review proof cites them, using the reference traversal from review validation.
    """
    registered = {source["source_id"]: source for source in artifact["sources"]}
    additional = verdict.get("sources", [])
    for source in additional:
        sid = source["source_id"]
        if sid in registered:
            raise ValueError("Reviewer source_id collides with extraction source register: " + sid)
        registered[sid] = source
    administrative = {"sources", "source_checks", "review_provenance", "prompt_provenance",
                      "retrieval_diagnostics"}
    scientific_review = {key: value for key, value in verdict.items()
                         if key not in administrative}
    required = _profile_sources(scientific_review, registered, "review")
    return [("extractor", source) for source in artifact["sources"]] + [
        ("independent_verifier", source) for source in additional
        if source["source_id"] in required]


def export_accepted(run: Path) -> dict:
    """Produce an immutable-review-linked import bundle; never modify the live atlas."""
    run = run.resolve()
    with run_lock(run):
        state, config = read(run / "state.json"), read(run / "config.json")
        check_frozen(run, state, config)
        if config.get("regression_only"):
            raise ValueError("Regression trial artifacts cannot be exported into the atlas")
        roots = (Path(config["atlas"]), run)
        validation_kwargs = validation_options(run, config)
        sources, profiles, provenance, evolution, diagnostics = {}, [], [], [], []
        source_provenance = []
        for mid, job in state["jobs"].items():
            if job["status"] != "accepted":
                continue
            extraction, review = Path(job["extraction"]), Path(job["review"])
            from .followup import pending_followup_reviews
            holds = pending_followup_reviews(run, mid, extraction)
            if holds:
                raise ValueError(f"Unresolved follow-up audit blocks export for {mid}: {holds}")
            from .accepted_transport import reviewer_label, validate_accepted_transports
            transport_rows = validate_accepted_transports(job, config)
            errors = validate_job_review(run, config, job, review, extraction, mid)
            if errors or read(review)["outcome"] not in ACCEPTED:
                raise ValueError(f"Invalid acceptance for {mid}: {errors}")
            artifact, verdict = read(extraction), read(review)
            if config.get("with_evolution"):
                from .evolution import evolution_export_status
                disposition = evolution_export_status(
                    verdict, artifact, mid, extraction.parent, roots,
                    known_method_ids=validation_kwargs["known_method_ids"], require_evaluated=True)
                evolution.append({"method_id": mid, "claims": artifact["evolution_claims"],
                                  "review": str(review), "review_sha256": sha(review),
                                  "disposition": disposition})
            profile = artifact["profiles"][0]
            profile["verification"] = {"outcome": verdict["outcome"],
                "reviewer": reviewer_label(transport_rows),
                "report_path": str(review),
                "report_sha256": sha(review), "verified_profile_sha256": canonical_hash(
                    artifact["profiles"][0]),
                "scientific_scope": verdict.get("scientific_scope", {}),
                "remaining_gaps": verdict["remaining_gaps"]}
            if config.get("connection_review_policy"):
                checks = verdict["topology_check"]["connection_checks"]
                profile["verification"]["connection_review"] = {
                    "policy": config["connection_review_policy"],
                    "checked_connections": len(checks),
                    "checks_sha256": canonical_hash(checks),
                }
            if config.get("scientific_detail_policy"):
                profile["verification"]["scientific_detail_review"] = {
                    "policy": config["scientific_detail_policy"],
                    "assertion_groups": len(verdict["assertion_checks"]),
                    "assertion_checks_sha256": canonical_hash(verdict["assertion_checks"]),
                    "module_handoff_scans": len(verdict["topology_check"]["module_handoff_checks"]),
                    "module_handoff_checks_sha256": canonical_hash(
                        verdict["topology_check"]["module_handoff_checks"]),
                }
            if "incremental_review" in verdict:
                profile["verification"]["incremental_review"] = verdict["incremental_review"]
            if config.get("review_scope_sha256") is not None:
                profile["verification"].update(
                    review_scope=read(run / "review_scope.json")["methods"][mid],
                    review_scope_sha256=config["review_scope_sha256"])
                if config.get("source_checklist_policy", "independent") == "independent":
                    profile["verification"].update(
                        source_checklist_path=job["source_checklist"],
                        source_checklist_sha256=job["source_checklist_sha256"],
                        source_to_record_check=verdict["source_to_record_check"])
                else:
                    profile["verification"].update(
                        source_checklist_policy="reviewer_source_audit",
                        source_audit=verdict["coverage_check"]["source_audit"])
            profiles.append(profile)
            # Retrieval references are local to their original artifact. Keep
            # these namespaces separate across methods and extraction/review;
            # access notes must never be merged into scientific source records.
            for artifact_role, document, path in (
                ("extractor", artifact, extraction),
                ("independent_verifier", verdict, review),
            ):
                if "retrieval_diagnostics" in document:
                    diagnostics.append({
                        "method_id": mid, "profile_id": profile["profile_id"],
                        "artifact_role": artifact_role,
                        "artifact_path": str(path), "artifact_sha256": sha(path),
                        "records": document["retrieval_diagnostics"],
                    })
            for artifact_role, source in reviewed_source_registrations(artifact, verdict):
                sid = source["source_id"]
                if sid in sources and sources[sid] != source:
                    raise ValueError("Conflicting source definitions: " + sid)
                sources[sid] = source
                path = extraction if artifact_role == "extractor" else review
                source_provenance.append({
                    "method_id": mid, "source_id": sid, "artifact_role": artifact_role,
                    "artifact_path": str(path), "artifact_sha256": sha(path),
                })
            provenance.append({"method_id": mid, "extraction": str(extraction),
                               "extraction_sha256": sha(extraction), "review": str(review),
                               "review_sha256": sha(review)})
        output = {"format_version": "protocol_component_extraction/1.0",
                  "scope": "Independently reviewed source-scoped candidates; not bench validation",
                  "sources": list(sources.values()), "profiles": profiles,
                  "promotion_provenance": provenance,
                  "source_registration_provenance": source_provenance}
        if config.get("with_evolution"):
            output["evolution_records"] = evolution
        if diagnostics:
            output["retrieval_diagnostics"] = diagnostics
        write(run / "accepted_profiles.json", output)
    return {"output": str(run / "accepted_profiles.json"), "profiles": len(profiles),
            "modules": sum(len(p["modules"]) for p in profiles)}


def _completion_budget(job: dict, config: dict, artifact: Path, role: str) -> dict | None:
    """Advisory time from the current host attempt, never a completion/acceptance gate."""
    try:
        attempt = job["attempts"][-1]
        if (attempt.get("finished_at") or attempt.get("role") != role
                or Path(attempt["directory"]).resolve() != artifact.parent
                or (artifact.parent / "result.json").exists()):
            return None
        started = datetime.fromisoformat(attempt["started_at"])
        current = datetime.fromisoformat(now())
        timeout = config["timeout_seconds"]
        if (type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0
                or started.utcoffset() is None or current.utcoffset() is None):
            return None
        elapsed = (current - started).total_seconds()
        if elapsed < 0:
            return None
        remaining, reserve = max(0.0, timeout - elapsed), min(120.0, timeout * .15)
        return {
            "remaining_seconds": remaining, "finalize_reserve_seconds": reserve,
            "phase": "finalize" if remaining <= reserve else "research",
            "instruction": (
                "Reserve time to save actual findings, correct integrity errors, and finish. "
                "Once the artifact is valid and reflects the work actually completed, finish "
                "the session without cosmetic rewrites. Retain honest gaps; this timing "
                "feedback and a valid artifact do not establish scientific acceptance. "
                "The worker's hard timeout remains unchanged."),
        }
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OSError, OverflowError):
        return None


def _review_draft_progress(artifact: Path, extraction: Path,
                           source_checklist: Path | None = None) -> dict | None:
    """Count saved observations against host inputs; never grant review credit."""
    try:
        document, profile = read(artifact), read(extraction)["profiles"][0]

        def counts(expected, entries, key):
            if not isinstance(entries, list):
                entries = []
            identities = Counter(item.get(key) for item in entries
                                 if isinstance(item, dict) and isinstance(item.get(key), str))
            observed = {item[key] for item in entries if isinstance(item, dict)
                        and isinstance(item.get(key), str) and item[key] in expected
                        and identities[item[key]] == 1
                        and item.get("outcome") in {
                            "accepted", "accepted_with_explicit_gaps", "needs_revision",
                            "unresolved_source_gap", "represented", "missing", "unresolved",
                            "outside_declared_scope", "resolved"}
                        and isinstance(item.get("observations"), list)
                        and any(isinstance(note, str) and note.strip()
                                for note in item["observations"])}
            return {"expected": len(expected), "entries_with_observations": len(observed),
                    "pending_or_unrecorded": len(expected - observed)}

        result = {"scope": "Saved draft observations only; not scientific validation, "
                           "coverage approval or inherited review credit.",
                  "modules": counts({item["module_id"] for item in profile["modules"]},
                                    document.get("module_checks"), "module_id")}
        if document.get("scientific_detail_policy") == "scientific-detail/1.0":
            from .assertion_review import make_assertion_checks
            result["assertion_groups"] = counts(
                {item["assertion_id"] for item in make_assertion_checks(profile)},
                document.get("assertion_checks"), "assertion_id")
            result["module_handoff_scans"] = counts(
                {item["module_id"] for item in profile["modules"]},
                document.get("topology_check", {}).get("module_handoff_checks"), "module_id")
        if source_checklist is not None:
            checklist = read(source_checklist)
            coverage = document.get("source_to_record_check", {})
            result["checklist_items"] = counts(
                {item["item_id"] for item in checklist["items"]},
                coverage.get("item_checks"), "item_id")
            result["checklist_gaps"] = counts(
                {item["gap_id"] for item in checklist.get("remaining_gaps", [])},
                coverage.get("gap_checks"), "gap_id")
        return result
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OSError):
        return None  # The existing integrity gate reports malformed artifacts.


def _review_reference_diagnostics(artifact: Path, extraction: Path, options: dict) -> dict | None:
    """Explain row-local citation failures without altering the acceptance gate."""
    try:
        from .review_reference_diagnostics import review_reference_diagnostics
        document, original = read(artifact), read(extraction)
        profile = original["profiles"][0]
        declared = {source["source_id"]: None
                    for source in [*original.get("sources", []), *document.get("sources", [])]
                    if isinstance(source, dict) and isinstance(source.get("source_id"), str)}
        return review_reference_diagnostics(document, profile, declared,
            connections=options.get("require_connection_reviews", False),
            detail=options.get("require_scientific_detail_reviews", False))
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OSError, RecursionError):
        return None  # The authoritative gate still reports malformed inputs.


def check_artifact(run: Path, method_id: str, artifact: Path, role: str) -> dict:
    """Read-only worker self-check; uses the scheduler's exact integrity gates."""
    run, artifact = run.resolve(), artifact.resolve()
    state, config = read(run / "state.json"), read(run / "config.json")
    check_frozen(run, state, config)
    if method_id not in state["jobs"] or not artifact.is_relative_to(run / "jobs" / method_id):
        raise ValueError("Artifact must belong to its frozen method job")
    roots, options = (Path(config["atlas"]), run), validation_options(run, config)
    if role == "extractor":
        errors = validate_extraction(artifact, method_id, roots, **options)
    elif role == "independent_verifier":
        extraction = state["jobs"][method_id].get("extraction")
        if not extraction:
            raise ValueError("Review requires a checkpointed extraction")
        errors = validate_job_review(run, config, state["jobs"][method_id], artifact,
                                     Path(extraction), method_id)
    elif role == "source_checklist":
        from .coverage import validate_checklist
        if config.get("source_checklist_policy") == "reviewer_source_audit":
            raise ValueError("Separate source checklist is disabled by reviewer_source_audit")
        if "review_scopes" not in options:
            raise ValueError("Source checklist requires opt-in frozen review scope")
        errors = validate_checklist(artifact, method_id, roots, options["review_scopes"][method_id],
                                    evidence_policy=config.get("evidence_policy"),
                                    prepared_sources=(options.get("prepared_sources")
                                        if config.get("independent_source_renditions") else None))
    else:
        raise ValueError("Unknown worker role")
    result = {"valid": not errors, "errors": errors,
              "scope": "Structure, source integrity and passage occurrence; "
                       "not scientific approval",
              "checker_hashes": {name: sha(Path(__file__).parent / name) for name in
                                 ("validation.py", "evolution.py", "incremental.py")}}
    if config.get("scientific_detail_policy"):
        result["checker_hashes"].update({name: sha(Path(__file__).parent / name)
                                      for name in ("assertion_review.py", "internal_handoffs.py")})
    if not errors and role != "source_checklist":
        profile_path = artifact if role == "extractor" else Path(extraction)
        advice = function_coverage_advice(read(profile_path)["profiles"][0])
        if advice:
            result["function_coverage_advice"] = advice
    if budget := _completion_budget(state["jobs"][method_id], config, artifact, role):
        result["runtime_budget"] = budget
    if role == "independent_verifier":
        if diagnostics := _review_reference_diagnostics(artifact, Path(extraction), options):
            result["reference_coverage_diagnostics"] = diagnostics
            result["checker_hashes"]["review_reference_diagnostics.py"] = sha(
                Path(__file__).parent / "review_reference_diagnostics.py")
        checklist = state["jobs"][method_id].get("source_checklist")
        if saved := _review_draft_progress(
                artifact, Path(extraction), Path(checklist) if checklist else None):
            result["draft_progress"] = saved
    current_index = run / "shared_sources/index.json"
    snapshot = artifact.parent / "shared_sources.json"
    if current_index.is_file() and snapshot.is_file() and sha(current_index) != sha(snapshot):
        result["source_updates"] = {
            "path": str(current_index), "sha256": sha(current_index),
            "action": "The shared source index has new entries. Inspect those relevant to your "
                      "method before finalizing evidence gaps; reopen the actual sources. "
                      "Preserve this attempt's original index snapshot."}
    return result
