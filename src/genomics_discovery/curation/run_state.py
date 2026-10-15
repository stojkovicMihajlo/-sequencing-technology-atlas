"""Durable run state, frozen inputs, atomic writes and read-only status."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_frozen(run: Path, state: dict, config: dict) -> None:
    if state["config_sha256"] != sha(run / "config.json"):
        raise ValueError("Run configuration changed; initialize a new run")
    if config["contracts_sha256"] != sha(run / "contracts.json"):
        raise ValueError("Frozen contracts changed")
    if config.get("review_scope_sha256") is not None:
        from .coverage import load_scopes
        if config["review_scope_sha256"] != sha(run / "review_scope.json"):
            raise ValueError("Frozen review scope changed")
        load_scopes(run / "review_scope.json", list(state["jobs"]))
    if config.get("with_evolution") and config.get("method_registry_sha256") != sha(
            run / "method_registry.json"):
        raise ValueError("Frozen method registry changed")
    if (config.get("source_catalogue_sha256") is not None
            and config["source_catalogue_sha256"] != sha(run / "shared_sources/index.json")):
        raise ValueError("Frozen source catalogue changed")
    if config.get("prepared_sources_sha256") is not None:
        from .prepared_sources import validate_packet
        packet_path = run / "prepared_sources.json"
        if sha(packet_path) != config["prepared_sources_sha256"]:
            raise ValueError("Frozen prepared sources changed")
        validate_packet(read(packet_path), Path(config["atlas"]), list(state["jobs"]))
    for mid, job in state["jobs"].items():
        if job["input_sha256"] != sha(run / "jobs" / mid / "input.json"):
            raise ValueError("Frozen method input changed: " + mid)
        if seed := job.get("seed"):
            from .continuation import validate_seed
            validate_seed(seed)
            if (seed["method_id"] != mid or seed["source_atlas"] != config["atlas"]
                    or seed["source_run"] != config.get("continue_from")):
                raise ValueError("Continuation seed does not match its frozen job: " + mid)
        for field in ("extraction", "review", "source_checklist"):
            if job.get(field) and sha(Path(job[field])) != job.get(field + "_sha256"):
                raise ValueError("Checkpointed artifact changed: " + mid + " " + field)
        for recovery in job.get("recoveries", []):
            if sha(Path(recovery["record"])) != recovery["sha256"]:
                raise ValueError("Recovery audit record changed: " + mid)
        for critique in job.get("provisional_critiques", []):
            receipt = Path(critique["record"])
            if sha(receipt) != critique["sha256"]:
                raise ValueError("Provisional critique receipt changed: " + mid)
            record = read(receipt)
            directory = Path(record["attempt"])
            if any(sha(directory / name) != value
                   for name, value in record["archive_sha256"].items()):
                raise ValueError("Provisional critique archive changed: " + mid)
        for attempt in job["attempts"]:
            if pin := attempt.get("review_plan"):
                if sha(Path(pin["path"])) != pin["sha256"]:
                    raise ValueError("Host-pinned review plan changed: " + mid)
            if pin := attempt.get("artifact_scaffold"):
                if sha(Path(pin["path"])) != pin["sha256"]:
                    raise ValueError("Host-pinned artifact scaffold changed: " + mid)
        if baseline := job.get("review_baseline"):
            for key in ("extraction", "review"):
                pin = baseline[key]
                if sha(Path(pin["path"])) != pin["sha256"]:
                    raise ValueError("Checkpointed review baseline changed: " + mid)


def now() -> str:
    return datetime.now(UTC).isoformat()


def write(path: Path, value: dict) -> None:
    """Only the orchestrator mutates shared state, with atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def progress(run: Path, event: dict) -> None:
    """Persist progress before best-effort terminal output; disk errors remain fatal."""
    line = json.dumps({"timestamp": now(), **event}, ensure_ascii=False)
    with (run / "progress.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        # A detached observer or closed stdout must never cancel research jobs.
        # Only presentation errors are ignored; the durable write is outside this block.
        pass


@contextmanager
def run_lock(run: Path):
    with (run / ".orchestrator.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("This run already has an active orchestrator") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def summarize(run: Path) -> dict:
    state, config = read(run / "state.json"), read(run / "config.json")
    usage: dict[str, int | float] = {}
    attempts = 0
    archived = 0
    drafts = Counter()
    jobs = {}
    for mid, job in state["jobs"].items():
        latest = None
        saved = set()
        for a in job["attempts"]:
            attempts += 1
            directory = Path(a["directory"])
            result = directory / "result.json"
            for role in ("extraction", "review"):
                if (directory / (role + ".json")).is_file():
                    saved.add(role)
            latest = {"directory": str(directory), "role": a["role"],
                      "started_at": a.get("started_at"),
                      "finished_at": a.get("finished_at"),
                      "result_archived": result.is_file()}
            started = directory / "started.json"
            if started.is_file():
                pid = read(started).get("pid")
                latest["pid"] = pid
                latest["pid_alive"] = _pid_alive(pid) if not result.exists() else False
            events = directory / "events.jsonl"
            if events.is_file():
                latest["last_event_at"] = datetime.fromtimestamp(
                    events.stat().st_mtime, UTC).isoformat()
            if result.exists():
                archived += 1
                archive = read(result)
                latest["archived_error"] = archive.get("error")
                for k, v in archive.get("usage", {}).items():
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        usage[k] = usage.get(k, 0) + v
        drafts.update(saved)
        jobs[mid] = {k: job.get(k) for k in
                     ("status", "revision", "extraction", "review", "last_error")}
        jobs[mid].update(attempts=len(job["attempts"]), latest_attempt=latest,
                         saved_drafts=sorted(saved), recoveries=len(job.get("recoveries", [])))
    return {"run": str(run.resolve()), "atlas_revision": config["atlas_revision"],
            "concurrency": {"total": config["workers"],
                            "extraction": config["workers"] - config["reviewers"],
                            "verification": config["reviewers"]},
            "counts": dict(Counter(j["status"] for j in state["jobs"].values())),
            "attempts": attempts, "reported_usage": usage,
            "usage_scope": "Cumulative archived result.json usage; excludes unfinished attempts",
            "attempt_counts": {"archived": archived, "without_result": attempts - archived},
            "methods_with_saved_drafts": dict(drafts),
            "draft_scope": "File presence only; saved drafts are not verified acceptance",
            "jobs": jobs}


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

