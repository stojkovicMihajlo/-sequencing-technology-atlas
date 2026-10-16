"""Registry integrity controls; synthetic positive reviews are not scientific validation."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from test_workflow_transformations import cases as cases, candidate_for, review_for

from genomics_discovery.curation.operation_registry.models import Assignment
from genomics_discovery.curation.operation_registry.store import (
    Registry, catalogue, check_proposal, graph_overlay, search, uses,
)
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipReview, load_occurrences,
)


def assignment(cases, snapshot, index=0, **updates):
    data = candidate_for(cases).model_dump(mode="json")
    data.update(format_version="concept_assignment/1.0", members=[data["members"][index]],
                registry_revision=digest(snapshot), decision="new",
                aliases=["Adapter fragment preparation"],
                scope_note="Synthetic example: transposition and repair, not arbitrary DNA joining.",
                lookup_queries=["adapter DNA transposase"],
                distinction="Synthetic membership gate example; not an experimental result.")
    data.update(updates)
    return Assignment.model_validate(data)


def review(proposal, cases, verdict="supported"):
    # Existing fixture helper supplies full-cut source coverage.
    obj = review_for(candidate_for(cases), cases).model_dump(mode="json")
    obj["candidate_sha256"] = digest(proposal)
    obj["checks"] = [c for c in obj["checks"]
                     if c["occurrence_id"] == proposal.members[0].occurrence_id]
    obj["checks"][0]["verdict"] = verdict
    return MembershipReview.model_validate(obj)


@pytest.fixture
def registry(tmp_path):
    result = Registry(tmp_path / "concepts.sqlite")
    result.initialize()
    return result


def test_create_lookup_and_reuse_preserve_two_contexts(cases, registry):
    before = digest(cases[2][0])
    first = assignment(cases, registry.snapshot())
    registry.apply(first, review(first, cases), *cases[:2])
    result = search(registry.snapshot(), "ADAPTER FRAGMENT PREPARATION")
    assert result["hits"][0]["exact_label_hit"]
    second = assignment(cases, registry.snapshot(), index=1, decision="reuse")
    registry.apply(second, review(second, cases), *cases[:2])
    snapshot = registry.snapshot()
    assert len(snapshot["concepts"]) == 1 and len(snapshot["assignments"]) == 2
    records = uses(snapshot, first.definition.id)["uses"]
    assert {r["method_id"] for r in records} == {"a", "b"}
    assert all(r["workflow_review_status"] == "needs_revision" for r in records)
    assert records[0]["inputs"] != records[1]["inputs"]
    assert digest(cases[2][0]) == before
    overlay = graph_overlay(snapshot)
    assert len(overlay["nodes"]) == 3 and len(overlay["edges"]) == 2
    assert {e["kind"] for e in overlay["edges"]} == {"instance_of_concept"}


def test_recorded_commit_base_is_the_actual_previous_registry(cases, registry):
    before = registry.snapshot()
    proposal = assignment(cases, before)
    registry.apply(proposal, review(proposal, cases), *cases[:2])
    assert registry.snapshot()["assignments"][digest(proposal)]["commit_base_revision"] == digest(before)


def test_catalogue_withholds_accumulated_evidence_and_uses_are_paginated(cases, registry):
    first = assignment(cases, registry.snapshot())
    registry.apply(first, review(first, cases), *cases[:2])
    second = assignment(cases, registry.snapshot(), index=1, decision="reuse")
    registry.apply(second, review(second, cases), *cases[:2])
    visible = catalogue(registry.snapshot())
    assert "assignments" not in visible and "checks" not in json.dumps(visible)
    first_page = uses(registry.snapshot(), first.definition.id, limit=1)
    second_page = uses(registry.snapshot(), first.definition.id, offset=first_page["next_offset"], limit=1)
    assert first_page["total"] == 2 and second_page["next_offset"] is None
    assert first_page["uses"][0]["method_id"] != second_page["uses"][0]["method_id"]


def test_mutating_a_stored_definition_invalidates_the_registry(cases, registry):
    import sqlite3
    proposal = assignment(cases, registry.snapshot())
    registry.apply(proposal, review(proposal, cases), *cases[:2])
    snapshot = registry.snapshot()
    snapshot["concepts"][proposal.definition.id]["definition"]["output"] = "An unreviewed changed output"
    with sqlite3.connect(registry.path) as db:
        db.execute("UPDATE registry SET payload=? WHERE id=1", (json.dumps(snapshot),))
    with pytest.raises(ValueError, match="Stored concept differs"):
        registry.snapshot()


@pytest.mark.parametrize("field", ["proposal", "occurrence", "review"])
def test_changed_archived_records_cannot_be_exported(cases, registry, field):
    proposal = assignment(cases, registry.snapshot())
    registry.apply(proposal, review(proposal, cases), *cases[:2])
    snapshot = registry.snapshot()
    row = snapshot["assignments"][digest(proposal)]
    if field == "proposal":
        row[field]["distinction"] = "Changed after review"
    elif field == "occurrence":
        row[field]["scope"] = "Changed source context"
    else:
        row[field]["checks"][0]["verdict"] = "unresolved"
    with pytest.raises(ValueError, match="Stored assignment"):
        graph_overlay(snapshot)


@pytest.mark.parametrize("verdict", ["unsupported", "unresolved"])
def test_negative_review_cannot_create_a_concept(cases, registry, verdict):
    proposal = assignment(cases, registry.snapshot())
    before = registry.snapshot()
    with pytest.raises(ValueError, match="not supported"):
        registry.apply(proposal, review(proposal, cases, verdict), *cases[:2])
    assert registry.snapshot() == before


def test_concurrent_new_proposals_cannot_overwrite_each_other(cases, registry):
    snapshot = registry.snapshot()
    proposals = [assignment(cases, snapshot, index=i) for i in (0, 1)]
    def apply(proposal):
        try:
            return registry.apply(proposal, review(proposal, cases), *cases[:2])
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, proposals))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert any("Stale registry revision" in r for r in results if isinstance(r, str))
    assert len(registry.snapshot()["concepts"]) == 1


@pytest.mark.parametrize("field,value", [("output", "RNA, not DNA"), ("name", "Changed label")])
def test_reuse_cannot_redefine_concept(cases, registry, field, value):
    first = assignment(cases, registry.snapshot())
    registry.apply(first, review(first, cases), *cases[:2])
    data = assignment(cases, registry.snapshot(), index=1, decision="reuse").model_dump(mode="json")
    data["definition"][field] = value
    changed = Assignment.model_validate(data)
    with pytest.raises(ValueError, match="exact existing concept"):
        check_proposal(registry.snapshot(), changed, *cases[:2])


def test_new_same_label_requires_disambiguation_and_alias_hit_never_assigns(cases, registry):
    first = assignment(cases, registry.snapshot())
    registry.apply(first, review(first, cases), *cases[:2])
    data = assignment(cases, registry.snapshot(), index=1).model_dump(mode="json")
    data["definition"]["id"] = "different-transformation"
    data["definition"]["output"] = "A genuinely different output in this synthetic test"
    with pytest.raises(ValueError, match="Preferred label already exists"):
        check_proposal(registry.snapshot(), Assignment.model_validate(data), *cases[:2])
    data["definition"]["name"] = "Qualified different transformation"
    second = Assignment.model_validate(data)
    registry.apply(second, review(second, cases), *cases[:2])
    before = registry.snapshot()
    hits = search(before, first.aliases[0])["hits"]
    assert sum(h["exact_label_hit"] for h in hits) == 2
    assert registry.snapshot() == before


def test_one_selection_can_have_multiple_labels(cases, registry):
    first = assignment(cases, registry.snapshot())
    registry.apply(first, review(first, cases), *cases[:2])
    data = assignment(cases, registry.snapshot(), relation="shared_purpose").model_dump(mode="json")
    data["definition"]["id"] = "prepare-dna-readout"
    data["definition"]["name"] = "Prepare DNA for readout"
    second = Assignment.model_validate(data)
    registry.apply(second, review(second, cases), *cases[:2])
    overlay = graph_overlay(registry.snapshot())
    assert len({e["source"] for e in overlay["edges"]}) == 1
    assert len({e["target"] for e in overlay["edges"]}) == 2


def test_forged_quote_and_stale_review_rejected(cases, registry):
    proposal = assignment(cases, registry.snapshot())
    data = proposal.model_dump(mode="json")
    data["members"][0]["evidence"][0]["quote"] = "This text does not occur in the source."
    with pytest.raises(ValueError, match="quote is not present"):
        check_proposal(registry.snapshot(), Assignment.model_validate(data), *cases[:2])
    old_review = review(proposal, cases)
    data = proposal.model_dump(mode="json")
    data["distinction"] = "Changed after review"
    with pytest.raises(ValueError, match="exact candidate"):
        registry.apply(Assignment.model_validate(data), old_review, *cases[:2])


def test_incomplete_review_and_changed_occurrence_rejected(cases, registry):
    proposal = assignment(cases, registry.snapshot())
    data = review(proposal, cases).model_dump(mode="json")
    data["checks"] = []
    with pytest.raises(ValueError, match="exactly one check"):
        registry.apply(proposal, MembershipReview.model_validate(data), *cases[:2])
    data = proposal.model_dump(mode="json")
    data["members"][0]["occurrence_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Stale occurrence"):
        check_proposal(registry.snapshot(), Assignment.model_validate(data), *cases[:2])


def test_alias_schema_and_unknown_reuse(cases, registry):
    with pytest.raises(ValidationError, match="must be distinct"):
        assignment(cases, registry.snapshot(), aliases=["Produce repaired adapter-bearing DNA"])
    proposal = assignment(cases, registry.snapshot(), decision="reuse")
    with pytest.raises(ValueError, match="exact existing"):
        check_proposal(registry.snapshot(), proposal, *cases[:2])


def test_unrelated_additions_do_not_force_repeated_review_for_reuse(cases, registry):
    first = assignment(cases, registry.snapshot())
    registry.apply(first, review(first, cases), *cases[:2])
    reused = assignment(cases, registry.snapshot(), index=1, decision="reuse")
    old_review = review(reused, cases)
    data = assignment(cases, registry.snapshot(), relation="shared_purpose").model_dump(mode="json")
    data["definition"]["id"] = "a-different-purpose-index"
    other = Assignment.model_validate(data)
    registry.apply(other, review(other, cases), *cases[:2])
    registry.apply(reused, old_review, *cases[:2])
    assert len(registry.snapshot()["assignments"]) == 3


def test_atlas_integration_joins_exact_nodes_and_retains_module_membership(cases, registry):
    from genomics_discovery.curation.operation_registry.graph import attach_to_graph
    from genomics_discovery.curation.technology_graph.assembly import node_id
    from genomics_discovery.curation.technology_graph.queries import neighbors, inspect
    proposal = assignment(cases, registry.snapshot())
    registry.apply(proposal, review(proposal, cases), *cases[:2])
    occurrence = cases[0][proposal.members[0].occurrence_id]
    w = cases[2][0]
    graph = {"workflows": {"workflow:example": {"workflow_sha256": digest(w),
        "packet_sha256": occurrence.source_packet_sha256, "atlas_method_id": "a",
        "workflow": w.model_dump(mode="json")}}, "views": {}, "profiles": {}, "comparisons": {},
        "development_details": [], "edges": [], "nodes": [
        {"id": "technology:a", "kind": "technology", "method_id": "a"},
        *[{"id": node_id("operation", "example", o.id), "kind": "operation"} for o in w.operations]]}
    before = digest(graph)
    enriched = attach_to_graph(graph, registry.snapshot())
    assert digest(graph) == before
    result = neighbors(enriched, "concept:" + proposal.definition.id, layer="concept")
    assert result["total"] == 1
    selection = result["results"][0]["neighbor"]
    assert selection["local_module_ids"]
    assert set(selection["operation_ids"]) == {"insert", "repair"}
    assert neighbors(enriched, selection["id"], layer="concept")["total"] == 3
    assert inspect(enriched, digest(proposal))["record_type"] == "concept_assignments"
    graph["workflows"]["workflow:example"]["packet_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="exact workflow/source"):
        attach_to_graph(graph, registry.snapshot())


def test_prepare_rebuilds_connected_cut_and_original_change_is_detected(cases, tmp_path, monkeypatch, capsys):
    from genomics_discovery.curation.operation_registry.__main__ import main, load_manifest
    import sys
    output = tmp_path / "prepared"
    monkeypatch.setattr(sys, "argv", ["concepts", "prepare", str(tmp_path / "a.workflow.json"),
        str(tmp_path / "a.packet.json"), "insert", "repair", "--review", str(tmp_path / "a.review.json"),
        "--output", str(output)])
    main()
    result = json.loads(capsys.readouterr().out)
    loaded, _ = load_occurrences(load_manifest(output / "manifest.json"))
    assert result["occurrence_id"] in loaded
    (tmp_path / "a.txt").write_text("A changed paper must not inherit old bindings.")
    with pytest.raises(ValueError):
        load_occurrences(load_manifest(output / "manifest.json"))
