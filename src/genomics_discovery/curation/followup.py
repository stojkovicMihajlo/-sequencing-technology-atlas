"""Reopen a record after a source-bound follow-up review, never grant acceptance."""

from pathlib import Path

from .harness import (
    _apply_review,
    _check_prior_workers,
    check_artifact,
    check_frozen,
    now,
    read,
    run_lock,
    sha,
    write,
)
from .validation import canonical_hash


def pending_followup_reviews(run: Path, method_id: str, extraction: Path) -> list[dict]:
    """Return unresolved, current-profile audit holds without changing live state.

    Malformed files in the designated audit directory block export pending
    inspection. A review of an older profile cannot block a revised profile.
    A hold never grants acceptance, even if its scientific conclusion is wrong.
    """
    run = Path(run).resolve()
    profile_hash = canonical_hash(read(extraction)["profiles"][0])
    applied = read(run / "state.json")["jobs"][method_id].get("followup_reviews", [])
    holds = []
    for path in sorted((run / "jobs" / method_id).glob("followup_audit*/review.json")):
        if not path.resolve().is_relative_to(run / "jobs" / method_id):
            holds.append({"review": str(path), "reason": "audit_path_outside_method"})
            continue
        try:
            review = read(path)
            if not isinstance(review, dict) or not review.get("profile_sha256"):
                raise ValueError("Missing reviewed profile identity")
        except (OSError, ValueError, UnicodeError):
            holds.append({"review": str(path), "reason": "unreadable_or_incomplete_audit"})
            continue
        if review["profile_sha256"] != profile_hash:
            if not any(entry.get("review_sha256") == sha(path)
                       and entry.get("profile_sha256") == review["profile_sha256"]
                       for entry in applied):
                holds.append({"review": str(path),
                              "reason": "unapplied_audit_references_a_different_profile"})
            continue
        if review.get("outcome") in {"needs_revision", "unresolved_source_gap"}:
            holds.append({"review": str(path), "review_sha256": sha(path),
                          "reason": "current_profile_has_nonaccepting_followup",
                          "outcome": review["outcome"]})
        elif review.get("outcome") not in {"accepted", "accepted_with_explicit_gaps"}:
            holds.append({"review": str(path), "reason": "unrecognized_followup_outcome"})
    return holds


def apply_followup_review(run: Path, method_id: str, review: Path) -> dict:
    """Queue bounded corrections while retaining the original accepted artifacts.

    Requires a stopped scheduler and a valid nonaccepting review of the current
    checkpoint. No model is called, no budget is increased, and existing source,
    extraction and review files are never rewritten. The caller is responsible
    for the scientific audit; deterministic validation does not establish it.
    """
    run, review = Path(run).resolve(), Path(review).resolve()
    with run_lock(run):
        state, config = read(run / "state.json"), read(run / "config.json")
        check_frozen(run, state, config)
        _check_prior_workers(state)
        checked = check_artifact(run, method_id, review, "independent_verifier")
        if not checked["valid"]:
            raise ValueError("Invalid follow-up review: " + "; ".join(checked["errors"]))
        verdict = read(review)
        if verdict["outcome"] not in {"needs_revision", "unresolved_source_gap"}:
            raise ValueError("Follow-up ingestion cannot accept a record")
        if not verdict.get("errors") and not verdict.get("remaining_gaps"):
            raise ValueError("Follow-up review needs an actionable finding or evidence gap")
        job = state["jobs"][method_id]
        digest = sha(review)
        if any(item["review_sha256"] == digest for item in job.get("followup_reviews", [])):
            raise ValueError("This follow-up review was already applied")
        entry = {
            "applied_at": now(), "review": str(review), "review_sha256": digest,
            "profile_sha256": verdict["profile_sha256"],
            "extraction": job["extraction"], "extraction_sha256": job["extraction_sha256"],
            "previous_status": job["status"], "previous_revision": job["revision"],
            "previous_review": job.get("review"),
            "previous_review_sha256": job.get("review_sha256"),
            "scope": "Targeted source audit reopening; not a new acceptance or model attempt",
        }
        _apply_review(job, review, config)
        entry.update(status=job["status"], revision=job["revision"])
        job.setdefault("followup_reviews", []).append(entry)
        state["updated_at"] = now()
        write(run / "state.json", state)
    return {"method_id": method_id, **entry}
