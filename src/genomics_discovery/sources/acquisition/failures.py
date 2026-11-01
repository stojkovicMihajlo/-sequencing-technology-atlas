"""Bounded operational diagnostics; raw native text stays in its original archive.

These labels report execution evidence, never source absence or scientific scope.
They do not alter worker or archive acceptance and cannot authorize a retry.
"""

import errno
import hashlib
import json
from pathlib import Path

_MAX_EVENT_BYTES = 8 * 1024 * 1024
_POLICY_PREFIX = "invalid prompt: we've limited access to this content for safety reasons."
_MESSAGES = {
    "provider_policy_rejection": "The native provider reported a safety restriction on this finder request.",
    "native_timeout": "The host worker timed out before a complete finder result.",
    "native_transport_failure": "The native worker reported a transport failure.",
    "native_execution_failure": "The native finder did not complete successfully.",
    "native_archive_invalid": "The finder archive or returned metadata did not pass existing integrity checks.",
    "finder_output_invalid": "The finder did not supply a readable, schema-conforming artifact for this method.",
}


def host_storage_failure(exc):
    """Report host capacity failures without attributing them to a source endpoint."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}:
            return {"stage": "host_storage", "class": "host_storage_failure",
                    "message": "Host storage capacity prevented retaining or inspecting source bytes.",
                    "errno": exc.errno, "source_availability": "not_assessed"}
        exc = exc.__cause__
    return None


def _pointer(path, *, line=None, pointer=None):
    path = Path(path).absolute()
    exists = path.is_file() and not path.is_symlink()
    binding = {"path": str(path), "exists": exists, "sha256": None,
               "line": line, "json_pointer": pointer}
    if exists:
        with path.open("rb") as handle:
            binding["sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    return binding


def _failure_events(attempt):
    """Read control events only; never promote tool text or a resolved reconnect."""
    path = Path(attempt) / "events.jsonl"
    error = failed = None
    if not path.is_file() or path.is_symlink():
        return error, failed, None
    binding = _pointer(path)
    with path.open("rb") as handle:
        line = 0
        while raw := handle.readline(_MAX_EVENT_BYTES + 1):
            line += 1
            try:
                if len(raw) > _MAX_EVENT_BYTES:
                    raise ValueError("Oversized event")
                event = json.loads(raw)
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    raise ValueError("Invalid event")
            except (ValueError, UnicodeDecodeError):
                return error, failed, {**binding, "line": line}
            if event["type"] == "turn.completed":
                if not isinstance(event.get("usage"), dict):
                    return error, failed, {**binding, "line": line,
                        "json_pointer": "/usage" if "usage" in event else ""}
                error = None  # The common worker also resolves reconnect errors.
            elif event["type"] in {"error", "turn.failed"}:
                pointer = "/message" if event["type"] == "error" else "/error"
                message = event.get("message") if event["type"] == "error" else event.get("error")
                if isinstance(message, dict):
                    message = message.get("message")
                    pointer += "/message"
                if not isinstance(message, str):
                    pointer = ""
                item = (message[:2048] if isinstance(message, str) else "", {**binding, "line": line, "json_pointer": pointer})
                if event["type"] == "error":
                    error = item
                else:
                    failed = item
    return error, failed, None


def _detail(kind, pointer, *, exit_code=None):
    return {"stage": "finder", "class": kind, "message": _MESSAGES[kind],
            "archive_pointer": pointer, "native_exit_code": exit_code,
            "source_availability": "not_assessed"}


def native_failure(attempt, result=None, *, archive=None, prompt_changed=False, worker_raised=False):
    """Classify a rejected attempt conservatively, without echoing provider text."""
    attempt = Path(attempt)
    error, failed, invalid = _failure_events(attempt)
    code = getattr(result, "exit_code", None)
    if invalid:
        return _detail("native_archive_invalid", invalid, exit_code=code)
    for item in (error, failed):
        if item and item[0].casefold().startswith(_POLICY_PREFIX):
            return _detail("provider_policy_rejection", item[1], exit_code=code)
    message = getattr(result, "error", None) or ""
    if code == 124 and message.startswith(("Codex exceeded the ", "Codex completion raised an unexpected timeout")):
        return _detail("native_timeout", _pointer(attempt / "result.json", pointer="/error"), exit_code=code)
    if message.startswith("Codex transport failed ("):
        return _detail("native_transport_failure", _pointer(attempt / "result.json", pointer="/error"), exit_code=code)
    if code not in (None, 0) or message or failed or worker_raised:
        return _detail("native_execution_failure", (failed or error or (None,
            _pointer(attempt / "result.json", pointer="/error")))[1], exit_code=code)
    pointer = _pointer(attempt / "result.json")
    if prompt_changed:
        pointer = _pointer(attempt / "prompt.txt")
    elif getattr(archive, "error", None) == "Archived attempt has no nonempty final.txt":
        pointer = _pointer(attempt / "final.txt")
    elif getattr(archive, "error", None) == "Archived events have no completed turn":
        pointer = _pointer(attempt / "events.jsonl")
    elif getattr(archive, "error", None) == "Archived transport evidence is missing or unreadable":
        for name in ("result.json", "events.jsonl", "final.txt"):
            if not (attempt / name).is_file():
                pointer = _pointer(attempt / name)
                break
    return _detail("native_archive_invalid", pointer, exit_code=code)


def output_failure(attempt, *, method_mismatch=False):
    return _detail("finder_output_invalid", _pointer(Path(attempt) / "artifact.json",
                   pointer="/method_id" if method_mismatch else ""))


class SourceFinderFailure(ValueError):
    def __init__(self, detail):
        self.detail = detail
        ref = detail["archive_pointer"]
        location = ref["path"] + (f":{ref['line']}" if ref["line"] else "")
        if ref["json_pointer"]:
            location += "#" + ref["json_pointer"]
        super().__init__(f"{detail['class']}: {detail['message']} Archive: {location}")
