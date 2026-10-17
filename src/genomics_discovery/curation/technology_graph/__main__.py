"""Build and inspect the integrated technology graph without promoting scientific claims."""

import argparse
import json
import sys
from pathlib import Path

from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_matching.models import QUESTIONS

from .assembly import build_graph
from .candidates import operation_candidates
from .comparison_jobs import prepare_comparison
from .discovery_cli import add_commands, dispatch
from .planning import plan_comparisons
from .queries import (
    ancestors,
    comparison_claims,
    comparison_leads,
    development,
    discover,
    find_operations,
    inspect,
    load_graph,
    neighbors,
    read_source,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add_commands(sub)
    build = sub.add_parser("build")
    build.add_argument("--atlas", type=Path, required=True)
    build.add_argument("--bindings", type=Path, required=True)
    build.add_argument("--index", type=Path, action="append", default=[])
    build.add_argument(
        "--extractions",
        type=Path,
        help="JSON mapping parent IDs to completed operation-extraction runs",
    )
    build.add_argument("--output", type=Path, required=True)
    candidate = sub.add_parser("candidates", help="Retrieve contextual operation seeds; no edges")
    candidate.add_argument("graph", type=Path)
    candidate.add_argument("workflow_id")
    candidate.add_argument("--operation", action="append", required=True)
    candidate.add_argument("--limit", type=int, default=10)
    candidate.add_argument("--per-method", type=int, default=2)
    prepare = sub.add_parser("prepare-comparison", help="Freeze seeds for fresh source review")
    prepare.add_argument("graph", type=Path)
    prepare.add_argument("--left-workflow", required=True)
    prepare.add_argument("--left-operation", action="append", required=True)
    prepare.add_argument("--right-workflow", required=True)
    prepare.add_argument("--right-operation", action="append", required=True)
    prepare.add_argument("--relation", choices=("shared_purpose", "shared_mechanism"),
                         required=True)
    prepare.add_argument("--question", required=True)
    prepare.add_argument("--output", type=Path, required=True)
    for name in (
        "inspect",
        "neighbors",
        "ancestors",
        "search",
        "source",
        "stats",
        "plan",
        "development",
        "comparisons",
        "operations",
        "claims",
    ):
        cmd = sub.add_parser(name)
        cmd.add_argument("graph", type=Path)
        if name in {"inspect", "neighbors", "ancestors", "source", "development", "comparisons",
                    "claims"}:
            kinds = {
                "inspect": ("RECORD_ID", "Exact returned node, edge, workflow or claim ID."),
                "neighbors": (
                    "NODE_ID",
                    "Exact node ID, e.g. technology:METHOD_ID or a returned operation node ID.",
                ),
                "ancestors": (
                    "METHOD_ID",
                    "Method slug, e.g. chipmentation; no technology: prefix.",
                ),
                "source": ("WORKFLOW_ID", "Bound workflow:... ID, not an operation or source ID."),
                "development": (
                    "HISTORICAL_CLAIM_ID",
                    "Historical claim:... ID returned by the graph.",
                ),
                "comparisons": ("VIEW_ID", "Connected view:... ID, not a historical claim ID."),
                "claims": ("COMPARISON_ID", "Exact comparison:... ID returned by an edge."),
            }
            metavar, help_text = kinds[name]
            cmd.add_argument("identifier", metavar=metavar, help=help_text)
        if name == "neighbors":
            cmd.add_argument(
                "--layer",
                choices=("history", "atlas_relations", "membership", "protocol", "cross_protocol", "concept"),
            )
            cmd.add_argument("--direction", choices=("in", "out", "both"), default="both")
            cmd.add_argument("--offset", type=int, default=0)
            cmd.add_argument("--limit", type=int, default=20)
        if name == "ancestors":
            cmd.add_argument("--depth", type=int, default=1)
        if name == "search":
            cmd.add_argument("query")
            cmd.add_argument("--dimension", choices=tuple(QUESTIONS))
            cmd.add_argument("--exclude-method")
            cmd.add_argument("--limit", type=int, default=10)
        if name == "operations":
            cmd.add_argument("query")
            cmd.add_argument("--method")
            cmd.add_argument("--limit", type=int, default=5)
        if name == "source":
            cmd.add_argument("source_id")
            cmd.add_argument("passage_id")
        if name == "plan":
            cmd.add_argument("--method")
        if name == "claims":
            cmd.add_argument("--kind", choices=("correspondence", "difference", "not_established"))
            cmd.add_argument("--dimension", choices=tuple(QUESTIONS))
            cmd.add_argument("--offset", type=int, default=0)
            cmd.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if args.command == "roles":
        result = dispatch(args)
    elif args.command == "prepare-comparison":
        result = prepare_comparison(
            args.graph, args.left_workflow, args.left_operation,
            args.right_workflow, args.right_operation, args.output,
            relation=args.relation, question=args.question,
        )
    elif args.command == "build":
        graph = build_graph(
            args.atlas, args.bindings, args.index, extractions_path=args.extractions
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists():
            raise FileExistsError(
                "Choose a new graph artifact; existing builds are not overwritten"
            )
        snapshot = args.output.with_suffix(".implementation")
        snapshot.mkdir(exist_ok=False)
        for name, expected in graph["builder_implementation"].items():
            source = Path(__file__).parent / name
            destination = snapshot / name
            destination.write_bytes(source.read_bytes())
            if sha(destination) != expected:
                raise ValueError("Builder implementation changed during snapshot")
        # A new build is a new inspectable artifact, never an overwrite of an existing atlas.
        with args.output.open("x") as out:
            out.write(json.dumps(graph, indent=2, ensure_ascii=False) + "\n")
        result = {
            "graph": str(args.output.resolve()),
            "revision_id": graph["revision_id"],
            **graph["statistics"],
        }
    else:
        graph = load_graph(args.graph)
        if args.command in {"discover-operations", "correspondences"}:
            result = dispatch(args, graph)
        elif args.command == "stats":
            result = graph["statistics"]
        elif args.command == "candidates":
            result = operation_candidates(
                graph, args.workflow_id, args.operation, limit=args.limit,
                per_method=args.per_method,
            )
        elif args.command == "plan":
            result = plan_comparisons(graph, method_id=args.method)
        elif args.command == "inspect":
            result = inspect(graph, args.identifier)
        elif args.command == "development":
            result = development(graph, args.identifier)
        elif args.command == "comparisons":
            result = comparison_leads(graph, args.identifier)
        elif args.command == "claims":
            result = comparison_claims(
                graph, args.identifier, kind=args.kind, dimension=args.dimension,
                offset=args.offset, limit=args.limit,
            )
        elif args.command == "neighbors":
            result = neighbors(
                graph,
                args.identifier,
                layer=args.layer,
                direction=args.direction,
                offset=args.offset,
                limit=args.limit,
            )
        elif args.command == "ancestors":
            result = ancestors(graph, args.identifier, depth=args.depth)
        elif args.command == "source":
            result = read_source(graph, args.identifier, args.source_id, args.passage_id)
        elif args.command == "operations":
            result = find_operations(graph, args.query, method_id=args.method, limit=args.limit)
        else:
            result = discover(
                graph,
                args.query,
                dimension=args.dimension,
                exclude_method=args.exclude_method,
                limit=args.limit,
            )
        result = {"graph_revision_id": graph["revision_id"], **result}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except KeyError as exc:
        command = sys.argv[1] if len(sys.argv) > 1 else ""
        hints = {
            "neighbors": "Use a returned node ID; a method slug becomes technology:METHOD_ID.",
            "source": "Use WORKFLOW_ID, SOURCE_ID and PASSAGE_ID; not an operation ID.",
            "comparisons": "Use a returned VIEW_ID; historical claim IDs belong to development.",
            "development": "Use a historical claim ID. Inspect returned comparison IDs separately.",
            "ancestors": "Use the method slug without the technology: prefix.",
        }
        print(
            json.dumps(
                {
                    "error": "unknown_graph_identifier",
                    "detail": str(exc),
                    "argument_hint": hints.get(command, "Inspect IDs returned by graph queries."),
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
