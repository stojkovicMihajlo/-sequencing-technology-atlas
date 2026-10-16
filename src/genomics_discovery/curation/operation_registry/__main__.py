"""Agent-callable lookup and checks; only the host should invoke apply."""

import argparse
import json
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowReview, digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipReview, cut_transformation, load_occurrences, review_membership,
)

from .models import Assignment, Decision
from .admission import (LabelDecision, AdmissionReview, VocabularyProposal, VocabularyReview,
                        validate_decision, validate_batch)
from .store import Registry, catalogue, check_proposal, graph_overlay, revision, search, uses


def load_manifest(path: Path):
    manifest = json.loads(path.read_text())
    for row in manifest.values():
        for key, value in row.items():
            if value is not None:
                row[key] = str((path.resolve().parent / value).resolve())
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "snapshot", "export-graph"):
        p = commands.add_parser(name)
        p.add_argument("database", type=Path)
    p = commands.add_parser("uses")
    p.add_argument("database", type=Path)
    p.add_argument("query")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=10)
    p = commands.add_parser("decisions")
    p.add_argument("database", type=Path)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--status")
    p = commands.add_parser("decision")
    p.add_argument("database", type=Path)
    p.add_argument("identifier")
    p = commands.add_parser("attach")
    p.add_argument("database", type=Path)
    p.add_argument("atlas", type=Path)
    for name in ("lookup", "inspect"):
        p = commands.add_parser(name)
        p.add_argument("snapshot", type=Path)
        p.add_argument("query")
        if name == "lookup":
            p.add_argument("--limit", type=int, default=10)
    p = commands.add_parser("schema")
    p.add_argument("kind", choices=("decision", "assignment", "review", "vocabulary", "vocabulary-review"))
    p = commands.add_parser("selections")
    p.add_argument("views", type=Path)
    p.add_argument("action", choices=("list", "overview", "inspect", "find"))
    p.add_argument("query", nargs="?")
    p.add_argument("--field")
    p.add_argument("--pointer", help="Exact JSON Pointer into the original selection view (inspect only)")
    p.add_argument("--limit", type=int, default=10)
    for name in ("check-vocabulary", "apply-vocabulary"):
        p = commands.add_parser(name)
        p.add_argument("registry", type=Path)
        p.add_argument("proposal", type=Path)
        p.add_argument("manifest", type=Path)
        p.add_argument("--review", type=Path, required=name == "apply-vocabulary")
    p = commands.add_parser("prepare")
    p.add_argument("workflow", type=Path)
    p.add_argument("packet", type=Path)
    p.add_argument("operation_ids", nargs="*")
    p.add_argument("--module")
    p.add_argument("--review", type=Path)
    p.add_argument("--output", required=True, type=Path)
    p = commands.add_parser("check")
    p.add_argument("snapshot", type=Path)
    p.add_argument("proposal", type=Path)
    p.add_argument("manifest", type=Path)
    p.add_argument("--review", type=Path)
    p = commands.add_parser("apply")
    p.add_argument("database", type=Path)
    p.add_argument("proposal", type=Path)
    p.add_argument("review", type=Path)
    p.add_argument("manifest", type=Path)
    p.add_argument("--admission-decision", type=Path, required=True)
    p.add_argument("--admission-review", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "schema":
        result = {"decision": Decision, "assignment": Assignment,
                  "review": MembershipReview, "vocabulary": VocabularyProposal,
                  "vocabulary-review": VocabularyReview}[args.kind].model_json_schema()
    elif args.command == "selections":
        from .selections import FIELDS, find_selections, list_selections
        from .selection_views import selection_overview, inspect_pointer
        if args.pointer is not None and (args.action != "inspect" or args.field):
            raise ValueError("Use --pointer only with inspect, without --field")
        if args.action in {"inspect", "overview"} and not args.query:
            raise ValueError("Supply an occurrence ID")
        views = json.loads(args.views.read_text())
        if args.action == "list":
            result = list_selections(views)
        elif args.action == "find":
            result = find_selections(views, args.query or "", field=args.field, limit=args.limit)
        elif args.action == "overview":
            if args.field:
                raise ValueError("Use inspect --field for a single field")
            result = selection_overview(views[args.query])
        else:
            view = views[args.query]
            if args.field and args.field not in FIELDS:
                raise ValueError("Unknown selection field")
            result = ({"occurrence_sha256": view["occurrence_sha256"], "pointer": args.pointer,
                       "value": inspect_pointer(view, args.pointer)} if args.pointer is not None
                      else {args.field: view["record"][args.field]} if args.field else view)
    elif args.command in {"check-vocabulary", "apply-vocabulary"}:
        occurrences, packets = load_occurrences(load_manifest(args.manifest))
        proposal = VocabularyProposal.model_validate_json(args.proposal.read_bytes())
        snapshot = (Registry(args.registry).snapshot() if args.command == "apply-vocabulary"
                    else json.loads(args.registry.read_text()))
        if args.command == "apply-vocabulary":
            result = Registry(args.registry).apply_batch(proposal, VocabularyReview.model_validate_json(
                args.review.read_bytes()), occurrences, packets)
        elif args.review:
            result = {"integrity": "valid", "outcomes": validate_batch(proposal,
                VocabularyReview.model_validate_json(args.review.read_bytes()), snapshot, occurrences, packets)}
        else:
            if set(occurrences) != {d.occurrence_id for d in proposal.decisions}:
                raise ValueError("Every selection needs a decision")
            result = {"integrity": "valid", "proposal_sha256": digest(proposal),
                      "decisions": [{"occurrence_id": d.occurrence_id, "decision_sha256": digest(d),
                                     "assignment_sha256": digest(d.assignment) if d.assignment else None}
                                    for d in proposal.decisions], "lookups":
                      [validate_decision(d, snapshot, occurrences, packets) for d in proposal.decisions]}
    elif args.command in {"init", "snapshot", "export-graph"}:
        registry = Registry(args.database)
        snapshot = registry.initialize() if args.command == "init" else registry.snapshot()
        result = graph_overlay(snapshot) if args.command == "export-graph" else catalogue(snapshot)
    elif args.command == "attach":
        from genomics_discovery.curation.technology_graph.queries import load_graph
        from genomics_discovery.curation.run_state import sha
        from .graph import attach_to_graph
        before = sha(args.database)
        atlas_before = sha(args.atlas)
        snapshot = Registry(args.database).snapshot()
        result = attach_to_graph(load_graph(args.atlas), snapshot)
        if sha(args.database) != before:
            raise ValueError("Registry changed during graph projection")
        if sha(args.atlas) != atlas_before:
            raise ValueError("Base atlas changed during graph projection")
        result["input_files"][str(args.database.resolve())] = before
        result["input_files"][str(args.atlas.resolve())] = atlas_before
        result.pop("revision_id", None)
        result["revision_id"] = digest(result)
    elif args.command == "uses":
        result = uses(Registry(args.database).snapshot(), args.query,
                      offset=args.offset, limit=args.limit)
    elif args.command in {"decisions", "decision"}:
        records = Registry(args.database).snapshot().get("decisions", {})
        if args.command == "decision":
            result = records[args.identifier]
        else:
            if args.offset < 0 or args.limit < 1:
                raise ValueError("Use a nonnegative offset and positive limit")
            rows = [{"decision_id": ident, "occurrence_id": row["decision"]["occurrence_id"],
                     "status": row["outcome"]["status"], "question": row["decision"]["case"]["question"],
                     "reason": row["review"]["rationale"],
                     "revision_requests": row["decision"]["case"]["revision_requests"]}
                    for ident, row in records.items()
                    if not args.status or row["outcome"]["status"] == args.status]
            end = args.offset + args.limit
            result = {"decisions": rows[args.offset:end], "total": len(rows),
                      "next_offset": end if end < len(rows) else None}
    elif args.command in {"lookup", "inspect"}:
        snapshot = json.loads(args.snapshot.read_text())
        if args.command == "lookup":
            result = search(snapshot, args.query, limit=args.limit)
        elif args.command == "inspect":
            result = {"registry_revision": revision(snapshot),
                      "concept": snapshot["concepts"][args.query],
                      "admission_status": snapshot.get("admission_status", {}).get(args.query, "unassessed")}
    elif args.command == "prepare":
        workflow = Workflow.model_validate_json(args.workflow.read_bytes())
        operation_ids = list(args.operation_ids)
        if args.module:
            modules = [m for m in workflow.modules if m.id == args.module]
            if len(modules) != 1:
                raise ValueError("Unknown module")
            operation_ids.extend(modules[0].operation_ids)
        occurrence = cut_transformation(workflow, load_packet(args.packet), operation_ids,
            review=WorkflowReview.model_validate_json(args.review.read_bytes()) if args.review else None)
        args.output.mkdir(parents=True, exist_ok=False)
        path = args.output / "occurrence.json"
        path.write_text(occurrence.model_dump_json(indent=2) + "\n")
        manifest = {occurrence.id: {"occurrence": str(path.resolve()),
            "workflow": str(args.workflow.resolve()), "packet": str(args.packet.resolve()),
            "review": str(args.review.resolve()) if args.review else None}}
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        result = {"manifest": str((args.output / "manifest.json").resolve()),
                  "occurrence_id": occurrence.id, "occurrence_sha256": digest(occurrence),
                  "operation_ids": [op.id for op in occurrence.operations]}
    else:
        occurrences, packets = load_occurrences(load_manifest(args.manifest))
        proposal = Assignment.model_validate_json(args.proposal.read_bytes())
        if args.command == "apply":
            result = Registry(args.database).apply(proposal,
                MembershipReview.model_validate_json(args.review.read_bytes()), occurrences, packets,
                admission_decision=LabelDecision.model_validate_json(args.admission_decision.read_bytes()),
                admission_review=AdmissionReview.model_validate_json(args.admission_review.read_bytes()))
        else:
            snapshot = json.loads(args.snapshot.read_text())
            lookups = check_proposal(snapshot, proposal, occurrences, packets)
            if args.review:
                review_membership(proposal, MembershipReview.model_validate_json(
                    args.review.read_bytes()), occurrences, packets)
            result = {"integrity": "valid", "proposal_sha256": digest(proposal),
                      "lookup_results": lookups,
                      "boundary": "Integrity only; this is not a positive scientific verdict."}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
