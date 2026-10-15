"""Synthetic projection and provenance controls; no synthetic scientific validation."""

import json
from copy import deepcopy
from graphlib import CycleError

import pytest
from test_workflow_matching import example as example
from test_workflow_transformations import cases as cases

from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.technology_graph.assembly import assemble_graph, build_graph
from genomics_discovery.curation.technology_graph.planning import plan_comparisons
from genomics_discovery.curation.technology_graph.queries import (
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
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_matching.models import AlignmentBatch, ProfileBatch
from genomics_discovery.curation.workflow_matching.tools import Context, validate_alignments


@pytest.fixture
def inputs(example):
    context, profiles, alignments, cases, review, *_ = example
    atlas = {
        "revision_id": "synthetic-atlas",
        "methods": [{"id": ident, "name": ident} for ident in ("a", "b", "c", "isolated")],
        "claims": [
            {
                "id": "development",
                "source": "a",
                "target": "b",
                "relation": "adapts",
                "relationship_kind": "method_development",
                "status": "supported",
                "tradeoff": "synthetic interpretation remains scoped",
            },
            {
                "id": "reuse",
                "source": "c",
                "target": "b",
                "relation": "uses_component",
                "relationship_kind": "component_reuse",
                "status": "supported",
            },
            {
                "id": "comparison",
                "source": "b",
                "target": "isolated",
                "relation": "compares",
                "relationship_kind": "comparison",
                "status": "supported",
            },
            {"id": "pending", "source": "a", "target": "isolated", "status": "awaiting_evidence"},
        ],
        "change_events": [],
        "evidence": [],
    }
    bindings = {
        k: {"atlas_method_id": k, "workflow_method_id": k, "basis": "Explicit synthetic identity"}
        for k in ("a", "b")
    }
    loaded = (
        context,
        profiles,
        alignments,
        validate_alignments(alignments, cases, context),
        review,
        cases,
    )
    return atlas, bindings, [("synthetic-index", loaded)]


def test_assembly_keeps_operations_distinct_and_localizes_historical_details(inputs):
    graph = assemble_graph(*inputs)
    assert sum(n["kind"] == "technology" for n in graph["nodes"]) == 4
    ops = [n for n in graph["nodes"] if n["kind"] == "operation"]
    assert len(ops) == 6 and len({n["id"] for n in ops}) == 6
    assert len([n for n in ops if n["local_id"] == "insert"]) == 2
    assert len(graph["development_details"]) == 1
    detail = graph["development_details"][0]
    assert detail["historical_claim_id"] == "development"
    assert detail["shared_claim_indices"] == [0]
    assert not detail["exact_variant_inheritance_assessed"]
    assert graph["historical_claims"]["development"] == inputs[0]["claims"][0]
    # A reviewed local comparison never upgrades the whole parent's needs_revision status.
    assert {r["review_status"] for r in graph["workflows"].values()} == {"needs_revision"}


def test_local_edges_expose_claim_indices_and_keep_inferences_distinct(inputs):
    graph = assemble_graph(*inputs)
    edges = [edge for edge in graph["edges"] if edge["layer"] == "cross_protocol"]
    assert edges
    for edge in edges:
        claims = graph["comparisons"][edge["comparison_id"]]["alignment"]["claims"]
        assert edge["assertion_level"] == "reviewed_candidate_correspondence"
        assert edge["claim_summary"]["correspondence_indices"] == [
            i for i, c in enumerate(claims) if c["kind"] == "correspondence"
        ]
        assert edge["claim_summary"]["inference_tagged_indices"] == [
            i for i, c in enumerate(claims)
            if any(e["support"] == "inferred"
                   for e in c["left_evidence"] + c["right_evidence"])
        ]
        assert "transfer compatibility" in edge["boundary"]


def test_comparison_claim_tool_keeps_original_indices_and_bound_source_endpoints(inputs):
    graph = assemble_graph(*inputs)
    cid = next(iter(graph["comparisons"]))
    actual = graph["comparisons"][cid]["alignment"]["claims"]
    result = comparison_claims(graph, cid, kind="correspondence", limit=1)
    expected = [i for i, c in enumerate(actual) if c["kind"] == "correspondence"]
    assert result["total"] == len(expected)
    assert result["claims"][0] == {"claim_index": expected[0], **actual[expected[0]]}
    assert result["endpoints"]["left"]["method_id"] == "a"
    assert result["endpoints"]["left"]["packet_sha256"]
    with pytest.raises(ValueError):
        comparison_claims(graph, cid, dimension="ancestry")


def test_history_traversal_excludes_reuse_comparisons_and_pending_claims(inputs):
    graph = assemble_graph(*inputs)
    result = ancestors(graph, "b", depth=5)
    assert result["ancestors"] == ["technology:a"]
    assert {e["kind"] for e in result["edges"]} == {"method_development"}
    assert ancestors(graph, "isolated", depth=5)["ancestors"] == []
    assert "pending" in graph["historical_claims"]
    assert not any(e.get("claim_id") == "pending" for e in graph["edges"])


def test_operations_are_searchable_without_matching_profiles_and_keep_interfaces(inputs):
    graph = assemble_graph(*inputs)
    graph["profiles"] = {}
    edges = deepcopy(graph["edges"])
    result = find_operations(graph, "chromatin associated", limit=1)
    assert result["total"] == 1
    hit = result["hits"][0]
    assert hit["method_id"] == "b"
    assert hit["inputs"][0]["description"] == "Chromatin-associated DNA"
    assert hit["outputs"][0]["description"] == "Tagged DNA"
    assert hit["operation"]["resources"][0]["description"] == "37 C"
    assert hit["review_status"] == "needs_revision"
    assert inspect(graph, hit["node_id"])["record"]["local_id"] == "insert"
    assert graph["edges"] == edges


def test_operation_retrieval_filters_method_and_reports_truncation(inputs):
    graph = assemble_graph(*inputs)
    result = find_operations(graph, "DNA", method_id="a", limit=1)
    assert result["total"] == 3 and result["truncated"]
    assert all(h["method_id"] == "a" for h in result["hits"])
    assert find_operations(graph, "DNA", method_id="isolated")["hits"] == []
    with pytest.raises(KeyError, match="Unknown technology"):
        find_operations(graph, "DNA", method_id="missing")
    with pytest.raises(ValueError):
        find_operations(graph, "   ")


def test_negative_result_is_preserved_without_a_positive_edge(inputs):
    atlas, bindings, runs = deepcopy(inputs)
    origin, loaded = runs[0]
    changed = loaded[2].model_dump(mode="json")
    changed["alignments"][0]["disposition"] = "no_supported_correspondence"
    changed["alignments"][0]["claims"][0]["kind"] = "difference"
    replacement = AlignmentBatch.model_validate(changed)
    # Pure assembler consumes validated inputs; review binding is checked at build_graph.
    loaded = (*loaded[:2], replacement, *loaded[3:])
    graph = assemble_graph(atlas, bindings, [(origin, loaded)])
    assert len(graph["comparisons"]) == 1
    assert not any(e["layer"] == "cross_protocol" for e in graph["edges"])


def test_protocol_edges_preserve_original_routes_and_conditions(inputs):
    graph = assemble_graph(*inputs)
    for record in graph["workflows"].values():
        expected = {f["id"]: f for f in record["workflow"]["flows"]}
        actual = {
            e["record"]["id"]: e["record"]
            for e in graph["edges"]
            if e["layer"] == "protocol"
            and e["workflow_id"] == next(k for k, v in graph["workflows"].items() if v is record)
        }
        assert actual == expected


@pytest.mark.parametrize("defect", ["missing", "wrong_method", "unknown_atlas", "blank_basis"])
def test_identity_bindings_are_explicit_not_fuzzy(inputs, defect):
    atlas, bindings, runs = deepcopy(inputs)
    if defect == "missing":
        del bindings["a"]
    elif defect == "wrong_method":
        bindings["a"]["workflow_method_id"] = "nearby-sounding-name"
    elif defect == "unknown_atlas":
        bindings["a"]["atlas_method_id"] = "missing"
    else:
        bindings["a"]["basis"] = " "
    with pytest.raises(ValueError, match="identity binding"):
        assemble_graph(atlas, bindings, runs)


def test_deduplication_does_not_duplicate_shared_workflow_or_edges(inputs):
    atlas, bindings, runs = inputs
    one = assemble_graph(atlas, bindings, runs)
    two = assemble_graph(atlas, bindings, runs + runs)
    assert two == one


def test_new_parent_review_is_a_separate_record_not_a_silent_verdict_replacement(inputs):
    atlas, bindings, runs = inputs
    old = deepcopy(runs[0][1][0].parents["a"])
    new = deepcopy(old)
    new["review"] = new["review"].model_copy(update={"notes": ("Another scoped review",)})
    contexts = [Context({"a": row}, {}, {}, set()) for row in (old, new)]
    isolated = [
        (str(i), (ctx, ProfileBatch(profiles=()), AlignmentBatch(alignments=()), {}, None, []))
        for i, ctx in enumerate(contexts)
    ]
    graph = assemble_graph(atlas, bindings, isolated)
    assert len(graph["workflows"]) == 2
    assert len({r["workflow_sha256"] for r in graph["workflows"].values()}) == 1
    assert len({r["review_sha256"] for r in graph["workflows"].values()}) == 2


def test_supported_historical_cycle_is_rejected(inputs):
    atlas, bindings, runs = deepcopy(inputs)
    atlas["claims"].append(
        {
            "id": "cycle",
            "source": "b",
            "target": "a",
            "relation": "adapts",
            "relationship_kind": "method_development",
            "status": "supported",
        }
    )
    with pytest.raises(CycleError):
        assemble_graph(atlas, bindings, runs)


def test_explicit_external_method_does_not_inherit_vendor_classification(inputs):
    atlas, bindings, runs = deepcopy(inputs)
    bindings["b"].update(
        atlas_method_id="external-b",
        external_method={"id": "external-b", "name": "External method B"},
    )
    graph = assemble_graph(atlas, bindings, runs)
    node = next(n for n in graph["nodes"] if n["id"] == "technology:external-b")
    assert node["record"]["outside_catalogue"] and node["record"]["original_categories"] == []
    assert len(graph["development_details"]) == 0
    assert len([n for n in graph["nodes"] if n["kind"] == "technology"]) == 5


def test_discovery_retains_provenance_and_does_not_create_edges(inputs):
    graph = assemble_graph(*inputs)
    before = digest(graph)
    result = discover(graph, "adapter insertion", dimension="transformation", exclude_method="a")
    assert {h["method_id"] for h in result["hits"]} == {"b"}
    assert all(h["view_id"] in graph["views"] for h in result["hits"])
    assert all(h["comparison_leads"] for h in result["hits"])
    assert digest(graph) == before
    with pytest.raises(KeyError):
        discover(graph, "adapter", exclude_method="missing")


def test_navigation_links_seed_and_expanded_views_without_new_scientific_edges(inputs):
    graph = assemble_graph(*inputs)
    right_seed = next(
        r["view_id"]
        for r in graph["profiles"].values()
        if graph["views"][r["view_id"]]["method_id"] == "b"
    )
    before = digest(graph)
    leads = comparison_leads(graph, right_seed)
    assert leads["total"] == 1
    assert leads["results"][0]["overlap_kind"] == "query_superset"
    assert leads["results"][0]["shared_operation_ids"] == ["insert"]
    assert digest(graph) == before


def test_symmetric_neighbor_navigation_is_scoped_and_paginated(inputs):
    graph = assemble_graph(*inputs)
    edge = next(e for e in graph["edges"] if e["layer"] == "cross_protocol")
    for endpoint in (edge["source"], edge["target"]):
        result = neighbors(graph, endpoint, layer="cross_protocol", direction="out", limit=1)
        assert result["results"][0]["edge"] == edge
        assert result["total"] == 1 and result["next_offset"] is None
    page = neighbors(graph, "technology:b", limit=1)
    assert page["total"] > 1 and page["next_offset"] == 1
    with pytest.raises(KeyError):
        neighbors(graph, "unknown")
    with pytest.raises(ValueError):
        neighbors(graph, edge["source"], layer="imaginary")


def test_exact_source_and_record_tools(inputs):
    graph = assemble_graph(*inputs)
    wid = next(iter(graph["workflows"]))
    passage = read_source(graph, wid, "paper", "p00002")
    assert "transposase" in passage["passage"]["text"]
    assert inspect(graph, "development")["development_details"]
    account = development(graph, "development")
    assert len(account["protocol_comparisons"]) == 1
    assert account["protocol_comparisons"][0]["shared_parts"][0]["kind"] == "correspondence"
    assert not account["protocol_comparisons"][0]["exact_variant_inheritance_assessed"]
    with pytest.raises(ValueError):
        development(graph, "pending")
    with pytest.raises(KeyError):
        read_source(graph, wid, "other_parent_source", "p00002")


def test_historical_worklist_targets_uncompared_versions_without_forcing_edges(inputs):
    graph = assemble_graph(*inputs)
    first = plan_comparisons(graph)
    assert first["total"] == 1
    assert first["requests"][0]["next_action"] == "inspect_existing_scope"
    graph["development_details"] = []
    plan = plan_comparisons(graph, method_id="b")
    assert plan["total"] == 1
    assert plan["requests"][0]["historical_claim_id"] == "development"
    assert plan["requests"][0]["next_action"] == "compare_sources"
    assert plan_comparisons(graph, method_id="isolated")["total"] == 0
    with pytest.raises(KeyError):
        plan_comparisons(graph, method_id="absent")


def test_build_pins_sources_and_graph_revision(inputs, tmp_path, monkeypatch):
    atlas, bindings, runs = inputs
    context = runs[0][1][0]
    ap, bp, index = (tmp_path / n for n in ("atlas.json", "bindings.json", "index.json"))
    ap.write_text(json.dumps(atlas))
    bp.write_text(json.dumps(bindings))
    review = tmp_path / "review.json"
    review.write_text("{}")
    (tmp_path / "config.json").write_text("{}")
    index.write_text(json.dumps({"files": {"review": str(review)}}))
    monkeypatch.setattr(
        "genomics_discovery.curation.technology_graph.assembly.load_reviewed", lambda _: runs[0][1]
    )
    graph = build_graph(ap, bp, [index])
    output = tmp_path / "graph.json"
    output.write_text(json.dumps(graph))
    assert load_graph(output)["revision_id"] == graph["revision_id"]
    assert graph["statistics"]["technologies_without_operation_graphs"] == 2
    graph["boundary"] = "tampered"
    output.write_text(json.dumps(graph))
    with pytest.raises(ValueError, match="revision"):
        load_graph(output)
    graph["revision_id"] = digest({k: v for k, v in graph.items() if k != "revision_id"})
    output.write_text(json.dumps(graph))
    source = next(p for p in context.files if p.suffix == ".txt")
    source.write_text("changed original")
    assert sha(source) != graph["input_files"][str(source)]
    with pytest.raises(ValueError, match="changed or is unavailable"):
        load_graph(output)
