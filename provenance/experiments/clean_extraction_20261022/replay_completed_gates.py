"""Read-only replay of completed extraction gates under their frozen runtime."""
import argparse
import json
from pathlib import Path

from genomics_discovery.curation.codex_worker import assess_archived_attempt
from genomics_discovery.curation.run_state import now, sha
from genomics_discovery.curation.workflow_coverage import (
    IntegrationCoverage, CoverageReview, validate_coverage_review, coverage_review_outcome,
)
from genomics_discovery.curation.workflow_extraction.models import (
    Workflow, WorkflowReview, ReadingReport, digest, validate_review, review_outcome,
)
from genomics_discovery.curation.workflow_extraction.field_review import (
    WorkflowFieldReview, validate_field_review, field_review_outcome,
)
from genomics_discovery.curation.workflow_extraction.obligations import (
    SourceObligations, SourceCoverageReview, validate_source_coverage_review, source_coverage_outcome,
)
from genomics_discovery.curation.workflow_extraction.sources import load_packet


def replay(run):
    state_path = run / "state.json"
    state = json.loads(state_path.read_text())
    if state["status"] not in {"supported", "needs_revision", "research_needed"}:
        raise ValueError("Only completed scientific outcomes can be replayed")
    packet = load_packet(run / "source_packet.json")
    artifacts = state["artifacts"]
    pins = {}
    def read(key, model):
        row = artifacts[key]; path = Path(row["path"])
        if sha(path) != row["sha256"]:
            raise ValueError("Artifact pin differs: " + key)
        pins[key] = {"path": str(path), "raw_sha256": row["sha256"]}
        return model.model_validate_json(path.read_bytes())
    workflow, review = read("integrator", Workflow), read("reviewer", WorkflowReview)
    field = read("field_review", WorkflowFieldReview)
    reviews = [a for a in state["attempts"] if a["role"] == "reviewer" and a["status"] == "validated"]
    history = {}
    if len(reviews) > 1:
        previous = reviews[-2]
        old_field, old_workflow = previous["field_review"], previous["inputs"]["candidate"]
        for row in (old_field, old_workflow):
            if sha(Path(row["path"])) != row["sha256"]:
                raise ValueError("Previous review input pin differs")
        history = {"prior_review": WorkflowFieldReview.model_validate_json(Path(old_field["path"]).read_bytes()),
                   "prior_workflow": Workflow.model_validate_json(Path(old_workflow["path"]).read_bytes())}
    errors = {"primary": validate_review(review, workflow, packet),
              "field": validate_field_review(field, workflow, packet, **history)}
    outcomes = {"primary": review_outcome(review, workflow),
                "field": field_review_outcome(field, workflow, packet, **history)}
    if state["require_coverage"]:
        reports = {role: read(role, ReadingReport) for role in ("chemistry", "information")}
        coverage = read("coverage", IntegrationCoverage)
        coverage_review = read("coverage_review", CoverageReview)
        errors["reader_coverage"] = validate_coverage_review(coverage_review, coverage, review, workflow, reports, packet)
        outcomes["reader_coverage"] = coverage_review_outcome(coverage_review, review, workflow)
    if state["require_source_coverage"]:
        requirements = read("source_obligations", SourceObligations)
        source_review = read("source_coverage_review", SourceCoverageReview)
        errors["source_coverage"] = validate_source_coverage_review(source_review, requirements, review, workflow, packet)
        outcomes["source_coverage"] = source_coverage_outcome(source_review, requirements, review, workflow)
    for attempt in state["attempts"]:
        if attempt["status"] != "validated":
            raise ValueError("Completed attempt is not validated")
        directory = Path(attempt["directory"])
        assessment = assess_archived_attempt(directory)
        if not assessment.complete or assessment.thread_id != attempt["thread_id"] or assessment.usage != attempt["usage"]:
            raise ValueError("Native archive replay differs")
        if sha(directory / "prompt.txt") != attempt["prompt_sha256"]:
            raise ValueError("Prompt archive differs")
        for name, pin in attempt["archive_sha256"].items():
            if sha(directory / name) != pin:
                raise ValueError("Native archive pin differs")
    return {"observed_at": now(), "state_sha256": sha(state_path), "state_status": state["status"],
            "workflow_sha256": digest(workflow), "source_packet_sha256": digest(packet),
            "inputs": pins, "gate_integrity_errors": errors, "gate_outcomes": outcomes,
            "validated_native_attempts_replayed": len(state["attempts"]),
            "supported_export_files_present": [name for name in ("graph_candidate.json", "protocol.txt", "overview.json", "overview.txt", "overview.html") if (run / name).exists()],
            "boundary": "Existing frozen software/source-registration and declared-review replay, not an independent entailment or wet-lab validation."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path); parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = replay(args.run)
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2); handle.write("\n")
    print(json.dumps({"output": str(args.output), "outcomes": result["gate_outcomes"], "errors": result["gate_integrity_errors"]}))
