"""Usage is observed delivery, not a proxy for good scientific reasoning."""
import hashlib
import json
from pathlib import Path

import pytest

from genomics_discovery.ideation.context import AtlasContext, _compact, _focus_sources, _json, main
from genomics_discovery.illumina.graph import get_record
from genomics_discovery.ideation.usage import build_usage_receipt, usage_from_logs
from tests.ideation.test_context import context, historical_context  # noqa: F401


def receipt(context):
    return usage_from_logs({"author/tool_calls.jsonl": context.audit_log.read_text()})["logs"][0]


def test_pointer_is_not_content_and_complete_pages_are_not_double_counted(context):
    context.max_result_chars = 1000
    result = context.node("illumina:method:operation:copy")
    assert result["status"] == "result_too_large"
    assert receipt(context)["content_delivered"] == []
    context.read_result(result["result_id"], start=0, limit=150)
    context.read_result(result["result_id"], start=0, limit=150)
    assert receipt(context)["pagination"][0]["delivered_characters"] == 150
    assert receipt(context)["content_delivered"] == []
    start = 150
    while start is not None:
        start = context.read_result(result["result_id"], start=start, limit=150)["data"]["next_start"]
    report = receipt(context)
    assert report["pagination"][0]["complete_content_delivered"]
    assert report["content_delivered"][0]["delivery"] == "complete_pagination"
    assert report["content_delivered"][0]["node_ids"] == ["illumina:method:operation:copy"]


def test_navigation_does_not_count_as_historical_claim_evidence(context):
    edge = historical_context(context)
    context.change_context(edge["id"])
    assert receipt(context)["navigation_delivered"]
    assert receipt(context)["historical_claim_ids_delivered"] == []
    full = context.claim(edge["id"])
    assert full["data"]["claim"]["unresolved"] == ["Transfer yield unknown"]
    assert full["evidence_index"]
    assert receipt(context)["historical_claim_ids_delivered"] == [edge["id"]]


def test_failed_and_unavailable_history_are_not_successful_reads(context, capsys):
    context.history("method")
    with pytest.raises(SystemExit):
        main(["--atlas", str(context.path), "--atlas-sha256", context.sha256,
              "--audit-log", str(context.audit_log), "claim", "unregistered-claim"])
    report = receipt(context)
    assert report["historical_claim_ids_delivered"] == []
    assert report["unavailable_or_no_match"][0]["result_state"] == "not_loaded"
    assert report["errors"][0]["action"] == "claim"


def test_source_no_match_is_distinct_from_source_passage(context, tmp_path):
    path = tmp_path / "paper.txt"
    path.write_text("The RNA was copied.")
    source = context.add_source(path, url="https://example.org", title="Paper", content_level="excerpt")["data"]
    context.read_source(source["source_id"], query="absent")
    assert receipt(context)["content_delivered"] == []
    assert receipt(context)["unavailable_or_no_match"][0]["returned_text_characters"] == 0
    context.read_source(source["source_id"], query="RNA")
    assert receipt(context)["content_delivered"][0]["returned_text_characters"] == len(path.read_text())


def test_legacy_complete_does_not_assert_delivered_or_merge_two_sessions():
    origin = {"action": "claim", "arguments": {"claim_id": "a"}, "status": "complete",
              "result_sha256": "a" * 64, "characters": 200, "delivery": "stored_pointer",
              "usage": {"kind": "historical_claim_content", "historical_claim_ids": ["a"]}}
    def page(start, end):
        return {"action": "result-read", "status": "complete", "delivery": "inline",
                "arguments": {"result_id": "a" * 64, "start": start, "limit": end - start},
                "usage": {"kind": "result_window", "start": start, "end": end, "total_characters": 200}}
    legacy = {"action": "node", "status": "complete", "arguments": {"node_id": "one"}, "characters": 200}
    logs = {"first": "\n".join(map(json.dumps, [origin, page(0, 100), legacy])),
            "second": "\n".join(map(json.dumps, [origin, page(100, 200)]))}
    result = usage_from_logs(logs)
    assert all(not log["historical_claim_ids_delivered"] for log in result["logs"])
    assert result["logs"][0]["legacy_delivery_unknown"][0]["delivery"] == "unknown_legacy_log"


def test_candidate_declarations_report_exact_node_delivery_without_claiming_support(context, tmp_path):
    context.node("illumina:method:operation:copy")
    reference = {"record_id": "method", "node_id": "illumina:method:operation:copy", "extraction_sha256": "e" * 64}
    candidates = {"draft": {"atlas_refs": [reference, {**reference, "extraction_sha256": "c" * 64}]}}
    result = usage_from_logs({"a": context.audit_log.read_text()}, candidates=candidates)
    refs = result["candidate_citations"][0]["declared_atlas_references"]
    assert [ref["exact_node_delivery_observed_any_session"] for ref in refs] == [True, False]
    assert "not proof" in result["boundary"] and "another team session" in result["boundary"]
    attempt = tmp_path / "run/attempts/01"
    attempt.mkdir(parents=True)
    (attempt / "tool_calls.jsonl").write_text(context.audit_log.read_text())
    candidate = tmp_path / "draft.json"
    candidate.write_text(json.dumps(candidates["draft"]))
    original = candidate.read_bytes()
    output = build_usage_receipt(tmp_path / "run", [candidate])
    assert output["candidate_artifacts"][0]["sha256"] == hashlib.sha256(original).hexdigest()
    assert candidate.read_bytes() == original


def test_malformed_logs_are_not_silently_counted_as_zero():
    with pytest.raises(ValueError, match="Malformed"):
        usage_from_logs({"a": '{}\n'})


def test_actual_legacy_baseline_reports_both_history_failures_without_credit():
    path = Path(__file__).resolve().parents[2] / "provenance/experiments/ideation_demo_20261105/observation_02/outputs.json"
    if not path.exists():
        pytest.skip("Frozen baseline observation not packaged in this runtime")
    logs = {}
    for name, entry in json.loads(path.read_text())["files"].items():
        if name.endswith("tool_calls.jsonl"):
            assert hashlib.sha256(entry["text"].encode()).hexdigest() == entry["sha256"]
            logs[name] = entry["text"]
    report = usage_from_logs(logs)
    assert len(report["logs"]) == 2
    assert sum(error["action"] == "history" for log in report["logs"] for error in log["errors"]) == 2
    assert all(log["historical_claim_ids_delivered"] == [] for log in report["logs"])
    assert all(log["legacy_delivery_unknown"] for log in report["logs"])


def test_history_navigation_replays_same_failed_overlay_without_changing_it(tmp_path):
    root = Path(__file__).resolve().parents[2]
    fixture = root / "provenance/experiments/ideation_demo_20261105/history_replay"
    if not fixture.exists():
        pytest.skip("Frozen pair observation not packaged in this runtime")
    manifest = fixture / "manifest.json"
    graph_path = fixture / "0349406d2bdf1daf7696a5f9/graph.json"
    before = graph_path.read_bytes()
    graph = json.loads(before)
    atlas = tmp_path / "atlas.json"
    atlas.write_text(json.dumps(graph["source_graph"]))
    context = AtlasContext(atlas, expected_sha256=hashlib.sha256(atlas.read_bytes()).hexdigest(),
        history_manifest=manifest, history_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        audit_log=tmp_path / "calls.jsonl")
    assert context.validate_history_inputs()["indexed_relationships"] == 5
    listing = context.history_list("quartz_seq2")["data"]
    assert listing["total"] == 1
    assert listing["other_direct_relationship_count"] == 4
    ident = listing["relationships"][0]["claim_id"]
    assert ident == hashlib.sha256(before).hexdigest() + ":C1"
    assert listing["relationships"][0]["read_commands"] == [["claim", ident], ["change-context", ident]]
    assert "claim" not in listing["relationships"][0]
    assert context.change_context(ident)["data"]["before"]["status"] == "exact_workflow_join"
    assert context.history_list("quartz_seq2", offset=1)["data"]["relationships"] == []
    assert context.history_list("quartz_seq")["data"]["total"] == 0
    assert graph_path.read_bytes() == before
    assert not receipt(context)["historical_claim_ids_delivered"]


def test_history_navigation_limits_and_unloaded_meaning(context):
    assert context.history_list("method")["data"]["status"] == "not_loaded"
    with pytest.raises(ValueError, match="limit"):
        context.history_list("method", limit=100)


def test_focused_provenance_preserves_scientific_payload_and_reports_missing_sources(context):
    registry = context.graph["cards"]["method"]["source_registry"]
    registry["paper"].update(association_status="source match needs care", original={"custody": "long history"})
    registry["unrelated"] = {"title": "Unrelated bibliography entry", "text": "Other full paper"}
    full = get_record(context.graph, "operation", "illumina:method:operation:copy")
    selected = _focus_sources(full, "method")
    for key in full:
        if key != "provenance":
            assert selected[key] == full[key]
    assert set(selected["provenance"]) == {"paper"}
    assert selected["provenance"]["paper"]["association_status"] == "source match needs care"
    assert "original" not in selected["provenance"]["paper"]
    assert selected["source_metadata_access"]["read_commands"][-1] == ["workflow", "method"]
    assert "unrelated" in context.workflow("method")["data"]["provenance"]
    full["original"]["evidence"].append({"source_id": "unbound", "quote": "Needs a source."})
    assert _focus_sources(full, "method")["source_metadata_access"]["missing_from_this_registry"] == ["unbound"]


def test_actual_node_provenance_reduction_preserves_all_science_and_evidence():
    path = Path(__file__).resolve().parents[2] / "provenance/experiments/ideation_demo_20261105/history_replay/0349406d2bdf1daf7696a5f9/graph.json"
    if not path.exists():
        pytest.skip("Frozen pair observation not packaged in this runtime")
    graph = json.loads(path.read_text())["source_graph"]
    full = get_record(graph, "operation", "illumina:quartz_seq:operation:add_rt_primer")
    focused = _focus_sources(full, "quartz_seq")
    assert all(focused[key] == value for key, value in full.items() if key != "provenance")
    before, after = _compact(full), _compact(focused)
    assert before["evidence_index"] == after["evidence_index"]
    assert len(_json(after)) < 24000 < len(_json(before))
    assert not focused["source_metadata_access"]["missing_from_this_registry"]
    assert focused["full_source_registry_artifact"] == full["full_source_registry_artifact"]
