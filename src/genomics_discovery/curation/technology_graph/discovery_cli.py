"""CLI wiring for read-only discovery, kept separate from graph construction."""

from pathlib import Path

from .correspondences import lookup_correspondences
from .discovery import discover_operations
from .workflow_roles import ROLES, catalogue


def add_commands(sub):
    sub.add_parser("roles", help="List optional overlapping workflow search questions")
    search = sub.add_parser("discover-operations", help="Retrieve seeds using context and/or questions")
    search.add_argument("graph", type=Path)
    search.add_argument("--workflow")
    search.add_argument("--operation", action="append", default=[])
    search.add_argument("--question")
    search.add_argument("--role", action="append", choices=tuple(ROLES), default=[])
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--per-method", type=int, default=2)
    search.add_argument("--semantic-cache", type=Path, help="Enable optional local semantic retrieval")
    search.add_argument("--download-model", action="store_true", help="Permit first model download")
    lookup = sub.add_parser("correspondences", help="Show exact comparison coverage and memberships")
    lookup.add_argument("graph", type=Path)
    lookup.add_argument("workflow_id")
    lookup.add_argument("--operation", action="append", required=True)


def dispatch(args, graph=None):
    if args.command == "roles":
        return catalogue()
    if args.command == "correspondences":
        return lookup_correspondences(graph, args.workflow_id, args.operation)
    embeddings = None
    if args.download_model and not args.semantic_cache:
        raise ValueError("--download-model requires --semantic-cache")
    if args.semantic_cache:
        from .embeddings import EmbeddingCache, LocalEmbedder
        embeddings = EmbeddingCache(args.semantic_cache / "vectors.sqlite3",
                                    LocalEmbedder(args.semantic_cache / "models",
                                                  download=args.download_model))
    return discover_operations(graph, workflow_id=args.workflow, operation_ids=args.operation,
                               question=args.question, roles=args.role, limit=args.limit,
                               per_method=args.per_method, embeddings=embeddings)
