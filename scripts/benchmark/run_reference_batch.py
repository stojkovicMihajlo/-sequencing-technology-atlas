"""Build model-reviewed reference candidates in four auditable native stages."""

import argparse
import asyncio
import json
from pathlib import Path

from genomics_discovery.curation.protocol_benchmark.runtime import run_batch


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")
    state = asyncio.run(run_batch(args.manifest, args.output, concurrency=args.concurrency,
        case_ids=args.case, prepare_only=args.prepare_only, driver_path=Path(__file__)))
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0 if state["status"] in {"completed", "prepared"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
