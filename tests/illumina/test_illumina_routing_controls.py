"""Incidence is not molecular lineage; source graphs remain byte-unchanged."""
import copy
import json

import pytest

from genomics_discovery.illumina.graph import neighbors, walk, main


def graph(pairs):
    """Typed synthetic graph; no extracted scientific facts/model calls."""
    prefix = "illumina:fixture:"
    operation_names = {op for _, op, _ in pairs}
    states = {state for left, _, right in pairs for state in [left, right]}
    ids = {name: prefix + ("operation:" if name in operation_names else "state:") + name
           for name in states | operation_names}
    nodes = [{"id": ids[name], "record_id": "fixture", "admission": "supported",
              "node_type": "operation" if name in operation_names else "state",
              "original": {"id": name, "label": name}} for name in sorted(ids)]
    edges, seen = [], set()
    for left, operation, right in pairs:
        for source, target, kind in [(left, operation, "material_input"), (operation, right, "data_output")]:
            if (source, target, kind) in seen:
                continue
            seen.add((source, target, kind))
            edges.append({"id": "flow:" + str(len(edges)), "source": ids[source], "target": ids[target],
                "kind": kind, "record_id": "fixture", "admission": "supported",
                "original": {"id": "f" + str(len(edges)), "evidence": [{"source_id": "diagram:fixture", "locator": "source arrow"}]}})
    return {"cards": {"fixture": {"record_id": "fixture", "source_registry": {"diagram:fixture": {"kind": "image"}}}},
            "nodes": nodes, "edges": edges}, ids


@pytest.fixture
def shared_caption():
    # Exact problematic G&T shape, expressed as synthetic source-level labels:
    # two independent libraries, one shared sequencing-caption operation, two
    # read states. The IR does not encode the desired pairwise routing.
    return graph([("rna_library", "sequence", "rna_reads"), ("dna_library", "sequence", "dna_reads")])


def test_default_forward_walk_stops_before_false_crossbranch_pairing(shared_caption):
    atlas, ids = shared_caption
    before = copy.deepcopy(atlas)
    result = walk(atlas, ids["rna_library"], depth=4)
    assert set(result["node_depths"]) == {ids["rna_library"], ids["sequence"]}
    assert ids["dna_reads"] not in result["node_depths"]
    assert result["routing_frontier_ids"] == [ids["sequence"]]
    assert result["routing_limited"] and not result["depth_limited"]
    options = result["untraversed_incidence_options"][ids["sequence"]]
    assert {e["target"] for e in options} == {ids["rna_reads"], ids["dna_reads"]}
    assert all(e["original"]["evidence"] for e in options)
    assert result["routing_ambiguities"][0]["status"] == "input_output_pairing_not_encoded"
    assert "molecular lineage" in result["semantics"]
    assert atlas == before


def test_reverse_walk_stops_before_false_parentage(shared_caption):
    atlas, ids = shared_caption
    result = walk(atlas, ids["rna_reads"], direction="in", depth=4)
    assert set(result["node_depths"]) == {ids["rna_reads"], ids["sequence"]}
    assert ids["dna_library"] not in result["node_depths"]
    assert result["routing_limited"]
    assert {e["source"] for e in result["untraversed_incidence_options"][ids["sequence"]]} == {
        ids["rna_library"], ids["dna_library"]}


def test_explicit_optin_exposes_only_raw_incidence_not_validated_pairing(shared_caption):
    atlas, ids = shared_caption
    result = walk(atlas, ids["rna_library"], depth=4, expand_ambiguous_incidence=True)
    assert ids["dna_reads"] in result["node_depths"]
    assert not result["routing_limited"]
    assert result["routing_ambiguities"]
    assert result["expand_ambiguous_incidence"] is True
    assert "does not establish" in result["semantics"]
    assert result["routing_ambiguities"][0]["status"] == "input_output_pairing_not_encoded"


def test_neighbor_query_retains_arrows_but_exposes_ambiguity(shared_caption):
    atlas, ids = shared_caption
    result = neighbors(atlas, ids["sequence"])
    assert set(result["neighbor_ids"]) == {ids["rna_reads"], ids["dna_reads"]}
    assert len(result["routing_ambiguities"]) == 1
    assert len(result["routing_ambiguities"][0]["incident_flows"]) == 4
    assert all(e["original"]["evidence"] for e in result["flows"])


def test_edge_kind_filter_cannot_hide_many_to_many_input_ambiguity(shared_caption):
    atlas, ids = shared_caption
    result = walk(atlas, ids["sequence"], edge_kinds=["data_output"], depth=2)
    assert result["routing_limited"]
    assert set(result["node_depths"]) == {ids["sequence"]}
    assert len(result["routing_ambiguities"][0]["input_state_ids"]) == 2


def test_legitimate_two_input_one_output_join_is_traversable():
    atlas, ids = graph([("rna", "join", "linked"), ("barcode", "join", "linked")])
    result = walk(atlas, ids["rna"], depth=3)
    assert ids["linked"] in result["node_depths"]
    assert not result["routing_limited"] and not result["routing_ambiguities"]
    reverse = walk(atlas, ids["linked"], direction="in", depth=3)
    assert {ids["rna"], ids["barcode"]}.issubset(reverse["node_depths"])


def test_one_input_two_output_split_is_traversable():
    atlas, ids = graph([("sample", "split", "left"), ("sample", "split", "right")])
    result = walk(atlas, ids["sample"], depth=3)
    assert {ids["left"], ids["right"]}.issubset(result["node_depths"])
    assert not result["routing_ambiguities"]


def test_both_direction_expansion_still_stops_at_unpaired_operation(shared_caption):
    atlas, ids = shared_caption
    result = walk(atlas, ids["rna_library"], direction="both", depth=5)
    assert set(result["node_depths"]) == {ids["rna_library"], ids["sequence"]}
    assert result["routing_limited"]


def test_nonboolean_optin_cannot_claim_semantic_interpretation(shared_caption):
    atlas, ids = shared_caption
    with pytest.raises(ValueError, match="boolean"):
        walk(atlas, ids["rna_library"], expand_ambiguous_incidence="lineage_verified")


def test_cli_optin_is_explicit_incidence_only(shared_caption, tmp_path, capsys):
    atlas, ids = shared_caption
    path = tmp_path / "atlas.json"
    path.write_text(json.dumps(atlas))
    assert main(["walk", str(path), ids["rna_library"], "--depth", "4"]) == 0
    default = json.loads(capsys.readouterr().out)
    assert default["routing_limited"]
    assert main(["walk", str(path), ids["rna_library"], "--depth", "4", "--expand-ambiguous-incidence"]) == 0
    expanded = json.loads(capsys.readouterr().out)
    assert expanded["expand_ambiguous_incidence"]
    assert expanded["routing_ambiguities"]
