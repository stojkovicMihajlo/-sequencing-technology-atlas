"""Structured tools for the shared registry's pending admission workflow."""

import argparse
import json
from pathlib import Path

from .admission import VocabularyProposal, CurrentVocabularyReview
from .definition_comparison import ComparisonReport
from .proposal_queue import enqueue, attach_review, plan, missing_plan, attach_comparisons, submission, withdraw
from .publication import publish
from .storage import connect
from .semantic_candidates import SEMANTIC_LIMIT


def run(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("submit", "source-review", "plan", "comparisons", "publish", "inspect", "list", "withdraw"):
        p = commands.add_parser(name)
        p.add_argument("database", type=Path)
        if name not in {"submit", "list"}:
            p.add_argument("submission_id")
        if name == "submit":
            for field in ("proposal", "catalogue", "manifest"):
                p.add_argument(field, type=Path)
        if name == "source-review":
            p.add_argument("review", type=Path)
        if name == "withdraw":
            p.add_argument("--reason", required=True)
        if name == "plan":
            p.add_argument("--missing", action="store_true")
        if name in {"submit", "plan", "comparisons", "publish"}:
            p.add_argument("--semantic-cache", type=Path,
                           help="Use cached local full-definition embeddings; never downloads a model")
        if name in {"submit", "plan"}:
            p.add_argument("--semantic-limit", type=int,
                           help=f"Operational semantic candidate count (default {SEMANTIC_LIMIT} at submission)")
        if name == "comparisons":
            p.add_argument("plan", type=Path)
            p.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    path = args.database.resolve()
    embeddings = None
    if getattr(args, "semantic_cache", None):
        from ..technology_graph.embeddings import EmbeddingCache, LocalEmbedder
        directory = args.semantic_cache.resolve()
        embeddings = EmbeddingCache(directory / "vectors.sqlite3", LocalEmbedder(directory / "models", download=False))
    elif getattr(args, "semantic_limit", None) is not None:
        raise ValueError("--semantic-limit requires --semantic-cache")
    if args.action == "submit":
        from .__main__ import load_manifest
        key = enqueue(path, VocabularyProposal.model_validate_json(args.proposal.read_bytes()),
                      json.loads(args.catalogue.read_text()), load_manifest(args.manifest),
                      embeddings=embeddings, semantic_limit=(SEMANTIC_LIMIT if args.semantic_limit is None
                                                             else args.semantic_limit))
        return {"submission_id": key}
    if args.action == "source-review":
        attach_review(path, args.submission_id, CurrentVocabularyReview.model_validate_json(args.review.read_bytes()))
        return {"status": "source_review_recorded", "submission_id": args.submission_id}
    if args.action == "plan":
        return (missing_plan if args.missing else plan)(path, args.submission_id,
            embeddings=embeddings, semantic_limit=args.semantic_limit)
    if args.action == "comparisons":
        attach_comparisons(path, args.submission_id, json.loads(args.plan.read_text()),
                           ComparisonReport.model_validate_json(args.report.read_bytes()), embeddings=embeddings)
        return {"status": "comparisons_recorded", "submission_id": args.submission_id}
    if args.action == "publish":
        return publish(path, args.submission_id, embeddings=embeddings)
    if args.action == "withdraw":
        return withdraw(path, args.submission_id, args.reason)
    with connect(path) as db:
        if args.action == "inspect":
            return submission(db, args.submission_id)
        return {"submissions": [{"submission_id": key, "source_reviewed": review is not None,
                                 "result": json.loads(result) if result else None}
                                for key, review, result in db.execute("SELECT id,review,result FROM submissions ORDER BY id")]}
