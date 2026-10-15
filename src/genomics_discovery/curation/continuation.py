"""Seed a new bounded queue from current records without inheriting acceptance."""

from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

from .validation import _sources, canonical_hash, validate_extraction

FORMAT = "atlas_continuation_seed/1.0"


def _pin(path: Path) -> dict:
    from .harness import sha
    path = path.resolve()
    return {"path": str(path), "sha256": sha(path)}


def _inside(path, root: Path) -> Path:
    candidate = Path(path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"Continuation artifact outside its source job: {candidate}")
    return candidate


def _check_selected_workers(jobs: dict, source_run: Path) -> None:
    from .harness import read
    from .inspection import _process
    for mid, job in jobs.items():
        for attempt in job["attempts"]:
            directory = _inside(attempt["directory"], source_run / "jobs" / mid)
            started = directory / "started.json"
            if started.is_file() and not (directory / "result.json").exists():
                observation = _process(read(started))
                if observation["status"] != "dead":
                    raise ValueError("Selected source worker is active or unverified: "
                                     f"{mid}: {observation}")


def prepare_continuation(source_run: Path, atlas: Path,
                         method_ids: list[str], *,
                         allow_timeout_correction_leads: bool = False) -> dict[str, dict]:
    """Snapshot selected checkpoint records under their original validation policy.

    Only the run lock is touched. No source state, protocol, review, or budget is
    changed. Feedback is for the next extractor, not the fresh independent reviewer.
    """
    from .harness import (
        _read_provisional_critique,
        _source_checklist_path,
        check_frozen,
        now,
        read,
        run_lock,
        sha,
        validate_job_review,
        validation_options,
    )
    source_run, atlas = source_run.resolve(), atlas.resolve()
    if not isinstance(allow_timeout_correction_leads, bool):
        raise ValueError("allow_timeout_correction_leads must be boolean")
    if (not method_ids or not all(isinstance(mid, str) and
                                  re.fullmatch(r"[a-z0-9][a-z0-9_-]*", mid)
                                  for mid in method_ids)
            or len(method_ids) != len(set(method_ids))):
        raise ValueError("Continuation requires explicit unique method IDs")
    with run_lock(source_run):
        config, state = read(source_run / "config.json"), read(source_run / "state.json")
        if Path(config["atlas"]).resolve() != atlas:
            raise ValueError("Continuation source run belongs to a different atlas")
        if set(method_ids) - state["jobs"].keys():
            raise ValueError("Continuation method is absent from the source checkpoint")
        check_frozen(source_run, state, config)
        selected = {mid: state["jobs"][mid] for mid in method_ids}
        _check_selected_workers(selected, source_run)
        state_hash = sha(source_run / "state.json")
        options, roots = validation_options(source_run, config), (atlas, source_run)
        seeds = {}
        for mid, job in selected.items():
            method_root = source_run / "jobs" / mid
            if not job.get("extraction") or not job.get("extraction_sha256"):
                raise ValueError("Continuation has no pinned current extraction: " + mid)
            extraction = _inside(job["extraction"], method_root)
            if sha(extraction) != job["extraction_sha256"]:
                raise ValueError("Continuation current extraction changed: " + mid)
            errors = validate_extraction(extraction, mid, roots, **options)
            if errors:
                raise ValueError(f"Invalid continuation extraction {mid}: {errors}")
            extracted = read(extraction)
            profile_hash = canonical_hash(extracted["profiles"][0])
            sources = _sources(extracted["sources"], extraction.parent, roots,
                               evidence_policy=config.get("evidence_policy"))
            source_files = {path: digest for files in sources.values()
                            for path, digest in files.items()}
            candidates = {}
            if job.get("review"):
                candidates[_inside(job["review"], method_root)] = "checkpoint"
            for entry in job.get("followup_reviews", []):
                path = _inside(entry["review"], method_root)
                if sha(path) != entry["review_sha256"]:
                    raise ValueError("Applied continuation audit changed: " + str(path))
                candidates.setdefault(path, "followup")
            for path in sorted(method_root.glob("followup_audit*/review.json")):
                candidates.setdefault(_inside(path, method_root), "followup")
            reviews, skipped, feedback = [], [], []
            for path, kind in candidates.items():
                document = read(path)
                if not isinstance(document, dict) or not document.get("profile_sha256"):
                    raise ValueError("Unidentifiable continuation review: " + str(path))
                pin = _pin(path)
                if document["profile_sha256"] != profile_hash:
                    skipped.append({**pin, "reason": "superseded_profile",
                                    "profile_sha256": document["profile_sha256"]})
                    continue
                errors = validate_job_review(source_run, config, job, path, extraction, mid)
                if errors:
                    raise ValueError(
                        f"Invalid current-profile continuation review {path}: {errors}")
                if document.get("sources"):
                    sources = _sources(document["sources"], extraction.parent, roots,
                                       evidence_policy=config.get("evidence_policy"))
                    source_files.update({path: digest for files in sources.values()
                                         for path, digest in files.items()})
                reviews.append({**pin, "kind": kind, "outcome": document["outcome"],
                                "source_base": str(extraction.parent)})
                feedback.append({"kind": kind, "review_path": str(path),
                                 "review_sha256": pin["sha256"],
                                 "source_base": str(extraction.parent),
                                 "outcome": document["outcome"],
                                 "errors": deepcopy(document["errors"]),
                                 "remaining_gaps": deepcopy(document["remaining_gaps"])})
            seed = {
                "format_version": FORMAT, "method_id": mid, "captured_at": now(),
                "source_run": str(source_run), "source_atlas": str(atlas),
                "source_state_sha256": state_hash,
                "source_config": _pin(source_run / "config.json"),
                "source_job_status": job["status"], "extraction": _pin(extraction),
                "source_artifacts": [{"path": str(path), "sha256": digest}
                                     for path, digest in sorted(source_files.items())],
                "reviews": reviews, "skipped_reviews": skipped,
                "feedback": {
                    "instruction": (
                        "Revise the pinned current extraction in a new output. These separately "
                        "attributed reviews are correction leads, not primary evidence or prior "
                        "acceptance. Recheck findings against source documents. Preserve supported "
                        "content and limits. Register only inspected source documents "
                        "and their faithful renditions as scientific evidence; move agent-written "
                        "retrieval diagnostics to separate diagnostic fields. Never relabel a note "
                        "as a paper. Review source paths are relative to source_base. A new full "
                        "independent review is required under the destination run's policy."),
                    "reviews": feedback,
                },
            }
            # Reuse the independent source reading only for identical source
            # bytes/identities and scope. No extraction or acceptance is reused.
            if config.get("review_scope_sha256") and config.get("prepared_sources_sha256"):
                checklist = _source_checklist_path(source_run, config, job, mid)
                if any(a["role"] == "source_checklist" and Path(a["directory"]) == checklist.parent
                       for a in job["attempts"]):
                    seed["source_checklist"] = {
                        **_pin(checklist), "scope_sha256": canonical_hash(
                            read(source_run / "review_scope.json")["methods"][mid]),
                        "source_packet_method_sha256": canonical_hash(
                            read(source_run / "prepared_sources.json")["methods"][mid]),
                        "scope": "Reused provisional independent source reading, not gold "
                                 "and not executed again in the destination run"}
            provisional = _read_provisional_critique(source_run, config, job, mid, extraction, {},
                allow_timeout_correction_leads=allow_timeout_correction_leads)
            if allow_timeout_correction_leads:
                seed["allow_timeout_correction_leads"] = True
                latest = next((a for a in reversed(job["attempts"])
                               if a.get("role") == "independent_verifier"
                               and a.get("revision") == job["revision"]), None)
                if latest is not None:
                    directory = _inside(latest["directory"], method_root)
                    result = directory / "result.json"
                    host_timeout = any(isinstance(error, str) and error.startswith("Codex exceeded")
                                       for error in job.get("last_error", []))
                    try:
                        timeout_exit = result.is_file() and read(result).get("exit_code") == 124
                    except (ValueError, AttributeError):
                        timeout_exit = False
                    if (provisional is None and (directory / "review.json").is_file()
                            and (host_timeout or timeout_exit)):
                        raise ValueError(
                            "Saved timeout critique is ineligible for correction leads: "
                            + str(directory))
            if provisional is not None:
                receipt, document = provisional
                seed["provisional_critique"] = receipt
                seed["feedback"]["unvalidated_review_leads"] = {
                    "review_path": str(Path(receipt["attempt"]) / "review.json"),
                    "integrity_errors": receipt["integrity_errors"],
                    "scope": receipt["scope"],
                    "instruction": "Reopen sources before acting on these unvalidated leads. "
                                   "Neither their scientific assertions nor their checks "
                                   "are certified.",
                    **{key: deepcopy(document.get(key, [])) for key in
                       ("errors", "remaining_gaps", "sources")}}
                if transport := receipt.get("transport_provenance"):
                    seed["feedback"]["unvalidated_review_leads"].update(
                        transport_provenance=deepcopy(transport),
                        instruction="This is a saved timed-out draft, not a completed review. "
                                    "Reopen original sources before acting on its untrusted "
                                    "correction leads. A fresh full independent review "
                                    "is required.")
            seed["metadata_sha256"] = canonical_hash(seed)
            validate_seed(seed)
            seeds[mid] = seed
        if sha(source_run / "state.json") != state_hash:
            raise ValueError("Continuation checkpoint changed while taking its snapshot")
        return seeds


def validate_seed(seed: dict) -> None:
    """Check immutable seed inputs; later source-queue progress is permitted."""
    from .harness import sha
    if not isinstance(seed, dict) or seed.get("format_version") != FORMAT:
        raise ValueError("Unsupported continuation seed")
    body = {key: value for key, value in seed.items() if key != "metadata_sha256"}
    if seed.get("metadata_sha256") != canonical_hash(body):
        raise ValueError("Continuation seed metadata changed")
    source_run = Path(seed["source_run"]).resolve()
    method_root = source_run / "jobs" / seed["method_id"]
    config = seed["source_config"]
    if Path(config["path"]).resolve() != source_run / "config.json":
        raise ValueError("Continuation source config is outside its run")
    pins = [config, seed["extraction"], *seed["reviews"], *seed["skipped_reviews"]]
    if checklist := seed.get("source_checklist"):
        pins.append(checklist)
    for pin in pins:
        path = Path(pin["path"]).resolve()
        if pin is not config:
            _inside(path, method_root)
        if sha(path) != pin["sha256"]:
            raise ValueError("Pinned continuation artifact changed: " + str(path))
    if receipt := seed.get("provisional_critique"):
        directory = _inside(receipt["attempt"], method_root)
        for name, digest in receipt["archive_sha256"].items():
            if sha(directory / name) != digest:
                raise ValueError("Pinned unvalidated critique archive changed")
    atlas = Path(seed["source_atlas"]).resolve()
    for pin in seed["source_artifacts"]:
        path = Path(pin["path"]).resolve()
        if not (path.is_relative_to(source_run) or path.is_relative_to(atlas)):
            raise ValueError("Continuation source artifact is outside its permitted roots")
        if sha(path) != pin["sha256"]:
            raise ValueError("Pinned continuation source changed: " + str(path))
