"""Prepare, run, inspect and query a source-scoped protocol workflow."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ..codex_worker import DEFAULT_EFFORT, DEFAULT_MODEL, CodexWorker
from .sources import build_packet, catalog, load_packet, read_batch, read_passage, search


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Register documents listed in a source manifest")
    prepare.add_argument("manifest", type=Path)
    prepare.add_argument("--output", type=Path, required=True)
    for name in ("catalog", "read", "search"):
        command = commands.add_parser(name)
        command.add_argument("packet", type=Path)
        if name == "read":
            command.add_argument("--source-id", required=True)
            command.add_argument("--passage-id", required=True)
        elif name == "catalog":
            command.add_argument("--focus", choices=("all", "methods"), default="all")
        elif name == "search":
            command.add_argument("query")
            command.add_argument("--limit", type=int, default=6)
            command.add_argument("--source-id", help="Search only this registered source")
    batch = commands.add_parser("read-batch", help="Read exact passages together")
    batch.add_argument("packet", type=Path)
    batch.add_argument("--ref", nargs=2, action="append", required=True,
                       metavar=("SOURCE_ID", "PASSAGE_ID"))
    run = commands.add_parser("run", help="Run extraction and fresh review; save a candidate")
    run.add_argument("packet", type=Path)
    run.add_argument("--run", type=Path, required=True)
    run.add_argument("--mode", choices=("specialists", "baseline", "review_only", "repair"),
                     default="specialists")
    run.add_argument("--initial-workflow", type=Path)
    run.add_argument("--repair-review", type=Path)
    run.add_argument("--feedback-packet", type=Path,
                     help="Original packet binding repair feedback before source expansion")
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--effort", default=DEFAULT_EFFORT)
    run.add_argument("--timeout", type=float, default=1800)
    run.add_argument("--max-revisions", type=int, choices=(0, 1), default=1)
    run.add_argument("--require-coverage", action="store_true",
                     help="Require the extraction skill and source-finding disposition review")
    run.add_argument("--require-source-coverage", action="store_true",
                     help="Freeze candidate-blind source obligations and gate completeness review")
    run.add_argument("--no-field-review", action="store_true",
                     help="Explicit legacy opt-out from default factual-field review; recorded in run config")
    run.add_argument("--source-obligations", type=Path,
                     help="Reuse frozen source obligations, revalidated against this packet")
    status = commands.add_parser("status", help="Show saved progress, not renewed acceptance")
    status.add_argument("run", type=Path)
    query = commands.add_parser("query", help="Inspect one operation and its explicit connections")
    query.add_argument("workflow", type=Path)
    query.add_argument("operation_id")
    path_query = commands.add_parser(
        "query-path", help="Inspect structural reachability without activating prose conditions")
    path_query.add_argument("workflow", type=Path)
    path_query.add_argument("source_id")
    path_query.add_argument("target_id")
    boundaries = commands.add_parser(
        "audit-boundaries", help="Inventory starts/endpoints; no scientific completeness verdict")
    boundaries.add_argument("workflow", type=Path)
    boundaries.add_argument("--manifest", type=Path,
                            help="Optional exact, Workflow-bound boundary dispositions")
    inspect = commands.add_parser("inspect", help="Show a lossless candidate with shared evidence")
    inspect.add_argument("workflow", type=Path)
    inspect.add_argument("--navigation", action="store_true",
                         help="Keep scientific fields; replace nested evidence text with exact locators")
    inspect.add_argument("--pointer", help="JSON Pointer into the Workflow; with --navigation, select its projected value")
    overview = commands.add_parser(
        "overview", help="Read the main structure; recipe and evidence remain in the workflow")
    overview.add_argument("workflow", type=Path)
    overview.add_argument("--format", choices=("text", "json", "html"), default="text")
    overview.add_argument("--output", type=Path, help="Create a new display file without overwriting")
    check = commands.add_parser(
        "check", help="Check structure and citations; no scientific verdict")
    check.add_argument("artifact", type=Path)
    check.add_argument("--packet", type=Path, required=True)
    check.add_argument("--kind", choices=("reading", "workflow", "review",
                                         "source_obligations", "source_coverage", "field_review"), required=True)
    check.add_argument("--candidate", type=Path)
    check.add_argument("--obligations", type=Path)
    check.add_argument("--workflow-review", type=Path)
    check.add_argument("--reading-report", type=Path, action="append", default=[])
    check.add_argument("--previous-workflow", type=Path,
                       help="Bound repair reader-gap references to a prior candidate; no inherited verdict")
    check.add_argument("--previous-field-review", type=Path,
                       help="Immediate prior field review, paired with --previous-workflow")
    assemble = commands.add_parser("assemble", help="Render explicit operation I/O as typed flows")
    assemble.add_argument("draft", type=Path)
    assemble.add_argument("--packet", type=Path, required=True)
    assemble.add_argument("--output", type=Path, required=True)
    compact = commands.add_parser("compact-authoring", help="Lossless editable evidence encoding")
    compact.add_argument("record", type=Path)
    compact.add_argument("--kind", choices=("draft", "workflow", "review"), required=True)
    expand = commands.add_parser("expand-authoring", help="Expand and validate an authored record")
    expand.add_argument("record", type=Path)
    expand.add_argument("--packet", type=Path, required=True)
    expand.add_argument("--output", type=Path, required=True)
    expand.add_argument("--candidate", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            source_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            for source_document in source_manifest["sources"]:
                source_file_path = Path(source_document["path"])
                source_document["path"] = str(
                    (args.manifest.resolve().parent / source_file_path).resolve())
            # Register source text, passage IDs and file hashes; no assay extraction yet.
            source_packet = build_packet(**source_manifest)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # Never overwrite a source packet used by a previous run.
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(source_packet, indent=2, ensure_ascii=False) + "\n")
            result = {"packet": str(args.output.resolve()),
                      "sources": len(source_packet["sources"]),
                      "passages": sum(len(s["passages"]) for s in source_packet["sources"])}
        elif args.command in {"catalog", "read", "read-batch", "search"}:
            packet = load_packet(args.packet)
            if args.command == "catalog":
                result = catalog(packet, focus=args.focus)
            elif args.command == "read":
                result = read_passage(packet, args.source_id, args.passage_id)
            elif args.command == "read-batch":
                result = read_batch(packet, [{"source_id": sid, "passage_id": pid}
                                             for sid, pid in args.ref])
            else:
                result = search(packet, args.query, args.limit, source_id=args.source_id)
        elif args.command == "status":
            result = json.loads((args.run / "state.json").read_text(encoding="utf-8"))
        elif args.command == "query":
            from .models import Workflow
            from .projection import query_workflow

            workflow = Workflow.model_validate_json(args.workflow.read_bytes())
            result = query_workflow(workflow, args.operation_id)
        elif args.command in {"query-path", "audit-boundaries"}:
            from .models import Workflow

            workflow = Workflow.model_validate_json(args.workflow.read_bytes())
            if args.command == "query-path":
                from .navigation import query_path

                result = query_path(workflow, args.source_id, args.target_id)
            else:
                from .boundaries import BoundaryManifest, audit_boundaries

                manifest = (BoundaryManifest.model_validate_json(args.manifest.read_bytes())
                            if args.manifest else None)
                result = audit_boundaries(workflow, manifest)
                if result["manifest_errors"]:
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                    raise SystemExit(1)
        elif args.command == "inspect":
            from .inspection import inspection_view, navigation_view, workflow_pointer
            from .models import Workflow

            workflow = Workflow.model_validate_json(args.workflow.read_bytes())
            result = (navigation_view(workflow, args.pointer) if args.navigation else
                      workflow_pointer(workflow, args.pointer) if args.pointer is not None else
                      inspection_view(workflow))
        elif args.command == "overview":
            from .models import Workflow
            from .overview import overview_text, workflow_overview

            workflow = Workflow.model_validate_json(args.workflow.read_bytes())
            if args.format == "html":
                from .overview_html import overview_html

                rendered = overview_html(workflow)
            elif args.format == "json":
                rendered = json.dumps(workflow_overview(workflow), indent=2, ensure_ascii=False) + "\n"
            else:
                rendered = overview_text(workflow)
            if args.output:
                with args.output.open("x", encoding="utf-8") as handle:
                    handle.write(rendered)
            else:
                print(rendered, end="")
            return
        elif args.command == "check":
            from .models import (
                ReadingReport,
                Workflow,
                WorkflowReview,
                validate_evidence,
                validate_reader_gaps,
                validate_repair_gap_references,
                validate_review,
            )
            from .obligations import (
                SourceObligations, SourceCoverageReview,
                validate_source_obligations, validate_source_coverage_review,
            )
            from .field_review import WorkflowFieldReview, validate_field_review

            packet = load_packet(args.packet)
            model = {"reading": ReadingReport, "workflow": Workflow,
                     "review": WorkflowReview, "source_obligations": SourceObligations,
                     "source_coverage": SourceCoverageReview,
                     "field_review": WorkflowFieldReview}[args.kind]
            try:
                document = model.model_validate_json(args.artifact.read_bytes())
                errors = validate_evidence(document, packet)
                if args.kind == "field_review":
                    if args.candidate is None:
                        raise ValueError("Field review check needs --candidate")
                    workflow = Workflow.model_validate_json(args.candidate.read_bytes())
                    prior_review = (WorkflowFieldReview.model_validate_json(
                        args.previous_field_review.read_bytes()) if args.previous_field_review else None)
                    prior_workflow = (Workflow.model_validate_json(args.previous_workflow.read_bytes())
                                      if args.previous_workflow else None)
                    errors.extend(validate_field_review(document, workflow, packet,
                        prior_review=prior_review, prior_workflow=prior_workflow))
                elif args.kind == "source_obligations":
                    errors.extend(validate_source_obligations(document, packet))
                elif args.kind == "source_coverage":
                    if not (args.candidate and args.obligations and args.workflow_review):
                        raise ValueError("Source coverage needs --candidate, --obligations and --workflow-review")
                    candidate = Workflow.model_validate_json(args.candidate.read_bytes())
                    obligations = SourceObligations.model_validate_json(args.obligations.read_bytes())
                    review = WorkflowReview.model_validate_json(args.workflow_review.read_bytes())
                    errors.extend(validate_source_coverage_review(
                        document, obligations, review, candidate, packet))
                elif args.kind == "workflow":
                    for key in ("method_id", "title", "scope", "variant"):
                        if getattr(document, key) != packet[key]:
                            errors.append(f"Workflow {key} differs from source scope")
                    readers = {}
                    for path in args.reading_report:
                        reading = ReadingReport.model_validate_json(path.read_bytes())
                        if reading.role in readers:
                            errors.append("Duplicate reader role")
                        readers[reading.role] = reading
                    if args.reading_report:
                        errors.extend(validate_reader_gaps(document, readers, packet))
                    if args.previous_workflow:
                        previous = Workflow.model_validate_json(args.previous_workflow.read_bytes())
                        errors.extend(validate_repair_gap_references(document, previous))
                elif args.kind == "reading":
                    inspected = {(ref.source_id, ref.passage_id)
                                 for ref in document.inspected_passages}
                    if not inspected:
                        errors.append("Reader requires nonempty inspected_passages")
                    for finding in document.findings:
                        if any((ev.source_id, ev.passage_id) not in inspected
                               for ev in finding.evidence):
                            errors.append(f"Finding {finding.id}: evidence not inspected")
                if args.kind == "review":
                    if args.candidate is None:
                        raise ValueError("Review check needs --candidate")
                    workflow = Workflow.model_validate_json(args.candidate.read_bytes())
                    errors.extend(validate_review(document, workflow, packet))
            except ValueError as exc:
                errors = [str(exc)]
            result = {"valid": not errors, "errors": errors,
                      "boundary": "Structure/provenance only; no scientific support verdict."}
            print(json.dumps(result, indent=2, ensure_ascii=False))
            raise SystemExit(0 if not errors else 1)
        elif args.command == "compact-authoring":
            from .authoring import MODELS, authoring_view

            result = authoring_view(MODELS[args.kind].model_validate_json(args.record.read_bytes()))
        elif args.command in {"assemble", "expand-authoring"}:
            from .assemble import assemble_workflow
            from .authoring import expand_authoring
            from .models import (
                Workflow,
                WorkflowDraft,
                WorkflowReview,
                validate_evidence,
                validate_review,
            )

            packet = load_packet(args.packet)
            record = (expand_authoring(json.loads(args.record.read_bytes()))
                      if args.command == "expand-authoring"
                      else WorkflowDraft.model_validate_json(args.draft.read_bytes()))
            workflow = assemble_workflow(record) if isinstance(record, WorkflowDraft) else record
            errors = validate_evidence(workflow, packet)
            if isinstance(workflow, WorkflowReview):
                if args.candidate is None:
                    raise ValueError("Review expansion needs --candidate")
                candidate = Workflow.model_validate_json(args.candidate.read_bytes())
                errors.extend(validate_review(workflow, candidate, packet))
            else:
                for key in ("method_id", "title", "scope", "variant"):
                    if getattr(workflow, key) != packet[key]:
                        errors.append(f"Workflow {key} differs from source scope")
            if errors:
                raise ValueError("; ".join(errors))
            args.output.write_text(workflow.model_dump_json(indent=2) + "\n", encoding="utf-8")
            result = {"output": str(args.output.resolve()),
                      "record_kind": ("review" if isinstance(workflow, WorkflowReview)
                                      else "workflow"),
                      "boundary": "Structural/provenance expansion; no inherited source verdict."}
        else:
            from .extraction_pipeline import execute

            worker = CodexWorker(model=args.model, effort=args.effort,
                                 timeout_seconds=args.timeout,
                                 web_search=False, network_access=False)
            result = asyncio.run(execute(args.packet, args.run, worker=worker,
                                        max_revisions=args.max_revisions, mode=args.mode,
                                        initial_workflow=args.initial_workflow,
                                        repair_review=args.repair_review,
                                        repair_feedback_packet=args.feedback_packet,
                                        require_coverage=args.require_coverage,
                                        require_field_review=not args.no_field_review,
                                        require_source_coverage=args.require_source_coverage,
                                        source_obligations=args.source_obligations))
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if result["status"] != "supported":
                raise SystemExit(1)
            return
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
