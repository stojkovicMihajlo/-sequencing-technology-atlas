"""Keep source identity and exact scientific caveats visible in graph retrieval."""
import copy
from pathlib import Path

import pytest
import tests.illumina.test_illumina_batch_controls as fixtures
from tests.illumina.test_illumina_graph import run_batch

from genomics_discovery.illumina import extraction as batch
from genomics_discovery.illumina.graph import build_graph, get_record

acquired = fixtures.acquired


def test_aggregate_retains_poster_scope_without_canonicalizing_or_dumping_overlay(acquired, tmp_path, monkeypatch):
    root, card, _, _ = acquired
    scoped = {**copy.deepcopy(card), "scope_kind": "source_depiction_row_not_canonical_technology",
        "source_row_id": "poster:p1:c1:r001", "source_method_labels": ["Example assay"],
        "verified_method_names": ["Example assay"], "canonical_technology_id": None,
        "source_category": "Illumina poster > rna_poster", "source_category_path": ["Illumina poster", "rna_poster"],
        "poster_scope": {"grouped_source_row": False, "literal_label_fragments": ["Example assay"],
            "line_wrap_reconstruction": None, "context_scope_warning": "Other visible rows are context only.",
            "source_overlay_row": {"large_legacy_paper_payload": "must not be duplicated"}}}
    fixtures.write(root / "inventory.json", {"cards": [scoped]})
    output, inventory = run_batch(acquired, tmp_path, monkeypatch)
    graph = build_graph(output, inventory)
    saved = graph["cards"]["example"]
    assert saved["source_scope"]["source_row_id"] == scoped["source_row_id"]
    assert saved["source_scope"]["canonical_technology_id"] is None
    assert saved["source_scope"]["verified_method_names"] == ["Example assay"]
    assert saved["source_scope"]["poster_context"]["context_scope_warning"] == scoped["poster_scope"]["context_scope_warning"]
    assert "source_overlay_row" not in saved["source_scope"]["poster_context"]
    assert saved["source_provenance"]["inventory"]["sha256"] == batch.sha(inventory)
    assert saved["source_provenance"]["source_id"] == "card:example"
    operation = get_record(graph, "operation", saved["operation_ids"][0])
    assert operation["source_scope"] == saved["source_scope"]
    assert operation["full_source_registry_artifact"]["sha256"] == saved["artifact_pins"]["source_registry.json"]
    assert Path(operation["full_source_registry_artifact"]["path"]).name == "source_registry.json"


def context_graph():
    card = {"record_id": "example", "source_scope": {"scope_kind": "source_card_kind_not_recorded"},
            "source_registry": {"diagram:source": {"kind": "image"}},
            "unknowns": [{"id": "u_op", "question": "Which terminal group?", "blocking": False, "affected_ids": ["operation:op1"]},
                {"id": "u_flow", "question": "Transfer route unclear", "blocking": False, "affected_ids": ["flow:f1"]},
                {"id": "u_state", "question": "Carrier not recorded", "blocking": False, "affected_ids": ["state:s1"]},
                {"id": "u_other", "question": "Unrelated", "blocking": False, "affected_ids": ["operation:op10"]},
                {"id": "u_global", "question": "General record limitation", "blocking": False, "affected_ids": []}]}
    nodes = [{"id": "op", "node_type": "operation", "record_id": "example", "admission": "supported", "original": {"id": "op1", "label": "Capture"}},
        {"id": "state", "node_type": "state", "record_id": "example", "admission": "supported", "original": {"id": "s1", "label": "RNA"}}]
    for ident, target, record in [("c_op", "operation:op1", "example"), ("c_flow", "flow:f1", "example"),
                                  ("c_other", "operation:op10", "example"), ("c_other_record", "operation:op1", "different")]:
        nodes.append({"id": ident, "node_type": "correction", "record_id": record, "admission": "supported",
            "original": {"id": ident, "target_id": target, "diagram_observation": "Literal diagram observation",
                         "primary_statement": "Primary-source statement", "disposition": "use_primary",
                         "evidence": [{"source_id": "paper:1", "quote": "Exact source excerpt"}]}})
    edges = [{"id": "flow", "source": "state", "target": "op", "kind": "material_input", "record_id": "example", "admission": "supported",
              "original": {"id": "f1", "evidence": []}}]
    return {"cards": {"example": card}, "nodes": nodes, "edges": edges}


def test_operation_lookup_exposes_exact_corrections_and_unknowns_without_new_edges():
    graph = context_graph()
    original = copy.deepcopy(graph)
    result = get_record(graph, "operation", "op")
    assert {c["id"] for c in result["affected_corrections"]} == {"c_op", "c_flow"}
    assert result["affected_corrections"][0]["diagram_observation"] == "Literal diagram observation"
    assert result["affected_corrections"][0]["primary_statement"] == "Primary-source statement"
    assert {u["id"] for u in result["affected_unknowns"]} == {"u_op", "u_flow"}
    assert [u["id"] for u in result["record_unknowns"]] == ["u_global"]
    assert result["full_source_registry_artifact"] is None
    assert graph == original


def test_state_lookup_gets_its_exact_and_incident_flow_caveats_not_operation_caveats():
    result = get_record(context_graph(), "state", "state")
    assert {u["id"] for u in result["affected_unknowns"]} == {"u_state", "u_flow"}
    assert {c["id"] for c in result["affected_corrections"]} == {"c_flow"}
    assert result["context_target_ids"] == ["flow:f1", "state:s1"]


def test_source_scope_retained_even_when_card_not_selected(acquired, tmp_path, monkeypatch):
    root, card, _, _ = acquired
    pending = {**copy.deepcopy(card), "record_id": "pending", "source_id": "card:pending",
               "scope_kind": "source_depiction_row_not_canonical_technology", "source_row_id": "poster:p1:r2"}
    fixtures.write(root / "inventory.json", {"cards": [card, pending]})
    output, inventory = run_batch(acquired, tmp_path, monkeypatch, records=("example",))
    graph = build_graph(output, inventory)
    assert graph["cards"]["pending"]["status"] == "not_selected"
    assert graph["cards"]["pending"]["source_scope"]["source_row_id"] == "poster:p1:r2"
    assert graph["cards"]["pending"]["source_provenance"]["source_id"] == "card:pending"
