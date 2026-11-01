"""Atomic controller records and a process-scoped, non-stealable POSIX lock."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import socket
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path: Path):
    return json.loads(path.read_bytes())


def atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def write(path: Path, value) -> None:
    atomic_bytes(path, (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False,
                                  allow_nan=False) + "\n").encode())


class ControllerBusy(RuntimeError):
    pass


@contextlib.contextmanager
def controller_lock(run: Path):
    """Keep the lock inode; unlinking it would permit competing controllers."""
    run.mkdir(parents=True, exist_ok=True)
    with (run / ".controller.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ControllerBusy(f"Another controller owns {run}") from exc
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(json.dumps({"pid": os.getpid(), "hostname": socket.gethostname(),
                                     "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
                                     "acquired_at": now()}) + "\n")
            handle.flush()
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def controller_liveness(run: Path) -> dict:
    """Inspect the shared lock without creating it or trusting a saved PID.

    Lock metadata describes the last owner and may have been copied from another
    host. Only the kernel lock establishes that a controller is active now.
    """
    try:
        with (run / ".controller.lock").open("r") as handle:
            try:
                owner = json.load(handle)
            except (ValueError, TypeError):
                owner = None
            try:
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"status": "active", "last_owner": owner}
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
                return {"status": "inactive", "last_owner": owner}
    except FileNotFoundError:
        return {"status": "inactive", "last_owner": None}
    except OSError as exc:
        return {"status": "unknown", "error": f"{type(exc).__name__}: {exc}"}


def pin_health(pins: dict[str, str]) -> dict[str, str]:
    """Read-only diagnostics; admission and execution still call check_pins."""
    health = {}
    for filename, expected in pins.items():
        try:
            check_pins({filename: expected})
            health[filename] = "unchanged"
        except PinDrift as exc:
            health[filename] = str(exc)
    return health


class PinDrift(ValueError):
    pass


def check_pins(pins: dict[str, str]) -> None:
    for filename, expected in pins.items():
        try:
            actual = sha(Path(filename))
        except OSError as exc:
            raise PinDrift(f"Pinned file missing/unreadable: {filename}") from exc
        if actual != expected:
            raise PinDrift(f"Pinned file changed: {filename}")
