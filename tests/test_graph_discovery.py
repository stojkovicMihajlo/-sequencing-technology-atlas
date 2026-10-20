"""Retrieval/coverage contracts, not a synthetic test of biochemical truth."""

import json
import sqlite3
from copy import deepcopy

import pytest
from test_technology_graph_assembly import inputs as inputs
from test_workflow_matching import example as example
from test_workflow_transformations import cases as cases

from genomics_discovery.curation.technology_graph.assembly import assemble_graph
from genomics_discovery.curation.technology_graph.correspondences import lookup_correspondences
from genomics_discovery.curation.technology_graph.discovery import discover_operations
from genomics_discovery.curation.technology_graph.embeddings import EmbeddingCache, LocalEmbedder
from genomics_discovery.curation.technology_graph.workflow_roles import catalogue
from genomics_discovery.curation.workflow_extraction.models import digest


@pytest.fixture
def graph(inputs):
    graph = assemble_graph(*inputs)
    graph["revision_id"] = digest(graph)
    return graph


def left(graph):
    comparison = next(iter(graph["comparisons"].values()))
    return graph["views"][comparison["left_view"]]["workflow_id"]


def test_context_is_not_silently_promoted_to_correspondence(graph):
    original = deepcopy(graph)
    result = lookup_correspondences(graph, left(graph), ["insert", "repair"])
    row = result["comparisons"][0]
    assert row["overlap_kind"] == "exact"
    assert row["direct_correspondence_operation_targets"] == ["insert"]
    assert row["selected_operations_without_direct_correspondence_target"] == ["repair"]
    assert row["claims"][0]["left_evidence"]
    assert graph == original
    narrow = lookup_correspondences(graph, left(graph), ["repair"])["comparisons"][0]
    assert narrow["overlap_kind"] == "query_subset"
    assert narrow["claims"][0]["queried_operation_targets"] == []
    assert narrow["requested_operations_without_direct_correspondence_target"] == ["repair"]


def test_state_claim_does_not_invent_operation_coverage(graph):
    claim = next(iter(graph["comparisons"].values()))["alignment"]["claims"][0]
    claim["left_targets"] = ["state:tagged"]
    row = lookup_correspondences(graph, left(graph), ["insert"])["comparisons"][0]
    assert row["direct_correspondence_operation_targets"] == []
    assert row["claims"][0]["left_targets"] == ["state:tagged"]


def test_external_claims_remain_visible_without_covering_selected_operations(graph):
    claim = next(iter(graph["comparisons"].values()))["alignment"]["claims"][0]
    claim["left_targets"] = ["operation:assay"]
    row = lookup_correspondences(graph, left(graph), ["insert", "repair"])["comparisons"][0]
    assert row["direct_correspondence_operation_targets"] == []
    assert row["selected_operations_without_direct_correspondence_target"] == ["insert", "repair"]
    assert row["claims"][0]["external_context_operation_targets"] == ["assay"]
    assert row["claims"][0]["queried_operation_targets"] == []
    claim["left_targets"] = ["operation:invented"]
    with pytest.raises(ValueError, match="outside its comparison selection/context"):
        lookup_correspondences(graph, left(graph), ["insert"])


def test_stale_comparison_selection_rejected(graph):
    view = next(iter(graph["comparisons"].values()))["left_view"]
    graph["views"][view]["cut"]["workflow_sha256"] = "stale"
    with pytest.raises(ValueError, match="bound workflow"):
        lookup_correspondences(graph, left(graph), ["insert"])


def test_concept_membership_scope_stays_exact(graph):
    wid = left(graph)
    workflow = graph["workflows"][wid]
    graph["nodes"].append({"id": "selection:test", "kind": "protocol_selection",
                           "workflow_id": wid, "operation_ids": ["insert", "repair"],
                           "workflow_sha256": workflow["workflow_sha256"],
                           "source_packet_sha256": workflow["packet_sha256"]})
    graph["edges"].append({"source": "selection:test", "target": "concept:test",
                           "kind": "instance_of_concept", "basis": "shared_purpose",
                           "assignment_id": "assignment:test", "review_sha256": "review",
                           "review_contract": "concept_admission_review/2.0"})
    row = lookup_correspondences(graph, wid, ["insert"])["concept_memberships"][0]
    assert row["overlap_kind"] == "query_subset"
    assert row["stored_operation_ids"] == ["insert", "repair"]
    graph["nodes"][-1]["source_packet_sha256"] = "stale"
    with pytest.raises(ValueError, match="Concept selection"):
        lookup_correspondences(graph, wid, ["insert"])


def test_role_queries_are_optional_overlapping_not_annotations(graph):
    original = deepcopy(graph)
    result = discover_operations(graph, roles=["molecular_processing", "information_encoding"],
                                 question="adapter insertion", limit=10)
    assert len(result["query_roles"]) == 2
    assert result["candidates"]
    assert all(r["status"] == "retrieval_only_requires_source_comparison"
               for r in result["candidates"])
    assert graph == original
    assert len(catalogue()["roles"]) >= 2
    assert discover_operations(graph, question="adapter insertion")["query_roles"] == []
    with pytest.raises(ValueError, match="free text"):
        discover_operations(graph, roles=["new chemistry"])


def test_context_retrieval_keeps_interfaces_and_unresolved_status(graph):
    result = discover_operations(graph, workflow_id=left(graph), operation_ids=["insert"])
    assert result["candidates"]
    for row in result["candidates"]:
        assert row["workflow_id"] != left(graph)
        assert row["review_status"] == "needs_revision"
        assert row["inputs"] and row["outputs"]
        assert row["workflow_gap_ids"]
        assert "evidence" in row["operation"]


@pytest.mark.parametrize("kwargs", [{}, {"workflow_id": "fake"},
                                   {"operation_ids": ["insert"]},
                                   {"question": " "}, {"question": "test", "limit": 0}])
def test_invalid_discovery_requests_fail_explicitly(graph, kwargs):
    with pytest.raises(ValueError):
        discover_operations(graph, **kwargs)


class Backend:
    metadata = {"model": "synthetic-A", "weights_sha256": "one"}

    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return [[len(text) + 1.0, 1.0] for text in texts]

    def segmentations(self, texts):
        return []


def test_cache_bound_to_exact_text_and_model(tmp_path):
    backend = Backend()
    path = tmp_path / "vectors.sqlite3"
    cache = EmbeddingCache(path, backend)
    assert cache.encode(["one", "one"])[0] == cache.encode(["one"])[0]
    assert backend.calls == [["one"]]
    cache.encode(["one", "changed"])
    assert backend.calls[-1] == ["changed"]
    backend.metadata = {**backend.metadata, "weights_sha256": "two"}
    updated = EmbeddingCache(path, backend)
    updated.encode(["one"])
    assert updated.last_stats["computed"] == 1
    with sqlite3.connect(path) as db:
        db.execute("UPDATE vectors SET value = ?", (json.dumps({"vector": [1, 2], "sha256": "bad"}),))
    with pytest.raises(ValueError, match="checksum"):
        updated.encode(["one"])


def test_semantic_retrieval_exposes_model_and_scores_without_edges(graph, tmp_path):
    original = deepcopy(graph)
    cache = EmbeddingCache(tmp_path / "vectors.sqlite3", Backend())
    # Synthetic vectors test wiring only, not quality. Real quality is measured separately.
    pytest.importorskip("numpy")
    result = discover_operations(graph, question="outside vocabulary", embeddings=cache)
    assert result["candidates"]
    assert result["configuration"]["semantic"]["model"] == "synthetic-A"
    assert result["embedding_cache"]["computed"] > 0
    assert graph == original
    again = discover_operations(graph, question="outside vocabulary", embeddings=cache)
    assert again["embedding_cache"]["computed"] == 0
    assert again["candidates"] == result["candidates"]


def test_long_text_partition_retains_tail_and_respects_token_budget():
    from types import SimpleNamespace
    backend = object.__new__(LocalEmbedder)
    backend.metadata = {"truncation": {"max_length": 12}}
    # Deliberately adversarial character tokenizer counts spaces and special tokens.
    backend.counter = SimpleNamespace(encode=lambda text: SimpleNamespace(ids=list(text) + [1, 2]))
    for text in ("beginning middle important tail", "LONG_IDENTIFIER_WITHOUT_WHITESPACE", "α→βγδ" * 10):
        chunks = backend._segments(text)
        assert "".join(chunks) == text
        assert all(len(backend.counter.encode(chunk).ids) <= 12 for chunk in chunks)
