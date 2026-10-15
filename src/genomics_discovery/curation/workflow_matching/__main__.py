"""Bounded tools for source-backed profiles and explicit local graph alignments."""

import argparse
import json
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.projection import query_workflow

from .consumer import query_reviewed
from .models import QUESTIONS, AlignmentBatch, Case, MatchingReview, ProfileBatch
from .tools import (
    load_context,
    review_targets,
    search_profiles,
    validate_alignments,
    validate_profiles,
    validate_review,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parents", type=Path)
    parser.add_argument("--manifest", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    schema = sub.add_parser("schema")
    schema.add_argument("kind", choices=("profiles", "alignments", "review"))
    sub.add_parser("units")
    get = sub.add_parser("get")
    get.add_argument("occurrence_id")
    parent = sub.add_parser("parent")
    parent.add_argument("parent_id")
    parent.add_argument("--operation")
    search = sub.add_parser("search")
    search.add_argument("profiles", type=Path)
    search.add_argument("query")
    search.add_argument("--dimension", choices=tuple(QUESTIONS))
    search.add_argument("--limit", type=int, default=10)
    reviewed = sub.add_parser("search-reviewed")
    reviewed.add_argument("index", type=Path)
    reviewed.add_argument("query")
    reviewed.add_argument("--dimension", choices=tuple(QUESTIONS))
    reviewed.add_argument("--limit", type=int, default=10)
    compare = sub.add_parser("compare")
    compare.add_argument("index", type=Path)
    compare.add_argument("case_id")
    compare.add_argument("--full-context", action="store_true")
    for command in ("check", "review-check"):
        check = sub.add_parser(command)
        check.add_argument("profiles", type=Path)
        check.add_argument("alignments", type=Path)
        check.add_argument("cases", type=Path)
        if command == "review-check":
            check.add_argument("review", type=Path)
    args = parser.parse_args()
    if args.command == "schema":
        result = {"profiles": ProfileBatch, "alignments": AlignmentBatch, "review": MatchingReview}[
            args.kind
        ].model_json_schema()
    elif args.command in {"search-reviewed", "compare"}:
        result = (
            query_reviewed(args.index, query=args.query, dimension=args.dimension, limit=args.limit)
            if args.command == "search-reviewed"
            else (query_reviewed(args.index, case_id=args.case_id, full_context=args.full_context))
        )
    else:
        if args.parents is None or args.manifest is None:
            parser.error("Source tools require --parents and --manifest")
        context = load_context(args.parents.resolve(), args.manifest.resolve())
        if args.command == "units":
            result = [
                {
                    "occurrence_id": u.id,
                    "occurrence_sha256": digest(u),
                    "parent_id": context.unit_parents[u.id],
                    "operations": [{"id": o.id, "name": o.name} for o in u.operations],
                    "inputs": [{"id": s.id, "description": s.description} for s in u.inputs],
                    "outputs": [{"id": s.id, "description": s.description} for s in u.outputs],
                }
                for u in context.occurrences.values()
            ]
        elif args.command == "get":
            unit = context.occurrences[args.occurrence_id]
            record = unit.model_dump(
                mode="json",
                exclude={"selected_checks", "workflow_review_status", "workflow_review_sha256"},
            )
            result = {
                "occurrence_sha256": digest(unit),
                "record": record,
                "parent_id": context.unit_parents[unit.id],
                "allowed_targets_note": "Use operation:ID, state:ID or flow:ID from this cut.",
            }
        elif args.command == "parent":
            workflow = context.parents[args.parent_id]["workflow"]
            result = (
                query_workflow(workflow, args.operation)
                if args.operation
                else {
                    "method_id": workflow.method_id,
                    "variant": workflow.variant,
                    "scope": workflow.scope,
                    "operations": [
                        {"id": o.id, "name": o.name, "inputs": o.inputs, "outputs": o.outputs}
                        for o in workflow.operations
                    ],
                }
            )
        else:
            profiles = ProfileBatch.model_validate_json(args.profiles.read_bytes())
            validate_profiles(profiles, context)
            if args.command == "search":
                result = {
                    **search_profiles(
                        profiles, args.query, dimension=args.dimension, limit=args.limit
                    ),
                    "profile_sha256": digest(profiles),
                    "index_review_status": "not_checked_by_this_discovery_command",
                }
            else:
                alignments = AlignmentBatch.model_validate_json(args.alignments.read_bytes())
                cases = [Case.model_validate(c) for c in json.loads(args.cases.read_text())]
                validate_alignments(alignments, cases, context)
                result = {
                    "integrity": "valid_not_entailment",
                    "profiles_sha256": digest(profiles),
                    "alignments_sha256": digest(alignments),
                    "review_targets": review_targets(profiles, alignments, context),
                }
                if args.command == "review-check":
                    review = MatchingReview.model_validate_json(args.review.read_bytes())
                    result = {
                        "integrity": "valid_not_entailment",
                        "status": validate_review(review, profiles, alignments, context),
                    }
    print(json.dumps(result, indent=2, ensure_ascii=False, default=list))


if __name__ == "__main__":
    main()
