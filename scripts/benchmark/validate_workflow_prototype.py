"""Run one bounded repair/review alongside source-checked review controls on Sherlock.

This reuses extraction and diagnostic runners. It does not promote a canonical
graph or convert model agreement into scientific accuracy. All inputs are pinned;
the historical extraction is read only. Control labels are not repair feedback.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker
from genomics_discovery.curation.protocol_benchmark.grounding_diagnostic import (
    Dataset, Labels, Result, read_closed,
)
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
from genomics_discovery.curation.workflow_extraction.verification_controls import control_gate

from prepare_workflow_controls import main as prepare_controls
from run_grounding_diagnostic import run_diagnostic


def checked_inputs(plan):
    if plan.get("format_version") != "workflow_prototype_plan/1.0":
        raise ValueError("Unknown prototype plan format")
    if (plan.get("model"), plan.get("effort")) != ("gpt-6.1-sol", "xhigh"):
        raise ValueError("Use the user's requested model and effort")
    required = {"packet", "workflow", "repair_feedback", "source_obligations", "controls"}
    if set(plan["inputs"]) != required:
        raise ValueError("Plan must declare exactly the five input roles")
    paths = {}
    for role, row in plan["inputs"].items():
        path = Path(row["path"])
        if not path.is_absolute() or not path.is_file() or sha(path) != row["sha256"]:
            raise ValueError(f"Missing or changed prototype input: {role}")
        paths[role] = path
    return paths


async def run(plan_path, output):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native prototype validation requires a Slurm allocation")
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    plan = json.loads(plan_path.read_text())
    paths = checked_inputs(plan)
    if output.exists():
        raise FileExistsError(f"Refuse to overwrite a prototype run: {output}")
    output.mkdir(parents=True)
    write(output / "plan.json", plan)
    state = {"format_version": "workflow_prototype_run/1.0", "status": "preparing",
             "started_at": now(), "slurm_job_id": os.environ["SLURM_JOB_ID"],
             "plan_sha256": sha(plan_path), "canonical_promotion": False,
             "source_scope": "same frozen packet; no newly acquired dependencies",
             "reader_coverage": "not rerun; repair-only mode has no fresh readers",
             "boundary": "A prototype with bounded source checks, not expert gold or zero-error proof."}
    write(output / "state.json", state)
    try:
        return await _execute(plan_path, output, plan, paths, state)
    except BaseException as exc:
        # This invocation owns the newly created directory. Never rewrite an old run.
        state.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                     finished_at=now(), error=f"{type(exc).__name__}: {exc}")
        write(output / "state.json", state)
        raise


async def _execute(plan_path, output, plan, paths, state):
    controls = output / "controls_prepared"
    prepare_controls(["--workflow", str(paths["workflow"]), "--packet", str(paths["packet"]),
                      "--definitions", str(paths["controls"]), "--output", str(controls)])
    state.update(status="running", native_started_at=now())
    write(output / "state.json", state)
    worker = CodexWorker(model=plan["model"], effort=plan["effort"],
                         timeout_seconds=None, web_search=False, network_access=False)
    tasks = [
        execute(paths["packet"], output / "repair", worker=worker, mode="repair",
                initial_workflow=paths["workflow"], repair_review=paths["repair_feedback"],
                max_revisions=1, require_field_review=True, require_source_coverage=True,
                source_obligations=paths["source_obligations"]),
        run_diagnostic(controls / "cases.json", output / "control_run",
                       labels_path=controls / "labels.json", prompt_directory=controls / "prompts"),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # Recheck immutable inputs before reporting any success.
    checked_inputs(plan)
    if sha(plan_path) != state["plan_sha256"]:
        raise ValueError("Prototype plan changed during execution")
    for key, result in zip(("repair", "controls"), results, strict=True):
        state[key] = ({"status": "failed", "error": f"{type(result).__name__}: {result}"}
                      if isinstance(result, BaseException) else result)
    transport_completed = all(not isinstance(result, BaseException)
                 and result.get("status") in {"supported", "completed"} for result in results)
    dataset = read_closed(controls / "cases.json", Dataset)
    labels = read_closed(controls / "labels.json", Labels)
    gates = {}
    if not isinstance(results[1], BaseException):
        for attempt in results[1]["attempts"]:
            artifact = Path(attempt["attempt"]) / "artifact.json"
            valid = (results[1]["status"] == "completed"
                     and attempt["status"] == "completed" and attempt["transport_complete"]
                     and artifact.is_file() and sha(artifact) == attempt.get("artifact_sha256"))
            result = read_closed(artifact, Result) if valid else None
            gates[attempt["configuration"]] = control_gate(
                dataset, labels, result, native_complete=valid)
    state["control_gates"] = gates
    state["transport_completed"] = transport_completed
    ready = transport_completed and gates.get("field_accountable_review", {}).get("passed", False)
    state.update(status="completed" if ready else "needs_attention", finished_at=now())
    state["semantic_validation"] = ("bounded_prototype_checks_passed" if ready
                                    else "bounded_prototype_checks_not_passed")
    state["scientific_accuracy"] = "not_established_by_this_small_control_set"
    write(output / "state.json", state)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    state = asyncio.run(run(args.plan, args.output))
    print(json.dumps({"status": state["status"], "output": str(args.output),
                      "semantic_validation": state["semantic_validation"]}))
    raise SystemExit(0 if state["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
