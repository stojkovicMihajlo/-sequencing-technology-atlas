"""Check local execution prerequisites without starting a model or copying login data."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

from .codex_worker import DEFAULT_EFFORT, DEFAULT_MODEL
from .workflow_extraction.sources import load_packet


def check_execution(*, workdir: Path, packets=(), executable="codex",
                    require_slurm=False) -> dict:
    checks = []
    allocation = os.environ.get("SLURM_JOB_ID")
    checks.append({"check": "slurm_allocation", "ok": bool(allocation) or not require_slurm,
                   "required": require_slurm, "job_id": allocation})
    try:
        workdir = Path(workdir).resolve(strict=True)
        with tempfile.TemporaryFile(dir=workdir) as probe:
            probe.write(b"atlas readiness probe\n")
            probe.flush()
            os.fsync(probe.fileno())
        free = shutil.disk_usage(workdir).free
        checks.append({"check": "workdir", "ok": free > 0,
                       "free_bytes": free, "path": str(workdir)})
    except OSError as exc:
        checks.append({"check": "workdir", "ok": False,
                       "error_type": type(exc).__name__, "errno": exc.errno})
    for path in packets:
        try:
            packet = load_packet(Path(path))
            checks.append({"check": "source_packet", "ok": True, "path": str(path),
                           "method_id": packet["method_id"], "sources": len(packet["sources"])})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks.append({"check": "source_packet", "ok": False, "path": str(path),
                           "error_type": type(exc).__name__})
    cli = shutil.which(executable)
    checks.append({"check": "codex_cli", "ok": cli is not None, "executable": cli})
    if cli:
        try:
            result = subprocess.run([cli, "login", "status"], capture_output=True,
                                    text=True, timeout=20, check=False)
            checks.append({"check": "codex_login", "ok": result.returncode == 0,
                           "exit_code": result.returncode})
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks.append({"check": "codex_login", "ok": False,
                           "error_type": type(exc).__name__})
    return {"format_version": "execution_readiness/1.0", "hostname": socket.gethostname(),
            "checks": checks, "ready_for_native_attempt": all(c["ok"] for c in checks),
            "native_execution_verified": False,
            "model_default": DEFAULT_MODEL, "effort_default": DEFAULT_EFFORT,
            "authentication": {"selection": "existing CLI authentication and environment",
                "openai_api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
                "codex_api_key_present": bool(os.environ.get("CODEX_API_KEY"))},
            "boundary": "Prerequisites only. A live native task must still prove tool access, "
                        "model availability, approvals and completion. No billing identity inferred."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--packet", type=Path, action="append", default=[])
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--require-slurm", action="store_true")
    args = parser.parse_args()
    report = check_execution(workdir=args.workdir, packets=args.packet,
                             executable=args.executable, require_slurm=args.require_slurm)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ready_for_native_attempt"] else 1)


if __name__ == "__main__":
    main()
