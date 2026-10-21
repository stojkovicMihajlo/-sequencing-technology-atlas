"""Launch one fresh native unit planner, then replay its contextual cuts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from genomics_discovery.curation.workflow_extraction.transformations import load_occurrences
from playground.workflow_unit_selection import (
    UnitSelection,
    SelectionTargets,
    build_selection_prompt,
    load_parents,
)


async def execute(parents_path: Path, run: Path, *, worker, max_units: int | None = None,
                  targets: Path | None = None) -> dict:
    """Selection establishes no common memberships or scientific acceptance."""
    if isinstance(worker, CodexWorker) and (worker.web_search or worker.network_access):
        raise ValueError("Source-packet runs require web search and network access disabled")
    if max_units is not None and (type(max_units) is not int or max_units < 1):
        raise ValueError("max_units must be a positive integer or None")
    if targets is not None:
        targets = targets.resolve()
        target_bytes = targets.read_bytes()
        target_document = SelectionTargets.model_validate_json(target_bytes)
    run, parents_path = run.resolve(), parents_path.resolve()
    run.mkdir(parents=True, exist_ok=False)
    state = {"status": "preparing", "started_at": now(), "backend": "native_codex_cli"}
    write(run / "state.json", state)
    pins = {}
    try:
        parents = load_parents(parents_path)
        snapshot = run / "parents.json"
        write(snapshot, {key: row.model_dump(mode="json") for key, row in parents.items()})
        schema = run / "schema.json"
        write(schema, UnitSelection.model_json_schema())
        selection, output = run / "selection.json", run / "units"
        files = {parents_path, snapshot, schema, Path(__file__),
                 Path(__file__).with_name("workflow_unit_selection.py")}
        package = Path(__file__).resolve().parents[1] / "src/genomics_discovery/curation"
        files |= set((package / "workflow_extraction").glob("*.py"))
        files |= {package / "codex_worker.py", package / "run_state.py",
                  package / "document_manifest.py", package / "comparison_rules.py",
                  package / "evidence_views.py",
                  package.parent / "core/artifacts.py"}
        target_snapshot = None
        if targets is not None:
            # Freeze query definitions, never prior memberships or reviewer opinions.
            if targets.read_bytes() != target_bytes:
                raise ValueError("Selection targets changed before snapshot")
            target_snapshot = run / "targets.json"
            write(target_snapshot, target_document.model_dump(mode="json"))
            files |= {targets, target_snapshot}
        for row in parents.values():
            files |= {path for path in row.model_dump().values() if path is not None}
            files |= {Path(s["original_path"]) for s in load_packet(row.packet)["sources"]}
        pins = {path: sha(path) for path in files}
        if targets is not None:
            pins[targets] = hashlib.sha256(target_bytes).hexdigest()
            if sha(targets) != pins[targets]:
                raise ValueError("Selection targets changed before prompt construction")
        prompt = build_selection_prompt(snapshot, schema=schema, selection=selection, output=output,
                                        max_units=max_units, targets=target_snapshot)
        write(run / "instruction.json", {"prompt": prompt})
        pins[run / "instruction.json"] = sha(run / "instruction.json")
        write(run / "config.json", {
            "model": worker.model, "effort": worker.effort, "max_units": max_units,
            "selection_purpose": "target_retrieval" if targets is not None else "open_unit_discovery",
            "targets_sha256": sha(target_snapshot) if target_snapshot else None,
            "files": {str(path): value for path, value in pins.items()},
        })
        pins[run / "config.json"] = sha(run / "config.json")
        state["status"] = "running"
        write(run / "state.json", state)
        result = await worker.run(prompt, run)
        assessment = assess_archived_attempt(run)
        if (not assessment.complete or not assessment.thread_id
                or type(result.exit_code) is not int or result.exit_code != 0
                or result.error is not None or result.thread_id != assessment.thread_id
                or result.usage != assessment.usage):
            raise ValueError("Unit planner requires a completed fresh verified native thread")
        if (run / "prompt.txt").read_text() != prompt:
            raise ValueError("Unit planner prompt differs from its instruction")
        if any(sha(path) != expected for path, expected in pins.items()):
            raise ValueError("Frozen planner input or implementation changed")
        plan = UnitSelection.model_validate_json(selection.read_bytes())
        if max_units is not None and len(plan.cuts) > max_units:
            raise ValueError("Planner exceeded the declared diagnostic selection cap")
        receipt = json.loads((output / "selection_receipt.json").read_text())
        manifest_path = output / "comparison_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        for row in manifest.values():
            for key in ("occurrence", "packet", "workflow", "review"):
                if row.get(key) is not None:
                    row[key] = str((manifest_path.parent / row[key]).resolve())
        units, _ = load_occurrences(manifest)
        expected = {(cut.parent_id, tuple(sorted(cut.operation_ids)), cut.rationale)
                    for cut in plan.cuts}
        actual = {(cut["parent_id"], tuple(sorted(cut["operation_ids"])), cut["rationale"])
                  for cut in receipt["cuts"]}
        if actual != expected or len(receipt["cuts"]) != len(plan.cuts):
            raise ValueError("Receipt differs from the planner's exact selections")
        if receipt["links_created"] != 0 or len(units) != len(receipt["cuts"]) or set(units) != {
                cut["occurrence_id"] for cut in receipt["cuts"]}:
            raise ValueError("Selection created links or omitted contextual cuts")
        for cut in receipt["cuts"]:
            parent = parents[cut["parent_id"]]
            row = manifest[cut["occurrence_id"]]
            expected_parent = {key: str(value) if value is not None else None
                               for key, value in parent.model_dump().items()}
            if any(row.get(key) != value for key, value in expected_parent.items()):
                raise ValueError("Selected cut is bound to another parent")
            if tuple(sorted(op.id for op in units[cut["occurrence_id"]].operations)) != tuple(
                    sorted(cut["operation_ids"])) or digest(units[cut["occurrence_id"]]) != (
                        cut["occurrence_sha256"]):
                raise ValueError("Selected operations or cut hash differs")
        state.update(status="completed", finished_at=now(), thread_id=assessment.thread_id,
                     usage=assessment.usage, units=len(units), links_created=0,
                     manifest=str(manifest_path), manifest_sha256=sha(manifest_path),
                     selection_sha256=sha(selection), elapsed_seconds=result.elapsed_seconds)
    except asyncio.CancelledError:
        state.update(status="cancelled", finished_at=now())
        state["archives"] = {name: sha(run / name) for name in (
            "prompt.txt", "events.jsonl", "result.json", "command.json", "started.json",
            "stderr.log", "final.txt") if (run / name).is_file()}
        write(run / "state.json", state)
        raise
    except Exception as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}", finished_at=now())
    state["archives"] = {name: sha(run / name) for name in (
        "prompt.txt", "events.jsonl", "result.json", "command.json", "started.json",
        "stderr.log", "final.txt") if (run / name).is_file()}
    write(run / "state.json", state)
    return state
