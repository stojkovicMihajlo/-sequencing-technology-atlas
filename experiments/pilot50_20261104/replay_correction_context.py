"""Replay the correction handoff; optionally run one pinned correction on Slurm.

The audit is a transport check. It cannot assess source support or claim that
the corrected native extraction has succeeded. The original attempt is immutable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from genomics_discovery.execution.codex_worker import CodexWorker
from genomics_discovery.illumina.extraction import (
    artifact_excerpt, extract_registered_workflow, read, registered_text_groups,
    revision_handoff, sha, write,
)
from genomics_discovery.illumina.models import parse_extraction, parse_review
from genomics_discovery.illumina.prompts import build_prompt

SEED_FILES = ("card.json", "source_registry.json", "extraction.json", "review.json")


def audit(source, output, *, seed_directory=None):
    source, output = Path(source).resolve(), Path(output).resolve()
    pins = {name: sha(source / name) for name in (*SEED_FILES, "failed_stage_prompt.txt")}
    old_prompt = (source / "failed_stage_prompt.txt").read_text()
    context = json.loads(old_prompt.split("CALLER CONTEXT\n", 1)[1].split(
        "\n\nREQUIRED OUTPUT SCHEMA", 1)[0])
    schema_path = old_prompt.split("REQUIRED OUTPUT SCHEMA (or read-only schema path)\n", 1)[1].split(
        "\n\nPREVIOUS CANDIDATE", 1)[0]
    candidate = parse_extraction((source / "extraction.json").read_bytes())
    review = parse_review((source / "review.json").read_bytes())
    output.mkdir(parents=True, exist_ok=False)
    files = revision_handoff(output, candidate, review)
    context.update(revision_feedback=files,
        artifact_helper="python -m genomics_discovery.illumina.extraction artifact")
    context['duplicate_text_registrations'] = [ids for ids in
        registered_text_groups(read(source/'source_registry.json')) if len(ids) > 1]
    prompt = build_prompt("author", context, schema_path)
    (output / "proposed_stage_prompt.txt").write_text(prompt)
    index = read(output / "feedback_index.json")
    recovered = 0
    for row in index["checks"]:
        reference = files["previous_review"]
        chunks, start = [], 0
        while start is not None:
            page = artifact_excerpt(reference["path"], reference["sha256"],
                pointer=row["pointer"], start=start, limit=16000)
            chunks.append(page["text"])
            start = page["next_start"]
        if json.loads("".join(chunks)) != review.checks[int(row["pointer"].split("/")[-1])].model_dump(mode="json"):
            raise ValueError("Feedback read changed a review record")
        recovered += 1
    seed = {"directory": str(seed_directory or source),
        "pins": {name: pins[name] for name in SEED_FILES}}
    write(output / "revision_seed.json", seed)
    unchanged = all(sha(source / name) == pin for name, pin in pins.items())
    if not unchanged or len(prompt) > 100_000 or recovered != len(review.checks):
        raise ValueError("Correction handoff replay failed")
    result = {"format_version": "illumina_correction_transport_replay/1",
        "source_pins": pins, "original_prompt_characters": len(old_prompt),
        "proposed_prompt_characters": len(prompt), "review_checks_indexed_and_reconstructed": recovered,
        "original_inputs_unchanged": unchanged,
        "prior_candidate_sha256": files["previous_candidate"]["sha256"],
        "prior_review_sha256": files["previous_review"]["sha256"],
        "boundary": "No native calls or source support revalidation; original extraction remains failed."}
    repository = Path(__file__).resolve().parents[2]
    result['implementation_pins'] = {name: sha(repository/name) for name in (
        'src/genomics_discovery/illumina/extraction.py',
        'src/genomics_discovery/illumina/prompts.py',
        'experiments/pilot50_20261104/replay_correction_context.py')}
    write(output / "RESULT.json", result)
    return result


async def run(seed_path, output):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native correction requires Sherlock Slurm")
    seed = read(seed_path)
    source = Path(seed["directory"])
    card, registry = read(source / "card.json"), read(source / "source_registry.json")
    candidate = parse_extraction((source / "extraction.json").read_bytes())
    images = [(sid, Path(row["path"])) for sid, row in registry.items() if row["kind"] == "image"]
    worker = CodexWorker(model="gpt-6.1-sol", effort="xhigh", timeout_seconds=None,
        web_search=False, network_access=False, pin_shell_environment=True)
    result = await extract_registered_workflow(card, registry, images, Path(output), worker,
        max_revisions=1, representation=candidate.format_version, revision_seed=seed)
    write(Path(output) / "RESULT.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("audit")
    check.add_argument("source", type=Path)
    check.add_argument("--output", type=Path, required=True)
    check.add_argument("--seed-directory", type=Path,
        help="Original Sherlock attempt path for the separately submitted native replay")
    launch = sub.add_parser("run")
    launch.add_argument("seed", type=Path)
    launch.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = (audit(args.source, args.output, seed_directory=args.seed_directory)
        if args.command == "audit" else asyncio.run(run(args.seed, args.output)))
    print(json.dumps(result, indent=2))
    return 0 if args.command == 'audit' or result['status'] == 'supported' else 1


if __name__ == "__main__":
    raise SystemExit(main())
