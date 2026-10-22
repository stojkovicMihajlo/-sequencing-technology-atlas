"""Validate one or two fresh, source-only extractions through the existing pipeline.

No prior Workflow, review, obligations, reference graph or case-specific repair
feedback is admitted by the plan. This is input selection, not filesystem isolation.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
from genomics_discovery.curation.workflow_extraction.sources import load_packet


def checked_plan(plan):
    if set(plan) != {"format_version", "model", "effort", "methods"}:
        raise ValueError("Clean plan admits only format_version, model, effort and methods")
    if plan["format_version"] != "clean_extraction_plan/1.0":
        raise ValueError("Unknown clean extraction plan format")
    if (plan["model"], plan["effort"]) != ("gpt-6.1-sol", "xhigh"):
        raise ValueError("Use the requested model and effort")
    methods = plan["methods"]
    if not isinstance(methods, list) or not 1 <= len(methods) <= 2:
        raise ValueError("This validation runner requires one or two methods")
    jobs, ids = [], set()
    for row in methods:
        if set(row) != {"id", "packet", "packet_sha256"}:
            raise ValueError("Each method admits only id, packet and packet_sha256")
        path = Path(row["packet"])
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or sha(path) != row["packet_sha256"]):
            raise ValueError("Missing or changed registered source packet")
        packet = load_packet(path)  # Replay existing source registration/custody checks.
        if row["id"] != packet["method_id"] or row["id"] in ids:
            raise ValueError("Method ID must match its packet and occur once")
        ids.add(row["id"])
        jobs.append((row["id"], path))
    return jobs


async def run(plan_path, output):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Clean native extraction requires a Sherlock Slurm allocation")
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    plan = json.loads(plan_path.read_text())
    jobs = checked_plan(plan)
    plan_pin, runner_pin = sha(plan_path), sha(Path(__file__))
    output.mkdir(parents=True, exist_ok=False)  # Claim exclusively before writing anything.
    state = {"format_version": "clean_extraction_run/1.0", "status": "preparing",
        "started_at": now(), "slurm_job_id": os.environ["SLURM_JOB_ID"],
        "plan_sha256": plan_pin, "runner_sha256": runner_pin,
        "canonical_promotion": False, "prior_artifacts_supplied": False,
        "methods": {ident: {"status": "queued"} for ident, _ in jobs},
        "boundary": "Fresh source-packet extraction with all existing review gates; "
                    "no scientific accuracy guarantee or filesystem-isolation claim."}
    tasks = []
    def save():
        write(output / "state.json", state)

    def check():
        checked_plan(plan)
        if sha(plan_path) != plan_pin or sha(Path(__file__)) != runner_pin:
            raise ValueError("Frozen clean plan or runner changed")

    async def one(index, ident, packet):
        directory = output / "methods" / f"{index:02d}"
        state["methods"][ident] = {"status": "running", "run": str(directory)}
        save()
        try:
            check()
            worker = CodexWorker(model=plan["model"], effort=plan["effort"],
                timeout_seconds=None, web_search=False, network_access=False)
            state["methods"][ident] = await execute(packet, directory, worker=worker,
                mode="specialists", max_revisions=1, require_coverage=True,
                require_source_coverage=True, require_field_review=True)
        except BaseException as exc:
            state["methods"][ident] = {"status": "cancelled" if isinstance(exc,
                asyncio.CancelledError) else "failed", "run": str(directory),
                "error": f"{type(exc).__name__}: {exc}"}
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            save()

    save()
    try:
        write(output / "plan.json", plan)
        state["status"] = "running"
        save()
        tasks = [asyncio.create_task(one(index, ident, packet))
                 for index, (ident, packet) in enumerate(jobs, 1)]
        await asyncio.gather(*tasks)
        check()
        statuses = [row["status"] for row in state["methods"].values()]
        state["status"] = ("completed" if all(s == "supported" for s in statuses) else
                           "failed" if "failed" in statuses else "needs_attention")
        state["finished_at"] = now()
        save()
        return state
    except BaseException as exc:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        state.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                     error=f"{type(exc).__name__}: {exc}", finished_at=now())
        save()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    state = asyncio.run(run(args.plan, args.output))
    print(json.dumps({"status": state["status"], "output": str(args.output),
                      "methods": {k: v["status"] for k, v in state["methods"].items()}}))
    return 0 if state["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
