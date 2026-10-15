"""Audit retained gaps or validate an auditor's draft; never overwrite parents."""

import argparse
import asyncio
import json
from pathlib import Path

from genomics_discovery.curation.codex_worker import DEFAULT_EFFORT, DEFAULT_MODEL, CodexWorker

from .revalidation import revalidate_completed
from .runtime import check_output, execute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("run")
    live.add_argument("graph", type=Path)
    live.add_argument("workflow_id")
    live.add_argument("output", type=Path)
    live.add_argument("--gap", action="append")
    live.add_argument("--model", default=DEFAULT_MODEL)
    live.add_argument("--effort", default=DEFAULT_EFFORT)
    live.add_argument("--originals", type=Path, help="Frozen original/rendition bindings")
    check = sub.add_parser("check")
    check.add_argument("run", type=Path)
    check.add_argument("artifact", type=Path)
    revalidate = sub.add_parser("revalidate", help="New validator record; preserve original run")
    revalidate.add_argument("run", type=Path)
    revalidate.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "revalidate":
        result = revalidate_completed(args.run, args.output)
        print(json.dumps({k: result[k] for k in ("status", "source_run_status", "action_counts")},
                         indent=2))
    elif args.command == "check":
        _, queue = check_output(args.run, args.artifact)
        print(json.dumps({"integrity": "passed", "action_counts": queue["action_counts"],
                          "boundary": queue["boundary"]}))
    else:
        state = asyncio.run(execute(
            args.graph, args.workflow_id, args.output, gap_ids=args.gap,
            originals_path=args.originals,
            worker=CodexWorker(model=args.model, effort=args.effort,
                               web_search=False, network_access=False)
        ))
        print(json.dumps(state, indent=2))
        if state["status"] != "followup_proposed":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
