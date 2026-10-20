"""Build independently reviewed cross-method benchmark comparison candidates."""

import argparse
import asyncio
import json
from pathlib import Path

from genomics_discovery.curation.protocol_benchmark.comparison_runtime import run_comparison_batch


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--reference-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)
    if not 1 <= args.concurrency <= 4:
        parser.error("--concurrency must be between 1 and 4")
    state = asyncio.run(run_comparison_batch(args.tasks, args.reference_run, args.output,
        concurrency=args.concurrency, driver_path=Path(__file__)))
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
