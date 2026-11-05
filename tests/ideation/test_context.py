"""Retrieval preserves exact provenance without treating proposals as evidence."""
import copy
import hashlib
import json

import pytest

from genomics_discovery.ideation.context import AtlasContext, main


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def context(tmp_path):
    record = "method"
    sid, oid, out = ("illumina:method:" + suffix for suffix in ("state:in", "operation:copy", "state:out"))
    evidence = {"source_id": "paper", "locator": "Methods", "basis": "text",
                "support": "reported", "quote": "The primer carries a unique identifier."}
    states = [
        {"id": sid, "node_type": "state", "record_id": record, "admission": "supported",
         "original": {"id": "in", "label": "Primer-associated RNA", "kind": "material", "features": [],
             "properties": [{"id": "p1", "subject": "self", "property": "terminal_group",
                             "value": "azide", "end": "three_prime", "evidence": [evidence]}], "evidence": [evidence]}},
        {"id": out, "node_type": "state", "record_id": record, "admission": "supported",
         "original": {"id": "out", "label": "Indexed cDNA", "kind": "material", "features": [],
                      "evidence": [evidence]}},
    ]
    operation = {"id": oid, "node_type": "operation", "record_id": record, "admission": "supported",
        "original": {"id": "copy", "label": "Copy RNA", "description": "Reverse transcription.",
            "inputs": ["in"], "outputs": ["out"], "input_roles": [{"state_id": "in", "roles": ["template"]}],
            "effects": [{"aspect": "attribution", "statement": "Retains origin barcode"}], "evidence": [evidence]}}
    edges = [{"id": "f1", "record_id": record, "source": sid, "target": oid, "kind": "material_input",
              "admission": "supported", "original": {"id": "input", "evidence": [evidence]}},
             {"id": "f2", "record_id": record, "source": oid, "target": out, "kind": "material_output",
              "admission": "supported", "original": {"id": "output", "evidence": [evidence]}}]
    card = {"record_id": record, "title": "Example", "status": "supported", "admission": "supported",
        "operation_ids": [oid], "publication_ids": [], "candidate_sha256": "c" * 64,
        "artifact_pins": {"extraction.json": "e" * 64}, "review_sha256": "r" * 64,
        "source_registry": {"paper": {"title": "Primary source", "text": "NEVER INSERT FULL PAPER"}},
        "unknowns": [{"affected_ids": ["operation:copy"], "question": "Conversion efficiency?"}],
        "semantic_checks": {"construction": {"decision": "supported"}}}
    diagnostic = copy.deepcopy(operation)
    diagnostic.update(id="held:operation:copy", record_id="held", admission="diagnostic")
    graph = {"cards": {record: card, "held": {**copy.deepcopy(card), "record_id": "held",
              "status": "research_needed", "admission": "diagnostic"}},
             "nodes": [*states, operation, diagnostic], "edges": edges}
    path = tmp_path / "atlas.json"
    path.write_text(json.dumps(graph))
    return AtlasContext(path, expected_sha256=sha(path.read_bytes()),
        sources_path=tmp_path / "sources.json", audit_log=tmp_path / "tool_calls.jsonl")


def test_supported_only_and_exact_reference_bindings(context):
    listing = context.list_methods()["data"]
    assert listing["total"] == 1
    assert listing["methods"][0]["extraction_sha256"] == "e" * 64
    oid = "illumina:method:operation:copy"
    assert context.validate_atlas_ref("method", oid, "e" * 64)["original"]["label"] == "Copy RNA"
    for record, node, digest in [("held", oid, "e" * 64), ("method", oid, "c" * 64),
                                 ("method", "copy", "e" * 64)]:
        with pytest.raises(ValueError):
            context.validate_atlas_ref(record, node, digest)
    with pytest.raises(ValueError, match="quote"):
        context.validate_atlas_ref("method", oid, "e" * 64, quote="Novel chemistry works")


def test_search_indexes_typed_properties_roles_and_effects(context):
    assert context.search("azide")["data"]["matches"][0]["node_type"] == "state"
    assert context.search("template")["data"]["matches"][0]["node_type"] == "operation"
    assert context.search("attribution")["data"]["matches"][0]["node_type"] == "operation"
    assert all(x["record_id"] == "method" for x in context.search("RNA")["data"]["matches"])
    assert "not biochemical" in context.search("RNA")["data"]["boundary"]


def test_graph_views_preserve_unknowns_ids_and_evidence_without_paper_body(context):
    result = context.workflow("method")
    assert result["data"]["card"]["unknowns"][0]["question"] == "Conversion efficiency?"
    assert "NEVER INSERT FULL PAPER" not in json.dumps(result)
    assert len(result["evidence_index"]) == 1
    op = context.node("illumina:method:operation:copy")
    assert op["data"]["affected_unknowns"]
    assert op["data"]["inputs"][0]["id"] == "illumina:method:state:in"
    group = context.group("method", ["illumina:method:operation:copy"])
    assert group["data"]["binding"]["extraction_sha256"] == "e" * 64
    assert group["data"]["unknowns"]
    assert group["data"]["input_state_ids"] == ["illumina:method:state:in"]
    with pytest.raises(ValueError, match="exact Operation"):
        context.group("method", ["copy"])


def test_pinning_rejects_changed_atlas(context):
    context.path.write_text(context.path.read_text() + " ")
    with pytest.raises(ValueError, match="Pinned input changed"):
        AtlasContext(context.path, expected_sha256=context.sha256)


def test_history_absence_and_version_mismatch_do_not_mean_no_ancestor(context):
    assert context.history("method")["data"]["status"] == "not_loaded"
    context.history_index = {"nodes": {"old": {"id": "old", "record_id": "method", "extraction_sha256": "c" * 64}},
                             "edges": [], "withheld": [], "overlay_sha256": [], "boundary": "fixture"}
    with pytest.raises(ValueError, match="Expected one exact workflow"):
        context.history("method")


def test_source_import_read_and_reference_validate_exact_bytes(context, tmp_path):
    path = tmp_path / "retrieved.txt"
    path.write_text("The primer carries a unique identifier. Recovery varied by condition.")
    source = context.add_source(path, url="https://example.org/paper", title="Primary paper", content_level="excerpt")["data"]
    assert source["content_level"] == "excerpt"
    assert source["provenance_status"].startswith("agent_imported_text")
    assert context.read_source(source["source_id"], query="Recovery")["data"]["text"]
    assert context.read_source(source["source_id"], query="absent")["data"]["text"] == ""
    assert context.validate_literature_ref(source["source_id"], source["content_sha256"], "Recovery varied by condition.")
    for digest, quote in [("0" * 64, "Recovery"), (source["content_sha256"], "An invented claim")]:
        with pytest.raises(ValueError):
            context.validate_literature_ref(source["source_id"], digest, quote)
    with pytest.raises(ValueError, match="relabel"):
        context.add_source(path, url=source["url"], title=source["title"], content_level="fulltext")
    from pathlib import Path
    Path(source["path"]).write_text("Changed")
    with pytest.raises(ValueError, match="bytes changed"):
        context.read_source(source["source_id"])


def test_source_title_is_not_passage(context, tmp_path):
    path = tmp_path / "title.txt"
    path.write_text("Primary paper")
    with pytest.raises(ValueError, match="not scientific evidence"):
        context.add_source(path, url="https://example.org", title="Primary paper", content_level="excerpt")


def test_oversized_results_are_available_losslessly_and_audited(context):
    context.max_result_chars = 1000
    result = context.workflow("method")
    assert result["status"] == "result_too_large"
    parts, start = [], 0
    while start is not None:
        window = context.read_result(result["result_id"], start=start, limit=200)["data"]
        parts.append(window["text"])
        start = window["next_start"]
    raw = "".join(parts)
    assert sha(raw.encode()) == result["result_id"]
    assert json.loads(raw)["data"]["card"]["unknowns"]
    records = [json.loads(line) for line in context.audit_log.read_text().splitlines()]
    assert records[0]["action"] == "workflow"
    assert all("result_sha256" in row for row in records)
    assert "NEVER INSERT FULL PAPER" not in context.audit_log.read_text()


def test_cli_lists_pinned_supported_methods(context, capsys):
    main(["--atlas", str(context.path), "--atlas-sha256", context.sha256, "list"])
    assert json.loads(capsys.readouterr().out)["data"]["total"] == 1


def test_cli_failed_lookup_is_audited_and_never_returns_unrelated_text(context, capsys):
    with pytest.raises(SystemExit) as error:
        main(["--atlas", str(context.path), "--atlas-sha256", context.sha256,
              "--audit-log", str(context.audit_log), "node", "invented-node"])
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "tool_error"
    assert json.loads(context.audit_log.read_text())["arguments"]["node_id"] == "invented-node"


def test_atlas_source_reopens_exact_registry_with_bounded_text(context, tmp_path):
    registry = tmp_path/'original'/'source_registry.json'
    registry.parent.mkdir()
    registry.write_text(json.dumps({'paper': {'title': 'Original', 'url': 'https://example.org/paper',
        'content_level': 'fulltext', 'text': 'Recorded source passage. Another paragraph.'}}))
    context.cards['method']['attempt_directory'] = str(registry.parent)
    context.cards['method']['artifact_pins']['source_registry.json'] = sha(registry.read_bytes())
    listing = context.atlas_source('method')['data']
    assert listing['sources'][0]['source_id'] == 'paper'
    assert 'text' not in listing['sources'][0]
    result = context.atlas_source('method', source_id='paper', limit=8)['data']
    assert result['text'] == 'Recorded'
    assert result['next_start'] == 8
    assert context.atlas_source('method', source_id='paper', query='absent')['data']['text'] == ''
    registry.write_text(registry.read_text()+' ')
    with pytest.raises(ValueError, match='Pinned input changed'):
        context.atlas_source('method', source_id='paper')


def test_history_overlays_are_loaded_lazily(context, tmp_path, monkeypatch):
    manifest = tmp_path/'history.json'
    manifest.write_text('{}')
    calls = []
    monkeypatch.setattr('genomics_discovery.ideation.context.build_history_index',
        lambda path: calls.append(path) or {'nodes': {}, 'edges': []})
    instance = AtlasContext(context.path, expected_sha256=context.sha256,
        history_manifest=manifest, history_sha256=sha(manifest.read_bytes()))
    instance.list_methods()
    instance.search('azide')
    assert not calls
    with pytest.raises(ValueError, match='Expected one exact workflow'):
        instance.history('method')
    assert calls == [manifest]


def test_host_fetch_adds_custody_without_rewriting_inherited_registration(context, tmp_path, monkeypatch):
    path = tmp_path/'remote.txt'; path.write_text('Actually acquired content.')
    receipt = {'request_url': 'https://example.org/paper', 'text_sha256': sha(path.read_bytes()),
               'origin_status': 'host_fetched'}
    monkeypatch.setattr('genomics_discovery.ideation.evidence.fetch_source', lambda url, directory:
        {'text_path': str(path), 'content_level': 'excerpt', 'receipt': receipt})
    imported = context.add_source(path, url=receipt['request_url'], title='Paper', content_level='excerpt')['data']
    before = json.loads(context.sources_path.read_text())
    fetched = context.fetch_source(receipt['request_url'], title='Paper')['data']
    assert json.loads(context.sources_path.read_text()) == before
    assert fetched['origin_receipt'] == receipt
    assert json.loads((context.sources_path.parent/'origin_receipts.json').read_text())[imported['source_id']] == [receipt]
    fresh = context.fetch_source('https://example.org/another', title='Other')['data']
    assert fresh['provenance_status'].startswith('host_fetched_text')
