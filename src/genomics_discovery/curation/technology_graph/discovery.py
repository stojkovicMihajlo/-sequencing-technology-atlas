"""Field-specific candidate retrieval; scientific matching remains a separate review."""

import math
from collections import Counter

from genomics_discovery.curation.workflow_extraction.models import digest

from .candidates import _context, _tokens, cut
from .workflow_roles import ROLES


def descriptions(operations, inputs, outputs):
    return {
        "transformation": " ".join(o["name"] + ". " + o["action"] for o in operations),
        "inputs": " ".join(s["description"] for s in inputs),
        "outputs": " ".join(s["description"] for s in outputs),
        "information": " ".join(o["information_effect"] for o in operations),
    }


def _lexical(query, documents):
    tokens = [_tokens(d) for d in documents]
    frequency = Counter(t for row in tokens for t in row)
    weights = {t: 1 + math.log((1 + len(tokens)) / (1 + n)) for t, n in frequency.items()}
    q = _tokens(query)
    qnorm = math.sqrt(sum(weights.get(t, 1 + math.log(1 + len(tokens))) ** 2 for t in q))
    return [sum(weights[t] ** 2 for t in q & row)
            / max(1e-12, qnorm * math.sqrt(sum(weights[t] ** 2 for t in row))) for row in tokens]


def discover_operations(graph, *, workflow_id=None, operation_ids=(), question=None, roles=(),
                        limit=10, per_method=2, embeddings=None):
    """Use optional role questions, free questions and/or a connected source selection.

    A hit is a seed, not a minimal reusable unit. Inspect its neighbors and expand
    the connected selection with prepare-comparison before source-based review.
    Ranking configuration is explicit and is not a biological decision rule.
    """
    if type(limit) is not int or limit < 1 or type(per_method) is not int or per_method < 1:
        raise ValueError("Candidate limits must be positive integers")
    if bool(workflow_id) != bool(operation_ids):
        raise ValueError("Provide both a workflow and operation IDs, or neither")
    unknown = set(roles) - ROLES.keys()
    if unknown:
        raise ValueError(f"Unknown search roles {sorted(unknown)}; use question for free text")
    roles = list(dict.fromkeys(roles))
    question = question.strip() if question else None
    if not workflow_id and not question and not roles:
        raise ValueError("Provide a source selection, question or search role")
    channels, source, own_method = [], None, None
    if workflow_id:
        selected = cut(graph, workflow_id, operation_ids)
        source = {"workflow_id": workflow_id, "operation_ids": [o.id for o in selected.operations],
                  "cut_sha256": digest(selected)}
        own_method = graph["workflows"][workflow_id]["atlas_method_id"]
        fields = descriptions([o.model_dump() for o in selected.operations],
                              [s.model_dump() for s in selected.inputs],
                              [s.model_dump() for s in selected.outputs])
        active = {f: text for f, text in fields.items() if text.strip()}
        channels.extend({"id": f, "field": f, "query": text, "weight": 1 / len(active)}
                        for f, text in active.items())
    focuses = ([('question', question)] if question else []) + [
        ("role:" + r, ROLES[r]["question"]) for r in roles]
    channels.extend({"id": key, "field": "all", "query": text, "weight": 1 / len(focuses)}
                    for key, text in focuses)
    records = []
    for wid, row in sorted(graph["workflows"].items()):
        if own_method is not None and row["atlas_method_id"] == own_method:
            continue
        for operation in sorted(row["workflow"]["operations"], key=lambda op: op["id"]):
            inputs, outputs = _context(row, operation)
            fields = descriptions([operation], inputs, outputs)
            fields["all"] = "\n".join(f"{f}: {text}" for f, text in fields.items())
            records.append((wid, row, operation, inputs, outputs, fields))
    vectors, warnings = {}, []
    if embeddings and records:
        texts = list(dict.fromkeys([c["query"] for c in channels] +
                     [r[-1][c["field"]] for c in channels for r in records]))
        vectors = dict(zip(texts, embeddings.encode(texts)))
        warnings = embeddings.backend.segmentations(texts)
    totals, details = [0.0] * len(records), [[] for _ in records]
    for channel in channels:
        docs = [r[-1][channel["field"]] for r in records]
        scores = {"lexical": _lexical(channel["query"], docs)}
        if embeddings and records:
            import numpy as np
            q = np.asarray(vectors[channel["query"]], dtype=float)
            matrix = np.asarray([vectors[d] for d in docs], dtype=float)
            scores["semantic"] = (matrix @ q / (np.linalg.norm(matrix, axis=1)
                                                * np.linalg.norm(q))).tolist()
        for mode, values in scores.items():
            order = sorted((i for i, score in enumerate(values) if score > 0),
                           key=lambda i: (-values[i], records[i][0], records[i][2]["id"]))
            previous, rank = None, 0
            for position, index in enumerate(order, 1):
                # Equal scores get equal weight; ID sorting is only a stable display order.
                if values[index] != previous:
                    rank, previous = position, values[index]
                contribution = channel["weight"] / len(scores) / (60 + rank)
                totals[index] += contribution
                details[index].append({"channel": channel["id"], "mode": mode, "rank": rank,
                                       "score": round(values[index], 6)})
    ordered = sorted((i for i, score in enumerate(totals) if score > 0),
                     key=lambda i: (-totals[i], records[i][0], records[i][2]["id"]))
    hits, counts = [], Counter()
    for index in ordered:
        wid, row, operation, inputs, outputs, _ = records[index]
        mid = row["atlas_method_id"]
        if counts[mid] >= per_method:
            continue
        counts[mid] += 1
        if len(hits) >= limit:
            continue
        states = set(operation["inputs"] + operation["outputs"])
        hits.append({
            "workflow_id": wid, "method_id": mid, "operation_ids": [operation["id"]],
            "score": round(totals[index], 9), "ranking_channels": details[index],
            "operation": operation, "inputs": inputs, "outputs": outputs,
            "neighbor_operations": [{"id": op["id"], "name": op["name"]}
                                    for op in row["workflow"]["operations"]
                                    if op["id"] != operation["id"]
                                    and states & set(op["inputs"] + op["outputs"])],
            "variant": row["workflow"]["variant"], "scope": row["workflow"]["scope"],
            "review_status": row["review_status"],
            "workflow_sha256": row["workflow_sha256"], "packet_sha256": row["packet_sha256"],
            "workflow_gap_ids": [g["id"] for g in row["workflow"]["unresolved"]],
            "status": "retrieval_only_requires_source_comparison",
        })
    configuration = {
        "algorithm": "field_specific_reciprocal_rank_fusion/1.1", "rank_constant": 60,
        "mode": "hybrid" if embeddings else "lexical", "channels": channels,
        "semantic": embeddings.metadata if embeddings else None,
        "scope": "single-operation seeds; connected groups are expanded during comparison",
        "limit": limit, "per_method": per_method,
    }
    return {
        "graph_revision_id": graph["revision_id"], "source": source,
        "query_roles": [{"id": r, **ROLES[r]} for r in roles], "question": question,
        "configuration": configuration,
        "request_id": "discovery:" + digest([graph["revision_id"], source, configuration]),
        "embedding_cache": embeddings.last_stats if embeddings and records else None,
        "segmented_embedding_texts": warnings,
        "indexed_operations": len(records), "qualified_candidates": len(ordered),
        "total_after_method_limit": sum(counts.values()), "candidates": hits,
        "truncated": sum(counts.values()) > limit,
        "boundary": "Search roles are optional questions, not assigned categories. Scores "
        "rank descriptions, including their uncertainties; they are not scientific confidence. "
        "A matching name, semantic hit or non-hit establishes no equivalence, compatibility, "
        "novelty or absence. Source operations and edges are unchanged. Use correspondences "
        "for existing scoped evidence and prepare-comparison for new independent review.",
    }
