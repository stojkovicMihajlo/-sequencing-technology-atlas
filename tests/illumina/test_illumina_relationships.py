"""Actual pilot replay plus controls against turning similarity into history."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from genomics_discovery.illumina.graph import operation_group
from genomics_discovery.illumina.relationships import (
    RELATIONS, load_reviewed_pair, relationship_neighbors, relationship_packet,
)

ROOT = Path(__file__).resolve().parents[2]
PAIR = ROOT / "provenance/experiments/method_pair_20261102/graph.json"
PAIR_HASH = "8c042d91b8c2e304003d54e962c39ff4f6c643d677cb24b157343a17755007de"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")}


def pair():
    return load_reviewed_pair(PAIR, PAIR_HASH)


def test_actual_history_query_only_returns_scoped_primer_adoption():
    result = relationship_neighbors(pair(), "chipmentation")
    assert [(r["claim_id"], r["relation"], r["source"], r["target"])
            for r in result["relationships"]] == [("c3", "component_adoption", "atac-seq", "chipmentation")]


def test_actual_other_layers_remain_separate():
    g = pair()
    assert {r["claim_id"] for r in relationship_neighbors(g, "atac-seq", layer="correspondence")["relationships"]} == {"c1", "c2"}
    assert [r["claim_id"] for r in relationship_neighbors(g, "atac-seq", layer="comparison")["relationships"]] == ["c4"]
    assert not relationship_neighbors(g, "atac-seq", layer="precedent")["relationships"]
    assert len(relationship_neighbors(g, "atac-seq", layer="all")["relationships"]) == 4


def test_packet_preserves_claim_review_boundaries_and_caveats_without_all_source_metadata():
    g = pair(); original = copy.deepcopy(g)
    p = relationship_packet(g, "c3")
    item = g["historical_links"][0]
    assert p["claim"] == item["claim"] and p["review"] == item["review"]
    assert len(p["claim"]["unresolved"]) == 3
    for side in ("left", "right"):
        full = operation_group(g["source_graph"], g["bindings"][side]["record_id"], p["claim"][side + "_operation_ids"])
        for key in ("nodes", "flows", "boundary_flows", "input_state_ids", "output_state_ids", "corrections", "unknowns"):
            assert p["groups"][side][key] == full[key]
        assert p["groups"][side]["full_source_registry_artifact"] == full["full_source_registry_artifact"]
        assert set(p["groups"][side]["provenance"]) < set(full["provenance"])
        assert all("original" not in s for s in p["groups"][side]["provenance"].values())
    assert "paper:24097267" in p["groups"]["left"]["provenance"]
    assert "Dates/priority" in p["boundary"]
    assert g == original


@pytest.mark.parametrize("mutation", ["promote_similarity", "flip_direction", "missing_evidence",
                                      "wrong_source", "invent_anchor", "reject_review", "block_issue",
                                      "omit_issue", "duplicate", "unknown_kind"])
def test_corrupt_relationship_is_not_returned(mutation):
    g = pair(); item = g["historical_links"][0]
    if mutation == "promote_similarity": item["claim"]["relation"] = "shared_mechanism"
    elif mutation == "flip_direction": item["claim"]["direction"] = "undirected"
    elif mutation == "missing_evidence": item["claim"]["left_evidence"] = []
    elif mutation == "wrong_source": item["claim"]["left_evidence"] = item["claim"]["right_evidence"]
    elif mutation == "invent_anchor": item["claim"]["left_operation_ids"] = ["invented"]
    elif mutation == "reject_review": item["review"]["decision"] = "reject"
    elif mutation == "block_issue": item["review"]["unresolved_assessments"][0]["blocks_relation"] = True
    elif mutation == "omit_issue": item["review"]["unresolved_assessments"].pop()
    elif mutation == "duplicate": g["historical_links"].append(copy.deepcopy(item))
    else: item["claim"]["relation"] = "improves_everything"
    with pytest.raises(ValueError):
        relationship_neighbors(g, "chipmentation")


def test_stale_artifact_hash_is_rejected():
    with pytest.raises(ValueError, match="recorded hash"):
        load_reviewed_pair(PAIR, "0" * 64)


def test_exact_ids_and_explicit_layers_required():
    with pytest.raises(ValueError): relationship_neighbors(pair(), "New ATAC-like method")
    with pytest.raises(ValueError): relationship_neighbors(pair(), "atac-seq", layer="similar_history")
    with pytest.raises(KeyError): relationship_packet(pair(), "unreviewed_proposal")


def test_cli_uses_recorded_graph_and_layer():
    run = subprocess.run([sys.executable, "-m", "genomics_discovery.illumina.relationships",
                          "--graph", str(PAIR), "--sha256", PAIR_HASH,
                          "--record-id", "chipmentation", "--layer", "history"],
                         capture_output=True, text=True, check=True, env=ENV)
    assert json.loads(run.stdout)["relationships"][0]["claim_id"] == "c3"


@pytest.mark.parametrize("script", ["run.py", "pair_run.py"])
def test_both_native_source_tools_return_empty_passage_on_query_miss(tmp_path, script):
    sources = {"s": {"text": "Irrelevant introduction", "content_level": "fulltext"}}
    (tmp_path / "sources.json").write_text(json.dumps(sources))
    args = (["read", "--sources", str(tmp_path / "sources.json")] if script == "run.py" else
            ["read", "--inputs", str(tmp_path)])
    run = subprocess.run([sys.executable, str(ROOT / "experiments/citation_lineage_pilot" / script),
                          *args, "--id", "s", "--query", "missing mechanism"],
                         capture_output=True, text=True, check=True, env=ENV)
    result = json.loads(run.stdout)
    assert result["text"] == "" and result["found"] is False and result["query_found"] is False


def test_policy_has_only_two_historical_types():
    assert {k for k, v in RELATIONS.items() if v["layer"] == "history"} == {"method_development", "component_adoption"}
