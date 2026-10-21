"""Restartable orchestration around the existing fresh-run extraction runtime.

Runtime stages remain owned by workflow_extraction.runtime.execute. Recovery
starts a fresh run, seeding only successful, pinned, validated artifacts through
its supported review_only/repair boundaries. We never patch an old runtime state.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import socket
from pathlib import Path

from ..codex_worker import CodexWorker, assess_archived_attempt
from ..workflow_extraction import runtime
from ..workflow_extraction.models import (
    Workflow, WorkflowReview, digest, review_outcome, validate_evidence, validate_review,
)
from ..workflow_extraction.sources import load_packet
from .models import BatchConfig, Inventory
from .seeds import freeze_seed
from .storage import (
    PinDrift, atomic_bytes, check_pins, controller_liveness, controller_lock,
    now, pin_health, read, sha, write,
)

SCIENTIFIC = {"supported", "needs_revision", "research_needed"}
TERMINAL = SCIENTIFIC | {"missing_sources", "input_error", "pin_drift", "artifact_drift"}


def _implementation_pins():
    """Pin the actual local code used, not an assumed upstream version name."""
    package = Path(runtime.__file__).resolve().parent
    curation = package.parent
    files = {*package.glob("*.py"), *Path(__file__).parent.glob("*.py"),
             *(curation / name for name in ("codex_worker.py", "run_state.py", "document_manifest.py")),
             *curation.glob("*coverage*.py"), curation.parent / "core/artifacts.py"}
    return {str(path): sha(path) for path in sorted(files)}


def _initial_state(frozen):
    return {"format_version": "extraction_batch_state/1.0", "status": "planned",
            "created_at": frozen["created_at"], "jobs": {
                job["id"]: {"status": job["planned_status"], "attempts": [],
                            **({"error": job["error"]} if "error" in job else {}),
                            **{key:job[key] for key in ("reason", "next_evidence") if key in job}}
                for job in frozen["jobs"]}, "accepted_parents": {}}


def plan(manifest: Path, run: Path, *, config: BatchConfig | None = None) -> dict:
    """Freeze one mixed inventory. Invalid ready jobs do not erase healthy jobs."""
    manifest, run = Path(manifest).resolve(), Path(run).resolve()
    config = BatchConfig.model_validate(config or BatchConfig())
    inventory = Inventory.model_validate_json(manifest.read_bytes())
    with controller_lock(run):
        if any(path.name != ".controller.lock" for path in run.iterdir()):
            raise FileExistsError("Plan destination must be empty; use run to resume it")
        global_pins = {str(manifest): sha(manifest), **_implementation_pins()}
        if inventory.source_receipts is not None:
            receipt = (manifest.parent / inventory.source_receipts).resolve()
            files = read(receipt).get("files")
            if not isinstance(files, dict):
                raise ValueError("Source receipt requires a files mapping of path to SHA256")
            global_pins[str(receipt)] = sha(receipt)
            for filename, expected in files.items():
                if not isinstance(filename, str) or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise ValueError("Invalid source receipt path/SHA256")
                path = (receipt.parent / filename).resolve()
                if str(path) in global_pins and global_pins[str(path)] != expected:
                    raise ValueError(f"Conflicting source receipt pin: {path}")
                global_pins[str(path)] = expected
            check_pins(global_pins)
        jobs = []
        for job in inventory.jobs:
            entry = job.model_dump(mode="json", exclude_none=True)
            entry["planned_status"] = job.status if job.status == "missing_sources" else "queued"
            entry["pins"] = {}
            if job.status == "ready":
                packet_path = (manifest.parent / job.packet).resolve()
                entry["packet"] = str(packet_path)
                try:
                    packet = load_packet(packet_path)
                    if job.method_id is not None and job.method_id != packet["method_id"]:
                        raise ValueError("Inventory method_id differs from source packet")
                    snapshot = run / "inputs" / job.id / "source_packet.json"
                    atomic_bytes(snapshot, packet_path.read_bytes())
                    entry.update(method_id=packet["method_id"], source_packet=str(snapshot))
                    paths = [packet_path, snapshot, *(Path(s["original_path"]) for s in packet["sources"])]
                    entry["pins"] = {str(path): sha(path) for path in paths}
                    if job.seed_runtime is not None:
                        seed, seed_pins = freeze_seed(
                            (manifest.parent / job.seed_runtime).resolve(), job.seed_mode,
                            packet, run / "inputs" / job.id / "seed", config)
                        entry["seed"] = seed
                        entry["pins"].update(seed_pins)
                except (OSError, ValueError, KeyError) as exc:
                    entry.update(planned_status="input_error", error=f"{type(exc).__name__}: {exc}")
            jobs.append(entry)
        frozen = {"format_version": "extraction_batch_plan/1.0", "created_at": now(),
                  "manifest": str(manifest), "config": config.model_dump(mode="json"),
                  "pins": global_pins, "jobs": jobs,
                  "boundary": "Source-packet batch; accepted-parent export is not automatic cross-method graph construction or expert acceptance."}
        check_pins(global_pins)
        for entry in jobs:
            check_pins(entry["pins"])
        write(run / "plan.json", frozen)
        atomic_bytes(run / "plan.sha256", (sha(run / "plan.json") + "\n").encode())
        state = _initial_state(frozen)
        write(run / "state.json", state)
        write(run / "parents.json", {})
        return state


def _load(run: Path, *, verify_input_pins=True):
    expected = (run / "plan.sha256").read_text().strip()
    if sha(run / "plan.json") != expected:
        raise PinDrift("Immutable plan/config changed")
    frozen = read(run / "plan.json")
    config = BatchConfig.model_validate(frozen["config"])
    if verify_input_pins:
        check_pins(frozen["pins"])
    state = read(run / "state.json") if (run / "state.json").exists() else _initial_state(frozen)
    if set(state["jobs"]) != {j["id"] for j in frozen["jobs"]}:
        raise PinDrift("State job IDs differ from immutable inventory")
    return frozen, config, state


def status(run: Path) -> dict:
    """Read an atomic snapshot without taking over or mutating the controller."""
    run = Path(run).resolve()
    frozen, _, state = _load(run, verify_input_pins=False)
    controller = controller_liveness(run)
    result = {**state, "run": str(run), "recorded_status": state["status"],
              "controller": controller, "global_input_health": pin_health(frozen["pins"]),
              "jobs": {mid: dict(row) for mid, row in state["jobs"].items()}}
    if state["status"] == "running" and controller["status"] == "inactive":
        result["status"] = "interrupted"
        result["native_session_uncertainty"] = _native_uncertainty(run, inspect_local_pids=False)
        for row in result["jobs"].values():
            if row["status"] == "running":
                row.update(recorded_status="running", status="interrupted")
    result["input_health"] = {}
    for job in frozen["jobs"]:
        try:
            check_pins(job["pins"])
            result["input_health"][job["id"]] = "unchanged"
        except PinDrift as exc:
            result["input_health"][job["id"]] = str(exc)
    result["input_status"] = "pin_drift" if any(
        health != "unchanged" for health in
        [*result["global_input_health"].values(), *result["input_health"].values()]) else "unchanged"
    return result


def _native_uncertainty(run: Path, *, inspect_local_pids=True) -> list[str]:
    """Do not replace a possibly live worker after an ungraceful controller death.

    A started=false record without result has a spawn-recording race, so fail
    closed. We never signal a process based on a recovered PID. A completed result
    is written by the transport only after process cleanup; its PID may be reused.
    """
    uncertain = []
    for started in run.glob("methods/*/attempts/*/attempts/*/started.json"):
        if (started.parent / "result.json").exists():
            continue
        try:
            item = read(started)
            if not inspect_local_pids or item.get("hostname") != socket.gethostname():
                uncertain.append(str(started))
                continue  # A remote/copy PID cannot establish local process death.
            pid = item.get("pid")
            if type(pid) is not int or pid <= 0:
                uncertain.append(str(started))
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                continue
            except PermissionError:
                pass
            uncertain.append(str(started))
        except (OSError, ValueError, AttributeError):
            uncertain.append(str(started))
    return uncertain


def _file(record: dict) -> Path:
    path = Path(record["path"])
    check_pins({str(path): record["sha256"]})
    return path


def _successful_artifact(attempt: dict) -> Path | None:
    if attempt.get("status") != "validated":
        return None
    path = _file({"path": attempt["artifact"], "sha256": attempt["artifact_sha256"]})
    check_pins({str(Path(attempt["directory"]) / name): value
                for name, value in attempt.get("archive_sha256", {}).items()})
    archived = assess_archived_attempt(Path(attempt["directory"]))
    if not archived.complete:
        raise PinDrift(f"Successful stage transport no longer verifies: {path}")
    if archived.thread_id != attempt.get("thread_id"):
        raise PinDrift(f"Successful stage thread identity changed: {path}")
    return path


def _workflow(path: Path, packet):
    workflow = Workflow.model_validate_json(path.read_bytes())
    errors = validate_evidence(workflow, packet)
    errors.extend(f"Workflow {key} differs from source scope" for key in
                  ("method_id", "title", "scope", "variant") if getattr(workflow, key) != packet[key])
    if errors:
        raise ValueError("; ".join(errors))
    return workflow


def _coverage(state, workflow, review, packet):
    # Import only when selected; this public extension is owned by extraction.
    from ..workflow_extraction.obligations import (
        SourceCoverageReview, SourceObligations, source_coverage_outcome,
        validate_source_coverage_review, validate_source_obligations,
    )
    artifacts = state.get("artifacts", {})
    if not all(k in artifacts for k in ("source_obligations", "source_coverage_review")):
        return None
    sidecar = artifacts["source_coverage_review"]
    if not any(a.get("source_coverage_review") == sidecar and _successful_artifact(a)
               for a in state.get("attempts", [])):
        return None
    obligations = SourceObligations.model_validate_json(_file(artifacts["source_obligations"]).read_bytes())
    coverage = SourceCoverageReview.model_validate_json(_file(artifacts["source_coverage_review"]).read_bytes())
    errors = validate_source_obligations(obligations, packet)
    errors += validate_source_coverage_review(coverage, obligations, review, workflow, packet)
    if errors:
        return None  # It may describe an earlier candidate, not the new primary review.
    return source_coverage_outcome(coverage, obligations, review, workflow)


def _field_policy(state, state_path):
    """Recover policy from pinned configuration, never from a state flag alone."""
    if "config_sha256" not in state:
        if any(a.get("status") == "validated" and a.get("role") != "source_obligations"
               for a in state.get("attempts", [])):
            raise ValueError("Successful runtime stages lack pinned configuration")
        return False  # Source-only preparation precedes configuration creation.
    path = _file({"path": str(state_path.parent / "config.json"),
                  "sha256": state["config_sha256"]})
    required = read(path).get("require_field_review", False)
    recorded = state.get("require_field_review", False)
    if type(required) is not bool or type(recorded) is not bool or required != recorded:
        raise ValueError("Field-review policy differs from pinned runtime configuration")
    return required


def _fields(attempt, workflow, packet, prior):
    """Replay the field gate for this successful reviewer and same-run history."""
    from ..workflow_extraction.field_review import WorkflowFieldReview, field_review_outcome

    record = attempt.get("field_review")
    if not record:
        return None
    path = _file(record)
    if path.resolve() != (Path(attempt["directory"]) / "field_review.json").resolve():
        raise ValueError("Field review must belong to its successful reviewer")
    review = WorkflowFieldReview.model_validate_json(path.read_bytes())
    if record.get("document_sha256") != digest(review):
        raise ValueError("Field review document digest changed")
    history = {}
    if prior is not None:
        old_review, old_workflow = prior
        inputs = attempt.get("inputs", {})
        if not all(key in inputs for key in ("previous_field_review", "previous_field_candidate")):
            raise ValueError("Field review repair lacks pinned immediate prior artifacts")
        prior_review = WorkflowFieldReview.model_validate_json(
            _file(inputs["previous_field_review"]).read_bytes())
        prior_workflow = _workflow(_file(inputs["previous_field_candidate"]), packet)
        if (digest(prior_review) != digest(old_review)
                or digest(prior_workflow) != digest(old_workflow)):
            raise ValueError("Field review history differs from the prior successful reviewer")
        history = {"prior_review": prior_review, "prior_workflow": prior_workflow}
    return review, field_review_outcome(review, workflow, packet, **history), record


def _recover(row, packet, config):
    """Latest validated candidate/pair across preserved fresh runtime attempts."""
    candidate = None
    pair = None
    obligations = None
    last_state = {}
    for recorded in row["attempts"]:
        state_path = Path(recorded["run"]) / "state.json"
        if not state_path.exists():
            continue
        try:
            state = read(state_path)
        except (OSError, ValueError):
            # A killed runtime may have torn its own state; never reuse unverifiable
            # artifacts merely because a JSON file happens to exist.
            continue
        last_state = state
        require_fields = _field_policy(state, state_path)
        prior_fields = None  # Field-review history is scoped to one runtime invocation.
        if config.require_source_coverage:
            record = state.get("artifacts", {}).get("source_obligations")
            if record:
                path = _file(record)
                # Preserve only a successful stage or a previously validated seed.
                if any(a.get("artifact") == str(path) and _successful_artifact(a)
                       for a in state.get("attempts", [])):
                    obligations = path
        for attempt in state.get("attempts", []):
            if attempt.get("role") not in {"integrator", "reviewer"}:
                continue
            path = _successful_artifact(attempt)
            if path is None:
                continue
            if attempt["role"] == "integrator":
                candidate = (path, _workflow(path, packet))
                pair = None
                continue
            candidate_record = attempt.get("inputs", {}).get("candidate")
            if not candidate_record:
                continue
            candidate_path = _file(candidate_record)
            workflow = _workflow(candidate_path, packet)
            review = WorkflowReview.model_validate_json(path.read_bytes())
            errors = validate_review(review, workflow, packet)
            if errors:
                raise ValueError("Validated review no longer verifies: " + "; ".join(errors))
            candidate = (candidate_path, workflow)
            pair = None
            outcome = review_outcome(review, workflow)
            fields = None
            if require_fields:
                fields = _fields(attempt, workflow, packet, prior_fields)
                if fields is None:
                    continue
                field_review, field_outcome, field_record = fields
                prior_fields = (field_review, workflow)
                if field_outcome == "unassessed":
                    continue  # Unfinished review is not a scientific acceptance.
            if config.require_source_coverage:
                outcome = _coverage(state, workflow, review, packet)
                if outcome is None:
                    continue
            if fields is not None and field_outcome != "supported":
                outcome = "research_needed" if outcome == "research_needed" else "needs_revision"
            pair = {"workflow": str(candidate_path), "review": str(path), "outcome": outcome,
                    "primary_outcome": review_outcome(review, workflow),
                    "runtime_run": str(state_path.parent),
                    "pins": {str(candidate_path): sha(candidate_path), str(path): sha(path)}}
            if config.require_source_coverage:
                pair["accepted_source_coverage"] = {
                    key: state["artifacts"][key] for key in
                    ("source_obligations", "source_coverage_review")}
            if fields is not None:
                pair["accepted_field_review"] = field_record
    return candidate, pair, obligations, last_state


class _LimitedWorker:
    def __init__(self, worker, semaphore, check):
        if getattr(worker, "web_search", False) or getattr(worker, "network_access", False):
            raise ValueError("Source-packet workers require web search and network access disabled")
        self.worker, self.semaphore, self.check = worker, semaphore, check

    def __getattr__(self, name):
        return getattr(self.worker, name)

    async def run(self, prompt, attempt_dir):
        async with self.semaphore:
            self.check()  # Check again at the actual native-session boundary.
            return await self.worker.run(prompt, attempt_dir)


def _classify_failure(state):
    failed = [a for a in state.get("attempts", []) if a.get("status") in {"failed", "cancelled"}]
    if failed:
        directory = Path(failed[-1]["directory"])
        assessment = assess_archived_attempt(directory)
        return "artifact_validation" if assessment.complete else "transport"
    return "runtime"


async def run(run: Path, *, worker_factory=None, extractor=None) -> dict:
    """Execute/retry operational failures, preserving scientific outcomes.

    worker_factory/extractor are injection boundaries for tests, not an alternate
    scientific implementation. The production path delegates every stage to the
    existing native transport and extraction runtime.
    """
    run = Path(run).resolve()
    with controller_lock(run):
        frozen, config, state = _load(run)
        unknown = _native_uncertainty(run)
        if unknown:
            state.update(status="blocked_native_sessions", native_session_uncertainty=unknown,
                         updated_at=now())
            write(run / "state.json", state)
            return state
        state.pop("native_session_uncertainty", None)
        native_limit = asyncio.Semaphore(config.concurrency)
        method_limit = asyncio.Semaphore(config.concurrency)
        factory = worker_factory or (lambda: CodexWorker(
            model=config.model, effort=config.effort, timeout_seconds=config.timeout_seconds,
            web_search=False, network_access=False))
        extract = extractor or runtime.execute
        state.update(status="running", updated_at=now())

        def save():
            state["updated_at"] = now()
            write(run / "state.json", state)

        save()

        async def one(job):
            row = state["jobs"][job["id"]]
            if row["status"] in {"missing_sources", "input_error"}:
                return
            async with method_limit:
                try:
                    check_pins(job["pins"])
                    packet = load_packet(Path(job["source_packet"]))
                    candidate, pair, obligations, prior = _recover(row, packet, config)
                except PinDrift as exc:
                    row.update(status="pin_drift", error=str(exc)); save(); return
                except (OSError, ValueError, KeyError) as exc:
                    row.update(status="artifact_drift", error=f"{type(exc).__name__}: {exc}")
                    save(); return
                if row["status"] in SCIENTIFIC or prior.get("status") in SCIENTIFIC or (
                        pair and pair["outcome"] == "supported"):
                    if pair is None:
                        row.update(status="artifact_drift", error="Completed work lacks a verified workflow/review pair")
                    else:
                        row.update(status=pair["outcome"], result=pair)
                    save(); return
                if row["attempts"] and row["status"] == "running":
                    row["attempts"][-1]["status"] = "interrupted"
                for _ in range(config.transport_retries + 1):
                    mode = config.mode
                    kwargs = {"max_revisions": config.max_revisions}
                    if candidate:
                        mode = "review_only"
                        kwargs["initial_workflow"] = candidate[0]
                        if pair and pair["primary_outcome"] == "needs_revision" and config.max_revisions:
                            mode = "repair"
                            kwargs["repair_review"] = Path(pair["review"])
                    elif job.get("seed"):
                        seed = job["seed"]
                        mode = seed["mode"]
                        kwargs["initial_workflow"] = Path(seed["workflow"])
                        if mode == "repair":
                            kwargs["repair_review"] = Path(seed["review"])
                    if config.require_source_coverage:
                        kwargs["require_source_coverage"] = True
                        obligation_input = obligations or job.get("seed", {}).get("source_obligations_origin")
                        if obligation_input:
                            kwargs["source_obligations"] = Path(obligation_input)
                    attempt_run = run / "methods" / job["id"] / "attempts" / f"{len(row['attempts']) + 1:04d}"
                    record = {"run": str(attempt_run), "mode": mode, "started_at": now(), "status": "running"}
                    row["attempts"].append(record)
                    row.update(status="running"); save()

                    def check():
                        check_pins(frozen["pins"])
                        check_pins(job["pins"])

                    try:
                        check()
                        result = await extract(Path(job["source_packet"]), attempt_run,
                                               worker=_LimitedWorker(factory(), native_limit, check),
                                               mode=mode, **kwargs)
                        check()
                        record.update(status=result["status"], finished_at=now())
                        candidate, pair, obligations, prior = _recover(row, packet, config)
                        if result["status"] in SCIENTIFIC:
                            if pair is None:
                                raise ValueError("Scientific outcome lacks verified completion artifacts")
                            row.update(status=pair["outcome"], result=pair)
                            row.pop("error", None)
                            row.pop("failure_kind", None)
                            break
                        row.update(status="failed", failure_kind=_classify_failure(result),
                                   error=result.get("error", "Extraction runtime failed"))
                    except asyncio.CancelledError:
                        record.update(status="interrupted", finished_at=now())
                        row.update(status="interrupted")
                        save()
                        raise
                    except PinDrift as exc:
                        record.update(status="failed", finished_at=now())
                        row.update(status="pin_drift", error=str(exc)); break
                    except Exception as exc:
                        record.update(status="failed", finished_at=now())
                        row.update(status="failed", failure_kind="runtime", error=f"{type(exc).__name__}: {exc}")
                        try:
                            candidate, pair, obligations, prior = _recover(row, packet, config)
                        except (OSError, ValueError, KeyError) as recovery_error:
                            row.update(status="artifact_drift", error=str(recovery_error))
                            break
                    finally:
                        save()

        tasks = [asyncio.create_task(one(job)) for job in frozen["jobs"]]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            state.update(status="interrupted")
            save()
            raise
        except BaseException:
            # Never release the controller lock with owned workers still running,
            # including unexpected storage/validation failures in a sibling task.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            state.update(status="failed")
            with contextlib.suppress(OSError):
                save()
            raise
        # Revalidate every supported parent at the moment of export; status alone
        # never admits an edge or even a parent. Other methods survive a bad one.
        parents, accepted = {}, {}
        for job in frozen["jobs"]:
            row = state["jobs"][job["id"]]
            if row["status"] != "supported":
                continue
            try:
                check_pins(job["pins"])
                packet = load_packet(Path(job["source_packet"]))
                _, pair, _, _ = _recover(row, packet, config)
                if not pair or pair["outcome"] != "supported":
                    raise ValueError("Parent no longer has a supported validated review")
                parents[job["id"]] = {key: pair[key] for key in ("workflow", "review")}
                parents[job["id"]]["packet"] = job["source_packet"]
                accepted[job["id"]] = {**pair, "packet_sha256": sha(Path(job["source_packet"]))}
            except (OSError, ValueError, KeyError) as exc:
                row.update(status="artifact_drift", error=str(exc))
        check_pins(frozen["pins"])
        write(run / "parents.json", parents)
        state.update(status="completed" if all(r["status"] in SCIENTIFIC for r in state["jobs"].values()) else "partial",
                     accepted_parents=accepted, finished_at=now(),
                     boundary="Accepted parents only. Cross-method unit selection, comparison evidence, edge admission and expert assessment are separate operations.")
        save()
        return state
