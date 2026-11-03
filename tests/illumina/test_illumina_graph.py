"""Aggregate coverage, admission and retrieval using fake archived transports."""

import asyncio
import copy
from pathlib import Path

import pytest
import tests.illumina.test_illumina_batch_controls as fixtures

from genomics_discovery.illumina import extraction as batch
from genomics_discovery.illumina.models import DiagramExtraction
from genomics_discovery.illumina.graph import (
    build_graph,
    get_record,
    main,
    neighbors,
    search_operations,
    walk,
    workflow,
)

acquired = fixtures.acquired


def run_batch(acquired, tmp_path, monkeypatch, *, correction=False, records=()):
    root, _, _, _ = acquired
    worker = fixtures.FakeWorker(request_correction=correction)
    monkeypatch.setenv("SLURM_JOB_ID", "fake-aggregate-check")
    monkeypatch.setattr(batch, "CodexWorker", lambda **kwargs: worker)
    output = tmp_path / "batch"
    asyncio.run(batch.run(root / "sources.json", output, workers=1,
                          records=records, max_revisions=0))
    return output, root / "inventory.json"


def test_supported_graph_reopens_native_outputs_and_exposes_exact_io(acquired, tmp_path, monkeypatch):
    output, inventory = run_batch(acquired, tmp_path, monkeypatch)
    graph = build_graph(output, inventory)
    assert graph["counts"] == {"supported": 1}
    operation_id = graph["cards"]["example"]["operation_ids"][0]
    operation = get_record(graph, "operation", operation_id)
    assert operation_id == "illumina:example:operation:capture"
    assert operation["inputs"][0]["original"]["label"] == "RNA"
    assert operation["outputs"][0]["original"]["label"] == "Captured RNA"
    assert {f["kind"] for f in operation["flows"]} == {"material_input", "material_output"}
    assert operation["provenance"]
    matches = search_operations(graph, input_text="RNA", transformation="capture", output_text="captured")
    assert matches[0]["operation_id"] == operation_id
    assert matches[0]["status"] == "unreviewed_comparison_candidate"
    assert all(e["kind"] not in {"method_development", "shared_mechanism", "equivalent_to"}
               for e in graph["edges"])
    paper_id = graph["cards"]["example"]["publication_ids"][0]
    paper = get_record(graph, "paper", paper_id)
    assert paper["identity_verified"] and paper["provenance"] and paper["links"]


def test_every_inventory_card_survives_missing_source_and_selection(acquired, tmp_path, monkeypatch):
    root, card, _, _ = acquired
    inventory = batch.read(root / "inventory.json")
    missing = {**copy.deepcopy(card), "record_id": "missing", "source_id": "card:missing",
               "source_title": "Missing source", "diagram_asset_ids": [], "references": []}
    unselected = {**copy.deepcopy(missing), "record_id": "unselected"}
    inventory["cards"].extend((missing, unselected))
    fixtures.write(root / "inventory.json", inventory)
    output, path = run_batch(acquired, tmp_path, monkeypatch, records=("example", "missing"))
    graph = build_graph(output, path)
    assert set(graph["cards"]) == {"example", "missing", "unselected"}
    assert graph["cards"]["missing"]["status"] == "source_recovery_needed"
    assert graph["cards"]["unselected"]["status"] == "not_selected"
    assert not graph["cards"]["missing"]["operation_ids"]


@pytest.mark.parametrize("artifact", ["extraction.json", "source_registry.json", "image"])
def test_changed_artifacts_or_delivered_image_do_not_publish(acquired, tmp_path, monkeypatch, artifact):
    output, path = run_batch(acquired, tmp_path, monkeypatch)
    row = batch.read(output / "state.json")["cards"]["example"]
    directory = Path(row["attempt_directory"])
    if artifact == "image":
        delivery = batch.read(directory / "attempts" / "002_reviewer" / "image_delivery.json")
        Path(delivery[0]["attachment"]).write_bytes(fixtures.png(b"\xff\x00\x00\xff"))
    else:
        target = directory / artifact
        target.write_text(target.read_text() + " ")
    graph = build_graph(output, path)
    assert graph["cards"]["example"]["status"] == "integrity_error"
    assert not graph["nodes"] and not graph["edges"]


def test_diagnostics_are_explicit_and_excluded_from_default_retrieval(acquired, tmp_path, monkeypatch):
    output, inventory = run_batch(acquired, tmp_path, monkeypatch, correction=True)
    graph = build_graph(output, inventory)
    assert graph["counts"] == {"needs_revision": 1}
    assert graph["cards"]["example"]["admission"] == "diagnostic"
    assert not search_operations(graph, transformation="capture")
    result = search_operations(graph, transformation="capture", include_diagnostic=True)
    assert result[0]["admission"] == "diagnostic"
    with pytest.raises(ValueError, match="Diagnostic"):
        get_record(graph, "operation", result[0]["operation_id"])
    assert get_record(graph, "card", "example")["status"] == "needs_revision"


def test_same_named_operation_instances_are_not_merged(acquired, tmp_path, monkeypatch):
    root, card, sources, _ = acquired
    second = {**copy.deepcopy(card), "record_id": "second", "source_id": "card:second",
              "source_title": "Second assay"}
    fixtures.write(root / "inventory.json", {"cards": [card, second]})
    original = fixtures.candidate

    def scoped_candidate(registry, **kwargs):
        candidate = original(registry, **kwargs).model_dump(mode="json")
        if "card:second" in registry:
            candidate.update(record_id="second", title="Second assay")
        return DiagramExtraction.model_validate(candidate)

    monkeypatch.setattr(fixtures, "candidate", scoped_candidate)
    output, inventory = run_batch(acquired, tmp_path, monkeypatch)
    graph = build_graph(output, inventory)
    assert graph["counts"] == {"supported": 2}
    operations = [n for n in graph["nodes"] if n["node_type"] == "operation"]
    assert len(operations) == 2 and len({n["id"] for n in operations}) == 2
    assert {n["original"]["label"] for n in operations} == {"Capture"}
    assert len([n for n in graph["nodes"] if n["node_type"] == "publication"]) == 1


def test_secondary_citations_on_one_page_do_not_become_a_publication(acquired, tmp_path, monkeypatch):
    root, _, _, _ = acquired
    inventory = batch.read(root / 'inventory.json')
    inventory['cards'][0]['description_verbatim'] = (
        'RNA is captured. Smith 2015: Separate paper. Jones 2016: Another paper.')
    fixtures.write(root / 'inventory.json', inventory)
    original = fixtures.candidate

    def with_mentions(sources, **kwargs):
        data = original(sources, **kwargs).model_dump(mode='json')
        for ident, quote in [('smith', 'Smith 2015: Separate paper.'),
                             ('jones', 'Jones 2016: Another paper.')]:
            data['paper_matches'].append({'id': ident, 'source_id': 'card:example',
                'methods': [], 'assessment': 'listed_not_read', 'relationship': 'catalogue_citation',
                'evidence': [{'source_id': 'card:example', 'basis': 'text', 'support': 'reported',
                              'locator': 'description', 'quote': quote}]})
        return DiagramExtraction.model_validate(data)

    monkeypatch.setattr(fixtures, 'candidate', with_mentions)
    output, inventory_path = run_batch(acquired, tmp_path, monkeypatch)
    graph = build_graph(output, inventory_path)
    assert graph['cards']['example']['admission'] == 'supported'
    assert len([n for n in graph['nodes'] if n['node_type'] == 'publication']) == 1
    assert len([n for n in graph['nodes'] if n['node_type'] == 'paper_match']) == 3
    assert len(graph['cards']['example']['publication_ids']) == 1


def test_changed_inventory_and_cli_query_boundaries(acquired, tmp_path, monkeypatch, capsys):
    output, inventory = run_batch(acquired, tmp_path, monkeypatch)
    path = tmp_path / "atlas.json"
    assert main(["build", str(output), str(inventory), "--output", str(path)]) == 0
    assert main(["list", str(path), "operation", "--limit", "1"]) == 0
    assert main(["search", str(path), "--input", "RNA", "--transformation", "Capture"]) == 0
    assert "unreviewed_comparison_candidate" in capsys.readouterr().out
    directory = Path(batch.read(output / "state.json")["cards"]["example"]["attempt_directory"])
    assert main(["protocol", str(directory / "graph.json")]) == 0
    assert "OPERATION capture" in capsys.readouterr().out
    with pytest.raises(ValueError):
        search_operations(batch.read(path), transformation="capture", limit=101)
    inventory.write_text(inventory.read_text() + " ")
    with pytest.raises(ValueError, match="Inventory differs"):
        build_graph(output, inventory)


def test_coverage_cannot_drop_selected_cards_or_collapse_duplicate_inventory_ids(acquired, tmp_path, monkeypatch):
    output, inventory = run_batch(acquired, tmp_path, monkeypatch)
    state = batch.read(output / "state.json")
    altered = copy.deepcopy(state)
    altered["cards"].pop("example")
    fixtures.write(output / "state.json", altered)
    with pytest.raises(ValueError, match="omitted a selected card"):
        build_graph(output, inventory)
    source = batch.read(inventory)
    source["cards"].append(copy.deepcopy(source["cards"][0]))
    fixtures.write(inventory, source)
    state["policy"]["inventory_sha256"] = batch.sha(inventory)
    fixtures.write(output / "state.json", state)
    with pytest.raises(ValueError, match="Duplicate inventory"):
        build_graph(output, inventory)


def test_recovered_caption_and_method_references_are_preserved(acquired, tmp_path, monkeypatch):
    root, card, _, _ = acquired
    recovered = {**card, "source_title": None, "recovered_title": "Example assay",
                 "references": [], "recovered_references": [
                     {**card["references"][0], "role": "recovered"}]}
    fixtures.write(root / "inventory.json", {"cards": [recovered]})
    output, inventory = run_batch(acquired, tmp_path, monkeypatch)
    graph = build_graph(output, inventory)
    assert graph["counts"] == {"supported": 1}
    assert graph["cards"]["example"]["title"] == "Example assay"
    assert graph["cards"]["example"]["source_reference_count"] == 1


def test_changed_metadata_original_is_rejected_when_registered_with_hash(acquired, tmp_path, monkeypatch):
    metadata = tmp_path / "pubmed.xml"
    metadata.write_text("<verified-pubmed-record/>")
    original = batch.card_sources

    def registered(*args):
        sources, images = original(*args)
        sources["metadata:1"].update(path=str(metadata), sha256=batch.sha(metadata))
        return sources, images

    monkeypatch.setattr(batch, "card_sources", registered)
    output, inventory = run_batch(acquired, tmp_path, monkeypatch)
    assert build_graph(output, inventory)["counts"] == {"supported": 1}
    metadata.write_text("<changed-record/>")
    graph = build_graph(output, inventory)
    assert graph["counts"] == {"integrity_error": 1}
    assert not graph["nodes"]


@pytest.fixture
def routes():
    """Synthetic route fixture, not extracted science or a model result."""
    prefix = "illumina:routes:"
    specs = [("state", "start"), ("state", "a"), ("state", "b"),
             ("state", "a_done"), ("state", "b_done"), ("state", "reads"),
             ("operation", "split"), ("operation", "left"),
             ("operation", "right"), ("operation", "merge")]
    identities = {name: prefix + kind + ":" + name for kind, name in specs}
    nodes = [{"id": identities[name], "node_type": kind, "record_id": "routes",
              "admission": "supported", "original": {"id": name, "label": name,
                  "kind": "data" if name == "reads" else "material", "evidence": []}}
             for kind, name in specs]
    pairs = [("start", "split", "material_input"), ("split", "a", "material_output"),
             ("split", "b", "material_output"), ("a", "left", "material_input"),
             ("left", "a_done", "material_output"), ("b", "right", "material_input"),
             ("right", "b_done", "material_output"), ("a_done", "merge", "material_input"),
             ("b_done", "merge", "material_input"), ("merge", "reads", "data_output")]
    edges = [{"id": "flow:" + str(i), "source": identities[a], "target": identities[b],
              "kind": kind, "record_id": "routes", "admission": "supported",
              "original": {"evidence": [{"source_id": "diagram:fixture", "locator": "arrow"}]}}
             for i, (a, b, kind) in enumerate(pairs)]
    nodes.extend([{"id": prefix + "workflow", "node_type": "source_workflow", "record_id": "routes",
                   "admission": "supported"}, {"id": "publication:1", "node_type": "publication",
                    "record_ids": ["routes"], "has_supported_link": True}])
    edges.extend([{"source": prefix + "workflow", "target": identities["split"],
                   "kind": "has_operation", "record_id": "routes", "admission": "supported"},
                  {"source": identities["split"], "target": "publication:1",
                   "kind": "references_publication", "record_id": "routes", "admission": "supported"}])
    return {"cards": {"routes": {"record_id": "routes", "status": "supported", "admission": "supported",
        "publication_ids": ["publication:1"], "source_registry": {"diagram:fixture": {"kind": "image"}}}},
        "nodes": nodes, "edges": edges}, identities


def test_state_lookup_exposes_its_producer_consumer_and_exact_flows(routes):
    graph, ids = routes
    state = get_record(graph, "state", ids["a"])
    assert state["producer_operation_ids"] == [ids["split"]]
    assert state["consumer_operation_ids"] == [ids["left"]]
    assert set(state["adjacent_operation_ids"]) == {ids["split"], ids["left"]}
    assert {f["kind"] for f in state["flows"]} == {"material_output", "material_input"}
    assert state["provenance"]
    result = neighbors(graph, ids["a"])
    assert result["neighbor_ids"] == [ids["left"]]
    assert result["flows"][0]["source"] == ids["a"]
    assert result["flows"][0]["original"]["evidence"]


def test_workflow_and_walk_preserve_both_branches_and_the_merge(routes):
    graph, ids = routes
    scoped = workflow(graph, "routes")
    assert len(scoped["flows"]) == 10
    assert len(scoped["edges"]) == 12
    forward = walk(graph, ids["split"], depth=8)
    assert {n["id"] for n in forward["nodes"]} == set(ids.values()) - {ids["start"]}
    incoming_merge = [e for e in forward["flows"] if e["target"] == ids["merge"]]
    assert {e["source"] for e in incoming_merge} == {ids["a_done"], ids["b_done"]}
    assert len([n for n in forward["nodes"] if n["id"] == ids["merge"]]) == 1
    assert all(e["kind"] != "references_publication" for e in forward["flows"])
    assert all(n["node_type"] != "publication" for n in forward["nodes"])
    assert not forward["truncated"] and not forward["depth_limited"]


def test_reverse_walk_keeps_original_arrow_directions(routes):
    graph, ids = routes
    result = neighbors(graph, ids["merge"], direction="in")
    assert set(result["neighbor_ids"]) == {ids["a_done"], ids["b_done"]}
    assert all(e["target"] == ids["merge"] for e in result["flows"])
    reverse = walk(graph, ids["reads"], direction="in", depth=8)
    assert ids["start"] in reverse["node_depths"]
    assert all(e in graph["edges"] for e in reverse["flows"])


def test_route_limits_report_frontiers_instead_of_implying_terminal_material(routes):
    graph, ids = routes
    shallow = walk(graph, ids["split"], depth=1)
    assert set(shallow["frontier_ids"]) == {ids["a"], ids["b"]}
    assert shallow["depth_limited"] and not shallow["truncated"]
    limited = neighbors(graph, ids["split"], limit=1)
    assert limited["truncated"] and len(limited["flows"]) == 1
    limited = walk(graph, ids["split"], depth=8, limit=1)
    assert limited["truncated"] and limited["frontier_ids"]


def test_precedes_is_explicit_and_metadata_edges_cannot_be_walked(routes):
    graph, ids = routes
    edge = {"id": "flow:precedence", "source": ids["left"], "target": ids["right"],
            "kind": "precedes", "record_id": "routes", "admission": "supported", "original": {}}
    graph["edges"].append(edge)
    assert neighbors(graph, ids["left"], edge_kinds=["precedes"])["flows"] == [edge]
    with pytest.raises(ValueError, match="Flow kinds"):
        neighbors(graph, ids["left"], edge_kinds=["has_operation"])
    with pytest.raises(ValueError, match="exact State or Operation"):
        walk(graph, "publication:1")


def test_diagnostic_state_workflow_and_routes_require_explicit_opt_in(routes):
    graph, ids = routes
    graph["cards"]["routes"].update(admission="diagnostic", status="needs_revision")
    for node in graph["nodes"]:
        if node.get("record_id") == "routes": node["admission"] = "diagnostic"
    for edge in graph["edges"]: edge["admission"] = "diagnostic"
    for action in (lambda: get_record(graph, "state", ids["a"]),
                   lambda: workflow(graph, "routes"), lambda: neighbors(graph, ids["a"]),
                   lambda: walk(graph, ids["split"])):
        with pytest.raises(ValueError, match="Diagnostic"):
            action()
    assert get_record(graph, "state", ids["a"], include_diagnostic=True)["admission"] == "diagnostic"
    assert workflow(graph, "routes", include_diagnostic=True)["flows"]
    assert walk(graph, ids["split"], include_diagnostic=True)["flows"]


def test_state_and_route_cli_calls_use_actual_ids(routes, tmp_path, capsys):
    graph, ids = routes
    path = tmp_path / "routes.json"; fixtures.write(path, graph)
    for args in (["get", str(path), "state", ids["a"]], ["workflow", str(path), "routes"],
                 ["neighbors", str(path), ids["a"]], ["walk", str(path), ids["split"], "--depth", "2"]):
        assert main(args) == 0
        assert "illumina:routes:" in capsys.readouterr().out
