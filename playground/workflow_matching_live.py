"""One source-grounded analyst and a fresh reviewer; export a retrieval/comparison overlay."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_matching.models import (
    AlignmentBatch,
    Case,
    MatchingReview,
    ProfileBatch,
    SearchRequest,
)
from genomics_discovery.curation.workflow_matching.prompts import build_prompt
from genomics_discovery.curation.workflow_matching.tools import (
    load_context,
    review_targets,
    search_profiles,
    validate_alignments,
    validate_profiles,
    validate_review,
)

ARCHIVES = (
    "prompt.txt",
    "command.json",
    "events.jsonl",
    "result.json",
    "started.json",
    "stderr.log",
    "final.txt",
)


async def execute(
    parents_path: Path,
    manifest_path: Path,
    cases_path: Path,
    run: Path,
    *,
    worker,
    seed_run: Path | None = None,
):
    if not isinstance(worker, CodexWorker) or worker.web_search or worker.network_access:
        raise ValueError("Use the existing native worker with frozen-source network settings")
    run = run.resolve()
    started = time.monotonic()
    run.mkdir(parents=True, exist_ok=False)
    state = {"status": "preparing", "started_at": now(), "attempts": [], "scientific_edges": 0}
    write(run / "state.json", state)
    pins = {}

    def unchanged():
        if any(sha(path) != expected for path, expected in pins.items()):
            raise ValueError("Frozen matching input, proposal or implementation changed")

    async def stage(role):
        unchanged()
        attempt = run / f"{len(state['attempts']) + 1:03d}_{role}"
        attempt.mkdir()
        prompt = build_prompt(
            role,
            run,
            parent_paths,
            output=attempt,
            seed=(run / "seed_proposals") if seed_run and role == "compiler" else None,
        )
        if role == "reviewer":
            prompt += f"\nSave the review to {attempt / 'artifact.json'}.\n"
        instruction = attempt / "instruction.json"
        write(instruction, {"prompt": prompt})
        pins[instruction] = sha(instruction)
        row = {"role": role, "path": str(attempt), "status": "running", "started_at": now()}
        state["attempts"].append(row)
        write(run / "state.json", state)
        start = time.monotonic()
        try:
            result = await worker.run(prompt, attempt)
            assessment = assess_archived_attempt(attempt)
            if (
                not assessment.complete
                or not assessment.thread_id
                or type(result.exit_code) is not int
                or result.exit_code != 0
                or result.error is not None
                or result.thread_id != assessment.thread_id
                or result.usage != assessment.usage
                or any(
                    old.get("thread_id") == assessment.thread_id
                    for old in state["attempts"]
                    if old is not row
                )
            ):
                raise ValueError("Matching requires a completed fresh verified native thread")
            if (attempt / "prompt.txt").read_text() != prompt:
                raise ValueError("Native prompt differs from the exact saved instruction")
            unchanged()
            row.update(
                status="transport_verified", thread_id=assessment.thread_id, usage=assessment.usage
            )
            return attempt
        except BaseException as exc:
            row.update(
                status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        finally:
            row.update(
                elapsed_seconds=time.monotonic() - start,
                finished_at=now(),
                archive_sha256={
                    name: sha(attempt / name) for name in ARCHIVES if (attempt / name).is_file()
                },
            )
            for name, value in row["archive_sha256"].items():
                pins[attempt / name] = value
            write(run / "state.json", state)

    try:
        parents_path, manifest_path, cases_path = (
            p.resolve() for p in (parents_path, manifest_path, cases_path)
        )
        context = load_context(parents_path, manifest_path)
        cases = [Case.model_validate(c) for c in json.loads(cases_path.read_text())]
        if (
            not cases
            or len({c.id for c in cases}) != len(cases)
            or any({c.left_parent, c.right_parent} - context.parents.keys() for c in cases)
        ):
            raise ValueError("Need distinct cases with registered parents")
        parent_paths = {
            k: {f: str(p) if p else None for f, p in row["paths"].items()}
            for k, row in context.parents.items()
        }
        manifest = json.loads(manifest_path.read_text())
        for row in manifest.values():
            for key, value in row.items():
                row[key] = str((manifest_path.parent / value).resolve()) if value else None
        write(run / "parents.json", parent_paths)
        write(run / "manifest.json", manifest)
        write(run / "cases.json", [c.model_dump(mode="json") for c in cases])
        write(run / "profiles_schema.json", ProfileBatch.model_json_schema())
        write(run / "alignments_schema.json", AlignmentBatch.model_json_schema())
        files = context.files | {cases_path, Path(__file__)}
        seed_record = None
        if seed_run is not None:
            source = seed_run.resolve()
            seed_files = []
            destination = run / "seed_proposals"
            destination.mkdir()
            for name in ("profiles.json", "alignments.json"):
                original, snapshot = source / name, destination / name
                snapshot.write_bytes(original.read_bytes())
                if sha(snapshot) != sha(original):
                    raise ValueError("Starting proposal changed during snapshot")
                files |= {original, snapshot}
                seed_files.append(
                    {"original": str(original), "snapshot": str(snapshot), "sha256": sha(snapshot)}
                )
            seed_record = {
                "status": "unaccepted_starting_proposals",
                "files": seed_files,
                "prior_scientific_review_inherited": False,
            }
        curation = Path(__file__).resolve().parents[1] / "src/genomics_discovery/curation"
        files |= set((curation / "workflow_matching").glob("*.py"))
        files |= set((curation / "workflow_extraction").glob("*.py"))
        files |= {
            curation / "codex_worker.py",
            curation / "run_state.py",
            curation / "document_manifest.py",
            curation.parent / "core/artifacts.py",
        }
        files |= {
            run / name
            for name in (
                "parents.json",
                "manifest.json",
                "cases.json",
                "profiles_schema.json",
                "alignments_schema.json",
            )
        }
        pins = {p: sha(p) for p in files}
        write(
            run / "config.json",
            {
                "model": worker.model,
                "effort": worker.effort,
                "files": {str(p): h for p, h in pins.items()},
                "backend": "native_codex_cli",
                "maximum_attempts": 2,
                "seed": seed_record,
                "implementation_snapshot": "implementation_snapshot/manifest.json",
            },
        )
        pins[run / "config.json"] = sha(run / "config.json")
        project = Path(__file__).resolve().parents[1]
        snapshot_rows = []
        for path, expected in list(pins.items()):
            if path.suffix != ".py" or not path.is_relative_to(project):
                continue
            destination = run / "implementation_snapshot" / path.relative_to(project)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(path.read_bytes())
            if sha(destination) != expected:
                raise ValueError("Implementation changed during snapshot")
            pins[destination] = expected
            snapshot_rows.append(
                {"original": str(path), "snapshot": str(destination), "sha256": expected}
            )
        snapshot_manifest = run / "implementation_snapshot/manifest.json"
        write(snapshot_manifest, snapshot_rows)
        pins[snapshot_manifest] = sha(snapshot_manifest)
        state["status"] = "compiling"
        compiler = await stage("compiler")
        profiles = ProfileBatch.model_validate_json((compiler / "profiles.json").read_bytes())
        alignments = AlignmentBatch.model_validate_json((compiler / "alignments.json").read_bytes())
        validate_profiles(profiles, context)
        cuts = validate_alignments(alignments, cases, context)
        requests = [
            SearchRequest.model_validate(r)
            for r in json.loads((compiler / "searches.json").read_text())
        ]
        if not requests:
            raise ValueError("Compiler must record candidate retrieval requests")
        for name in ("profiles.json", "alignments.json", "searches.json"):
            source, target = compiler / name, run / name
            target.write_bytes(source.read_bytes())
            if sha(source) != sha(target):
                raise ValueError("Validated proposal changed during host handoff")
            pins[source] = sha(source)
        write(
            run / "retrieval_replay.json",
            {
                "profiles_sha256": digest(profiles),
                "discovery_only": True,
                "results": [search_profiles(profiles, **r.model_dump()) for r in requests],
            },
        )
        write(run / "selection_views.json", cuts)
        required = review_targets(profiles, alignments, context)
        write(
            run / "required_targets.json",
            {
                "profiles_sha256": digest(profiles),
                "alignments_sha256": digest(alignments),
                "targets": {k: sorted(v) for k, v in required.items()},
            },
        )
        schema = MatchingReview.model_json_schema()
        schema["properties"]["checks"].update(minItems=len(required), maxItems=len(required))
        schema["$defs"]["ReviewCheck"]["properties"]["target_id"]["enum"] = list(required)
        write(run / "review_schema.json", schema)
        for name in (
            "profiles.json",
            "alignments.json",
            "searches.json",
            "retrieval_replay.json",
            "selection_views.json",
            "required_targets.json",
            "review_schema.json",
        ):
            pins[run / name] = sha(run / name)
        state["status"] = "reviewing"
        attempt = await stage("reviewer")
        review = MatchingReview.model_validate_json((attempt / "artifact.json").read_bytes())
        status = validate_review(review, profiles, alignments, context)
        unchanged()
        state.update(
            status="completed" if status == "supported_local_comparisons" else status,
            review_status=status,
            profile_count=len(profiles.profiles),
            alignment_count=len(alignments.alignments),
            review_checks=len(review.checks),
            review_sha256=digest(review),
            outcomes={a.case_id: a.disposition for a in alignments.alignments},
        )
        if status == "supported_local_comparisons":
            write(
                run / "matching_index.json",
                {
                    "format_version": "workflow_matching_index/1.1",
                    "profiles": profiles.model_dump(mode="json"),
                    "profiles_sha256": digest(profiles),
                    "review_sha256": digest(review),
                    "review_path": str(attempt / "artifact.json"),
                    "alignments_sha256": digest(alignments),
                    "files": {
                        "parents": "parents.json",
                        "manifest": "manifest.json",
                        "profiles": "profiles.json",
                        "alignments": "alignments.json",
                        "cases": "cases.json",
                        "review": str((attempt / "artifact.json").relative_to(run)),
                    },
                    "scope": "Model/source-reviewed facets; inference is not experimental proof.",
                    "scientific_edges_created": 0,
                },
            )
    except asyncio.CancelledError:
        state.update(status="cancelled", finished_at=now())
        write(run / "state.json", state)
        raise
    except Exception as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    state["finished_at"] = now()
    state["wall_seconds"] = time.monotonic() - started
    write(run / "state.json", state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("parents", "manifest", "cases"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="xhigh")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument(
        "--seed-run",
        type=Path,
        help="Unaccepted starting proposals; no scientific verdict is inherited",
    )
    args = parser.parse_args()
    worker = CodexWorker(
        model=args.model,
        effort=args.effort,
        timeout_seconds=args.timeout,
        web_search=False,
        network_access=False,
    )
    result = asyncio.run(
        execute(
            args.parents, args.manifest, args.cases, args.run, worker=worker, seed_run=args.seed_run
        )
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
