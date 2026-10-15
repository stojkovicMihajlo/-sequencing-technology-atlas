"""Run multi-agent extraction jobs and source-reviewed common-element construction."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
from typing import Annotated

from pydantic import Field, model_validator

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.codex_worker import DEFAULT_EFFORT, DEFAULT_MODEL, CodexWorker
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.runtime import execute as extract
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from playground.workflow_transformations_live import execute as compare
from playground.workflow_unit_selection_live import execute as select


class Job(StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
    packet: Path


class Batch(StrictModel):
    jobs: Annotated[tuple[Job, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_ids(self):
        if len({job.id for job in self.jobs}) != len(self.jobs):
            raise ValueError("Duplicate batch job IDs")
        return self


async def execute(manifest: Path, run: Path, *, model=DEFAULT_MODEL, effort=DEFAULT_EFFORT,
                  concurrency=2, timeout=1800, mode="specialists", max_revisions=1,
                  with_graph=True, worker_factory=None, require_coverage=False) -> dict:
    """One failed method never erases another's completed protocol/review artifacts."""
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError("Concurrency must be a positive integer")
    if mode not in {"specialists", "baseline"}:
        raise ValueError("Batch mode must be specialists or baseline")
    if type(require_coverage) is not bool or require_coverage and mode != "specialists":
        raise ValueError("require_coverage requires specialists with fresh reader lineage")
    if type(max_revisions) is not int or max_revisions not in (0, 1):
        raise ValueError("Batch allows max_revisions 0 or 1")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not (
            math.isfinite(timeout) and timeout > 0):
        raise ValueError("Stage timeout must be finite and positive")
    manifest, run = manifest.resolve(), run.resolve()
    batch = Batch.model_validate_json(manifest.read_bytes())
    jobs = [(job.id, (manifest.parent / job.packet).resolve()) for job in batch.jobs]
    pins = {manifest: sha(manifest)}
    for _, path in jobs:
        packet = load_packet(path)
        pins[path] = sha(path)
        pins.update({Path(source["original_path"]): sha(Path(source["original_path"]))
                     for source in packet["sources"]})
    run.mkdir(parents=True, exist_ok=False)
    state = {"status": "running", "started_at": now(), "jobs": {}, "graph": None,
             "require_coverage": require_coverage}
    write(run / "state.json", state)
    write(run / "manifest.json", {"jobs": [{"id": ident, "packet": str(path)}
                                          for ident, path in jobs]})
    write(run / "config.json", {"manifest": str(manifest), "manifest_sha256": sha(manifest),
          "model": model, "effort": effort, "concurrency": concurrency,
          "timeout_seconds_per_stage": timeout, "mode": mode, "max_revisions": max_revisions,
          "with_graph": with_graph, "require_coverage": require_coverage,
          "files": {str(path): value for path, value in pins.items()}})
    for path in (run / "manifest.json", run / "config.json"):
        pins[path] = sha(path)
    factory = worker_factory or (lambda: CodexWorker(
        model=model, effort=effort, timeout_seconds=timeout,
        web_search=False, network_access=False))
    semaphore = asyncio.Semaphore(concurrency)

    def check():
        for path, expected in pins.items():
            if sha(path) != expected:
                raise ValueError(f"Frozen batch input changed: {path}")

    async def one(ident, packet):
        async with semaphore:
            state["jobs"][ident] = {"status": "running", "run": str(run / "methods" / ident)}
            write(run / "state.json", state)
            try:
                check()
                result = await extract(packet, run / "methods" / ident, worker=factory(),
                                       mode=mode, max_revisions=max_revisions,
                                       require_coverage=require_coverage)
                state["jobs"][ident] = result
            except asyncio.CancelledError:
                state["jobs"][ident]["status"] = "cancelled"
                raise
            except Exception as exc:
                state["jobs"][ident].update(status="failed", error=f"{type(exc).__name__}: {exc}")
            finally:
                write(run / "state.json", state)

    tasks = [asyncio.create_task(one(*job)) for job in jobs]
    try:
        await asyncio.gather(*tasks)
        check()
        parents = {ident: {"workflow": row["workflow"], "review": row["review"],
                          "packet": str(run / "methods" / ident / "source_packet.json")}
                   for ident, row in state["jobs"].items() if row["status"] == "supported"}
        parents_path = run / "parents.json"
        write(parents_path, parents)
        if with_graph and parents:
            state["selection"] = await select(
                parents_path, run / "unit_selection", worker=factory())
            write(run / "state.json", state)
            if state["selection"]["status"] == "completed":
                state["graph"] = await compare(Path(state["selection"]["manifest"]),
                                               run / "comparisons", worker=factory())
        failures = [ident for ident, row in state["jobs"].items() if row["status"] != "supported"]
        check()
        graph_failed = bool(with_graph and parents) and (
            state.get("selection", {}).get("status") != "completed"
            or (state.get("graph") or {}).get("status") != "completed")
        state.update(status="partial" if failures or graph_failed else "completed",
                     finished_at=now(), nonpositive_methods=failures,
                     included_methods=list(parents),
                     boundary="Prepared-source pipeline; model source support is not expert "
                              "or experimental validation. No historical ancestry or atlas import.")
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        state.update(status="cancelled", finished_at=now())
        write(run / "state.json", state)
        raise
    except Exception as exc:
        state.update(status="partial", error=f"{type(exc).__name__}: {exc}", finished_at=now())
    write(run / "state.json", state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--effort", default=DEFAULT_EFFORT)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--mode", choices=("specialists", "baseline"), default="specialists")
    parser.add_argument("--max-revisions", type=int, choices=(0, 1), default=1)
    parser.add_argument("--require-coverage", action="store_true",
                        help="Require source-finding coverage and its same-turn source review; "
                             "specialists mode only")
    parser.add_argument("--no-graph", action="store_true")
    args = parser.parse_args()
    state = asyncio.run(execute(args.manifest, args.run, model=args.model, effort=args.effort,
        concurrency=args.concurrency, timeout=args.timeout, mode=args.mode,
        max_revisions=args.max_revisions, with_graph=not args.no_graph,
        require_coverage=args.require_coverage))
    print(json.dumps({"status": state["status"], "jobs": {
        key: row["status"] for key, row in state["jobs"].items()},
        "included_methods": state.get("included_methods"), "run": str(args.run.resolve()),
        "require_coverage": args.require_coverage}))
    raise SystemExit(0 if state["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
