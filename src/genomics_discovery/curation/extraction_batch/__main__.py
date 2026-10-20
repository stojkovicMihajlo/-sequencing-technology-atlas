"""Plan, run, resume and inspect a durable source-packet extraction inventory."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from . import BatchConfig, plan, run, status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("plan", help="Freeze inventory/config/source pins; no native sessions")
    create.add_argument("manifest", type=Path)
    create.add_argument("--run", type=Path, required=True)
    create.add_argument("--model", default="gpt-6.1-sol")
    create.add_argument("--effort", default="xhigh")
    create.add_argument("--concurrency", type=int, default=2, help="Maximum concurrent native sessions")
    create.add_argument("--timeout", type=float, default=None, help="Optional stage timeout; default has no deadline")
    create.add_argument("--mode", choices=("baseline", "specialists"), default="baseline")
    create.add_argument("--max-revisions", type=int, choices=(0,1), default=1)
    create.add_argument("--transport-retries", type=int, default=1,
                        help="Additional fresh attempts per method/invocation after operational failures")
    create.add_argument("--require-source-coverage", action=argparse.BooleanOptionalAction,
                        default=True, help="Candidate-blind source obligations and coverage review (default enabled)")
    create.add_argument("--json", action="store_true", help="Print full machine-readable state")
    for name in ("run", "status"):
        command = commands.add_parser(name)
        command.add_argument("run", type=Path)
        command.add_argument("--json", action="store_true", help="Print full machine-readable state")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan(args.manifest, args.run, config=BatchConfig(
                model=args.model, effort=args.effort, concurrency=args.concurrency,
                timeout_seconds=args.timeout, mode=args.mode, max_revisions=args.max_revisions,
                transport_retries=args.transport_retries,
                require_source_coverage=args.require_source_coverage))
        elif args.command == "run":
            result = asyncio.run(run(args.run))
        else:
            result = status(args.run)
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status":"error", "error":f"{type(exc).__name__}: {exc}"}))
        return 2
    if args.json:
        display = result
    else:
        display = {"status": result["status"], "run": str(args.run.resolve()),
                   **({"input_status": result["input_status"]} if "input_status" in result else {}),
                   "status_counts": dict(Counter(row["status"] for row in result["jobs"].values())),
                   "accepted_count": len(result.get("accepted_parents", {})),
                   "nonpositive_methods": [{"id": ident, "status": row["status"],
                       "reason": row.get("error", row.get("reason", row.get("result", {}).get("outcome", row["status"])))}
                       for ident, row in result["jobs"].items()
                       if row["status"] not in {"queued", "running", "supported"}]}
    print(json.dumps(display, ensure_ascii=False))
    if result.get("input_status") == "pin_drift":
        return 2
    return 0 if result["status"] in {"planned", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
