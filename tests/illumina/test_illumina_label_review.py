"""Enforce review coverage/versioning, not the truth of a model's judgments."""

import asyncio
import copy

import pytest

from genomics_discovery.illumina import extraction as batch
from genomics_discovery.illumina.graph import build_graph, get_record, operation_group
from genomics_discovery.illumina.models import (
    CurrentDiagramReview, DiagramExtraction, review_targets,
    validate_review, review_outcome,
)
from tests.illumina.test_illumina_batch_controls import acquired, FakeWorker
from tests.illumina.test_illumina_review_controls import control, positive_review


def current_review(candidate, image):
    old = positive_review(candidate, image).model_dump(mode="json")
    old["format_version"] = "illumina_diagram_review/1.1"
    existing = {c["target"] for c in old["checks"]}
    for target in sorted(review_targets(candidate, format_version=old["format_version"]) - existing):
        owner = target.replace("_label:", ":", 1)
        check = copy.deepcopy(next(c for c in old["checks"] if c["target"] == owner))
        check.update(target=target, rationale="The label identifies the supported boundary entity/action.")
        old["checks"].append(check)
    return CurrentDiagramReview.model_validate(old)


def test_legacy_review_does_not_acquire_new_label_judgments(control):
    data, sources, image, _, _ = control
    c = DiagramExtraction.model_validate(data)
    old = positive_review(c, image)
    assert old.format_version == "illumina_diagram_review/1.0"
    assert not any("_label:" in check.target for check in old.checks)
    assert review_outcome(old, c, sources) == "supported"
    with pytest.raises(ValueError):
        CurrentDiagramReview.model_validate(old.model_dump(mode="json"))


@pytest.mark.parametrize("missing", ["state_label:before", "state_label:after", "operation_label:capture"])
def test_current_review_cannot_skip_a_label_check(control, missing):
    data, sources, image, _, _ = control
    c = DiagramExtraction.model_validate(data)
    raw = current_review(c, image).model_dump(mode="json")
    raw["checks"] = [check for check in raw["checks"] if check["target"] != missing]
    r = CurrentDiagramReview.model_validate(raw)
    assert any("every record target" in error for error in validate_review(r, c, sources))
    with pytest.raises(ValueError):
        review_outcome(r, c)


def test_negative_label_judgment_withholds_an_otherwise_positive_record(control):
    data, sources, image, _, _ = control
    c = DiagramExtraction.model_validate(data)
    raw = current_review(c, image).model_dump(mode="json")
    check = next(x for x in raw["checks"] if x["target"] == "state_label:after")
    check.update(verdict="unsupported", action="revise", rationale="Label names the biological target instead of the product.")
    r = CurrentDiagramReview.model_validate(raw)
    assert not validate_review(r, c, sources)
    assert review_outcome(r, c, sources) == "needs_revision"


def test_label_check_cannot_be_supported_by_metadata_only(control):
    data, sources, image, _, metadata = control
    c = DiagramExtraction.model_validate(data)
    raw = current_review(c, image).model_dump(mode="json")
    next(x for x in raw["checks"] if x["target"] == "state_label:after")["evidence"] = [metadata]
    errors = validate_review(CurrentDiagramReview.model_validate(raw), c, sources)
    assert any("target's scientific evidence: state_label:after" in error for error in errors)


def test_corrected_label_review_requires_the_primary_source(control):
    data, sources, image, paper, _ = control
    data["states"][1]["evidence"] = [image, paper]
    data["corrections"] = [{"id": "c", "target_id": "state:after",
        "diagram_observation": "Ambiguous product caption", "primary_statement": "Supported product interpretation",
        "disposition": "use_primary", "evidence": [image, paper]}]
    c = DiagramExtraction.model_validate(data)
    raw = current_review(c, image).model_dump(mode="json")
    next(x for x in raw["checks"] if x["target"] == "state_label:after")["evidence"] = [image]
    errors = validate_review(CurrentDiagramReview.model_validate(raw), c, sources)
    assert any("correction's primary source: state_label:after" in error for error in errors)


def test_self_check_rejects_the_wrong_contract(control, tmp_path, capsys):
    data, sources, image, _, _ = control
    c = DiagramExtraction.model_validate(data)
    for name, value in [("candidate", c.model_dump(mode="json")), ("sources", sources),
                         ("review", positive_review(c, image).model_dump(mode="json"))]:
        batch.write(tmp_path / (name + ".json"), value)
    result = batch.main(["check", str(tmp_path / "review.json"), "--registry", str(tmp_path / "sources.json"),
        "--record-id", c.record_id, "--candidate", str(tmp_path / "candidate.json"),
        "--review-format", "illumina_diagram_review/1.1"])
    assert result != 0
    assert "required review contract" in capsys.readouterr().out


def test_fresh_pipeline_publishes_label_judgments_to_agent_queries(acquired, tmp_path, monkeypatch):
    root, card, _, _ = acquired
    worker = FakeWorker()
    monkeypatch.setattr(batch, "CodexWorker", lambda **_: worker)
    monkeypatch.setenv("SLURM_JOB_ID", "fake-label-contract-test")
    output = tmp_path / "batch"
    result = asyncio.run(batch.run(root / "sources.json", output, workers=1))
    assert result["cards"]["example"]["status"] == "supported"
    graph = build_graph(output, root / "inventory.json")
    owner = graph["cards"]["example"]
    assert owner["review_format_version"] == "illumina_diagram_review/1.1"
    assert {"state_label:before", "state_label:after", "operation_label:capture"} <= owner["label_checks"].keys()
    op_id = owner["operation_ids"][0]
    record = get_record(graph, "operation", op_id)
    assert record["label_review"]["status"] == "separately_reviewed"
    assert record["label_review"]["check"]["target"] == "operation_label:capture"
    group = operation_group(graph, "example", [op_id])
    assert set(group["label_checks"]) == set(owner["label_checks"])
    assert 'state_label:before' in worker.calls[1]["prompt"]
    assert '--review-format illumina_diagram_review/1.1' in worker.calls[1]["prompt"]
    # Historical publications must not be portrayed as separately label-reviewed.
    del owner["label_checks"]
    owner["review_format_version"] = "illumina_diagram_review/1.0"
    legacy = get_record(graph, "operation", op_id)["label_review"]
    assert legacy["check"] is None and legacy["status"] == "not_separately_reviewed"
