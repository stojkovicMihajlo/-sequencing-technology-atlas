"""Find public sources, retrieve original bytes and register inputs for fresh review."""

import argparse
import asyncio
import json
from pathlib import Path

from .runtime import acquire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("worklist", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--method", action="append", help="Known worklist ID; repeat to select a subset")
    parser.add_argument("--scopes", type=Path, help="Explicit JSON method-ID to Scope mapping for ambiguous origins")
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    result = asyncio.run(acquire(args.worklist, args.output,
        method_ids=set(args.method) if args.method else None,
        scopes=json.loads(args.scopes.read_bytes()) if args.scopes else None,
        concurrency=args.concurrency))
    print(json.dumps({"counts": result["counts"], "registration": result["registration"],
                      "scientific_resolution": False, "result": str(args.output.resolve() / "result.json")}))


if __name__ == "__main__":
    main()
