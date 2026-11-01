"""Run an auditable native Codex job in a caller-owned attempt directory.

This is a job transport, not the message-by-message provider in ``core.codex_cli``.
Codex can read papers, search, and create files throughout one native agent turn.
The caller owns pool concurrency, artifact validation, and retry policy.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import shlex
import signal
import socket
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_EFFORT = "xhigh"

_ARCHIVES = (
    "prompt.txt", "command.json", "started.json", "events.jsonl", "stderr.log", "final.txt",
    "result.json",
)
_PARENT_SESSION_ENV = {"CODEX_THREAD_ID", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE"}
_MAX_EVENT_BYTES = 8 * 1024 * 1024
_SHUTDOWN_GRACE_SECONDS = 2.0
_PROCESS_EXIT_POLL_SECONDS = 0.05
_STREAM_ERROR = "Codex emitted error; see events.jsonl"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def pinned_shell_environment(*, python_executable=None, environment=None) -> dict[str, str]:
    """Explicit nonsecret overrides; preserve virtualenv paths without resolving symlinks."""
    environment = os.environ if environment is None else environment
    python = Path(python_executable or sys.executable).absolute()
    path = str(python.parent) + (os.pathsep + environment["PATH"] if environment.get("PATH") else "")
    return {"PATH": path, **{key: environment[key] for key in ("PYTHONPATH", "LD_LIBRARY_PATH")
                             if key in environment}}


def preflight_pinned_shell_environment(*, overrides=None, python_executable=None) -> dict:
    """Optional caller-run Bash probe, not automatic worker integration or a model call.

    Compare the intended interpreter with bare python under the same pinned env.
    BASH_ENV and other startup behavior are retained: a mismatch fails, never gets
    silently repaired. Arbitrary later commands can still override the environment.
    """
    python = Path(python_executable or sys.executable).absolute()
    env = dict(os.environ)
    env.update(pinned_shell_environment(python_executable=python) if overrides is None else overrides)
    code = "import json,sys; print(json.dumps({'executable':sys.executable,'version':list(sys.version_info[:3]),'prefix':sys.prefix}))"
    expected = subprocess.run([str(python), "-c", code], env=env, capture_output=True, text=True, check=True)
    observed = subprocess.run(["/bin/bash", "-c", "command -v python; python -c " + shlex.quote(code)],
                              env=env, capture_output=True, text=True, check=True)
    try:
        reference = json.loads(expected.stdout)
        resolved, fingerprint = observed.stdout.strip().split("\n")
        actual = json.loads(fingerprint)
        valid = (Path(resolved).absolute().parent == python.parent
                 and Path(actual["executable"]).absolute().parent == python.parent
                 and actual["version"] == reference["version"] and actual["prefix"] == reference["prefix"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Pinned Python preflight produced an invalid fingerprint") from exc
    if not valid:
        raise ValueError("Bare python does not match the pinned interpreter/version/prefix")
    return {"passed": True, "python_resolution": resolved, "fingerprint": actual,
            "boundary": "This Bash startup passed; later commands can explicitly override interpreter or environment."}


@dataclass
class _EventState:
    """Reduce native CLI events without retaining provider diagnostic text."""

    thread_id: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    completed_turns: int = 0
    invalid_lines: int = 0
    turn_in_progress: bool = False
    failed_turn: bool = False
    unresolved_error: bool = False

    def record(self, raw: bytes) -> None:
        try:
            event = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            self.invalid_lines += 1
            return  # The raw archive retains non-JSON diagnostics.
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            self.invalid_lines += 1
            return
        event_type = event["type"]
        if event_type == "thread.started":
            if not isinstance(event.get("thread_id"), str) or not event["thread_id"]:
                self.invalid_lines += 1
                return
            self.thread_id = event["thread_id"]
        elif event_type == "turn.started":
            self.turn_in_progress = True
        elif event_type == "turn.completed":
            if not isinstance(event.get("usage"), dict):
                self.invalid_lines += 1
                return
            self.completed_turns += 1
            self.turn_in_progress = False
            # Native exec emits `error` for reconnect attempts too. A subsequent
            # successful terminal event resolves those diagnostics, but can never
            # erase an explicit turn.failed from this attempt.
            self.unresolved_error = False
            for key, value in event["usage"].items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self.usage[key] = self.usage.get(key, 0) + value
        elif event_type == "turn.failed":
            self.failed_turn = True
            self.turn_in_progress = False
        elif event_type == "error":
            self.unresolved_error = True

    @property
    def error(self) -> str | None:
        # Provider messages stay in events.jsonl, never scheduler result.json.
        if self.failed_turn:
            return "Codex emitted turn.failed; see events.jsonl"
        if self.unresolved_error:
            return _STREAM_ERROR
        if self.turn_in_progress:
            return "Codex exited with an unfinished turn; see events.jsonl"
        return None


@dataclass(frozen=True)
class ArchivedAttemptAssessment:
    complete: bool
    error: str | None
    thread_id: str | None
    usage: dict[str, Any]
    completed_turns: int


def assess_archived_attempt(attempt_dir: Path) -> ArchivedAttemptAssessment:
    """Read, never rewrite, archived transport evidence for explicit recovery.

    This is stricter than live diagnostic parsing: malformed/truncated JSONL,
    missing completion, mismatched result metadata, and failures other than the
    old sticky stream-error classification are ineligible. Artifact validation
    and archive snapshotting remain the caller's responsibility.
    """
    attempt_dir = Path(attempt_dir)
    state = _EventState()

    def assessment(error: str | None) -> ArchivedAttemptAssessment:
        return ArchivedAttemptAssessment(
            error is None, error, state.thread_id, state.usage, state.completed_turns
        )

    try:
        result = json.loads((attempt_dir / "result.json").read_bytes())
        if (
            not isinstance(result, dict)
            or type(result.get("exit_code")) is not int
            or "error" not in result
            or "thread_id" not in result
            or not isinstance(result.get("usage"), dict)
            or not isinstance(result.get("elapsed_seconds"), (int, float))
            or isinstance(result["elapsed_seconds"], bool)
            or not math.isfinite(result["elapsed_seconds"])
            or result["elapsed_seconds"] < 0
        ):
            return assessment("Invalid archived result.json")
        if result["exit_code"] != 0:
            return assessment("Archived Codex process did not exit successfully")
        if result["error"] not in (None, _STREAM_ERROR):
            return assessment("Archived attempt has a nonrecoverable transport error")
        with (attempt_dir / "events.jsonl").open("rb") as handle:
            while raw := handle.readline(_MAX_EVENT_BYTES + 1):
                if len(raw) > _MAX_EVENT_BYTES:
                    return assessment("Archived events contain an oversized JSONL record")
                state.record(raw)
        if state.invalid_lines:
            return assessment("Archived events contain invalid JSONL records")
        if state.error:
            return assessment(state.error)
        if not state.completed_turns:
            return assessment("Archived events have no completed turn")
        if result["thread_id"] != state.thread_id or result["usage"] != state.usage:
            return assessment("Archived result metadata disagrees with events.jsonl")
        final = attempt_dir / "final.txt"
        if not final.is_file() or not final.stat().st_size:
            return assessment("Archived attempt has no nonempty final.txt")
    except (OSError, ValueError, UnicodeDecodeError):
        return assessment("Archived transport evidence is missing or unreadable")
    return assessment(None)


def classify_archived_terminal_failure(attempt_dir: Path) -> dict[str, Any]:
    """Nonaccepting diagnosis from typed terminal records, never model/tool text.

    Only a final turn.failed, not superseded by a later start, can supply a known
    category. Earlier error records can be recoverable; even explicit capacity
    is evidence about this attempt, not global load or a reason to retry/change
    models. Raw messages remain in the archive. Invalid archives remain unknown.
    """
    diagnosis: dict[str, Any] = {"category": "unknown", "basis": "no_terminal_failure",
                               "event": None, "archive_sha256": {}}
    try:
        result_raw = (Path(attempt_dir) / "result.json").read_bytes()
        diagnosis["archive_sha256"]["result.json"] = hashlib.sha256(result_raw).hexdigest()
        result = json.loads(result_raw)
        if not isinstance(result, dict) or type(result.get("exit_code")) is not int:
            diagnosis["basis"] = "invalid_result_archive"
            return diagnosis
        thread_id = result.get("thread_id")
        if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
            diagnosis["basis"] = "invalid_result_archive"
            return diagnosis
        last_terminal = None
        malformed = False
        thread_mismatch = False
        archive_hash = hashlib.sha256()
        with (Path(attempt_dir) / "events.jsonl").open("rb") as handle:
            line = 0
            while raw := handle.readline(_MAX_EVENT_BYTES + 1):
                line += 1
                if len(raw) > _MAX_EVENT_BYTES:
                    diagnosis["basis"] = "oversized_event_archive"
                    return diagnosis
                archive_hash.update(raw)
                try:
                    event = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    malformed = True
                    continue
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    malformed = True
                    continue
                # Only native top-level identity is authoritative; item payloads
                # can contain arbitrary model/tool-authored thread identifiers.
                if "thread_id" in event or event["type"] == "thread.started":
                    event_thread = event.get("thread_id")
                    if not isinstance(event_thread, str) or not event_thread:
                        malformed = True
                    elif thread_id is not None and event_thread != thread_id:
                        thread_mismatch = True
                    else:
                        thread_id = event_thread
                if event["type"] in {"thread.started", "turn.started"}:
                    # A prior failure cannot diagnose a new unfinished turn.
                    last_terminal = None
                elif event["type"] in {"turn.completed", "turn.failed"}:
                    last_terminal = (line, raw, event)
        diagnosis["archive_sha256"]["events.jsonl"] = archive_hash.hexdigest()
        if malformed:
            diagnosis["basis"] = "invalid_event_archive"
            return diagnosis
        if thread_mismatch:
            diagnosis["basis"] = "thread_identity_mismatch"
            return diagnosis
        if last_terminal is None or last_terminal[2]["type"] != "turn.failed":
            return diagnosis
        line, raw, event = last_terminal
        diagnosis["event"] = {"type": "turn.failed", "line": line,
                               "sha256": hashlib.sha256(raw).hexdigest(),
                               "error_pointer": "/error"}
        error = event.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
            diagnosis["basis"] = "invalid_terminal_error"
            return diagnosis
        # This exact uncoded message is pinned from CLI 0.163.0. Other provider
        # codes/phrases (including quota/auth/output errors) are not inferred.
        observed_capacity = ("code" not in error and error["message"] ==
            "Selected model is at capacity. Please try a different model.")
        diagnosis.update(category="provider_capacity" if observed_capacity else "unknown",
                         basis="terminal_error_record")
    except (OSError, ValueError, UnicodeDecodeError):
        diagnosis["basis"] = "unreadable_or_invalid_archive"
    return diagnosis


def _process_start_identity(pid: int) -> dict[str, str] | None:
    """Best-effort PID-reuse evidence; never inspect process arguments or credentials.

    This is metadata for checking an interrupted job, not authorization to signal
    a PID. A missing identity requires further inspection before killing a process.
    """
    if os.name != "posix":
        return None
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0 and result.stdout.strip():
        return {"kind": "ps_lstart", "value": result.stdout.strip()}
    return None


@dataclass(frozen=True)
class WorkerResult:
    exit_code: int
    thread_id: str | None
    usage: dict[str, Any]
    elapsed_seconds: float
    error: str | None


def _image_inventory(attempt_dir: Path, image_paths) -> list[dict[str, str]]:
    """Bind explicitly staged PNG bytes, without claiming page identity or vision."""
    if isinstance(image_paths, (str, bytes, os.PathLike, set, frozenset, dict)):
        raise ValueError("image_paths must be an ordered collection of staged PNG paths")
    try:
        image_paths = tuple(image_paths)
    except TypeError as exc:
        raise ValueError("image_paths must be an ordered collection of staged PNG paths") from exc
    inventory, seen = [], set()
    for supplied in image_paths:
        try:
            path = Path(supplied)
            if "," in str(path):
                raise ValueError("Image paths must not contain the CLI comma-list delimiter")
            path = path if path.is_absolute() else attempt_dir / path
            relative = path.relative_to(attempt_dir)
            if ".." in relative.parts:
                raise ValueError("Image paths must not escape or traverse their attempt")
            current = attempt_dir
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise ValueError("Image paths must not contain symlinks")
            info = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Image inputs must be regular PNG files")
            identity = (info.st_dev, info.st_ino)
            if identity in seen:
                raise ValueError("Duplicate image input")
            seen.add(identity)
            raw = path.read_bytes()
            if not raw.startswith(_PNG_SIGNATURE):
                raise ValueError("Image inputs must have a PNG signature")
        except (OSError, TypeError) as exc:
            raise ValueError("Image input is not an available staged PNG file") from exc
        inventory.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()})
    return inventory


class CodexWorker:
    """One sandboxed process per job; no implicit nested agent pool or retries."""

    def __init__(
        self,
        executable: str = "codex",
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        timeout_seconds: float | None = 1800,
        web_search: bool = True,
        network_access: bool = True,
        pin_shell_environment: bool = False,
    ) -> None:
        if not executable or not model or not effort:
            raise ValueError("executable, model, and effort must be nonempty")
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be None or finite and positive")
        self.executable = executable
        self.model = model
        self.effort = effort
        self.timeout_seconds = timeout_seconds
        self.web_search = web_search
        self.network_access = network_access
        if type(pin_shell_environment) is not bool:
            raise ValueError("pin_shell_environment must be a boolean")
        self.pin_shell_environment = pin_shell_environment
        self._shell_python = str(Path(sys.executable).absolute()) if pin_shell_environment else None
        self._shell_overrides = pinned_shell_environment() if pin_shell_environment else {}

    def _command(self, attempt_dir: Path, image_paths=()) -> list[str]:
        command = [
            self.executable,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "workspace-write",
            # Noninteractive exec otherwise cannot request an approved escalation
            # when the host cannot start its inner sandbox. Review every request;
            # never substitute a sandbox bypass or weaken managed requirements.
            "--config",
            'approval_policy="on-request"',
            "--config",
            'approvals_reviewer="auto_review"',
            "--skip-git-repo-check",
            "--disable",
            "multi_agent",
            "--disable",
            "multi_agent_v2",
            "--color",
            "never",
            "--output-last-message",
            str(attempt_dir / "final.txt"),
            "--config",
            f"model_reasoning_effort={json.dumps(self.effort)}",
            "--config",
            f'web_search="{"live" if self.web_search else "disabled"}"',
            "--config",
            f"sandbox_workspace_write.network_access={str(self.network_access).lower()}",
        ]
        if self.pin_shell_environment:
            command.extend(("--config", "allow_login_shell=false", "--disable", "shell_snapshot"))
            for key, value in self._shell_overrides.items():
                command.extend(("--config", f"shell_environment_policy.set.{key}={json.dumps(value)}"))
        if self.model != "default":
            command.extend(("--model", self.model))
        for path in image_paths:
            command.extend(("--image", str(path)))
        return [*command, "-"]

    async def run(self, prompt: str, attempt_dir: Path, *, image_paths=()) -> WorkerResult:
        """Archive an attempt and return its outcome; cancellation still propagates.

        An attempt directory may already contain input files, but existing job
        archives are never overwritten. A failed attempt must be retried in a new
        directory. Usage sums numeric fields from each ``turn.completed`` event
        once; cumulative item events are not counted as additional usage.

        Optional images must already be regular PNG files inside this attempt.
        Invalid attachment arguments raise before archives/process creation. Image
        hashes are checked before launch and after cleanup; a changed input makes
        the result nonaccepting. The caller owns original PDF/page/source binding;
        explicit CLI attachment does not establish image understanding/entailment.
        """
        attempt_dir = Path(attempt_dir).resolve()
        attempt_dir.mkdir(parents=True, exist_ok=True)
        if any((attempt_dir / name).exists() for name in _ARCHIVES):
            raise FileExistsError(f"Attempt already has job archives: {attempt_dir}")
        images = _image_inventory(attempt_dir, image_paths)
        # Exclusive prompt creation also prevents two workers claiming the same attempt.
        with (attempt_dir / "prompt.txt").open("x", encoding="utf-8") as handle:
            handle.write(prompt)
        command = self._command(attempt_dir, [row["path"] for row in images])
        environment = {
            key: value for key, value in os.environ.items() if key not in _PARENT_SESSION_ENV
        }
        (attempt_dir / "command.json").write_text(
            json.dumps({
                "argv": command,
                "cwd": str(attempt_dir),
                **({"shell_environment_profile": {"enabled": True, "python_executable": self._shell_python,
                    "pinned_variable_names": list(self._shell_overrides), "automatic_preflight": False,
                    "guarantee": "Codex default shell uses non-login semantics, snapshots are disabled and declared environment values are explicitly injected.",
                    "limits": "BASH_ENV and managed policy remain intact; startup or explicit commands may override interpreter/environment. This is not a universal Python-execution guarantee."}}
                   if self.pin_shell_environment else {}),
                **({"image_inputs": images, "image_input_boundary":
                    "Explicit CLI attachments only; caller owns PDF/page/source identity. "
                    "Attachment does not establish image understanding or entailment."} if images else {}),
                "authentication": {
                    "selection": "normal Codex CLI authentication",
                    "credential_mode": "inherited environment and existing CLI login; not forced",
                    "openai_api_key_present": bool(environment.get("OPENAI_API_KEY")),
                    "codex_api_key_present": bool(environment.get("CODEX_API_KEY")),
                    "note": "Presence flags do not establish the backend or billing account used.",
                },
            }, indent=2) + "\n",
            encoding="utf-8",
        )
        (attempt_dir / "started.json").write_text(
            json.dumps({"pid": None, "process_group_id": None, "started_at": None,
                        "process_start_identity": None, "spawned": False,
                        "hostname": socket.gethostname(),
                        "slurm_job_id": environment.get("SLURM_JOB_ID")}, indent=2) + "\n",
            encoding="utf-8",
        )
        started = time.monotonic()
        event_state = _EventState()
        process: asyncio.subprocess.Process | None = None
        completion: asyncio.Task[None] | None = None
        exit_code = 1
        error: str | None = None
        cancelled = False
        cleanup_complete = False

        def record_started() -> None:
            assert process is not None
            record = {
                "pid": process.pid,
                "process_group_id": process.pid if os.name == "posix" else None,
                "started_at": datetime.now(UTC).isoformat(),
                "process_start_identity": _process_start_identity(process.pid),
                "spawned": True,
                "cwd": str(attempt_dir),
                "hostname": socket.gethostname(),
                "slurm_job_id": environment.get("SLURM_JOB_ID"),
            }
            temporary = attempt_dir / "started.json.tmp"
            temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            temporary.replace(attempt_dir / "started.json")

        async def stream_stdout(handle: Any) -> None:
            assert process is not None and process.stdout is not None
            pending = bytearray()
            oversized = False
            while chunk := await process.stdout.read(64 * 1024):
                handle.write(chunk)
                handle.flush()
                pieces = chunk.split(b"\n")
                for index, piece in enumerate(pieces):
                    if not oversized:
                        pending.extend(piece)
                        if len(pending) > _MAX_EVENT_BYTES:
                            pending.clear()
                            oversized = True
                    if index < len(pieces) - 1:
                        if not oversized:
                            event_state.record(bytes(pending))
                        pending.clear()
                        oversized = False
            if pending and not oversized:
                event_state.record(bytes(pending))

        async def finish_process(handle: Any) -> None:
            assert process is not None and process.stdin is not None

            async def send_prompt() -> None:
                assert process is not None and process.stdin is not None
                try:
                    process.stdin.write(prompt.encode("utf-8"))
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass  # The process exit and stderr describe an early failure.
                finally:
                    process.stdin.close()

            tasks = [
                asyncio.create_task(send_prompt()),
                asyncio.create_task(stream_stdout(handle)),
                asyncio.create_task(process.wait()),
            ]
            try:
                await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        async def wait_for_leader_or_completion() -> None:
            assert process is not None and completion is not None
            # A descendant can inherit stdout after its leader exits. Older
            # asyncio Process.wait() futures can also await pipe disconnection.
            # Public returncode detects leader exit without requiring EOF; this
            # cadence is observation latency, never a deadline for a living CLI.
            while process.returncode is None and not completion.done():
                await asyncio.sleep(_PROCESS_EXIT_POLL_SECONDS)
            if completion.done():
                completion.result()  # Propagate I/O errors before normal validation.

        async def stop_process() -> None:
            if process is None:
                return

            def send_signal(sig: signal.Signals) -> None:
                with contextlib.suppress(ProcessLookupError):
                    if os.name == "posix":
                        # start_new_session gave only this job this process group.
                        # Signal it even if its leader exited but descendants remain.
                        os.killpg(process.pid, sig)
                    elif process.returncode is None:
                        process.terminate() if sig == signal.SIGTERM else process.kill()

            send_signal(signal.SIGTERM)
            if completion is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        asyncio.shield(completion), timeout=_SHUTDOWN_GRACE_SECONDS
                    )
            # A descendant may ignore TERM after the leader has exited. Never leave
            # such children running simply because process.returncode is populated.
            send_signal(signal.SIGKILL)
            if completion is not None and not completion.done():
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        asyncio.shield(completion), timeout=_SHUTDOWN_GRACE_SECONDS
                    )
            if completion is not None and not completion.done():
                completion.cancel()
            if completion is not None:
                await asyncio.gather(completion, return_exceptions=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=_SHUTDOWN_GRACE_SECONDS)

        async def cleanup_process() -> None:
            nonlocal cancelled, cleanup_complete
            if cleanup_complete:
                return
            # Cancellation can arrive while a timeout or other failure is already
            # reaping the process. Shield every cleanup path, then propagate only
            # after the child is reaped and the result archive has been written.
            cleanup = asyncio.create_task(stop_process())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            await cleanup
            cleanup_complete = True

        with (attempt_dir / "events.jsonl").open("xb") as events_handle, (
            attempt_dir / "stderr.log"
        ).open("xb") as stderr_handle:
            try:
                if images != _image_inventory(attempt_dir, [row["path"] for row in images]):
                    raise ValueError("Image inputs changed before launch")
                spawning = asyncio.create_task(
                    asyncio.create_subprocess_exec(
                        *command,
                        cwd=attempt_dir,
                        env=environment,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=stderr_handle,
                        start_new_session=os.name == "posix",
                    )
                )
                try:
                    process = await asyncio.shield(spawning)
                except asyncio.CancelledError:
                    # Creation can have launched a child before yielding its handle.
                    # Recover the handle so cancellation cannot orphan that child.
                    while not spawning.done():
                        try:
                            await asyncio.shield(spawning)
                        except asyncio.CancelledError:
                            continue
                    with contextlib.suppress(OSError):
                        process = spawning.result()
                    if process is not None:
                        with contextlib.suppress(OSError):
                            record_started()
                    raise
                record_started()
                completion = asyncio.create_task(finish_process(events_handle))
                await asyncio.wait_for(wait_for_leader_or_completion(), timeout=self.timeout_seconds)
                exit_code = process.returncode if process.returncode is not None else 1
                # Drain stdout while stopping the owned group even when a child
                # holds its pipe open. Validate only after this bounded cleanup.
                await cleanup_process()
                if completion.cancelled():
                    error = "Codex stdout did not reach EOF during process-group cleanup"
                elif completion.exception() is not None:
                    raise completion.exception()
                elif exit_code:
                    error = f"Codex exited with code {exit_code}; see stderr.log and events.jsonl"
                elif event_state.error:
                    error = event_state.error
                elif not (attempt_dir / "final.txt").is_file():
                    error = "Codex exited without final.txt"
                elif not (attempt_dir / "final.txt").stat().st_size:
                    error = "Codex produced an empty final.txt"
                elif not event_state.completed_turns:
                    error = "Codex exited without a completed turn; see events.jsonl"
            except TimeoutError:
                await cleanup_process()
                exit_code = 124
                error = (f"Codex exceeded the {self.timeout_seconds:g}-second attempt timeout"
                         if self.timeout_seconds is not None else
                         "Codex completion raised an unexpected timeout without a host time limit")
            except asyncio.CancelledError:
                cancelled = True
                await cleanup_process()
            except OSError as exc:
                await cleanup_process()
                exit_code = 127 if isinstance(exc, FileNotFoundError) else 1
                error = f"Codex could not run ({type(exc).__name__}, errno={exc.errno})"
            except Exception as exc:
                await cleanup_process()
                error = f"Codex transport failed ({type(exc).__name__}); inspect attempt archives"

        # Every attempted job has the same archive layout, including failed starts.
        if cancelled:
            exit_code = -signal.SIGTERM
            error = "Codex attempt cancelled"
        if images:
            try:
                unchanged = images == _image_inventory(attempt_dir, [row["path"] for row in images])
            except ValueError:
                unchanged = False
            if not unchanged:
                image_error = "Codex image input changed or is unavailable after execution"
                error = f"{error}; {image_error}" if error else image_error
        (attempt_dir / "final.txt").touch(exist_ok=True)
        result = WorkerResult(
            exit_code, event_state.thread_id, event_state.usage, time.monotonic() - started, error
        )
        temporary_result = attempt_dir / "result.json.tmp"
        temporary_result.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
        temporary_result.replace(attempt_dir / "result.json")
        if cancelled:
            raise asyncio.CancelledError
        return result
