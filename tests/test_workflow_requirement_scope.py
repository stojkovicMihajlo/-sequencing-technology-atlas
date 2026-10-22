"""Scope guidance and fail-closed accounting, using injected jobs only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_workflow_extraction_runtime import source_case
from test_workflow_source_obligations import SourceWorker
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.obligations import (
    SourceCoverageCheck,
    SourceCoverageReview,
    SourceObligation,
    SourceObligations,
)


# Explicit pre-description serialized artifacts and digest pins. These values
# were captured before adding Field descriptions; no new runtime fixtures or
# source files are needed to exercise legacy serialization compatibility.
REF = {"source_id": "source", "passage_id": "p00001"}
EVIDENCE = {**REF, "quote": "Wash the sample.", "support": "reported",
            "rationale": "The source reports washing."}
LEGACY_OBLIGATIONS = {
    "format_version": "source_obligations/1.0",
    "source_packet_sha256": "1" * 64,
    "method_id": "method",
    "scope": "General source-described assay.",
    "variant": "original",
    "source_scope": [{"registered_source_id": "source", "disposition": "inspected",
        "inspected_passages": [REF], "rationale": "The relevant passage was read.",
        "next_evidence": None, "blocks_completeness": False}],
    "obligations": [{"id": "wash", "requirement": "Wash the sample.",
        "applicability": "required", "condition": None,
        "rationale": "Preserve the reported handling boundary.", "evidence": [EVIDENCE]}],
    "source_limits": [],
}
LEGACY_OBLIGATIONS_SHA256 = "ef0fb2e9382727ce7b4f41fe677a0082adc57a3e7e84cb8f84e7b2d82cde16d8"
LEGACY_COVERAGE = {
    "format_version": "source_coverage_review/1.0",
    "source_packet_sha256": "1" * 64,
    "obligations_sha256": LEGACY_OBLIGATIONS_SHA256,
    "workflow_sha256": "2" * 64,
    "workflow_review_sha256": "3" * 64,
    "inspected_passages": [REF],
    "checks": [{"obligation_id": "wash", "disposition": "satisfied", "selected": True,
        "targets": ["operation:wash"], "rationale": "The operation preserves the handling.",
        "evidence": [EVIDENCE]}],
}
LEGACY_COVERAGE_SHA256 = "9ea549402eb2219674c92e38f12278d78999efdff15d6af1e0a987dd4e26eb67"


@pytest.mark.parametrize("model,artifact,pin", [
    (SourceObligations, LEGACY_OBLIGATIONS, LEGACY_OBLIGATIONS_SHA256),
    (SourceCoverageReview, LEGACY_COVERAGE, LEGACY_COVERAGE_SHA256),
])
def test_scope_descriptions_preserve_legacy_serialization_and_digest(model, artifact, pin):
    record = model.model_validate(artifact)
    assert record.model_dump(mode="json") == artifact
    assert record.model_dump_json() == json.dumps(artifact, separators=(",", ":"))
    assert digest(record) == pin


def test_scope_semantics_are_present_in_exported_schemas_without_new_fields():
    source = SourceObligation.model_json_schema()["properties"]
    coverage = SourceCoverageCheck.model_json_schema()["properties"]
    assert set(source) == {"id", "requirement", "applicability", "condition", "rationale", "evidence"}
    assert set(coverage) == {"obligation_id", "disposition", "selected", "targets", "rationale", "evidence"}
    assert all(prop.get("description") for prop in (*source.values(), *coverage.values()))
    assert "independently applicable" in source["requirement"]["description"]
    assert "omitted" in source["rationale"]["description"]
    assert "rather than only in requirement prose" in source["condition"]["description"]
    assert "selected and uncertain" in coverage["disposition"]["description"]
    assert "Unconditional" in coverage["selected"]["description"]
    assert source["condition"]["default"] is None
    assert coverage["targets"]["default"] == []
    assert source["applicability"]["enum"] == ["required", "optional", "outside_scope"]


class DisputedScopeWorker(SourceWorker):
    """Inject a valid selected/uncertain judgment; do not simulate its truth."""

    async def run(self, prompt, directory):
        result = await super().run(prompt, directory)
        if self.calls[-1]["role"] == "reviewer":
            path = Path(directory) / "source_coverage_review.json"
            coverage = json.loads(path.read_text())
            coverage["checks"][0].update(
                selected=True,
                disposition="uncertain",
                rationale="The source reports the operation, but its unconditional necessity "
                          "for this scope is disputed. A source/scope assessment is needed; "
                          "candidate-only repair cannot resolve this premise.",
            )
            path.write_text(json.dumps(coverage))
        return result


@pytest.mark.parametrize("dispute,expected_status,expected_revisions,expected_reviews", [
    (True, "research_needed", 0, 1),
    (False, "needs_revision", 1, 2),
])
async def test_disputed_requirement_stops_safely_while_genuine_omission_keeps_repair_gate(
    source_case, tmp_path, dispute, expected_status, expected_revisions, expected_reviews,
):
    worker = DisputedScopeWorker(source_case) if dispute else SourceWorker(source_case, omit_wash=True)
    run = tmp_path / ("scope_dispute" if dispute else "genuine_omission")
    state = await execute(source_case[1], run, worker=worker, mode="baseline",
                          require_source_coverage=True, max_revisions=1)
    assert state["status"] == expected_status, state.get("error")
    assert state["revision"] == expected_revisions
    assert worker.integrations == worker.reviews == expected_reviews
    assert [call["role"] for call in worker.calls].count("source_obligations") == 1
    assert not (run / "graph_candidate.json").exists()
    assert not (run / "protocol.txt").exists()
    source_prompt = worker.calls[0]["prompt"]
    assert "one independently applicable coverage predicate" in source_prompt
    assert "not only inside requirement prose" in source_prompt
    reviewer_prompt = next(call["prompt"] for call in worker.calls if call["role"] == "reviewer")
    assert "Accuracy of included scoped detail and minimum scope coverage are separate tests" in reviewer_prompt
    assert "selected=true with disposition=uncertain" in reviewer_prompt
    assert "An unconditional required item cannot be waived" in reviewer_prompt
    assert "not permission" in reviewer_prompt
