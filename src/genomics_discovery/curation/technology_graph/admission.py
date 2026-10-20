"""Admit completed extraction runs without requiring any cross-method correspondence."""

import json
from pathlib import Path
import re

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
from genomics_discovery.curation.workflow_extraction.obligations import (
    SourceCoverageReview,
    SourceObligations,
    source_coverage_outcome,
    validate_source_coverage_review,
    validate_source_obligations,
)
from genomics_discovery.curation.workflow_matching.models import AlignmentBatch, ProfileBatch
from genomics_discovery.curation.workflow_matching.tools import Context


def _bound_artifact(metadata, model, files: set[Path], label: str):
    if not isinstance(metadata, dict) or not isinstance(metadata.get("path"), str):
        raise ValueError(f"Missing {label} artifact checkpoint")
    path = Path(metadata["path"]).resolve()
    if not path.is_file() or sha(path) != metadata.get("sha256"):
        raise ValueError(f"{label} artifact changed or disappeared")
    document = model.model_validate_json(path.read_bytes())
    if metadata.get("document_sha256") != digest(document):
        raise ValueError(f"{label} canonical artifact binding changed")
    files.add(path)
    return document, path


def _native_owner(state: dict, artifact: Path, role: str, files: set[Path]):
    owners = [record for record in state.get("attempts", [])
              if record.get("role") == role and record.get("artifact") == str(artifact)]
    if len(owners) != 1 or owners[0].get("status") != "validated":
        raise ValueError(f"{role} artifact must have one validated native owner")
    owner = owners[0]
    directory = artifact.parent
    if owner.get("directory") != str(directory) or artifact.name != "artifact.json":
        raise ValueError(f"{role} artifact ownership directory differs")
    assessment = assess_archived_attempt(directory)
    archives = owner.get("archive_sha256", {})
    required_archives = {"result.json", "events.jsonl", "final.txt"}
    if (not assessment.complete or not assessment.thread_id
            or assessment.thread_id != owner.get("thread_id")
            or assessment.usage != owner.get("usage")
            or not required_archives <= archives.keys()
            or any(not (directory / name).is_file() or sha(directory / name) != expected
                   for name, expected in archives.items())
            or owner.get("artifact_sha256") != sha(artifact)):
        raise ValueError(f"{role} native transport/checkpoint is incomplete or changed")
    for name in ("stage_prompt.txt", "prompt.txt"):
        prompt = directory / name
        if not prompt.is_file() or sha(prompt) != owner.get("prompt_sha256"):
            raise ValueError(f"{role} archived prompt checkpoint changed")
        if not re.search(r"ROLE:\s*" + re.escape(role) + r"\b", prompt.read_text()):
            raise ValueError(f"{role} native prompt belongs to another stage")
        files.add(prompt)
    if any(record is not owner and record.get("thread_id") == assessment.thread_id
           for record in state.get("attempts", [])):
        raise ValueError(f"{role} must have its own independent native session")
    for metadata in owner.get("inputs", {}).values():
        path = Path(metadata["path"]).resolve()
        if not path.is_file() or sha(path) != metadata.get("sha256"):
            raise ValueError(f"{role} native input checkpoint changed")
        files.add(path)
    files.update(directory / name for name in archives)
    return owner, assessment


def _source_obligations_lineage(run, state, config, packet, files, *, seen=()):
    """Trace only explicit runtime copies; external authorship is a separate policy.

    A completed source stage can be reused even if its later candidate failed.
    Its native archive, packet binding and independent ownership must still exist.
    """
    run = run.resolve()
    if run in seen or len(seen) >= 16:
        raise ValueError("Source-obligations reuse lineage is cyclic or exceeds 16 copies")
    if (config.get("require_source_coverage") is not True
            or state.get("require_source_coverage") is not True):
        raise ValueError("Source-obligations owner lacks a source-coverage runtime checkpoint")
    metadata = state.get("artifacts", {}).get("source_obligations")
    obligations, artifact = _bound_artifact(metadata, SourceObligations, files,
                                           "Source obligations")
    errors = validate_source_obligations(obligations, packet)
    if errors:
        raise ValueError("Source obligations gate failed: " + "; ".join(errors))
    origin = config.get("source_obligations_input")
    if origin is None:
        owner, assessment = _native_owner(state, artifact, "source_obligations", files)
        if owner.get("document_sha256") != digest(obligations) or owner.get("inputs"):
            raise ValueError("Source obligations native owner is not the candidate-blind stage")
        return obligations, {assessment.thread_id}
    if (not isinstance(origin, dict) or not isinstance(origin.get("path"), str)
            or artifact != run / "source_obligations.json"):
        raise ValueError("Reused source obligations lack an exact runtime-copy binding")
    source = Path(origin["path"]).resolve()
    if (not source.is_file() or sha(source) != origin.get("sha256")
            or sha(source) != metadata["sha256"]):
        raise ValueError("Reused source obligations differ from their frozen origin")
    files.add(source)
    # Native artifacts are run/attempts/stage/artifact.json; runtime copies are
    # run/source_obligations.json. Do not search arbitrary directory trees.
    for directory in list(source.parents)[:3]:
        state_path, config_path = directory / "state.json", directory / "config.json"
        if not state_path.is_file() or not config_path.is_file():
            continue
        prior_state = json.loads(state_path.read_text())
        prior_metadata = prior_state.get("artifacts", {}).get("source_obligations", {})
        if (not isinstance(prior_metadata.get("path"), str)
                or Path(prior_metadata["path"]).resolve() != source):
            continue
        prior_config = json.loads(config_path.read_text())
        packet_path = directory / "source_packet.json"
        if (prior_state.get("config_sha256") != sha(config_path)
                or not packet_path.is_file()
                or prior_config.get("source_packet_sha256") != sha(packet_path)
                or prior_config.get("source_packet_digest") != digest(packet)):
            raise ValueError("Reused source-obligations owner checkpoint changed")
        prior_packet = load_packet(packet_path)
        if digest(prior_packet) != digest(packet):
            raise ValueError("Reused source obligations belong to another source packet")
        files.update({state_path, config_path, packet_path})
        files.update(Path(item["original_path"]) for item in prior_packet["sources"])
        prior, threads = _source_obligations_lineage(
            directory, prior_state, prior_config, packet, files, seen=(*seen, run))
        if digest(prior) != digest(obligations):
            raise ValueError("Reused source obligations changed their canonical content")
        return obligations, threads
    raise ValueError("External/manual source obligations have no native runtime owner; "
                     "graph admission needs an explicit external-provenance mode, not yet supported")


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
        source_required = config.get("require_source_coverage", False)
        if (type(source_required) is not bool
                or state.get("require_source_coverage", False) is not source_required):
            raise ValueError("Extraction source-coverage requirement checkpoint differs")
        source_artifacts = state.get("artifacts", {})
        if not source_required and (
            any(key in source_artifacts for key in ("source_obligations", "source_coverage_review"))
            or any(record.get("role") == "source_obligations"
                   or "source_coverage_review" in record for record in state.get("attempts", []))
        ):
            raise ValueError("Source-coverage lineage cannot be silently downgraded")
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
        owner, assessment = _native_owner(state, paths["review"], "reviewer", context.files)
        attempt = paths["review"].parent
        archives = owner.get("archive_sha256", {})
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
        if source_required:
            obligations, source_threads = _source_obligations_lineage(
                run, state, config, packet, context.files)
            if source_threads & {record.get("thread_id") for record in state.get("attempts", [])
                                 if record.get("role") != "source_obligations"}:
                raise ValueError("Source obligations and candidate stages need independent sessions")
            if source_threads & reused_threads:
                raise ValueError("Source obligations reused a candidate-stage session")
            metadata = source_artifacts.get("source_coverage_review")
            source_review, sidecar = _bound_artifact(
                metadata, SourceCoverageReview, context.files, "Source coverage review")
            if (sidecar != attempt / "source_coverage_review.json"
                    or owner.get("source_coverage_review") != metadata):
                raise ValueError("Source coverage sidecar lacks final reviewer ownership")
            inputs = owner.get("inputs", {})
            if inputs.get("source_obligations", {}).get("sha256") != source_artifacts[
                "source_obligations"]["sha256"]:
                raise ValueError("Reviewer did not consume the frozen source obligations")
            errors = validate_source_coverage_review(
                source_review, obligations, review, workflow, packet)
            if errors or source_coverage_outcome(
                source_review, obligations, review, workflow
            ) != "supported":
                raise ValueError("Extraction source-completeness gate failed: " + "; ".join(errors))
            paths.update(source_obligations=Path(source_artifacts["source_obligations"]["path"]),
                         source_coverage_review=sidecar)
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
