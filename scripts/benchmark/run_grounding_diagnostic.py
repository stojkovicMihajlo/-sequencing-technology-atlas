"""Two matched native grounding batches; actual execution requires Sherlock Slurm.

Only a whitelist of visible passages, fields, schema and instructions is staged
for each reader. This is input separation, not a claim of filesystem isolation.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.curation.protocol_benchmark.grounding_diagnostic import (
    BOUNDARY, Dataset, Labels, Result, public_input, read_closed, score_result,
    validate_labels, validate_result, verify_dataset,
)
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.models import digest

CONFIGS = ("basic_review", "field_accountable_review")
DEFAULT_PROMPTS = Path(__file__).resolve().parents[2] / "benchmarks/protocol_grounding_v1/prompts"
BUDGET = {"model": "gpt-6.1-sol", "effort": "xhigh", "timeout_seconds": 3600,
          "web_search": False, "network_access": False, "batches_per_configuration": 1,
          "automatic_retries": 0, "data_repairs": 0}
ARCHIVES = ("prompt.txt", "command.json", "started.json", "events.jsonl",
            "stderr.log", "final.txt", "result.json")
STAGED = ("reader_input.json", "output_schema.json", "review_instructions.txt")


def verify_pins(pins):
    for name, expected in pins.items():
        path = Path(name)
        if not path.is_file() or path.is_symlink() or sha(path) != expected:
            raise ValueError(f"Frozen input/archive changed: {path}")


def observed_threads(attempt):
    threads = set()
    events = attempt / "events.jsonl"
    if events.is_file():
        for line in events.read_bytes().splitlines():
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(event, dict) and event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
                threads.add(event["thread_id"])
    return threads


def build_prompt(attempt, count):
    return ("ROLE: protocol_grounding_reader\n"
            f"Read review_instructions.txt, reader_input.json and output_schema.json in {attempt}.\n"
            f"Judge all {count} supplied cases once, using only their listed visible contexts.\n"
            "The supplied value_sha256 is the host canonical JSON digest; copy it exactly.\n"
            "not_established means the context does not establish the value; contradicted requires "
            "incompatible source evidence. unassessed means you did not decide; do not convert it "
            "to an absence or negative claim. Cite joint source_id/passage_id references from the "
            "case's context. No outside knowledge, web, network, other cases, original packet paths, "
            "labels, prior attempts or other configuration outputs are permitted inputs.\n"
            "Read applicable system/AGENTS instructions as required; these are operational inputs, "
            "not scientific evidence. Do not inspect any other unlisted files.\n"
            "Write artifact.json in this attempt directory. You may create, edit and replace "
            "artifact.json incrementally during construction; its final content must be exactly "
            "one JSON object conforming to output_schema.json. Do not create scratch/helper JSON "
            "or other outputs. Transport archives are managed by the host.\n")


async def run_diagnostic(cases_path, output, *, labels_path=None, prompt_directory=DEFAULT_PROMPTS,
                         prepare_only=False, worker_factory=None):
    """Run at most two sequential fresh workers. Fixtures may inject transport only."""
    if not prepare_only and not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native grounding diagnostics require a Sherlock Slurm allocation")
    cases_path, output = Path(cases_path).resolve(), Path(output).resolve()
    prompt_directory = Path(prompt_directory).resolve()
    if output.exists():
        raise FileExistsError(f"Refuse to overwrite a diagnostic run: {output}")
    dataset = read_closed(cases_path, Dataset)
    pins = {str(cases_path): sha(cases_path), **verify_dataset(dataset)}
    labels = None
    if labels_path is not None:
        labels_path = Path(labels_path).resolve()
        labels = read_closed(labels_path, Labels)
        validate_labels(labels, dataset)
        pins[str(labels_path)] = sha(labels_path)
    prompt_paths = {config: prompt_directory / (config + ".txt") for config in CONFIGS}
    for path in prompt_paths.values():
        if not path.read_text(encoding="utf-8").strip():
            raise ValueError(f"Empty prompt configuration: {path}")
        pins[str(path)] = sha(path)
    for obj in (run_diagnostic, Result, CodexWorker, verify_dataset):
        path = Path(inspect.getfile(obj)).resolve()
        pins[str(path)] = sha(path)
    verify_pins(pins)
    output.mkdir(parents=True)
    state = {"format_version": "protocol_grounding_run/1.0", "status": "prepared",
             "boundary": BOUNDARY, "annotation_status": "model_authored_diagnostic_candidate",
             "started_at": now(), "budget_per_configuration": BUDGET,
             "case_count": len(dataset.cases), "cases_sha256": sha(cases_path),
             "cases_digest": digest(dataset), "labels_sha256": sha(labels_path) if labels_path else None,
             "labels_format": labels.format_version if labels else None,
             "input_separation": "Whitelisted reader files; no claim of filesystem isolation",
             "attempts": []}
    public = public_input(dataset)
    for config in CONFIGS:
        attempt = output / "attempts" / config
        attempt.mkdir(parents=True)
        write(attempt / "reader_input.json", public)
        write(attempt / "output_schema.json", Result.model_json_schema())
        (attempt / "review_instructions.txt").write_bytes(prompt_paths[config].read_bytes())
        prompt = build_prompt(attempt, len(dataset.cases))
        # The host prompt copy stays outside the reader directory until transport starts.
        prompt_path = output / "prompts" / (config + ".txt")
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        record = {"configuration": config, "attempt": str(attempt), "status": "prepared",
                  "transport_complete": False, "prompt_sha256": sha(prompt_path),
                  "instructions_sha256": sha(attempt / "review_instructions.txt"),
                  "reader_input_sha256": sha(attempt / "reader_input.json"),
                  "schema_sha256": sha(attempt / "output_schema.json")}
        state["attempts"].append(record)
        pins.update({str(attempt / name): sha(attempt / name) for name in STAGED})
        pins[str(prompt_path)] = sha(prompt_path)
    write(output / "input_bindings.json", pins)
    write(output / "state.json", state)
    if prepare_only:
        return state

    used_threads, results = set(), {}
    for record in state["attempts"]:
        config, attempt = record["configuration"], Path(record["attempt"])
        record.update(status="running", started_at=now())
        state["status"] = "running"
        write(output / "state.json", state)
        try:
            verify_pins(pins)
            prompt = (output / "prompts" / (config + ".txt")).read_text(encoding="utf-8")
            worker = worker_factory() if worker_factory else CodexWorker(**{
                key: BUDGET[key] for key in ("model", "effort", "timeout_seconds", "web_search", "network_access")})
            if isinstance(worker, CodexWorker) and any(getattr(worker, key) != BUDGET[key]
                    for key in ("model", "effort", "timeout_seconds", "web_search", "network_access")):
                raise ValueError("Worker configuration differs from matched diagnostic budget")
            returned = await worker.run(prompt, attempt)
            checked = assess_archived_attempt(attempt)
            observed = observed_threads(attempt)
            record.update(thread_id=returned.thread_id, usage=returned.usage,
                          elapsed_seconds=returned.elapsed_seconds)
            if (not checked.complete or not checked.thread_id or returned.exit_code != 0
                    or returned.error is not None or returned.thread_id != checked.thread_id
                    or returned.usage != checked.usage or observed != {checked.thread_id}
                    or observed & used_threads
                    or (attempt / "prompt.txt").read_text(encoding="utf-8") != prompt
                    or any(not (attempt / name).is_file() for name in ARCHIVES)):
                raise ValueError("Native archive is incomplete, changed or reuses a thread")
            record["transport_complete"] = True
            verify_pins(pins)
            allowed = {*STAGED, *ARCHIVES, "artifact.json"}
            if any(path.is_symlink() or path.name not in allowed or path.parent != attempt
                   for path in attempt.rglob("*")):
                raise ValueError("Attempt contains an undeclared output or symlink")
            result = read_closed(attempt / "artifact.json", Result)
            validate_result(result, dataset)
            results[config] = result
            record.update(status="completed", artifact_sha256=sha(attempt / "artifact.json"),
                          artifact_digest=digest(result))
        except asyncio.CancelledError:
            record.update(status="cancelled", error_category="cancelled")
            raise
        except Exception as exc:
            record.update(status="failed", error_category="native_or_contract_failure",
                          error=f"{type(exc).__name__}: {exc}")
        finally:
            observed = observed_threads(attempt)
            used_threads.update(observed)
            record.update(observed_thread_ids=sorted(observed), finished_at=now())
            # Failed and malformed transports are preserved too, without scoring them.
            for name in (*ARCHIVES, "artifact.json"):
                path = attempt / name
                if path.is_file() and not path.is_symlink():
                    pins[str(path)] = sha(path)
            write(output / "input_bindings.json", pins)
            write(output / "state.json", state)
    try:
        verify_pins(pins)
    except ValueError as exc:
        # A changed fixed input invalidates the run, including an earlier result.
        state["integrity_error"] = str(exc)
        results.clear()
    state.update(status="completed" if len(results) == len(CONFIGS) else "failed", finished_at=now(),
                 distinct_observed_thread_count=len(used_threads),
                 completed_native_sessions=sum(r["transport_complete"] for r in state["attempts"]))
    report = {"format_version": "protocol_grounding_report/1.0", "boundary": BOUNDARY,
              "annotation_status": "model_authored_diagnostic_candidate",
              "labels_available": labels is not None,
              "configurations": {record["configuration"]: {
                  **(score_result(dataset, labels, results.get(record["configuration"])) if labels else {}),
                  "attempt_status": record["status"], "transport_complete": record["transport_complete"],
                  "metrics_status": "withheld_input_integrity" if "integrity_error" in state else
                      "scored" if labels is not None and record["configuration"] in results else
                      "failed_attempt" if labels is not None else "labels_not_supplied"}
                  for record in state["attempts"]}}
    write(output / "report.json", report)
    write(output / "state.json", state)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--prompt-directory", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    state = asyncio.run(run_diagnostic(args.cases, args.output, labels_path=args.labels,
        prompt_directory=args.prompt_directory, prepare_only=args.prepare_only))
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0 if state["status"] in {"prepared", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
