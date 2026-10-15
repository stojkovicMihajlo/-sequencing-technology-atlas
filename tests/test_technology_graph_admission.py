"""Extraction admission tests use simulated archives, never real scientific verdicts."""

import json
from pathlib import Path

import pytest
from test_workflow_completion_runtime import CompletionWorker
from test_workflow_extraction_runtime import FakeWorker
from test_workflow_extraction_runtime import source_case as source_case

from genomics_discovery.curation.technology_graph.admission import load_extractions
from genomics_discovery.curation.technology_graph.assembly import build_graph
from genomics_discovery.curation.workflow_extraction.runtime import execute


@pytest.mark.parametrize("coverage", [False, True])
async def test_supported_isolated_extraction_enters_graph_without_matching(
    source_case, tmp_path, coverage
):
    run = tmp_path / "extraction"
    worker = CompletionWorker(source_case) if coverage else FakeWorker(source_case)
    state = await execute(
        source_case[1],
        run,
        worker=worker,
        mode="specialists" if coverage else "baseline",
        max_revisions=0,
        require_coverage=coverage,
    )
    assert state["status"] == "supported"
    registry = tmp_path / "extractions.json"
    registry.write_text(json.dumps({"sample": str(run)}))
    loaded = load_extractions(registry)
    assert loaded[0].parents["sample"]["workflow"].method_id == "branched_assay"
    assert loaded[1].profiles == () and loaded[2].alignments == () and loaded[4] is None
    atlas, bindings = tmp_path / "atlas.json", tmp_path / "bindings.json"
    atlas.write_text(
        json.dumps(
            {
                "revision_id": "synthetic",
                "methods": [{"id": "branched", "name": "Branched assay"}],
                "claims": [],
            }
        )
    )
    bindings.write_text(
        json.dumps(
            {
                "sample": {
                    "atlas_method_id": "branched",
                    "workflow_method_id": "branched_assay",
                    "basis": "Explicit synthetic identity",
                }
            }
        )
    )
    graph = build_graph(atlas, bindings, [], extractions_path=registry)
    assert graph["statistics"]["technologies_with_operation_graphs"] == 1
    assert graph["statistics"]["comparisons"] == 0
    assert len(graph["workflows"]) == 1 and graph["development_details"] == []
    assert not any(e["layer"] in {"history", "cross_protocol"} for e in graph["edges"])


@pytest.mark.parametrize("defect", ["nonpositive", "artifact", "archive", "coverage"])
async def test_admission_rejects_stale_or_nonpositive_extraction(source_case, tmp_path, defect):
    run = tmp_path / "extraction"
    state = await execute(
        source_case[1],
        run,
        worker=CompletionWorker(source_case),
        mode="specialists",
        max_revisions=0,
        require_coverage=True,
    )
    assert state["status"] == "supported"
    registry = tmp_path / "extractions.json"
    registry.write_text(json.dumps({"sample": str(run)}))
    if defect == "nonpositive":
        state["status"] = "research_needed"
        (run / "state.json").write_text(json.dumps(state))
    elif defect == "artifact":
        Path(state["review"]).write_text("{}")
    elif defect == "archive":
        (Path(state["review"]).parent / "events.jsonl").write_text("")
    else:
        Path(state["artifacts"]["coverage_review"]["path"]).write_text("{}")
    with pytest.raises(ValueError):
        load_extractions(registry)
