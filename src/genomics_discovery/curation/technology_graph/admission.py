"""Admit completed extraction runs without requiring any cross-method correspondence."""

import json
from pathlib import Path

from genomics_discovery.curation.codex_worker import assess_archived_attempt
from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_coverage import (
    CoverageReview,
    IntegrationCoverage,
    coverage_review_outcome,
    validate_coverage,
    validate_coverage_review,
)
from genomics_discovery.curation.workflow_extraction.models import (
    ReadingReport,
    Workflow,
    WorkflowReview,
    digest,
    review_outcome,
    validate_evidence,
    validate_review,
)
from genomics_discovery.curation.workflow_extraction.sources import load_packet
from genomics_discovery.curation.workflow_matching.models import AlignmentBatch, ProfileBatch
from genomics_discovery.curation.workflow_matching.tools import Context


def load_extractions(registry: Path):
    """Registry maps explicit parent IDs to workflow_extraction runtime directories."""
    registry = registry.resolve()
    rows = json.loads(registry.read_text())
    if not isinstance(rows, dict) or not rows:
        raise ValueError("Extraction registry must map parent IDs to completed runs")
    context = Context({}, {}, {}, {registry})
    for parent, directory in rows.items():
        if not isinstance(parent, str) or not parent.strip() or not isinstance(directory, str):
            raise ValueError("Extraction registry needs explicit parent IDs and run paths")
        run = (registry.parent / directory).resolve()
        state_path, config_path = run / "state.json", run / "config.json"
        state, config = json.loads(state_path.read_text()), json.loads(config_path.read_text())
        if state.get("status") != "supported" or state.get("config_sha256") != sha(config_path):
            raise ValueError("Only completed supported extraction runs with bound config may enter")
        packet_path = run / "source_packet.json"
        if config.get("source_packet_sha256") != sha(packet_path):
            raise ValueError("Extraction source packet differs from its runtime checkpoint")
        packet = load_packet(packet_path)
        resumed = config.get("resumed_completed_stages")
        reused_threads = set()
        if resumed is not None:
            manifest = run / "resume_manifest.json"
            if (not config.get("require_coverage") or not manifest.is_file()
                    or json.loads(manifest.read_text()) != resumed
                    or resumed.get("source_packet_sha256") != digest(packet)
                    or not resumed.get("files")):
                raise ValueError("Resumed extraction lacks its bound completed-stage lineage")
            context.files.add(manifest)
            for name, expected in resumed["files"].items():
                path = Path(name)
                if not path.is_file() or sha(path) != expected:
                    raise ValueError("Resumed upstream artifact/archive changed or disappeared")
                context.files.add(path)
            records = resumed.get("reused_stage_records", [])
            intent = resumed.get("intent", "operational_resume")
            expected_roles = ["chemistry", "information", "integrator"]
            if intent == "source_backed_revision":
                expected_roles.append("reviewer")
            elif intent != "operational_resume":
                raise ValueError("Unknown completed-stage continuation intent")
            if sorted(r["role"] for r in records) != expected_roles:
                raise ValueError("Continuation must preserve its declared completed upstream roles")
            for record in records:
                directory = Path(record["directory"])
                prior = assess_archived_attempt(directory)
                if (not prior.complete or not prior.thread_id
                        or prior.thread_id != record.get("thread_id")
                        or prior.usage != record.get("usage")
                        or sha(Path(record["artifact"])) != record["artifact_sha256"]
                        or not record.get("archive_sha256")
                        or any(sha(directory / n) != expected
                               for n, expected in record["archive_sha256"].items())):
                    raise ValueError("Resumed native stage is incomplete or changed")
                reused_threads.add(prior.thread_id)
                if record["role"] in {"chemistry", "information"}:
                    copy = Path(state["artifacts"][record["role"]]["path"])
                    if sha(copy) != record["artifact_sha256"]:
                        raise ValueError("Resumed reader copy differs from its native artifact")
            if len(reused_threads) != len(expected_roles):
                raise ValueError("Resumed readers/integrator must have distinct native sessions")
            if intent == "source_backed_revision":
                by_role = {r["role"]: r for r in records}
                old_workflow = Workflow.model_validate_json(
                    Path(by_role["integrator"]["artifact"]).read_bytes())
                old_review = WorkflowReview.model_validate_json(
                    Path(by_role["reviewer"]["artifact"]).read_bytes())
                old_coverage = IntegrationCoverage.model_validate_json(
                    Path(by_role["integrator"]["coverage"]["path"]).read_bytes())
                old_coverage_review = CoverageReview.model_validate_json(
                    Path(by_role["reviewer"]["coverage_review"]["path"]).read_bytes())
                old_readers = {role: ReadingReport.model_validate_json(
                    Path(by_role[role]["artifact"]).read_bytes())
                    for role in ("chemistry", "information")}
                errors = validate_review(old_review, old_workflow, packet)
                errors += validate_coverage(old_coverage, old_workflow, old_readers, packet)
                errors += validate_coverage_review(old_coverage_review, old_coverage,
                                                   old_review, old_workflow, old_readers, packet)
                if errors or coverage_review_outcome(
                    old_coverage_review, old_review, old_workflow
                ) != "needs_revision":
                    raise ValueError("Explicit continuation lacks valid needs_revision feedback")
        paths = {k: Path(state[k]).resolve() for k in ("workflow", "review")}
        for key, path in paths.items():
            if state.get(key + "_sha256") != sha(path):
                raise ValueError("Extraction artifact differs from its runtime checkpoint")
        workflow = Workflow.model_validate_json(paths["workflow"].read_bytes())
        review = WorkflowReview.model_validate_json(paths["review"].read_bytes())
        errors = validate_evidence(workflow, packet) + validate_review(review, workflow, packet)
        if errors or review_outcome(review, workflow) != "supported":
            raise ValueError("Extraction source/review gate failed: " + "; ".join(errors))
        owner = [
            r
            for r in state["attempts"]
            if r.get("artifact") == str(paths["review"]) and r.get("role") == "reviewer"
        ]
        if len(owner) != 1 or owner[0].get("status") != "validated":
            raise ValueError("Review must belong to one validated native reviewer attempt")
        owner = owner[0]
        attempt = paths["review"].parent
        assessment = assess_archived_attempt(attempt)
        archives = owner.get("archive_sha256", {})
        if (
            not assessment.complete
            or not assessment.thread_id
            or assessment.thread_id != owner.get("thread_id")
            or assessment.usage != owner.get("usage")
            or not archives
            or any(sha(attempt / name) != expected for name, expected in archives.items())
            or sha(attempt / "prompt.txt") != owner.get("prompt_sha256")
            or owner.get("artifact_sha256") != sha(paths["review"])
        ):
            raise ValueError("Extraction reviewer transport/checkpoint is incomplete or changed")
        if any(
            a.get("thread_id") == assessment.thread_id and a is not owner for a in state["attempts"]
        ) or assessment.thread_id in reused_threads:
            raise ValueError("Extraction reviewer must have its own fresh session")
        context.files.update(
            {
                state_path,
                config_path,
                packet_path,
                *paths.values(),
                attempt / "prompt.txt",
                *(attempt / name for name in archives),
            }
        )
        if config.get("require_coverage"):
            artifacts = state["artifacts"]

            def sidecar(key, model, artifacts=artifacts):
                path = Path(artifacts[key]["path"]).resolve()
                if sha(path) != artifacts[key]["sha256"]:
                    raise ValueError("Extraction completion-coverage artifact changed")
                context.files.add(path)
                return model.model_validate_json(path.read_bytes())

            readers = {role: sidecar(role, ReadingReport) for role in ("chemistry", "information")}
            coverage = sidecar("coverage", IntegrationCoverage)
            coverage_review = sidecar("coverage_review", CoverageReview)
            errors = validate_coverage(coverage, workflow, readers, packet)
            errors += validate_coverage_review(
                coverage_review, coverage, review, workflow, readers, packet
            )
            if errors or coverage_review_outcome(coverage_review, review, workflow) != "supported":
                raise ValueError("Extraction finding-coverage gate failed")
        context.parents[parent] = {
            "workflow": workflow,
            "packet": packet,
            "review": review,
            "paths": {**paths, "packet": packet_path},
        }
        context.files.update(Path(s["original_path"]) for s in packet["sources"])
        if digest(packet) != config.get("source_packet_digest"):
            raise ValueError("Extraction packet canonical binding changed")
    # No invented seeds, comparison proposals, or comparison review for an isolated method.
    return (context, ProfileBatch(profiles=()), AlignmentBatch(alignments=()), {}, None, [])
