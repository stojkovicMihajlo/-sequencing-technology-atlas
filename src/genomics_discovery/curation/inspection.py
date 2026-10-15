"""Read-only observations of queue state, process identity and checkpointed evidence.

No worker is launched and no checkpoint is repaired. Process observations are a
snapshot, not a promise that an observed worker will continue running.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

from .codex_worker import _process_start_identity, assess_archived_attempt
from .harness import now, read
from .validation import canonical_hash

_DIAGNOSTICS = {
    "routing": re.compile(r"workspace routing discovery failed", re.I),
    "network": re.compile(
        r"error sending request|stream disconnected|connection (?:reset|closed|refused)|"
        r"network is unreachable|name resolution|websocket", re.I
    ),
    "capacity": re.compile(
        r"usage limit|rate limit|quota exceeded|capacity|overloaded|too many requests|\b429\b",
        re.I,
    ),
    "timeout": re.compile(r"timed out|timeout", re.I),
}
_TERMINAL = {"accepted", "accepted_with_explicit_gaps", "research_needed", "failed"}


def _local_path(run: Path, value) -> Path | None:
    """Never follow state references into credentials or other external files."""
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    path = (path if path.is_absolute() else run / path).resolve()
    return path if path.is_relative_to(run) else None


def _object(path: Path | None) -> dict | None:
    if path is None:
        return None
    try:
        value = read(path)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _process(record: dict | None) -> dict:
    record = record or {}
    pid, saved = record.get("pid"), record.get("process_start_identity")
    observation = {"pid": pid, "status": "unknown", "reason": "missing_process_identity"}
    if type(pid) is not int or pid <= 0:
        return observation
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {**observation, "status": "dead", "reason": "pid_absent"}
    except (PermissionError, OSError):
        return {**observation, "reason": "process_probe_unavailable"}
    current = _process_start_identity(pid)
    if not saved or not current:
        return observation
    if saved != current:
        return {**observation, "status": "dead", "reason": "pid_reused"}
    return {**observation, "status": "alive", "reason": "creation_identity_matches"}


def _scheduler(run: Path) -> dict:
    launches = []
    for path in sorted((run / "launches").glob("*.json")):
        receipt = _object(_local_path(run, str(path)))
        launches.append({"receipt": str(path), "created_at": (receipt or {}).get("created_at"),
                         "recorded_status": (receipt or {}).get("status"),
                         "process": _process(receipt)})
    statuses = {item["process"]["status"] for item in launches}
    status = "alive" if "alive" in statuses else (
        "unknown" if not statuses or "unknown" in statuses else "dead")
    return {"status": status, "launches": launches,
            "scope": "Recorded background launch identities only; foreground schedulers "
                     "without receipts cannot be identified."}


def _checkpoint(run: Path, job: dict, field: str) -> tuple[dict, dict | None]:
    recorded = job.get(field)
    report = {"path": recorded, "checkpointed": bool(recorded), "integrity": "not_checkpointed"}
    if not recorded:
        return report, None
    path = _local_path(run, recorded)
    if path is None:
        return {**report, "integrity": "outside_run"}, None
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
    except (OSError, ValueError, UnicodeDecodeError):
        return {**report, "integrity": "missing_or_unreadable"}, None
    expected = job.get(field + "_sha256")
    integrity = ("unrecorded_hash" if not expected else
                 "matches" if hashlib.sha256(raw).hexdigest() == expected else "mismatch")
    return {**report, "integrity": integrity}, value


def _transport_issues(directory: Path, result: dict) -> list[dict]:
    """Classify explicit native transport diagnostics, never scientific tool output.

    Preserve a file/line and the matched diagnostic, rather than copying provider
    payloads. These are observed categories, not inferred causes of a failure.
    """
    found = {}

    def record(message, filename, line=None):
        if not isinstance(message, str):
            return
        for category, pattern in _DIAGNOSTICS.items():
            match = pattern.search(message)
            if match:
                found.setdefault((category, filename), {
                    "category": category, "file": str(directory / filename),
                    "line": line, "diagnostic": match.group(),
                })

    record(result.get("error"), "result.json")
    try:
        with (directory / "events.jsonl").open(encoding="utf-8") as events:
            for number, line in enumerate(events, 1):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get("type") not in {"error", "turn.failed"}:
                    continue
                record(event.get("message"), "events.jsonl", number)
                error = event.get("error")
                if isinstance(error, dict):
                    record(error.get("message"), "events.jsonl", number)
                    record(error.get("code"), "events.jsonl", number)
                else:
                    record(error, "events.jsonl", number)
    except (OSError, UnicodeDecodeError):
        pass
    return list(found.values())


def _attempt(run: Path, attempt: dict) -> dict:
    directory = _local_path(run, attempt.get("directory"))
    report = {key: attempt.get(key) for key in ("directory", "role", "revision", "epoch")}
    if directory is None:
        return {**report, "activity": "unknown", "reason": "attempt_outside_run",
                "transport_issues": [], "result_archived": False}
    started = _object(_local_path(run, str(directory / "started.json")))
    result_path = _local_path(run, str(directory / "result.json"))
    result_archived = result_path is not None and result_path.is_file()
    result = _object(result_path)
    process = _process(started)
    completion = bool(result_archived or attempt.get("finished_at"))
    if completion:
        activity = "finished"
    elif started and started.get("spawned") is False:
        activity = "not_started"
    else:
        activity = {"alive": "active", "dead": "stale", "unknown": "unknown"}[process["status"]]
    # Archives are fixed files inside the attempt; refuse symlinks out of this run.
    safe_archive = all(_local_path(run, str(directory / name)) is not None for name in
                       ("result.json", "events.jsonl", "final.txt"))
    assessment = assess_archived_attempt(directory) if result_archived and safe_archive else None
    return {**report, "activity": activity, "process": process,
            "result_archived": result_archived, "completion_recorded": completion,
            "transport_complete": assessment.complete if assessment else None,
            "transport_assessment": assessment.error if assessment else None,
            "archived_error": (result or {}).get("error"),
            "transport_issues": _transport_issues(directory, result or {})
            if result_archived and safe_archive else []}


def inspect_run(run: Path | str) -> dict:
    """Return a JSON-safe, nonmutating report; never interpret acceptance as recipe completion."""
    run = Path(run).resolve()
    state_path = _local_path(run, str(run / "state.json"))
    if state_path is None:
        raise ValueError("Run state points outside the run directory")
    state = read(state_path)
    scheduler = _scheduler(run)
    jobs = {}
    transport_counts = Counter()
    for method, job in state["jobs"].items():
        attempts = [_attempt(run, attempt) for attempt in job.get("attempts", [])]
        for attempt in attempts:
            transport_counts.update({issue["category"] for issue in attempt["transport_issues"]})
        extraction, document = _checkpoint(run, job, "extraction")
        review, verdict = _checkpoint(run, job, "review")
        checklist, _ = _checkpoint(run, job, "source_checklist")
        profiles = (document or {}).get("profiles", [])
        profile = profiles[0] if isinstance(profiles, list) and len(profiles) == 1 else None
        if not isinstance(profile, dict):
            profile = None
        review.update(applicable=False, recorded_outcome=(verdict or {}).get("outcome"))
        if review["checkpointed"]:
            if (extraction["integrity"] != "matches" or review["integrity"] != "matches"
                    or (checklist["checkpointed"] and checklist["integrity"] != "matches")):
                review["reason"] = "checkpoint_integrity_unverified"
            elif profile is None:
                review["reason"] = "no_unique_checkpointed_profile"
            elif (verdict.get("profile_sha256") != canonical_hash(profile)
                  or verdict.get("profile_id") != profile.get("profile_id")):
                review["reason"] = "superseded_profile"
            else:
                review.update(applicable=True, reason="matches_checkpointed_profile")
        review["applicable_outcome"] = review["recorded_outcome"] if review["applicable"] else None
        review["scientific_scope"] = (verdict or {}).get("scientific_scope")
        metadata = (profile or {}).get("profile_metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        extraction["declared_coverage"] = {key: metadata.get(key) for key in
                                          ("architecture_coverage", "recipe_coverage")}
        followup_holds = []
        if extraction["integrity"] == "matches" and profile is not None:
            from .followup import pending_followup_reviews
            followup_holds = pending_followup_reviews(
                run, method, Path(extraction["path"]))
        active = [attempt["directory"] for attempt in attempts if attempt["activity"] == "active"]
        uncertain = any(attempt["activity"] == "unknown" for attempt in attempts)
        status = job.get("status")
        needs_extraction = (not job.get("extraction")
                            or status in {"pending", "extracting", "needs_revision"})
        stage = ("terminal" if status in _TERMINAL else
                 "source_checklist" if status == "preparing_checklist" else
                 "extraction" if needs_extraction else "review")
        interrupted = (status in {"extracting", "verifying", "preparing_checklist"}
                       and bool(attempts)
                       and attempts[-1]["activity"] == "stale")
        activity = ("active" if active else "unknown" if uncertain else
                    "terminal" if stage == "terminal" else
                    "interrupted" if interrupted else "queued_or_paused")
        jobs[method] = {
            "recorded_status": status, "revision": job.get("revision"), "stage": stage,
            "activity": activity, "active_attempts": active,
            "activity_reason": "worker_disappeared_without_terminal_archive"
            if activity == "interrupted" else None,
            "checkpointed_extraction": extraction, "checkpointed_review": review,
            "checkpointed_source_checklist": checklist,
            "acceptance": review["applicable_outcome"], "protocol_completeness": "not_assessed",
            "followup_audit_holds": followup_holds,
            "historical_last_error": job.get("last_error"), "attempts": attempts,
        }
    return {
        "run": str(run), "observed_at": now(), "scheduler": scheduler,
        "method_counts": {
            "total": len(jobs),
            "by_recorded_status": dict(Counter(j["recorded_status"] for j in jobs.values())),
            "by_activity": dict(Counter(j["activity"] for j in jobs.values())),
            "checkpointed_extractions": sum(j["checkpointed_extraction"]["integrity"] == "matches"
                                           for j in jobs.values()),
            "applicable_reviews": sum(j["checkpointed_review"]["applicable"]
                                      for j in jobs.values()),
            "methods_with_followup_holds": sum(bool(j["followup_audit_holds"])
                                               for j in jobs.values()),
            "by_applicable_review_outcome": dict(Counter(
                j["acceptance"] for j in jobs.values() if j["acceptance"] is not None)),
        },
        "archived_attempts_with_transport_diagnostics": dict(transport_counts),
        "scope": "Counts are unique methods except archived attempt diagnostics. Queued or paused "
                 "means a nonterminal stage without a confirmed active worker. Interrupted means "
                 "the latest recorded in-progress worker is confirmed absent or its PID reused, "
                 "with no terminal archive and no active or unverified worker. This does not "
                 "identify the interruption's cause. A draft or scaffold is not completion. "
                 "Inspection does not repair state or relaunch workers. Checkpoint hashes "
                 "and review applicability are checked; source evidence is not revalidated. "
                 "Acceptance is the recorded source-scoped review outcome, not protocol "
                 "completeness or experimental validation. Transport categories describe explicit "
                 "archived diagnostics, not a root-cause determination.",
        "jobs": jobs,
    }
