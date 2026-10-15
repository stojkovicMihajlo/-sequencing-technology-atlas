"""Graph retrieval is context-preserving and never creates inferred scientific edges."""

import json
import sys
from copy import deepcopy

import pytest
from test_workflow_transformations import candidate_for, review_for
from test_workflow_transformations import cases as cases

from genomics_discovery.curation.workflow_extraction.models import Workflow, digest
from genomics_discovery.curation.workflow_extraction.projection import query_workflow
from genomics_discovery.curation.workflow_extraction.sources import build_packet
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipReview,
    cut_transformation,
    review_membership,
)
from playground.workflow_comparison_query import main, query_graph, validate_graph
from playground.workflow_transformations_live import comparison_graph


def comparison(cases, embedded=False):
    candidate = candidate_for(cases)
    review = review_for(candidate, cases)
    record = review_membership(candidate, review, cases[0], cases[1])
    if embedded:
        record.update(candidate=candidate.model_dump(mode="json"),
                      review=review.model_dump(mode="json"))
    else:
        record.pop("candidate", None)
        record.pop("review", None)
    return record


def test_definition_discovery_follows_only_existing_direct_links(cases):
    graph = comparison_graph(cases[0], [comparison(cases)])
    result = query_graph(graph, query="adapter bearing", relation="shared_mechanism")
    assert result["retrieval_only"] and result["total"] == 2
    assert result["collections_total"] == 1
    assert result["collections"][0]["relation"] == "shared_mechanism"
    assert {row["occurrence_id"] for row in result["hits"]} == set(cases[0])
    wrong_relation = query_graph(graph, query="DNA", relation="shared_purpose")
    assert wrong_relation["hits"] == wrong_relation["collections"] == []


@pytest.fixture
def lexical_tier_graph(tmp_path):
    """General synthetic route: preparation shares parent context with a later copying step."""
    occurrences, packets, workflows = {}, {}, []
    selected = {}
    for method in ("a_context", "b_definition", "c_definition", "z_actual"):
        source = tmp_path / f"{method}.txt"
        source.write_text("Prepare material with buffer, then perform amplification with enzyme.")
        packet = build_packet(
            method_id=method, title="Preparation and amplification",
            scope="Prepare material and perform amplification", variant="Synthetic route",
            sources=[{"source_id": "paper", "title": "Synthetic protocol",
                      "url": "https://example.org/protocol", "role": "primary_paper",
                      "path": str(source)}])
        passage = packet["sources"][0]["passages"][0]
        evidence = [{"source_id": "paper", "passage_id": passage["passage_id"],
                     "quote": passage["text"], "support": "reported",
                     "rationale": "Synthetic source fixture only."}]
        workflow = Workflow.model_validate({
            **{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
            "states": [{"id": sid, "kind": "material", "description": description,
                        "evidence": evidence}
                       for sid, description in (("input", "Input material"),
                                                ("prepared", "Prepared material"),
                                                ("copied", "Copied material"))],
            "operations": [
                {"id": "prepare", "name": "Prepare material", "action": "Treat material",
                 "inputs": ["input"], "outputs": ["prepared"],
                 "resources": [{"kind": "reagent", "description": "Preparation buffer",
                                "evidence": evidence}],
                 "information_effect": "Retain material identity", "evidence": evidence},
                {"id": "amplify", "name": "Amplification", "action": "Amplify material",
                 "inputs": ["prepared"], "outputs": ["copied"],
                 "resources": [{"kind": "reagent", "description": "Copying enzyme",
                                "evidence": evidence}],
                 "information_effect": "Copy molecular information", "evidence": evidence}],
            "flows": [{"id": fid, "source": start, "target": end, "kind": kind,
                       "basis": "Synthetic material routing", "evidence": evidence}
                      for fid, start, end, kind in (
                          ("prepare_in", "input", "prepare", "material_input"),
                          ("prepare_out", "prepare", "prepared", "material_output"),
                          ("amplify_in", "prepared", "amplify", "material_input"),
                          ("amplify_out", "amplify", "copied", "material_output"))],
            "modules": [{"id": "route", "name": "Route",
                         "operation_ids": ["prepare", "amplify"],
                         "boundary_rationale": "Synthetic route"}], "unresolved": []})
        selection = "amplify" if method == "z_actual" else "prepare"
        occurrence = cut_transformation(workflow, packet, [selection])
        occurrences[occurrence.id], packets[occurrence.id] = occurrence, packet
        workflows.append(workflow)
        selected[method] = occurrence.id
    pair = {selected[method]: occurrences[selected[method]]
            for method in ("b_definition", "c_definition")}
    pair_cases = pair, packets, workflows
    candidate = candidate_for(pair_cases, relation="shared_purpose", definition={
        "id": "prepare-for-amplification", "name": "Prepare material",
        "substrate": "Input material", "operation": "Treat material",
        "physical_mechanism": None, "mechanism_unknown_reason": "Synthetic fixture",
        "output": "Prepared material", "information_effect": "Retain identity",
        "purpose": "Supply prepared material for amplification",
        "context_requirements": "Synthetic preparation buffer"})
    review = review_for(candidate, pair_cases)
    record = review_membership(candidate, review, occurrences, packets)
    return comparison_graph(occurrences, [record]), occurrences, selected


def test_actual_operation_precedes_inherited_method_context_under_limit(lexical_tier_graph):
    graph, occurrences, selected = lexical_tier_graph
    before = digest(graph)
    assert selected["a_context"] < selected["z_actual"]
    result = query_graph(graph, query="amplification", limit=1)
    assert result["total"] == 4 and result["truncated"] is True
    assert result["hits"][0]["occurrence_id"] == selected["z_actual"]
    assert result["hits"][0]["match_tier"] == 1
    assert result["hits"][0]["match_basis"] == "unit_content"
    full = query_graph(graph, selected["a_context"])
    assert full["occurrence"] == occurrences[selected["a_context"]].model_dump(mode="json")
    assert full["occurrence"]["scope"] == "Prepare material and perform amplification"
    assert digest(graph) == before


def test_definition_and_context_tiers_are_explicit_and_deterministic(lexical_tier_graph):
    graph, _, selected = lexical_tier_graph
    result = query_graph(graph, query="amplification")
    assert [(hit["occurrence_id"], hit["match_tier"], hit["match_basis"])
            for hit in result["hits"]] == [
        (selected["z_actual"], 1, "unit_content"),
        (selected["b_definition"], 2, "direct_reviewed_definition"),
        (selected["c_definition"], 2, "direct_reviewed_definition"),
        (selected["a_context"], 3, "method_context_fallback")]
    assert "do not establish semantic relevance" in result["index_boundary"]
    reordered = deepcopy(graph)
    reordered["nodes"].reverse()
    reordered["edges"].reverse()
    assert query_graph(reordered, query="amplification") == result
    filtered = query_graph(graph, query="amplification", relation="shared_purpose", limit=1)
    assert filtered["total"] == 2 and filtered["truncated"] is True
    assert filtered["hits"][0]["occurrence_id"] == selected["b_definition"]
    assert filtered["hits"][0]["match_tier"] == 2
    assert query_graph(graph, query="amplification", relation="shared_mechanism")["total"] == 0


@pytest.mark.parametrize("query", ["copied", "enzyme"])
def test_own_states_and_resources_are_primary_matches(lexical_tier_graph, query):
    graph, _, selected = lexical_tier_graph
    result = query_graph(graph, query=query)
    assert result["total"] == 1
    assert result["hits"][0]["occurrence_id"] == selected["z_actual"]
    assert result["hits"][0]["match_tier"] == 1
    assert result["hits"][0]["match_basis"] == "unit_content"


def test_unit_matches_take_precedence_over_definition_matches(lexical_tier_graph):
    graph, _, selected = lexical_tier_graph
    result = query_graph(graph, query="material")
    assert result["total"] == 4 and result["collections_total"] == 1
    assert [hit["occurrence_id"] for hit in result["hits"]] == sorted(selected.values())
    assert all(hit["match_tier"] == 1 and hit["match_basis"] == "unit_content"
               for hit in result["hits"])


def test_mixed_unit_and_method_terms_remain_labelled_context_hits(lexical_tier_graph):
    graph, _, selected = lexical_tier_graph
    result = query_graph(graph, query="a_context prepared")
    assert result["total"] == 1
    assert result["hits"][0]["occurrence_id"] == selected["a_context"]
    assert result["hits"][0]["match_tier"] == 3
    assert result["hits"][0]["match_basis"] == "method_context_fallback"


def test_unrelated_parent_uncertainty_and_old_review_text_are_not_discovery_hits(cases):
    graph = comparison_graph(cases[0], [])
    # Present only in the parent's unknown fact / previous opinion, not the cut's descriptions.
    assert query_graph(graph, query="Chemical detail remains unknown")["hits"] == []
    assert query_graph(graph, query="Record correction remains")["hits"] == []


def test_invalid_relation_cannot_create_new_semantics(cases):
    graph = comparison_graph(cases[0], [])
    with pytest.raises(ValueError, match="Unknown relation"):
        query_graph(graph, query="DNA", relation="compatible")


def test_full_units_interfaces_contexts_and_negative_parents_survive_supported_comparison(cases):
    graph = comparison_graph(cases[0], [comparison(cases)])
    ident = next(iter(cases[0]))
    result = query_graph(graph, ident)
    assert result["occurrence"] == cases[0][ident].model_dump(mode="json")
    assert result["occurrence"]["requirements"] and result["occurrence"]["external_operations"]
    assert result["parent"]["review_status"] == "needs_revision"
    peer = result["direct_reviewed_peers"][0]
    assert peer["peer"]["parent"]["review_status"] == "needs_revision"
    assert peer["provenance"].startswith("hash_only")
    assert "candidate" not in peer["comparison"] and "review" not in peer["comparison"]
    assert "compatibility" in result["boundary"] and "source_packet_sha256" in result[
        "source_read_hint"]


def test_two_direct_comparisons_do_not_infer_transitive_peers(cases):
    occurrences, packets, workflows = cases
    a, b = list(occurrences)
    extra = cut_transformation(workflows[0], packets[a], ["insert"])
    extended = {**occurrences, extra.id: extra}
    packets = {**packets, extra.id: packets[a]}
    def pair(left, right):
        return {key: extended[key] for key in (left, right)}, packets, workflows

    records = [comparison(pair(a, b)), comparison(pair(b, extra.id))]
    graph = comparison_graph(extended, records)
    result = query_graph(graph, a)
    assert [peer["peer"]["occurrence"]["id"] for peer in result["direct_reviewed_peers"]] == [b]
    assert extra.id not in {peer["peer"]["occurrence"]["id"]
                            for peer in result["direct_reviewed_peers"]}
    assert query_graph(graph, b)["parent"]["review_status"] == "needs_revision"
    assert result["parent"]["review_status"] == "multiple_review_contexts"


def test_ambiguous_copy_word_retrieves_molecular_and_data_contexts_without_linking(cases, tmp_path):
    source = tmp_path / "data_copy.txt"
    source.write_text("Copy the barcode table into a new data table without molecular changes.")
    packet = build_packet(
        method_id="table_copy", title="Table processing", scope="Data transformation",
        variant="Digitized barcode table", sources=[{
            "source_id": "paper", "title": "Data protocol", "url": "https://example.org/table",
            "role": "primary_paper", "path": str(source)}])
    passage = packet["sources"][0]["passages"][0]
    evidence = [{"source_id": "paper", "passage_id": passage["passage_id"],
                 "quote": passage["text"], "support": "reported", "rationale": "Table copying."}]
    workflow = Workflow.model_validate({
        **{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
        "states": [{"id": sid, "kind": "data", "description": sid, "evidence": evidence}
                   for sid in ("input_table", "copied_table")],
        "operations": [{"id": "copy", "name": "Copy table", "action": "Copy barcode table",
                        "inputs": ["input_table"], "outputs": ["copied_table"], "resources": [],
                        "information_effect": "Duplicate table without molecular copying.",
                        "evidence": evidence}],
        "flows": [{"id": fid, "source": start, "target": end, "kind": kind,
                   "basis": "Reported data copying", "evidence": evidence}
                  for fid, start, end, kind in (("in", "input_table", "copy", "data_input"),
                                                ("out", "copy", "copied_table", "data_output"))],
        "modules": [{"id": "data", "name": "Data", "operation_ids": ["copy"],
                     "boundary_rationale": "Declared data scope"}], "unresolved": []})
    occurrence = cut_transformation(workflow, packet, ["copy"])
    molecular = next(iter(cases[0].values()))
    graph = comparison_graph({molecular.id: molecular, occurrence.id: occurrence}, [])
    before = digest(graph)
    result = query_graph(graph, query="copy")
    assert result["retrieval_only"] is True and len(result["hits"]) == 2
    assert result["view"] == "discovery_index" and result["total"] == 2
    assert {hit["inputs"][0]["kind"] for hit in result["hits"]} == {
        "material", "data"}
    assert all("direct_reviewed_peers" not in hit for hit in result["hits"])
    assert all("evidence" not in hit and "resources" not in hit and "flows" not in hit
               for hit in result["hits"])
    assert digest(graph) == before
    assert {edge["kind"] for edge in graph["edges"]} == {"contains_unit"}


@pytest.mark.parametrize("change", ["node_id", "endpoint", "flow_endpoint", "flow_type", "type",
                                   "duplicate", "parent_status", "member_status", "acceptance",
                                   "transitive", "unsupported", "hash", "incomplete_proof"])
def test_malformed_or_laundered_graphs_do_not_return_peers(cases, change):
    graph = comparison_graph(cases[0], [comparison(cases, embedded=True)])
    occurrence = next(node for node in graph["nodes"]
                      if node["kind"] == "transformation_occurrence")
    parent = next(node for node in graph["nodes"] if node["kind"] == "workflow")
    record = graph["comparisons"][0]
    if change == "node_id":
        occurrence["id"] = "different"
    elif change == "endpoint":
        graph["edges"][0]["target"] = "absent"
    elif change == "flow_endpoint":
        occurrence["record"]["flows"][0]["source"] = "absent"
    elif change == "flow_type":
        occurrence["record"]["flows"][0]["kind"] = "data_input"
    elif change == "type":
        graph["edges"][0]["kind"] = "compatible"
    elif change == "duplicate":
        graph["nodes"].append(deepcopy(occurrence))
    elif change == "parent_status":
        parent["review_status"] = "supported"
    elif change == "member_status":
        record["members"][0]["workflow_review_status"] = "supported"
    elif change == "acceptance":
        record["production_acceptance"] = "supported"
    elif change == "transitive":
        record["transitive_inference"] = True
    elif change == "unsupported":
        record["review"]["checks"][0]["verdict"] = "unsupported"
        record["review_sha256"] = digest(record["review"])
    elif change == "hash":
        record["candidate_sha256"] = "0" * 64
    else:
        del record["review"]
    with pytest.raises((ValueError, KeyError)):
        validate_graph(graph)


def test_unknown_occurrences_and_empty_search_terms_fail_explicitly(cases):
    graph = comparison_graph(cases[0], [])
    with pytest.raises(KeyError, match="Unknown occurrence"):
        query_graph(graph, "absent")
    with pytest.raises(ValueError, match="nonempty"):
        query_graph(graph, query="?!")


def test_discovery_index_limit_is_explicit_and_full_get_is_lossless(cases):
    occurrences, packets, workflows = cases
    cuts = {}
    for ident, workflow in zip(occurrences, workflows, strict=True):
        for selection in (["insert"], ["repair"], ["assay"], ["insert", "repair"],
                          ["insert", "assay"], ["insert", "repair", "assay"]):
            cut = cut_transformation(workflow, packets[ident], selection)
            cuts[cut.id] = cut
    graph = comparison_graph(cuts, [])
    index = query_graph(graph, query="DNA")
    assert index["limit"] == 10 and index["total"] == 12 and index["truncated"] is True
    assert len(index["hits"]) == 10 and "recipe" in index["index_boundary"]
    limited = query_graph(graph, query="DNA", limit=1)
    assert limited["total"] == 12 and limited["truncated"] is True
    assert len(limited["hits"]) == 1
    complete = query_graph(graph, query="DNA", limit=20)
    assert complete["total"] == 12 and complete["truncated"] is False
    assert query_graph(graph, query="copy")["total"] == 6
    for hit in index["hits"]:
        ident = hit["full_get_hint"]["value"]
        full = query_graph(graph, ident)
        assert full["occurrence"] == cuts[ident].model_dump(mode="json")
        assert full["parent_context_included"] is False
        assert full["parent_operation_contexts"] == []


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_discovery_limits_fail_explicitly(cases, limit):
    with pytest.raises(ValueError, match="positive integer"):
        query_graph(comparison_graph(cases[0], []), query="copy", limit=limit)


def test_duplicate_retained_flow_id_is_invalid_even_without_embedded_proof(cases):
    graph = comparison_graph(cases[0], [comparison(cases)])
    node = next(node for node in graph["nodes"] if node["kind"] == "transformation_occurrence")
    node["record"]["flows"].append(deepcopy(node["record"]["flows"][0]))
    with pytest.raises(ValueError, match="Duplicate or conflicting retained"):
        validate_graph(graph)


def test_proof_bundle_lineage_is_returned_as_recorded_metadata_without_inherited_verdict(cases):
    graph = comparison_graph(cases[0], [comparison(cases, embedded=True)])
    metadata = {"original_export": {"path": "graph_candidate.json", "sha256": "a" * 64},
                "original_run_state": {"path": "state.json", "sha256": "b" * 64},
                "native_artifacts": [{"role": "reviewer", "path": "artifact.json",
                                      "sha256": "c" * 64}],
                "proof_bundle_format": "transformation_graph_provenance/1.0",
                "review_backend": {"kind": "application_subagent",
                                   "native_cli_transport_claimed": False}}
    graph.update(metadata)
    result = query_graph(graph, next(iter(cases[0])))
    assert result["recorded_export_provenance"] == metadata
    assert result["parent"]["review_status"] == "needs_revision"
    assert result["direct_reviewed_peers"][0]["provenance"] == (
        "embedded_digest_bound; source entailment not established by syntax")


def test_cli_reads_graph_without_mutation_and_unknown_id_does_not_return_results(
    cases, tmp_path, monkeypatch, capsys,
):
    graph = comparison_graph(cases[0], [comparison(cases)])
    path = tmp_path / "graph_candidate.json"
    path.write_text(json.dumps(graph))
    before = path.read_bytes()
    ident = next(iter(cases[0]))
    monkeypatch.setattr(sys, "argv", ["query", str(path), "--occurrence", ident])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["occurrence"]["id"] == ident and result["direct_reviewed_peers"]
    assert path.read_bytes() == before
    monkeypatch.setattr(sys, "argv", ["query", str(path), "--search", "copy", "--limit", "1"])
    main()
    index = json.loads(capsys.readouterr().out)
    assert index["view"] == "discovery_index" and index["total"] == 2 and index["truncated"]
    assert len(index["hits"]) == 1 and "occurrence" not in index["hits"][0]
    monkeypatch.setattr(sys, "argv", ["query", str(path), "--occurrence", "unknown"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2 and not capsys.readouterr().out


def test_embedded_proof_and_manifest_revalidate_quotes_and_parent_operation_contexts(
    cases, tmp_path,
):
    graph = comparison_graph(cases[0], [comparison(cases, embedded=True)])
    manifest = {ident: {key: str(tmp_path / f"{occurrence.method_id}.{key}.json")
                        for key in ("occurrence", "packet", "workflow", "review")}
                for ident, occurrence in cases[0].items()}
    ident = next(iter(cases[0]))
    result = query_graph(graph, ident, manifest=manifest)
    assert result["parent_operation_contexts"] == []
    assert result["parent_context_included"] is False
    assert result["occurrence"] == cases[0][ident].model_dump(mode="json")
    expanded = query_graph(graph, ident, manifest=manifest, parent_context=True)
    assert expanded["parent_context_included"] is True
    assert expanded["parent_operation_contexts"] == [
        query_workflow(cases[2][0], op.id) for op in cases[0][ident].operations]
    assert result["source_read_hint"]["packet_path"] == manifest[ident]["packet"]
    assert "with_source_integrity" in result["direct_reviewed_peers"][0]["provenance"]
    record = deepcopy(graph["comparisons"][0])
    record["review"]["checks"][0]["evidence"][0]["quote"] = "Invented source transformation."
    review = MembershipReview.model_validate(record["review"])
    record["review_sha256"] = digest(review)
    forged_graph = comparison_graph(cases[0], [record])
    # A self-consistent proof digest does not establish that a quotation exists in its source.
    validate_graph(forged_graph)
    with pytest.raises(ValueError, match="source/provenance"):
        validate_graph(forged_graph, manifest)
    with pytest.raises(ValueError, match="source/provenance"):
        query_graph(forged_graph, query="copy", manifest=manifest, limit=1)


def test_parent_context_is_only_explicit_full_retrieval_with_source_manifest(cases):
    graph = comparison_graph(cases[0], [])
    ident = next(iter(cases[0]))
    with pytest.raises(ValueError, match="Parent context"):
        query_graph(graph, ident, parent_context=True)
    with pytest.raises(ValueError, match="Parent context"):
        query_graph(graph, query="copy", manifest={}, parent_context=True)
