"""Admission-time archive checks, independent of scientific judgments.

New host-tagged Codex CLI attempts require current complete archives matching
their host checkpoint. An injected worker is not
reclassified as native because it emits similar test events. Old unmarked jobs
without CLI command/start archives retain compatibility but establish no native
transport guarantee. Legacy CLI-shaped archives are checked, never backfilled
with a claimed original completion hash.
"""

from pathlib import Path

from .codex_worker import CodexWorker, assess_archived_attempt
from .validation import _file_sha256, _load, _require

NATIVE = "codex-exec-json/1.0"
INJECTED = "injected-worker/1.0"
ARCHIVES = ("command.json", "started.json", "prompt.txt", "result.json", "events.jsonl",
            "final.txt")


def reviewer_label(rows):
    """Name only the provenance established by the current host archive check."""
    reviewer = next(row for row in rows if row["role"] == "independent_verifier")
    return ("independent Codex session" if reviewer["native_completion_established"]
            else "independent worker; native transport not established")


def native_archive_hashes(directory):
    """Hash exact transport proof before/after assessment without emitting bodies."""
    directory = Path(directory).resolve()
    before = {name: _file_sha256(directory / name) for name in ARCHIVES}
    assessment = assess_archived_attempt(directory)
    _require(assessment.complete, "Incomplete native archive: " + str(assessment.error))
    after = {name: _file_sha256(directory / name) for name in ARCHIVES}
    _require(before == after, "Native archive changed during transport assessment")
    return before


def validate_accepted_transports(job, config):
    """Recheck current native proofs; return only classified integrity metadata.

    This helper is for accepted export/admission, not active draft selfchecks.
    Seeded/legacy artifacts without current attempt ownership are unclassified;
    their existing seed/source/scope validations remain the caller's obligation.
    All runtime classification comes from host state, not worker review fields.
    """
    rows = []
    for role, field, filename in (
        ("source_checklist", "source_checklist", "checklist.json"),
        ("extractor", "extraction", "extraction.json"),
        ("independent_verifier", "review", "review.json"),
    ):
        if not job.get(field):
            continue
        artifact = Path(job[field]).resolve()
        matches = [attempt for attempt in job["attempts"]
                   if Path(attempt["directory"]).resolve() == artifact.parent]
        _require(len(matches) <= 1, "Duplicate current transport attempt ownership")
        if not matches:
            rows.append({"role": role, "kind": "unclassified_legacy_or_seed",
                         "native_completion_established": False})
            continue
        attempt = matches[0]
        _require(attempt["role"] == role and artifact.name == filename,
                 "Current artifact does not match its host attempt role")
        kind = attempt.get("worker_transport")
        _require(kind is None or kind in {NATIVE, INJECTED}, "Unknown host worker_transport")
        if kind == INJECTED:
            rows.append({"role": role, "kind": INJECTED,
                         "native_completion_established": False})
            continue
        legacy_cli = kind is None and any(
            (artifact.parent / name).exists() for name in ("command.json", "started.json"))
        if kind != NATIVE and not legacy_cli:
            rows.append({"role": role, "kind": "unclassified_legacy_worker",
                         "native_completion_established": False})
            continue
        checkpoint = attempt.get("artifact_sha256", job.get(field + "_sha256"))
        _require(checkpoint == _file_sha256(artifact),
                 "Native artifact differs from its host completion checkpoint")
        # Require process-launch evidence; event shape alone is not native identity.
        command, started = (_load(artifact.parent / name) for name in
                            ("command.json", "started.json"))
        _require(isinstance(command.get("argv"), list) and command["argv"]
                 and all(isinstance(arg, str) and arg for arg in command["argv"])
                 and command.get("cwd") == str(artifact.parent),
                 "Missing native command binding")
        expected = CodexWorker(command["argv"][0], model=config["model"], effort=config["effort"],
                              web_search=config.get("prepared_sources_sha256") is None,
                              network_access=config.get("prepared_sources_sha256") is None
                              )._command(artifact.parent)
        _require(command["argv"] == expected, "Native command differs from frozen policy")
        _require(started.get("spawned") is True, "Native process launch is not established")
        hashes = native_archive_hashes(artifact.parent)
        recorded = attempt.get("native_archive_sha256")
        _require(recorded is None or recorded == hashes,
                 "Native archive differs from original host completion hashes")
        _require(kind != NATIVE or recorded is not None,
                 "New native attempt lacks original completion archive hashes")
        rows.append({"role": role, "kind": NATIVE if kind == NATIVE else "legacy_cli_archive",
                     "native_completion_established": True,
                     "host_archive_hashes_established": recorded is not None,
                     "archive_pin_origin": attempt.get("native_archive_pin_origin"),
                     "archive_sha256": hashes})
    return rows
