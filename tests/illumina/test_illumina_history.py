"""Walk real scoped contributions without inferring transitive inheritance."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from genomics_discovery.illumina.history import (
    build_history_index, history_summary, select_history_claim, technology_history,
)

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "experiments/evolution_review_20261103/history_inputs.json"


def test_real_two_hop_contributions_join_only_the_identical_atac_artifact():
    index = build_history_index(MANIFEST)
    workflows = [n for n in index["nodes"].values() if n["kind"] == "bound_workflow"]
    assert {n["record_id"] for n in workflows} == {"atac-seq", "chipmentation"}
    assert len(workflows) == 2
    result = technology_history(index, "chipmentation")
    assert {e["claim_id"] for e in result["historical_contributions"]} == {"c3", "claim_r11"}
    assert {e["relation"] for e in result["historical_contributions"]} == {"component_adoption"}
    assert all(e["claim"]["unresolved"] for e in result["historical_contributions"])
    assert len(result["nodes"]) == 3
    assert len(result["other_direct_relationships"]) == 3
    assert "transitive" in result["boundary"]
    assert result["frontier"][0]["reason"] == "no_further_history_in_loaded_evidence"
    assert "Adey" in result["frontier"][0]["node"]["name"]


def test_depth_limit_is_explicit_and_does_not_erase_other_relationships():
    result = technology_history(build_history_index(MANIFEST), "chipmentation", max_depth=1)
    assert [e["claim_id"] for e in result["historical_contributions"]] == ["c3"]
    assert result["frontier"][0]["reason"] == "depth_limit"
    assert result["frontier"][0]["node"]["record_id"] == "atac-seq"


def test_atac_comparators_and_precedents_are_not_called_ancestors():
    result = technology_history(build_history_index(MANIFEST), "atac-seq")
    assert [e["claim_id"] for e in result["historical_contributions"]] == ["claim_r11"]
    kinds = [e["relation"] for e in result["other_direct_relationships"]]
    assert kinds.count("mechanistic_precedent") == 4
    assert kinds.count("comparison") == 7


def test_a_second_version_is_never_silently_selected_by_name():
    index = build_history_index(MANIFEST)
    node = copy.deepcopy(next(n for n in index["nodes"].values() if n.get("record_id") == "atac-seq"))
    original_hash = node["extraction_sha256"]
    node.update(id="other_version", extraction_sha256="0" * 64)
    index["nodes"][node["id"]] = node
    with pytest.raises(ValueError, match="multiple versions"):
        technology_history(index, "atac-seq")
    assert len(technology_history(index, "atac-seq", extraction_sha256=original_hash)["historical_contributions"]) == 1
    assert not technology_history(index, "atac-seq", extraction_sha256="0" * 64)["historical_contributions"]


def manifest_copy(tmp_path):
    data = json.loads(MANIFEST.read_text())
    for item in data["overlays"]:
        for field in ("path", "seed_extraction"):
            if field in item:
                item[field] = str((MANIFEST.parent / item[field]).resolve())
    path = tmp_path / "manifest.json"
    return data, path


def test_unchanged_source_binding_is_required(tmp_path):
    data, path = manifest_copy(tmp_path)
    raw = json.loads(Path(data["overlays"][0]["path"]).read_text())
    raw["bindings"]["left"]["extraction_sha256"] = "0" * 64
    changed = tmp_path / "changed.json"; changed.write_text(json.dumps(raw))
    data["overlays"][0].update(path=str(changed), sha256=hashlib.sha256(changed.read_bytes()).hexdigest())
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="binding disagrees"):
        build_history_index(path)


def test_duplicate_overlays_do_not_multiply_evidence(tmp_path):
    data, path = manifest_copy(tmp_path)
    data["overlays"].append(data["overlays"][0])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Duplicate relationship overlay"):
        build_history_index(path)


def test_contradictory_history_cycle_is_rejected(tmp_path):
    data, path = manifest_copy(tmp_path)
    raw = json.loads(Path(data["overlays"][0]["path"]).read_text())
    raw["historical_links"][0]["claim"]["direction"] = "right_to_left"
    changed = tmp_path / "reverse_fixture.json"; changed.write_text(json.dumps(raw))
    data["overlays"].append({"path": str(changed), "sha256": hashlib.sha256(changed.read_bytes()).hexdigest()})
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="cycle"):
        build_history_index(path)


def test_unloaded_method_is_not_reported_as_having_no_predecessor():
    with pytest.raises(ValueError, match="unknown record"):
        technology_history(build_history_index(MANIFEST), "drop-seq")


def current_citation_fixture(tmp_path):
    """Synthetic /3 export declarations, not a reclassification of real papers."""
    extraction = tmp_path / "extraction.json"
    extraction.write_text(json.dumps({"record_id": "new_assay", "operations": [{"id": "o1"}]}))
    pin = hashlib.sha256(extraction.read_bytes()).hexdigest()
    evidence = [{"source_id": "seed", "quote": "Synthetic later implementation uses this contribution."},
                {"source_id": "R1", "quote": "Synthetic earlier demonstration of the contribution."}]
    claim = {"id": "c1", "reference_id": "R1", "contributor": "Earlier contribution",
             "contributor_kind": "experimental_component", "relation": "component_adoption",
             "seed_operation_ids": ["o1"], "earlier_demonstration": "Earlier demonstrated procedure.",
             "seed_design_change": "Retained the component in a changed substrate context.",
             "limitation_addressed": "Reason reported for the assay change.",
             "reported_gain": "No isolated causal gain established.",
             "tradeoffs_and_transfer_limits": "Transfer to other substrates remains untested.",
             "seed_evidence": evidence[:1], "predecessor_evidence": evidence[1:],
             "catalogue_candidates": ["new_assay"],
             "unresolved": ["Earlier workflow has not been extracted."],
             "citation_context": "Synthetic source paragraph citing R1.", "claim_scope": "seed_workflow"}
    review = {"claim_id": "c1", "decision": "accept", "rationale": "Synthetic review accepts only scoped adoption.",
              "evidence": evidence, "unresolved_assessments": [
                  {"issue_index": 0, "blocks_relation": False, "rationale": "Does not block this scoped claim."}]}
    graph = {"format_version": "citation_lineage_pilot/3", "analysis_sha256": "a" * 64,
             "nodes": [{"id": "seed", "kind": "assay", "name": "New assay", "bindings": {
                 "record_id": "new_assay", "parent_artifact_pins": {"extraction.json": pin}}},
                 {"id": "contributor:c1", "kind": "experimental_component", "name": "Earlier contribution",
                  "reference_id": "R1", "catalogue_candidates": ["new_assay"],
                  "identity_status": "scoped_to_cited_contribution; catalogue_matches_are_candidates"}],
             "links": [{"id": "c1", "source": "contributor:c1", "target": "seed",
                        "kind": "component_adoption", "historical_development_edge": False,
                        "relationship_layer": "history", "status": "model_reviewed_pilot_overlay",
                        "claim": claim, "review": review}],
             "withheld": [{"claim": {"id": "withdrawn"}, "reason": "blocking_unresolved_issue"}]}
    overlay = tmp_path / "graph.json"
    manifest = tmp_path / "manifest.json"

    def save():
        overlay.write_text(json.dumps(graph))
        manifest.write_text(json.dumps({"format_version": "illumina_history_inputs/1", "overlays": [{
            "path": str(overlay), "sha256": hashlib.sha256(overlay.read_bytes()).hexdigest(),
            "seed_extraction": str(extraction)}]}))
        return manifest

    return graph, save


def test_current_citation_keeps_scope_qualifications_and_external_identity(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    result = technology_history(build_history_index(save()), "new_assay")
    assert len(result["historical_contributions"]) == 1
    assert result["historical_contributions"][0]["claim"] == graph["links"][0]["claim"]
    assert len(result["nodes"]) == 2  # Catalogue candidate is not an identity merge.
    assert result["frontier"][0]["node"]["id"].startswith("external:")
    assert result["frontier"][0]["reason"] == "no_further_history_in_loaded_evidence"
    assert result["frontier"][0]["continuation_status"] == "unbound_external_contribution"
    assert all(e["claim_id"] != "withdrawn" for e in result["historical_contributions"])
    assert result["direct_withheld_claims"][0]["finding"] == graph["withheld"][0]
    assert result["direct_withheld_claims"][0]["status"] == "withheld_not_a_graph_edge"


def test_legacy_general_tool_reuse_stays_queryable_but_not_in_experimental_lineage(tmp_path):
    graph,save=current_citation_fixture(tmp_path)
    graph['links'][0]['claim']['contributor_kind']='general_method'
    graph['nodes'][1]['kind']='general_method'
    path=save();original=(tmp_path/'graph.json').read_bytes()
    index=build_history_index(path)
    result=technology_history(index,'new_assay')
    assert not result['historical_contributions']
    assert len(result['other_direct_relationships'])==1
    edge=result['other_direct_relationships'][0]
    assert edge['relationship_layer']=='supporting_context'
    assert edge['historical_development_edge'] is False
    assert edge['relation']=='component_adoption'
    assert edge['claim']==graph['links'][0]['claim']
    assert (tmp_path/'graph.json').read_bytes()==original


def test_scoped_publication_flags_cannot_promote_general_tool_to_assay_history(tmp_path):
    from genomics_discovery.illumina.history import _check_current_citation
    graph,_=current_citation_fixture(tmp_path);item=graph['links'][0]
    item['claim']['relation']='documented_reuse';item['kind']='documented_reuse'
    item['historical_development_edge']=True
    _check_current_citation(item,scoped_history=True)
    item['claim']['contributor_kind']='general_method'
    with pytest.raises(ValueError,match='layer'):
        _check_current_citation(item,scoped_history=True)
    item.update(relationship_layer='supporting_context',historical_development_edge=False)
    _check_current_citation(item,scoped_history=True)


@pytest.mark.parametrize("mutation", ["context", "scope", "scope_anchor", "review_side", "layer",
                                      "development_flag", "anchor", "reject", "blocking", "source_side"])
def test_current_citation_invalid_declarations_are_not_queried(tmp_path, mutation):
    graph, save = current_citation_fixture(tmp_path)
    item = graph["links"][0]
    claim, review = item["claim"], item["review"]
    if mutation == "context": claim["citation_context"] = " "
    elif mutation == "scope": claim.update(claim_scope="supporting_experiment", seed_operation_ids=[])
    elif mutation == "scope_anchor": claim["seed_operation_ids"] = []
    elif mutation == "review_side": review["evidence"] = review["evidence"][:1]
    elif mutation == "layer": item["relationship_layer"] = "precedent"
    elif mutation == "development_flag": item["historical_development_edge"] = True
    elif mutation == "anchor": claim["seed_operation_ids"] = ["invented"]
    elif mutation == "reject": review["decision"] = "reject"
    elif mutation == "blocking": review["unresolved_assessments"][0]["blocks_relation"] = True
    elif mutation == "source_side": claim["predecessor_evidence"][0]["source_id"] = "another_reference"
    with pytest.raises(ValueError):
        build_history_index(save())


def test_current_precedent_remains_nonhistorical(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    item = graph["links"][0]
    item.update(kind="mechanistic_precedent", relationship_layer="precedent")
    item["claim"]["relation"] = "mechanistic_precedent"
    result = technology_history(build_history_index(save()), "new_assay")
    assert not result["historical_contributions"]
    assert len(result["other_direct_relationships"]) == 1


def test_current_citation_requires_exact_seed_bytes(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    path = save()
    (tmp_path / "extraction.json").write_text("{}")
    with pytest.raises(ValueError, match="History input changed"):
        build_history_index(path)


def test_summary_indexes_repeated_evidence_and_roundtrips_without_scientific_loss():
    result = technology_history(build_history_index(MANIFEST), "chipmentation")
    before = copy.deepcopy(result)
    summary = history_summary(result)
    assert result == before
    assert summary["evidence"]

    def expand(value):
        if isinstance(value, dict):
            if set(value) == {"evidence_id"}:
                return summary["evidence"][value["evidence_id"]]["original"]
            return {k: expand(v) for k, v in value.items()}
        if isinstance(value, list): return [expand(v) for v in value]
        return value

    for section in ("historical_contributions", "other_direct_relationships", "direct_withheld_claims"):
        for original, compact in zip(result[section], summary[section], strict=True):
            restored = expand(compact)
            restored["artifact"] = summary["artifacts"][restored.pop("artifact_id")]
            assert restored == original
    for field in ("nodes", "other_relationship_nodes", "technology", "frontier", "boundary", "max_depth", "overlay_sha256"):
        assert summary[field] == result[field]
    assert len(json.dumps(summary)) < len(json.dumps(result))


def test_summary_evidence_ids_keep_overlays_separate(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    result = technology_history(build_history_index(save()), "new_assay")
    other = copy.deepcopy(result["historical_contributions"][0])
    other["artifact"]["sha256"] = "f" * 64
    result["historical_contributions"].append(other)
    summary = history_summary(result)
    assert len(summary["evidence"]) == 4
    assert len(summary["artifacts"]) == 2


def test_withheld_research_leads_are_seed_scoped_not_traversable(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    index = build_history_index(save())
    other = copy.deepcopy(index["withheld"][0])
    other["target"] = "another_workflow"
    index["withheld"].append(other)
    result = technology_history(index, "new_assay")
    assert len(result["direct_withheld_claims"]) == 1
    assert len(result["historical_contributions"]) == 1
    assert len(result["nodes"]) == 2


def test_cli_current_summary_retains_details_without_rewriting(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    path = save()
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    run = subprocess.run([sys.executable, "-m", "genomics_discovery.illumina.history",
                          "--manifest", str(path), "--record-id", "new_assay", "--view", "summary"],
                         capture_output=True, text=True, check=True, env=env)
    result = json.loads(run.stdout)
    assert result["format_version"] == "illumina_history_context/1"
    claim = result["historical_contributions"][0]["claim"]
    assert claim["seed_design_change"] == graph["links"][0]["claim"]["seed_design_change"]
    handle = claim["seed_evidence"][0]["evidence_id"]
    assert result["evidence"][handle]["original"] == graph["links"][0]["claim"]["seed_evidence"][0]
    assert result["direct_withheld_claims"][0]["finding"] == graph["withheld"][0]


def test_exact_claim_selection_keeps_qualifiers_and_rejects_ambiguous_ids(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    result = technology_history(build_history_index(save()), "new_assay")
    duplicate = copy.deepcopy(result["historical_contributions"][0])
    duplicate["artifact"]["sha256"] = "f" * 64
    result["historical_contributions"].append(duplicate)
    with pytest.raises(ValueError, match="Ambiguous"):
        select_history_claim(result, "c1")
    selected = select_history_claim(result, "c1", overlay_sha256="f" * 64)
    assert selected["historical_contributions"] == [duplicate]
    assert selected["historical_contributions"][0]["claim"]["unresolved"] == graph["links"][0]["claim"]["unresolved"]
    assert not selected["direct_withheld_claims"]
    assert len(result["historical_contributions"]) == 2  # No mutation of the full result.
    with pytest.raises(ValueError, match="No exact claim"):
        select_history_claim(result, "C1")
    with pytest.raises(ValueError, match="No exact claim"):
        select_history_claim(result, "c1", overlay_sha256="0" * 64)


def test_exact_withheld_selection_has_no_asserted_predecessor(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    result = technology_history(build_history_index(save()), "new_assay")
    selected = select_history_claim(result, "withdrawn")
    assert not selected["historical_contributions"] and not selected["other_direct_relationships"]
    assert len(selected["nodes"]) == 1
    assert selected["nodes"][0]["kind"] == "bound_workflow"
    assert selected["direct_withheld_claims"][0]["finding"] == graph["withheld"][0]


def test_exact_nonhistory_selection_includes_both_endpoints(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    item = graph["links"][0]
    item.update(kind="mechanistic_precedent", relationship_layer="precedent")
    item["claim"]["relation"] = "mechanistic_precedent"
    result = technology_history(build_history_index(save()), "new_assay")
    selected = select_history_claim(result, "c1")
    assert not selected["historical_contributions"]
    assert len(selected["other_direct_relationships"]) == 1
    assert len(selected["nodes"]) == 2
    assert selected["other_direct_relationships"][0]["claim"] == item["claim"]


def test_cli_exact_claim_view_excludes_other_claims():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    run = subprocess.run([sys.executable, "-m", "genomics_discovery.illumina.history",
                          "--manifest", str(MANIFEST), "--record-id", "chipmentation",
                          "--view", "summary", "--claim-id", "c3"],
                         capture_output=True, text=True, check=True, env=env)
    result = json.loads(run.stdout)
    assert [x["claim_id"] for x in result["historical_contributions"]] == ["c3"]
    assert not result["other_direct_relationships"]
    assert result["selection"]["scope"].startswith("One exact")


def attach_source_registry(manifest_path, registry):
    path = manifest_path.parent / "sources.json"
    path.write_text(json.dumps(registry))
    manifest = json.loads(manifest_path.read_text())
    manifest["overlays"][0]["source_registry"] = {
        "path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest_path.write_text(json.dumps(manifest))
    return path


def test_pinned_source_registry_returns_metadata_without_paper_body(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    path = save()
    registry = {
        "seed": {"title": "Later assay paper", "content_level": "fulltext", "pmid": "1234",
                 "url": "https://example.org/later", "text": "PRIVATE_PAPER_BODY_SENTINEL",
                 "provenance": {"original": {"path": "/original/later.xml", "sha256": "a" * 64,
                                              "text": "PRIVATE_NESTED_BODY_SENTINEL"}}},
        "R1": {"title": "Earlier contribution paper", "content_level": "abstract", "pmid": "5678",
               "url": "https://example.org/earlier", "text": "PRIVATE_ABSTRACT_SENTINEL",
               "metadata_receipt": {"path": "retrieval/pubmed.xml", "sha256": "b" * 64}},
        "uncited": {"title": "Unused paper", "text": "UNUSED_PAPER_SENTINEL"},
    }
    source_path = attach_source_registry(path, registry)
    result = technology_history(build_history_index(path), "new_assay")
    metadata = result["historical_contributions"][0]["artifact"]["source_registry"]
    assert metadata["status"] == "pinned_metadata"
    assert metadata["path"] == str(source_path)
    assert metadata["sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert set(metadata["sources"]) == {"seed", "R1"}
    assert metadata["sources"]["R1"]["content_level"] == "abstract"
    assert metadata["sources"]["seed"]["document_artifacts"]["provenance.original"] == {
        "path": "/original/later.xml", "sha256": "a" * 64}
    assert "SENTINEL" not in json.dumps(result)
    compact = history_summary(select_history_claim(result, "c1"))
    assert next(iter(compact["artifacts"].values()))["source_registry"] == metadata


def test_source_registry_pointer_is_explicit_when_not_supplied(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    result = technology_history(build_history_index(save()), "new_assay")
    metadata = result["historical_contributions"][0]["artifact"]["source_registry"]
    assert metadata["status"] == "not_supplied"
    assert metadata["path"] is None and metadata["sha256"] is None
    assert metadata["sources"] == {}


def test_source_registry_hash_change_is_rejected(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    path = save()
    source_path = attach_source_registry(path, {"seed": {}, "R1": {}})
    source_path.write_text("{}")
    with pytest.raises(ValueError, match="History input changed"):
        build_history_index(path)


def test_unknown_evidence_source_rejects_supplied_registry(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    path = save()
    attach_source_registry(path, {"seed": {}})
    with pytest.raises(ValueError, match="Evidence source IDs absent.*R1"):
        build_history_index(path)


def test_withheld_evidence_also_requires_registered_source(tmp_path):
    graph, save = current_citation_fixture(tmp_path)
    graph["withheld"][0]["claim"]["seed_evidence"] = [
        {"source_id": "unknown_withheld_source", "quote": "Synthetic missing source reference."}]
    path = save()
    attach_source_registry(path, {"seed": {}, "R1": {}})
    with pytest.raises(ValueError, match="unknown_withheld_source"):
        build_history_index(path)
