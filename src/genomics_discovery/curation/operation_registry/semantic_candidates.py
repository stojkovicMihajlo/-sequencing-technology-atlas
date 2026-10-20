"""Optional full-definition retrieval; ranks nominate review, never identity."""

import math

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.technology_graph.discovery import _lexical
from .candidate_index import FIELDS, features

SEMANTIC_LIMIT = 10
RANK_CONSTANT = 60


def definition_text(concept):
    definition = concept["definition"]
    # A null mechanism and its explanation are uncertainty, not a shared mechanism.
    rows = ["labels: " + "; ".join([definition["name"], *concept.get("aliases", [])])]
    rows.extend(f"{field}: {definition[field]}" for field in FIELDS if definition.get(field) is not None)
    rows.extend(f"{field}: {concept[field]}" for field in ("scope_note", "relation") if concept.get(field))
    return "\n".join(rows)


def retrieval_policy(embeddings, semantic_limit=SEMANTIC_LIMIT):
    if type(semantic_limit) is not int or semantic_limit < 1:
        raise ValueError("Semantic candidate limit must be a positive integer")
    metadata = embeddings.metadata
    return {"kind": "full_definition_hybrid/1.0", "semantic_limit": semantic_limit,
            "rank_constant": RANK_CONSTANT, "fusion": "reciprocal_rank_union/1.0",
            "embedding_metadata": metadata, "embedding_fingerprint": digest(metadata),
            "cache_namespace": getattr(embeddings, "namespace", digest(metadata)),
            "text_format": "definition_fields_labels_scope/1.0",
            "limit_basis": "operational_count_not_accuracy_optimum"}


def validate_retrieval_policy(policy, embeddings=None, semantic_limit=None):
    if not isinstance(policy, dict) or "semantic_limit" not in policy:
        raise ValueError("Invalid semantic retrieval policy")
    limit = policy["semantic_limit"] if semantic_limit is None else semantic_limit
    if embeddings is None:
        raise ValueError("Configured semantic retrieval requires its embedding backend")
    if policy != retrieval_policy(embeddings, limit):
        raise ValueError("Semantic retrieval model, cache fingerprint or settings changed")


def candidate_binding(candidates):
    rows = {owner: {"definition_sha256": digest(concept),
                    "text_sha256": digest(definition_text(concept))}
            for owner, concept in sorted(candidates.items())}
    return {"definitions": rows, "candidate_snapshot_sha256": digest(rows)}


def _ranks(scores, keys, *, positive_only=False):
    order = sorted((i for i, value in enumerate(scores) if not positive_only or value > 0),
                   key=lambda i: (-scores[i], keys[i]))
    ranks, previous, rank = {}, None, 0
    for position, i in enumerate(order, 1):
        if scores[i] != previous:
            rank, previous = position, scores[i]
        ranks[i] = rank
    return order, ranks


def retrieve(query, candidates, lexical_routes, embeddings, semantic_limit):
    """Union every lexical hit with top semantic definitions, then fuse ranks.

    Exact duplicate owners share a retrieval slot. No similarity cutoff declares
    absence; context-different near neighbors remain explicit comparison targets.
    """
    qhash = digest(query)
    unique = {digest(c): c for c in candidates.values() if digest(c) != qhash}
    keys = sorted(unique)
    if not keys:
        return lexical_routes, {}
    concepts = [unique[k] for k in keys]
    texts = [definition_text(query), *map(definition_text, concepts)]
    vectors = embeddings.encode(texts)
    if len(vectors) != len(texts) or len({len(v) for v in vectors}) != 1 or any(
        not v or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in v)
        or not sum(x*x for x in v) for v in vectors
    ):
        raise ValueError("Invalid or inconsistent semantic candidate vectors")
    q, docs = vectors[0], vectors[1:]
    qnorm = math.sqrt(sum(x*x for x in q))
    semantic = [sum(x*y for x, y in zip(q, v)) /
                (qnorm * math.sqrt(sum(x*x for x in v))) for v in docs]
    lexical = _lexical(" ".join(sorted(features(query)["all"])),
                       [" ".join(sorted(features(c)["all"])) for c in concepts])
    order, semantic_ranks = _ranks(semantic, keys)
    _, lexical_ranks = _ranks(lexical, keys, positive_only=True)
    selected = {keys[i] for i in order[:semantic_limit]}
    routes = {owner: list(values) for owner, values in lexical_routes.items()}
    for owner, concept in candidates.items():
        if digest(concept) in selected:
            routes.setdefault(owner, []).append("semantic_full_definition")
    details = {}
    for i, key in enumerate(keys):
        channels = [{"mode": mode, "rank": ranks[i]} for mode, ranks in
                    (("lexical", lexical_ranks), ("semantic", semantic_ranks)) if i in ranks]
        details[key] = {"channels": channels,
                        "rrf_score": sum(1 / (RANK_CONSTANT + row["rank"]) for row in channels)}
    return routes, details
