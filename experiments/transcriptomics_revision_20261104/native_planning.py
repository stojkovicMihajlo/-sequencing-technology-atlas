"""Bounded method-identity planning with independent native review in Slurm.

This runner reads original registered sources, not prior extracted assertions.
It publishes a reviewed worklist and per-unit source bundles; extraction is a
separate caller-controlled stage after its own software/native pilot gate.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import shlex
import sys
import time

from genomics_discovery.execution.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.illumina.extraction import (
    now, read, sha, source_excerpt, stage_images, verify_registry_files, verify_stage_inputs, write,
)
from genomics_discovery.illumina.models import _scientific

try:
    from .planning import PlanReview, SourceEvidence, WorkPlan, admit, digest, validate_plan
except ImportError:
    from planning import PlanReview, SourceEvidence, WorkPlan, admit, digest, validate_plan


def evidence_rows(value):
    if isinstance(value, dict):
        if {"record_id", "source_id", "basis", "finding"} <= value.keys():
            yield SourceEvidence.model_validate(value)
        else:
            for child in value.values(): yield from evidence_rows(child)
    elif isinstance(value, list):
        for child in value: yield from evidence_rows(child)


def validate_source_support(document, registries, plan=None):
    """Exact registered quote/image checks; source entailment still needs review."""
    for e in evidence_rows(document.model_dump(mode="json")):
        registry = registries.get(e.record_id, {})
        source = registry.get(e.source_id)
        if not source or source.get("kind") != e.basis:
            raise ValueError("Wrong record/source or evidence basis")
        if e.basis == "text" and e.quote not in source.get("text", ""):
            raise ValueError("Planning quote is not present in registered source")
        if e.basis == "image" and (not source.get("path") or not source.get("sha256")):
            raise ValueError("Planning image evidence lacks registered content")
    groups = []
    if isinstance(document, WorkPlan):
        groups += [(set((d.left, d.right)), d.evidence) for d in document.identity_decisions
                   if d.decision in {"same_scoped_method", "distinct_implementations", "grouped_members"}]
        groups += [({b.record_id}, b.evidence) for u in document.work_units for b in u.bindings]
    else:
        if plan is None:
            raise ValueError("Review source checks require the exact proposed plan")
        required_by_target = {"unit:" + u.unit_id: {b.record_id for b in u.bindings} for u in plan.work_units}
        required_by_target.update({"pair:" + "|".join(sorted((d.left, d.right))): {d.left, d.right}
                                  for d in plan.identity_decisions
                                  if d.decision in {"same_scoped_method", "distinct_implementations", "grouped_members"}})
        groups += [(required_by_target[a.target], a.evidence) for a in document.assessments
                   if a.decision == "accept" and a.target in required_by_target]
    for required, evidence in groups:
        supported = {e.record_id for e in evidence if _scientific(e, registries[e.record_id])}
        if required - supported:
            raise ValueError("Implementation identity requires depicted/descriptive support from each side, not metadata/title alone")


def prepare_inputs(census_path, output):
    census = read(census_path)
    inputs = output / "inputs"; inputs.mkdir(parents=True, exist_ok=False)
    write(inputs / "census.json", census)
    registries, cards, pins, images = {}, {}, {}, []
    for row in census["rows"]:
        rid = row["record_id"]
        source_path, card_path = Path(row["source_registry_path"]), Path(row["card_path"])
        registry, card = read(source_path), read(card_path)
        if card["record_id"] != rid:
            raise ValueError("Original card differs from census record")
        if set(row["registered_sources"]) - set(registry):
            raise ValueError("Source registry is missing archived source IDs")
        verify_registry_files(registry)
        pins.update({str(source_path): sha(source_path), str(card_path): sha(card_path)})
        registries[rid], cards[rid] = registry, card
        for sid, source in registry.items():
            if source.get("kind") == "image":
                if sha(source["path"]) != source["sha256"]:
                    raise ValueError("Original diagram changed")
                images.append({"source_id": rid + "::" + sid, "record_id": rid,
                               "original_source_id": sid, "path": source["path"], "sha256": source["sha256"]})
    write(inputs / "registries.json", registries)
    write(inputs / "cards.json", cards)
    write(inputs / "original_pins.json", pins)
    write(inputs / "images.json", images)
    return inputs


def validate_artifact(role, artifact, inputs, plan_path=None):
    model = WorkPlan if role == "author" else PlanReview
    document = model.model_validate_json(Path(artifact).read_bytes())
    census, registries = read(inputs / "census.json"), read(inputs / "registries.json")
    for registry in registries.values(): verify_registry_files(registry)
    if role == "author":
        validate_source_support(document, registries)
        validate_plan(census, document)
    else:
        plan = WorkPlan.model_validate_json(plan_path.read_bytes())
        validate_source_support(document, registries, plan)
        expected_targets = validate_plan(census, plan)
        targets = [a.target for a in document.assessments]
        if document.plan_sha256 != digest(plan.model_dump(mode="json")):
            raise ValueError("Review must bind exact plan")
        if len(targets) != len(set(targets)) or set(targets) != expected_targets:
            raise ValueError("Review target coverage differs from exact plan")
        # Validate evidence-side coverage even when a rejection is returned.
        rows = {r["record_id"] for r in census["rows"]}
        required = {"source:" + r: {r} for r in rows}
        required.update({"unit:" + u.unit_id: {b.record_id for b in u.bindings} for u in plan.work_units})
        required.update({"pair:" + "|".join(sorted((d.left, d.right))): {d.left, d.right} for d in plan.identity_decisions})
        if any(required[a.target] - {e.record_id for e in a.evidence} for a in document.assessments):
            raise ValueError("Review omits affected-source evidence")
    return document


def write_workflow_bundles(inputs, worklist, output):
    """Exact source namespaces; no scientific rewriting or legacy artifact mutation."""
    registries, cards = read(inputs / "registries.json"), read(inputs / "cards.json")
    output.mkdir(parents=True, exist_ok=False)
    bundles = []
    for index, unit in enumerate(worklist["units"]):
        directory = output / f"unit_{index + 1:04d}"; directory.mkdir()
        primary = next(b["record_id"] for b in unit["bindings"] if b["role"] == "primary_depiction")
        card = copy.deepcopy(cards[primary])
        card.update(record_id=unit["unit_id"], source_title=unit["method_name"],
                    verified_method_names=[unit["method_name"]],
                    source_method_labels=[unit["method_name"]],
                    implementation_scope=unit["implementation_scope"], work_unit=unit,
                    reference_source_prefix=primary + "::", source_bindings=unit["bindings"])
        if card.get("source_id"):
            card["source_id"] = primary + "::" + card["source_id"]
        merged, images, origins = {}, [], []
        for binding in unit["bindings"]:
            rid = binding["record_id"]
            verify_registry_files(registries[rid])
            for sid, source in registries[rid].items():
                namespaced = rid + "::" + sid
                merged[namespaced] = {**copy.deepcopy(source),
                                     "worklist_origin": {"record_id": rid, "source_id": sid,
                                                         "role": binding["role"], "scope": binding["selected_scope"]}}
                if source.get("kind") == "image":
                    images.append({"source_id": namespaced, "path": source["path"]})
            origins.append({"binding": binding, "original_card": cards[rid]})
        write(directory / "card.json", card)
        write(directory / "source_registry.json", merged)
        write(directory / "source_cards.json", origins)
        write(directory / "images.json", images)
        write(directory / "worklist_binding.json", {"worklist_sha256": digest(worklist), "unit_id": unit["unit_id"]})
        bundles.append({"unit_id": unit["unit_id"], "method_name": unit["method_name"],
                        "card_path": str(directory / "card.json"),
                        "registry_path": str(directory / "source_registry.json"),
                        "images": images,
                        "pins": {p.name: sha(p) for p in directory.iterdir() if p.is_file()}})
    write(output / "manifest.json", {"worklist_sha256": digest(worklist), "bundles": bundles})
    return bundles


async def execute(census_path, output, *, worker=None):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native planning must run in a Sherlock Slurm allocation")
    output = output.resolve(); output.mkdir(parents=True, exist_ok=False)
    state = {"status": "preparing", "job_id": os.environ["SLURM_JOB_ID"],
             "model": "gpt-6.1-sol", "effort": "xhigh", "stages": []}
    write(output / "state.json", state)
    worker = worker or CodexWorker(model=state["model"], effort=state["effort"], timeout_seconds=None,
                                   web_search=False, network_access=False, pin_shell_environment=True)
    try:
        inputs = prepare_inputs(census_path, output)
        registries = read(inputs / "registries.json")
        census = read(inputs / "census.json")
        template = Path(__file__).with_name("PLAN_PROMPT.txt").read_text()

        async def stage(role, previous_plan=None, previous_review=None):
            attempt = output / "attempts" / f"{len(state['stages']) + 1:03d}_{role}"
            attempt.mkdir(parents=True, exist_ok=False)
            model = WorkPlan if role == "author" else PlanReview
            write(attempt / "schema.json", model.model_json_schema())
            attached, delivery = stage_images([(r["source_id"], Path(r["path"])) for r in read(inputs / "images.json")], attempt)
            command = [sys.executable, str(Path(__file__).resolve())]
            context = {"role": role, "census": str(inputs / "census.json"), "census_sha256": digest(census),
                       "source_cards": str(inputs / "cards.json"), "schema": str(attempt / "schema.json"),
                       "output": str(attempt / "artifact.json"), "attached_images": delivery,
                       "source_tool": shlex.join([*command, "source", "--inputs", str(inputs)]) + " --record RECORD [--id SOURCE] [--query TEXT] [--start N]",
                       "source_summaries": {rid: {sid: {"kind": s.get("kind"), "level": s.get("content_level"), "title": s.get("title"), "characters": len(s.get("text", ""))} for sid, s in registry.items()} for rid, registry in registries.items()}}
            check = [*command, "check", "--inputs", str(inputs), "--role", role, "--artifact", str(attempt / "artifact.json")]
            if previous_plan:
                context.update(plan=str(previous_plan), plan_sha256=digest(read(previous_plan)))
                if role == "reviewer": check += ["--plan", str(previous_plan)]
            if previous_review: context["previous_review"] = str(previous_review)
            context["self_check"] = shlex.join(check)
            prompt = template + "\nWrite artifact.json in your attempt directory. Source files are read-only. No prior extraction output is evidence.\nCONTEXT\n" + json.dumps(context, indent=2)
            (attempt / "stage_prompt.txt").write_text(prompt)
            pins = {str(p): sha(p) for p in inputs.iterdir() if p.is_file()}
            pins.update(read(inputs / "original_pins.json"))
            for p in (Path(__file__), Path(__file__).with_name("planning.py"), Path(__file__).with_name("PLAN_PROMPT.txt"), attempt / "schema.json", attempt / "stage_prompt.txt", attempt / "image_delivery.json", previous_plan, previous_review):
                if p: pins[str(p)] = sha(p)
            write(attempt / "input_pins.json", pins)
            entry = {"role": role, "path": str(attempt), "status": "running", "started_at": now()}
            state["stages"].append(entry); state["status"] = role; write(output / "state.json", state)
            started = time.monotonic()
            try:
                result = await worker.run(prompt, attempt, image_paths=attached)
                archived = assess_archived_attempt(attempt)
                if (not archived.complete or result.exit_code != 0 or result.error or result.thread_id != archived.thread_id
                        or result.usage != archived.usage or any(s.get("thread_id") == archived.thread_id for s in state["stages"][:-1])):
                    raise ValueError("Native planning stage failed transport/fresh-thread verification")
                if (attempt / "prompt.txt").read_text() != prompt:
                    raise ValueError("Archived prompt differs from requested prompt")
                verify_stage_inputs(attempt, delivery, pins)
                document = validate_artifact(role, attempt / "artifact.json", inputs, previous_plan)
                entry.update(status="validated", thread_id=archived.thread_id, usage=archived.usage,
                             artifact_sha256=sha(attempt / "artifact.json"))
                return document, attempt / "artifact.json"
            except BaseException as exc:
                entry.update(status="failed", error=f"{type(exc).__name__}: {exc}"); raise
            finally:
                entry.update(elapsed_seconds=time.monotonic() - started, finished_at=now())
                write(output / "state.json", state)

        plan_path, review_path = None, None
        for revision in range(2):
            plan, plan_path = await stage("author", plan_path, review_path)
            review, review_path = await stage("reviewer", plan_path)
            write(output / "plan.json", plan.model_dump(mode="json"))
            write(output / "review.json", review.model_dump(mode="json"))
            if all(a.decision == "accept" for a in review.assessments): break
        if any(a.decision != "accept" for a in review.assessments):
            state.update(status="held_after_review", revision=revision)
            return None
        worklist = admit(census, plan, review)
        write(output / "worklist.json", worklist)
        bundles = write_workflow_bundles(inputs, worklist, output / "workflow_bundles")
        state.update(status="reviewed", revision=revision, work_units=len(bundles),
                     worklist_sha256=digest(worklist), bundles=str(output / "workflow_bundles/manifest.json"),
                     bundles_sha256=sha(output / "workflow_bundles/manifest.json"))
        return bundles
    except BaseException as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}"); raise
    finally: write(output / "state.json", state)


def main():
    p = argparse.ArgumentParser(description=__doc__); sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run"); r.add_argument("census", type=Path); r.add_argument("output", type=Path)
    r = sub.add_parser("source"); r.add_argument("--inputs", type=Path, required=True); r.add_argument("--record", required=True)
    r.add_argument("--id"); r.add_argument("--query"); r.add_argument("--start", type=int, default=0); r.add_argument("--limit", type=int, default=8000)
    r = sub.add_parser("check"); r.add_argument("--inputs", type=Path, required=True); r.add_argument("--role", choices=("author", "reviewer"), required=True)
    r.add_argument("--artifact", type=Path, required=True); r.add_argument("--plan", type=Path)
    args = p.parse_args()
    if args.command == "run": asyncio.run(execute(args.census, args.output))
    elif args.command == "check":
        if args.role == "reviewer" and args.plan is None: p.error("reviewer check requires --plan")
        validate_artifact(args.role, args.artifact, args.inputs, args.plan); print("validated")
    else:
        registry = read(args.inputs / "registries.json")[args.record]
        result = (source_excerpt(registry, args.id, query=args.query, start=args.start, limit=args.limit) if args.id else
                  {sid: {"kind": s.get("kind"), "content_level": s.get("content_level"), "title": s.get("title"), "characters": len(s.get("text", "")), "path": s.get("path")} for sid, s in registry.items()})
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
