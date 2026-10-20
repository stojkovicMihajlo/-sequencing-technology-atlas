"""Offline benchmark schemas, integrity checks and bound judgment counts."""

import argparse
import json
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import (
    Workflow, digest, review_outcome, validate_review,
)
from genomics_discovery.curation.workflow_extraction.sources import load_packet

from .models import BenchmarkAssessment, GraphReviewAssessment, ProtocolReference, ReferenceReview
from .scoring import score_assessment
from .validation import (
    reference_review_outcome, validate_assessment, validate_reference, validate_reference_review,
)

MODELS = {"reference": ProtocolReference, "review": ReferenceReview,
          "assessment": BenchmarkAssessment, "graph-review": GraphReviewAssessment}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("schema")
    schema.add_argument("kind", choices=MODELS)
    check = commands.add_parser("check")
    check.add_argument("kind", choices=MODELS)
    check.add_argument("document", type=Path)
    score = commands.add_parser("score")
    score.add_argument("document", type=Path)
    for command in (check, score):
        command.add_argument("--packet", type=Path, required=True)
        command.add_argument("--reference", type=Path)
        command.add_argument("--workflow", type=Path)
    args = parser.parse_args(argv)
    if args.command == "schema":
        print(json.dumps(MODELS[args.kind].model_json_schema(), indent=2))
        return 0
    kind = args.kind if args.command == "check" else "assessment"
    if kind in {"review", "assessment", "graph-review"} and args.reference is None:
        parser.error("--reference is required for review/assessment")
    if kind in {"assessment", "graph-review"} and args.workflow is None:
        parser.error("--workflow is required for assessment")
    try:
        packet = load_packet(args.packet)
        document = MODELS[kind].model_validate_json(args.document.read_bytes())
        reference = ProtocolReference.model_validate_json(args.reference.read_bytes()) if (
            args.reference is not None) else None
        workflow = Workflow.model_validate_json(args.workflow.read_bytes()) if (
            args.workflow is not None) else None
        if args.command == "score":
            result = score_assessment(document, reference, workflow, packet)
        else:
            errors = (validate_reference(document, packet) if kind == "reference" else
                validate_reference_review(document, reference, packet) if kind == "review" else
                validate_review(document.workflow_review, workflow, packet) + validate_assessment(
                    document.assessment, reference, workflow, packet) if kind == "graph-review" else
                validate_assessment(document, reference, workflow, packet))
            result = {"integrity_valid": not errors, "errors": errors,
                      "document_sha256": digest(document),
                      "boundary": "Integrity only; no automatic source entailment or gold status."}
            if not errors and kind == "review":
                result["review_outcome"] = reference_review_outcome(document)
            if not errors and kind == "graph-review":
                result["workflow_review_outcome"] = review_outcome(document.workflow_review, workflow)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {"integrity_valid": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("integrity_valid") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
