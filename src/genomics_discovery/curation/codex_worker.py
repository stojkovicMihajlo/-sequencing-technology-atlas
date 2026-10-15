"""Run an auditable native Codex job in a caller-owned attempt directory.

This is a job transport, not the message-by-message provider in ``core.codex_cli``.
Codex can read papers, search, and create files throughout one native agent turn.
The caller owns pool concurrency, artifact validation, and retry policy.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import signal
import subprocess
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
_STREAM_ERROR = "Codex emitted error; see events.jsonl"


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

    def _command(self, attempt_dir: Path) -> list[str]:
        command = [
            self.executable,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "workspace-write",
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
        if self.model != "default":
            command.extend(("--model", self.model))
        return [*command, "-"]

    async def run(self, prompt: str, attempt_dir: Path) -> WorkerResult:
        """Archive an attempt and return its outcome; cancellation still propagates.

        An attempt directory may already contain input files, but existing job
        archives are never overwritten. A failed attempt must be retried in a new
        directory. Usage sums numeric fields from each ``turn.completed`` event
        once; cumulative item events are not counted as additional usage.
        """
        attempt_dir = Path(attempt_dir).resolve()
        attempt_dir.mkdir(parents=True, exist_ok=True)
        if any((attempt_dir / name).exists() for name in _ARCHIVES):
            raise FileExistsError(f"Attempt already has job archives: {attempt_dir}")
        # Exclusive prompt creation also prevents two workers claiming the same attempt.
        with (attempt_dir / "prompt.txt").open("x", encoding="utf-8") as handle:
            handle.write(prompt)
        command = self._command(attempt_dir)
        environment = {
            key: value for key, value in os.environ.items() if key not in _PARENT_SESSION_ENV
        }
        (attempt_dir / "command.json").write_text(
            json.dumps({
                "argv": command,
                "cwd": str(attempt_dir),
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
                        "process_start_identity": None, "spawned": False}, indent=2) + "\n",
            encoding="utf-8",
        )
        started = time.monotonic()
        event_state = _EventState()
        process: asyncio.subprocess.Process | None = None
        completion: asyncio.Task[None] | None = None
        exit_code = 1
        error: str | None = None
        cancelled = False

        def record_started() -> None:
            assert process is not None
            record = {
                "pid": process.pid,
                "process_group_id": process.pid if os.name == "posix" else None,
                "started_at": datetime.now(UTC).isoformat(),
                "process_start_identity": _process_start_identity(process.pid),
                "spawned": True,
                "cwd": str(attempt_dir),
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
                completion.cancel()
            if completion is not None:
                await asyncio.gather(completion, return_exceptions=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=_SHUTDOWN_GRACE_SECONDS)

        async def cleanup_process() -> None:
            nonlocal cancelled
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

        with (attempt_dir / "events.jsonl").open("xb") as events_handle, (
            attempt_dir / "stderr.log"
        ).open("xb") as stderr_handle:
            try:
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
                await asyncio.wait_for(asyncio.shield(completion), timeout=self.timeout_seconds)
                exit_code = process.returncode if process.returncode is not None else 1
                # A completed CLI can leave background tool children whose streams
                # were redirected. Release the slot only after its owned group is
                # stopped, including on ordinary success or nonzero process exit.
                await cleanup_process()
                if exit_code:
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
