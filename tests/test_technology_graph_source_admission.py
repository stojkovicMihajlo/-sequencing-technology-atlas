"""Native lineage and source-completeness admission with synthetic worker verdicts."""

import json
from pathlib import Path

import pytest
from test_workflow_extraction_bindings import case as binding_case
from test_workflow_extraction_runtime import source_case as source_case
from test_workflow_source_obligations import SourceWorker, obligations_for

from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.technology_graph.admission import load_extractions
from genomics_discovery.curation.technology_graph.assembly import assemble_graph, node_id
from genomics_discovery.curation.technology_graph.queries import inspect, neighbors
from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.models import WorkflowDraft, digest
from genomics_discovery.curation.workflow_extraction.obligations import SourceCoverageReview
from genomics_discovery.curation.workflow_extraction.runtime import execute
from genomics_discovery.curation.workflow_matching.models import AlignmentBatch, ProfileBatch
from genomics_discovery.curation.workflow_matching.tools import Context


def save(path, value):
    Path(path).write_text(json.dumps(value))


async def source_run(case, run, *, origin=None, omit_wash=False):
    state = await execute(case[1], run, worker=SourceWorker(case, omit_wash=omit_wash),
                          mode="baseline", require_source_coverage=True,
                          source_obligations=origin, max_revisions=0)
    assert state["status"] == ("needs_revision" if omit_wash else "supported"), state.get("error")
    registry = run.parent / (run.name + "-registry.json")
    save(registry, {"sample": str(run)})
    return state, registry


def reviewer(state):
    return next(record for record in state["attempts"]
                if record["role"] == "reviewer" and record["artifact"] == state["review"])


def rebind_source_sidecar(state, raw):
    """Recompute mutable checkpoints to exercise the independent scientific gate."""
    metadata = state["artifacts"]["source_coverage_review"]
    path = Path(metadata["path"])
    save(path, raw)
    document = SourceCoverageReview.model_validate(raw)
    metadata.update(sha256=sha(path), document_sha256=digest(document))
    reviewer(state)["source_coverage_review"] = dict(metadata)


async def test_source_coverage_artifacts_and_native_inputs_are_pinned(source_case, tmp_path):
    run = tmp_path / "source-first"
    state, registry = await source_run(source_case, run)
    context = load_extractions(registry)[0]
    for key in ("source_obligations", "source_coverage_review"):
        assert Path(state["artifacts"][key]["path"]) in context.files
    for attempt in state["attempts"]:
        if attempt["role"] in {"source_obligations", "reviewer"}:
            directory = Path(attempt["directory"])
            assert {directory / n for n in attempt["archive_sha256"]} <= context.files
            assert directory / "stage_prompt.txt" in context.files
            assert {Path(m["path"]) for m in attempt["inputs"].values()} <= context.files


@pytest.mark.parametrize("defect", [
    "missing_metadata", "missing_sidecar", "modified_sidecar", "failed_sidecar",
    "wrong_binding", "wrong_owner", "external_sidecar", "source_owner_spoof",
    "source_archive", "downgraded_flag",
])
async def test_supported_main_review_cannot_bypass_source_gate(source_case, tmp_path, defect):
    run = tmp_path / defect
    state, registry = await source_run(source_case, run)
    metadata = state["artifacts"]["source_coverage_review"]
    path = Path(metadata["path"])
    if defect == "missing_metadata":
        state["artifacts"].pop("source_coverage_review")
    elif defect == "missing_sidecar":
        path.unlink()
    elif defect == "modified_sidecar":
        path.write_text("{}")
    elif defect in {"failed_sidecar", "wrong_binding"}:
        raw = json.loads(path.read_text())
        if defect == "failed_sidecar":
            raw["checks"][0].update(disposition="missing", targets=[])
        else:
            raw["workflow_review_sha256"] = "0" * 64
        rebind_source_sidecar(state, raw)
    elif defect == "wrong_owner":
        reviewer(state).pop("source_coverage_review")
    elif defect == "external_sidecar":
        external = tmp_path / "external-review.json"
        external.write_bytes(path.read_bytes())
        metadata["path"] = str(external)
        reviewer(state)["source_coverage_review"] = dict(metadata)
    elif defect == "source_owner_spoof":
        source_owner = next(a for a in state["attempts"] if a["role"] == "source_obligations")
        source_owner["directory"] = str(Path(state["review"]).parent)
    elif defect == "source_archive":
        source_owner = next(a for a in state["attempts"] if a["role"] == "source_obligations")
        (Path(source_owner["directory"]) / "events.jsonl").write_text("")
    else:
        config_path = run / "config.json"
        config = json.loads(config_path.read_text())
        config["require_source_coverage"] = False
        save(config_path, config)
        state.update(require_source_coverage=False, config_sha256=sha(config_path))
    state["status"] = "supported"
    save(run / "state.json", state)
    with pytest.raises(ValueError):
        load_extractions(registry)


async def test_forged_supported_state_rechecks_actual_failed_source_outcome(source_case, tmp_path):
    run = tmp_path / "missing-wash"
    state, registry = await source_run(source_case, run, omit_wash=True)
    state["status"] = "supported"
    save(run / "state.json", state)
    with pytest.raises(ValueError, match="source-completeness gate failed"):
        load_extractions(registry)


async def test_legitimate_reused_obligations_trace_original_and_copied_runtime_owners(
    source_case, tmp_path,
):
    origin_run = tmp_path / "origin"
    original, _ = await source_run(source_case, origin_run)
    origin = Path(original["artifacts"]["source_obligations"]["path"])
    copied_run = tmp_path / "copy"
    copied, _ = await source_run(source_case, copied_run, origin=origin)
    copied_path = Path(copied["artifacts"]["source_obligations"]["path"])
    final, registry = await source_run(source_case, tmp_path / "copy-again", origin=copied_path)
    context = load_extractions(registry)[0]
    assert {origin, copied_path, Path(final["artifacts"]["source_obligations"]["path"])} <= context.files
    assert {origin_run / "state.json", origin_run / "config.json",
            copied_run / "state.json", copied_run / "config.json"} <= context.files
    origin_owner = next(a for a in original["attempts"] if a["role"] == "source_obligations")
    assert {Path(origin_owner["directory"]) / name for name in origin_owner[
        "archive_sha256"]} <= context.files


@pytest.mark.parametrize("defect", ["ownership", "archive", "changed_origin", "cycle",
                                   "shared_thread"])
async def test_reused_obligations_cannot_lose_native_lineage(source_case, tmp_path, defect):
    origin_run = tmp_path / "origin"
    original, _ = await source_run(source_case, origin_run)
    source = Path(original["artifacts"]["source_obligations"]["path"])
    run = tmp_path / "copy"
    state, registry = await source_run(source_case, run, origin=source)
    owner = next(a for a in original["attempts"] if a["role"] == "source_obligations")
    if defect == "ownership":
        owner["artifact"] = original["review"]
        save(origin_run / "state.json", original)
    elif defect == "archive":
        (Path(owner["directory"]) / "final.txt").write_text("Changed native completion.")
    elif defect == "changed_origin":
        source.write_text("{}")
    elif defect == "cycle":
        config_path = run / "config.json"
        config = json.loads(config_path.read_text())
        snapshot = state["artifacts"]["source_obligations"]
        config["source_obligations_input"] = {"path": snapshot["path"], "sha256": snapshot["sha256"]}
        save(config_path, config)
        state["config_sha256"] = sha(config_path)
        save(run / "state.json", state)
    else:
        # A reused preparer is outside this run's attempts; validate its session
        # against the current reviewer, rather than only checking local uniqueness.
        current = reviewer(state)
        directory = Path(current["directory"])
        thread = owner["thread_id"]
        result = json.loads((directory / "result.json").read_text())
        result["thread_id"] = thread
        save(directory / "result.json", result)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        for event in events:
            if event.get("type") == "thread.started":
                event["thread_id"] = thread
        (directory / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        current["thread_id"] = thread
        for name in ("result.json", "events.jsonl"):
            current["archive_sha256"][name] = sha(directory / name)
        save(run / "state.json", state)
    with pytest.raises(ValueError):
        load_extractions(registry)


async def test_unowned_external_obligations_are_a_provenance_policy_rejection(source_case, tmp_path):
    external = tmp_path / "authored-checklist.json"
    save(external, obligations_for(source_case[0]))
    state, registry = await source_run(source_case, tmp_path / "external", origin=external)
    assert state["status"] == "supported"  # Runtime accepts a scoped supplied document.
    with pytest.raises(ValueError, match="explicit external-provenance mode"):
        load_extractions(registry)


def test_optional_exclusive_association_fields_survive_graph_and_queries(binding_case, tmp_path):
    data, packet, _ = binding_case
    workflow = assemble_workflow(WorkflowDraft.model_validate(data), require_bindings=True)
    context = Context({"sample": {"workflow": workflow, "packet": packet, "review": None,
                                  "paths": {"packet": tmp_path / "packet.json"}}}, {}, {}, set())
    loaded = context, ProfileBatch(profiles=()), AlignmentBatch(alignments=()), {}, None, []
    graph = assemble_graph(
        {"revision_id": "test", "methods": [{"id": "method", "name": "Test method"}],
         "claims": []},
        {"sample": {"atlas_method_id": "method", "workflow_method_id": workflow.method_id,
                    "basis": "Explicit test binding."}}, [("test", loaded)])
    expected = {flow.id: flow.model_dump(mode="json") for flow in workflow.flows}
    actual = {edge["record"]["id"]: edge for edge in graph["edges"] if edge["layer"] == "protocol"}
    assert {key: edge["record"] for key, edge in actual.items()} == expected
    measure = next(node["id"] for node in graph["nodes"]
                   if node["kind"] == "operation" and node["record"]["id"] == "measure")
    edges = neighbors(graph, measure, layer="protocol", direction="in")["results"]
    assert sum(row["edge"]["record"]["selection"] == "alternative" for row in edges) == 2
    optional = next(row["edge"] for row in edges
                    if row["edge"]["record"]["selection"] == "optional")
    assert inspect(graph, optional["id"])["record"]["record"]["condition"] == "Control is available."
    assert optional["record"]["association"] == "source_qualified"
