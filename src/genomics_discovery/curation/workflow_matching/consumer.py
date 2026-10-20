"""Reopen a reviewed run with source/proposal bindings before serving a design agent."""

import json
from pathlib import Path
import re

from genomics_discovery.curation.codex_worker import assess_archived_attempt
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.models import digest

from .models import AlignmentBatch, Case, MatchingReview, ProfileBatch
from .tools import (
    load_context,
    search_profiles,
    validate_alignments,
    validate_profiles,
    validate_review,
)


def _replay_native_handoff(run, paths, profiles, alignments, review, context):
    """A complete archive alone cannot authorize a different proposal or review.

    Historical 1.1 records remain usable when their existing state, exact compiler
    handoff and native ownership can be replayed. Missing checkpoints do not inherit
    the stronger guarantee merely because their quotes or transport are valid.
    """
    state_path, config_path = run / "state.json", run / "config.json"
    if not state_path.is_file():
        raise ValueError("Matching lacks a completed native ownership checkpoint; "
                         "legacy records require explicit re-review before new admission")
    state = json.loads(state_path.read_text())
    if (state.get("status") != "completed"
            or state.get("review_status") != "supported_local_comparisons"
            or state.get("review_sha256") != digest(review)
            or state.get("profile_count") != len(profiles.profiles)
            or state.get("alignment_count") != len(alignments.alignments)
            or state.get("review_checks") != len(review.checks)
            or state.get("outcomes") != {a.case_id: a.disposition for a in alignments.alignments}):
        raise ValueError("Completed matching checkpoint differs from proposals/review")
    attempts = state.get("attempts", [])
    if (len(attempts) != 2
            or [row.get("role") for row in attempts] != ["compiler", "reviewer"]):
        raise ValueError("Matching needs one recorded compiler and independent reviewer")
    files, threads = {state_path, config_path}, set()
    for number, row in enumerate(attempts, 1):
        role = row["role"]
        directory = run / f"{number:03d}_{role}"
        if (row.get("path") != str(directory) or row.get("status") != "transport_verified"):
            raise ValueError("Matching native stage ownership changed")
        archives = row.get("archive_sha256", {})
        if not {"prompt.txt", "events.jsonl", "result.json", "final.txt"} <= archives.keys():
            raise ValueError("Matching native archive checkpoint is incomplete")
        if any(not (directory / name).is_file() or sha(directory / name) != expected
               for name, expected in archives.items()):
            raise ValueError("Matching native archive changed")
        assessment = assess_archived_attempt(directory)
        if (not assessment.complete or not assessment.thread_id
                or assessment.thread_id != row.get("thread_id")
                or assessment.usage != row.get("usage") or assessment.thread_id in threads):
            raise ValueError("Matching requires distinct completed native stage sessions")
        threads.add(assessment.thread_id)
        instruction, prompt = directory / "instruction.json", directory / "prompt.txt"
        saved = json.loads(instruction.read_text()).get("prompt")
        if (saved != prompt.read_text()
                or not re.search(r"ROLE:\s*source_bound_workflow_" + role + r"\b", saved)):
            raise ValueError("Matching native prompt differs from its recorded stage instruction")
        files.update(directory / name for name in archives)
        files.add(instruction)
        if role == "compiler":
            for name in ("profiles", "alignments"):
                original = directory / f"{name}.json"
                if original.read_bytes() != paths[name].read_bytes():
                    raise ValueError("Matching proposal differs from its exact compiler handoff")
                files.add(original)
        elif paths["review"] != directory / "artifact.json":
            raise ValueError("Matching review is not owned by its recorded native reviewer")
    context.files.update(files)


def load_reviewed(index_path: Path):
    index_path = index_path.resolve()
    index = json.loads(index_path.read_text())
    if index.get("format_version") != "workflow_matching_index/1.1":
        raise ValueError("Unsupported reviewed matching index")
    paths = {k: (index_path.parent / v).resolve() for k, v in index["files"].items()}
    if set(paths) != {"parents", "manifest", "profiles", "alignments", "cases", "review"}:
        raise ValueError("Reviewed index requires all source/proposal/review files")
    configuration = json.loads((index_path.parent / "config.json").read_text())
    for key in ("parents", "manifest", "cases"):
        if configuration["files"].get(str(paths[key])) != sha(paths[key]):
            raise ValueError("Frozen comparison context changed")
    context = load_context(paths["parents"], paths["manifest"])
    if any(configuration["files"].get(str(path)) != sha(path) for path in context.files):
        raise ValueError("Frozen comparison source or occurrence changed")
    profiles = ProfileBatch.model_validate_json(paths["profiles"].read_bytes())
    alignments = AlignmentBatch.model_validate_json(paths["alignments"].read_bytes())
    review = MatchingReview.model_validate_json(paths["review"].read_bytes())
    cases = [Case.model_validate(c) for c in json.loads(paths["cases"].read_text())]
    if index["profiles"] != profiles.model_dump(mode="json") or (
        index["profiles_sha256"],
        index["alignments_sha256"],
        index["review_sha256"],
    ) != (digest(profiles), digest(alignments), digest(review)):
        raise ValueError("Index differs from the exact proposals/review")
    validate_profiles(profiles, context)
    cuts = validate_alignments(alignments, cases, context)
    if validate_review(review, profiles, alignments, context) != "supported_local_comparisons":
        raise ValueError("Index has an incomplete or nonpositive source review")
    _replay_native_handoff(index_path.parent, paths, profiles, alignments, review, context)
    return context, profiles, alignments, cuts, review, cases


def query_reviewed(
    index_path: Path, *, query=None, dimension=None, limit=10, case_id=None, full_context=False
):
    _, profiles, alignments, cuts, review, cases = load_reviewed(index_path)
    if (query is None) == (case_id is None):
        raise ValueError("Choose a retrieval query or one exact comparison case")
    header = {
        "review_sha256": digest(review),
        "scientific_edges_created": 0,
        "boundary": "Source/model-reviewed local claims; no ancestry, substitution, "
        "experimental feasibility or literature novelty is established.",
    }
    if query is not None:
        return {
            **search_profiles(profiles, query, dimension=dimension, limit=limit),
            "index_review_status": "source_reviewed_with_integrity_replay",
            **header,
        }
    matches = [a for a in alignments.alignments if a.case_id == case_id]
    if len(matches) != 1:
        raise KeyError(f"Unknown comparison case: {case_id}")

    def summary(cut):
        return {
            **{k: cut[k] for k in ("id", "method_id", "variant", "workflow_sha256", "boundary")},
            "operations": [{"id": o["id"], "name": o["name"]} for o in cut["operations"]],
            "inputs": [{k: s[k] for k in ("id", "kind", "description")} for s in cut["inputs"]],
            "outputs": [{k: s[k] for k in ("id", "kind", "description")} for s in cut["outputs"]],
            "flows": [
                {k: f[k] for k in ("id", "source", "target", "kind", "condition")}
                for f in cut["flows"]
            ],
            "requirements": [
                {k: r[k] for k in ("kind", "description")} for r in cut["requirements"]
            ],
            "external_operations": cut["external_operations"],
            "unresolved_ids": [g["id"] for g in cut["unresolved"]],
        }

    return {
        "requested_comparison": next(c.model_dump(mode="json") for c in cases if c.id == case_id),
        "alignment": matches[0].model_dump(mode="json"),
        "context_view": "full_record" if full_context else "navigation_summary_not_full_interface",
        "full_context_hint": "Use compare --full-context for raw states, evidence and gaps.",
        "retained_views": cuts[case_id]
        if full_context
        else {side: summary(cut) for side, cut in cuts[case_id].items()},
        **header,
    }
