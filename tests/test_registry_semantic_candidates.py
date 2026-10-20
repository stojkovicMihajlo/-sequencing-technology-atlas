"""Synthetic retrieval contracts; these examples do not measure scientific recall."""

from copy import deepcopy
import json

import pytest

from test_operation_registry import registry as registry
from test_workflow_transformations import cases as cases
from test_shared_registry import bundle, queue
from test_shared_registry_regressions import comparison

from genomics_discovery.curation.operation_registry.candidate_index import FIELDS, index_definition
from genomics_discovery.curation.operation_registry.definition_comparison import ComparisonReport
from genomics_discovery.curation.operation_registry.proposal_queue import (
    enqueue, plan, missing_plan, attach_comparisons, submission, validate_candidate_policy,
)
from genomics_discovery.curation.operation_registry.semantic_candidates import (
    definition_text, retrieve, retrieval_policy,
)
from genomics_discovery.curation.operation_registry.storage import connect
from genomics_discovery.curation.operation_registry.store import catalogue
from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache
from genomics_discovery.curation.workflow_extraction.models import digest


class Backend:
    metadata = {"model": "synthetic-semantic-fixture", "weights_sha256": "a" * 64}

    def __init__(self):
        self.seen = []

    def encode(self, texts):
        self.seen.extend(texts)
        return [[1.0, 0.0] if {"operation: cleave", "operation: sever"} & set(text.splitlines())
                else [0.0, 1.0] for text in texts]


def concept(ident, stem, operation):
    return {"definition": {"id": ident, "name": stem + "label",
        **{field: stem + field for field in FIELDS}, "operation": operation,
        "mechanism_unknown_reason": None}, "aliases": [], "scope_note": stem + "scope",
        "relation": "shared_transformation"}


def cache(tmp_path):
    return EmbeddingCache(tmp_path / "vectors.sqlite3", Backend())


def configured(registry, cases, tmp_path, embeddings):
    p, _, manifest = bundle(cases, registry.snapshot(), tmp_path, cid="query")
    key = enqueue(registry.path, p, catalogue(registry.snapshot()), manifest,
                  embeddings=embeddings, semantic_limit=2)
    return key, p, manifest


def test_semantic_union_recovers_disjoint_paraphrase_and_retains_context_negative(tmp_path):
    embeddings = cache(tmp_path)
    query = concept("query", "alpha", "cleave")
    paraphrase = concept("paraphrase", "beta", "sever")
    negative = concept("negative", "gamma", "sever")
    negative["definition"]["context_requirements"] = "different compartment exclusion"
    lexical = concept("lexical", "delta", "cleave but oxidize")
    candidates = {"concept:p": paraphrase, "proposal:pending:n": negative, "concept:l": lexical}
    routes, details = retrieve(query, candidates, {"concept:l": ["operation"]}, embeddings, 2)
    assert set(routes) == set(candidates)
    assert routes["concept:p"] == routes["proposal:pending:n"] == ["semantic_full_definition"]
    assert details[digest(paraphrase)]["channels"] == [{"mode": "semantic", "rank": 1}]
    assert details[digest(negative)]["rrf_score"] == details[digest(paraphrase)]["rrf_score"]
    assert candidates["proposal:pending:n"]["definition"]["context_requirements"] != query["definition"]["context_requirements"]
    assert embeddings.backend.seen[0] == definition_text(query)
    assert all(query["definition"][field] in embeddings.backend.seen[0] for field in FIELDS)
    assert all("identity" not in row for row in details.values())


def test_unknown_mechanism_reason_is_not_a_retrieval_signal(tmp_path):
    query, target = concept("q", "alpha", "cleave"), concept("t", "beta", "sever")
    for item in (query, target):
        item["definition"].update(physical_mechanism=None, mechanism_unknown_reason="same unknown placeholder")
    text = definition_text(query)
    assert "None" not in text and "unknown placeholder" not in text and "physical_mechanism:" not in text
    embeddings = cache(tmp_path)
    routes, _ = retrieve(query, {"concept:t": target}, {}, embeddings, 1)
    assert routes == {"concept:t": ["semantic_full_definition"]}
    assert all("unknown placeholder" not in text for text in embeddings.backend.seen)


def test_semantic_top_count_keeps_nearest_even_with_zero_or_negative_cosine():
    query = concept("q", "alpha", "cleave")
    zero, negative = concept("z", "beta", "sever"), concept("n", "gamma", "oxidize")

    class Geometry:
        def encode(self, texts):
            return [[1.0, 0.0], *([0.0, 1.0] if "operation: sever" in text else [-1.0, 0.0]
                                  for text in texts[1:])]

    routes, details = retrieve(query, {"concept:z": zero, "concept:n": negative}, {}, Geometry(), 2)
    assert set(routes) == {"concept:z", "concept:n"}
    assert details[digest(zero)]["channels"] == [{"mode": "semantic", "rank": 1}]
    assert details[digest(negative)]["channels"] == [{"mode": "semantic", "rank": 2}]


def test_policy_and_cache_bind_model_settings_and_all_definition_text(tmp_path):
    embeddings = cache(tmp_path)
    policy = retrieval_policy(embeddings, 2)
    assert policy["embedding_fingerprint"] == digest(embeddings.metadata)
    assert policy["cache_namespace"] == embeddings.namespace
    assert policy["semantic_limit"] == 2 and policy["rank_constant"] == 60
    query = concept("q", "alpha", "cleave")
    target = concept("t", "beta", "sever")
    retrieve(query, {"concept:t": target}, {}, embeddings, 1)
    retrieve(query, {"concept:t": target}, {}, embeddings, 1)
    assert embeddings.last_stats["computed"] == 0
    changed = deepcopy(target)
    changed["definition"]["context_requirements"] += " altered"
    retrieve(query, {"concept:t": changed}, {}, embeddings, 1)
    assert embeddings.last_stats["computed"] == 1


def test_hybrid_queue_binds_pending_and_accepted_catalogue_and_detects_evolution(cases, registry, tmp_path):
    embeddings = cache(tmp_path)
    key, _, _ = configured(registry, cases, tmp_path, embeddings)
    pending = queue(registry, cases, tmp_path, index=1, cid="pending", name="Pending definition")
    visible = catalogue(registry.snapshot())
    late_proposal, _, late_manifest = bundle(cases, registry.snapshot(), tmp_path, index=1,
                                            cid="late", name="New pending definition")
    accepted = concept("accepted", "omega", "sever")
    with connect(registry.path, write=True) as db:
        db.execute("INSERT INTO concepts VALUES (?,?)", ("accepted", json.dumps(accepted)))
        index_definition(db, "concept:accepted", accepted)
    planned = plan(registry.path, key, embeddings=embeddings)
    bindings = planned["candidate_policy"]["definitions"]
    assert "concept:accepted" in bindings and f"proposal:{pending}:pending" in bindings
    assert bindings["concept:accepted"]["text_sha256"] == digest(definition_text(accepted))
    with connect(registry.path) as db:
        validate_candidate_policy(db, {**planned, "pairs": planned["pairs"][:1]}, embeddings=embeddings)
    enqueue(registry.path, late_proposal, visible, late_manifest)
    report = ComparisonReport(plan_sha256=digest(planned), comparisons=tuple(
        comparison(pair["pair_id"], output="different", relation="different") for pair in planned["pairs"]))
    with connect(registry.path) as db:
        with pytest.raises(ValueError, match="candidate metadata changed"):
            validate_candidate_policy(db, planned, embeddings=embeddings)
    attach_comparisons(registry.path, key, planned, report, embeddings=embeddings)
    refreshed = missing_plan(registry.path, key, embeddings=embeddings)
    assert {pair["target"]["definition"]["id"] for pair in refreshed["pairs"]} == {"late"}


def test_hybrid_submission_fails_closed_for_missing_or_changed_backend(cases, registry, tmp_path):
    embeddings = cache(tmp_path)
    key, _, _ = configured(registry, cases, tmp_path, embeddings)
    with pytest.raises(ValueError, match="requires its embedding backend"):
        plan(registry.path, key)
    with pytest.raises(ValueError, match="settings changed"):
        plan(registry.path, key, embeddings=embeddings, semantic_limit=3)
    changed = cache(tmp_path / "changed")
    changed.metadata = {**changed.metadata, "model": "changed-model"}
    with pytest.raises(ValueError, match="fingerprint or settings changed"):
        plan(registry.path, key, embeddings=changed)
    plain = queue(registry, cases, tmp_path, index=1, cid="plain")
    with pytest.raises(ValueError, match="fresh submission"):
        plan(registry.path, plain, embeddings=embeddings)


def test_legacy_default_payload_plan_shape_and_hash_remain_exact(cases, registry, tmp_path):
    p, _, manifest = bundle(cases, registry.snapshot(), tmp_path, cid="plain")
    visible = catalogue(registry.snapshot())
    expected = {"proposal": p.model_dump(mode="json"), "catalogue": visible, "manifest": manifest}
    key = enqueue(registry.path, p, visible, manifest)
    assert key == digest(expected)
    with connect(registry.path) as db:
        saved = submission(db, key)
    assert saved == {**expected, "review": None, "result": None}
    expected_plan = {"format_version": "definition_comparison_plan/1.0", "submission_id": key,
        "candidate_policy": {"kind": "overlapping_field_indexes", "exhaustive_threshold": 64,
            "semantic_retriever": False, "recall_guaranteed": False}, "pairs": []}
    assert plan(registry.path, key) == missing_plan(registry.path, key) == expected_plan


@pytest.mark.parametrize("limit", [0, -1, 1.5, True])
def test_invalid_semantic_counts_are_rejected(tmp_path, limit):
    with pytest.raises(ValueError, match="positive integer"):
        retrieval_policy(cache(tmp_path), limit)
