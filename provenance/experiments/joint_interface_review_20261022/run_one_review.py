"""Run one preregistered production review; no expected scientific labels here."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute


async def run(plan_path, arm_name, output):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native review requires a Sherlock allocation")
    plan = json.loads(plan_path.read_text())
    arm = next(row for row in plan["arms"] if row["id"] == arm_name)
    if Path(os.environ["ATLAS_REPOSITORY"]).resolve() != Path(arm["runtime"]).resolve():
        raise ValueError("Selected runtime differs from preregistered arm")
    pins = {plan_path: sha(plan_path), Path(__file__): sha(Path(__file__))}
    for item in plan["inputs"].values():
        path = Path(item["path"])
        if not path.is_absolute() or path.is_symlink() or sha(path) != item["sha256"]:
            raise ValueError("Changed registered scientific input")
        pins[path] = item["sha256"]
    output.mkdir(parents=True, exist_ok=False)
    write(output / "input_receipt.json", {"started_at": now(), "arm": arm,
          "pins": {str(path): pin for path, pin in pins.items()},
          "expected_scientific_labels_supplied": False})
    inputs = plan["inputs"]
    state = await execute(Path(inputs["source_packet"]["path"]), output / "execution",
        worker=CodexWorker(model="gpt-6.1-sol", effort="xhigh", timeout_seconds=None,
                           web_search=False, network_access=False),
        mode="review_only", initial_workflow=Path(inputs["workflow"]["path"]),
        max_revisions=0, require_field_review=True, require_source_coverage=True,
        source_obligations=Path(inputs["source_requirements"]["path"]))
    for path, expected in pins.items():
        if sha(path) != expected:
            raise ValueError("Frozen input changed during review")
    write(output / "returned_state.json", state)
    print(json.dumps({"arm": arm_name, "status": state["status"], "output": str(output)}))
    return 1 if state["status"] in {"failed", "cancelled"} else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("arm")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.plan, args.arm, args.output)))
