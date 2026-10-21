"""Fresh, bounded native workflow extraction; structural checks never accept science."""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path

from ..codex_worker import DEFAULT_EFFORT, DEFAULT_MODEL, CodexWorker, assess_archived_attempt
from ..run_state import now, read, sha, write
from ..workflow_coverage import (
    CoverageReview,
    IntegrationCoverage,
    coverage_review_outcome,
    coverage_review_view,
    validate_coverage,
    validate_coverage_review,
)
from .authoring import authoring_schema
from .models import (
    ReadingReport,
    Workflow,
    WorkflowDraft,
    WorkflowReview,
    digest,
    review_outcome,
    target_ids,
    validate_evidence,
    validate_reader_gaps,
    validate_repair_gap_references,
    validate_review,
)
from .projection import export_candidate, export_diagnostic
from .obligations import (
    SourceObligations, SourceCoverageReview, validate_source_obligations,
    validate_source_coverage_review, source_coverage_outcome,
)
from .prompts import build_prompt
from .sources import load_packet


async def execute(packet_path: Path, run: Path, *, worker=None,
                  max_revisions: int = 1, mode: str = "specialists",
                  initial_workflow: Path | None = None,
                  repair_review: Path | None = None,
                  repair_feedback_packet: Path | None = None,
                  require_coverage: bool = False,
                  require_source_coverage: bool = False,
                  source_obligations: Path | None = None) -> dict:
    """Run two readers (or the baseline), integration, and independent source review.

    Operational failures return a persisted failed state. Cancellation is persisted
    and propagated after all owned stage tasks stop. There is no recovery/resume or
    transport retry: an incomplete artifact is never reused in a subsequent stage.
    """
    if type(max_revisions) is not int or max_revisions not in (0, 1):
        raise ValueError("Workflow v1 allows max_revisions 0 or 1")
    if mode not in {"specialists", "baseline", "review_only", "repair"}:
        raise ValueError("Unknown workflow extraction mode")
    if type(require_coverage) is not bool or require_coverage and mode != "specialists":
        raise ValueError("require_coverage requires specialists with fresh reader lineage")
    if type(require_source_coverage) is not bool:
        raise ValueError("require_source_coverage must be a boolean")
    if source_obligations is not None and not require_source_coverage:
        raise ValueError("source_obligations requires require_source_coverage")
    if (mode in {"review_only", "repair"}) != (initial_workflow is not None):
        raise ValueError("initial_workflow is required only for review_only or repair modes")
    if (mode == "repair") != (repair_review is not None) or mode == "repair" and not max_revisions:
        raise ValueError("repair requires repair_review and max_revisions=1")
    if repair_feedback_packet is not None and mode != "repair":
        raise ValueError("repair_feedback_packet is allowed only for repair")
    worker = worker or CodexWorker(model=DEFAULT_MODEL, effort=DEFAULT_EFFORT,
                                  web_search=False, network_access=False)
    if isinstance(worker, CodexWorker) and (worker.web_search or worker.network_access):
        raise ValueError("Source-packet runs require web search and network access disabled")
    packet_path, run = Path(packet_path).resolve(), Path(run).resolve()
    if run.exists() and (not run.is_dir() or any(run.iterdir())):
        raise ValueError("Run must be fresh; workflow v1 does not resume existing artifacts")
    run.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    state = {"format_version": "workflow_run/1.0", "run": str(run), "status": "preparing",
             "started_at": now(), "revision": 0, "attempts": [], "artifacts": {},
             "require_coverage": require_coverage,
             "require_source_coverage": require_source_coverage}
    # Exclusive initial creation prevents two orchestrators claiming an empty run.
    with (run / "state.json").open("x", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")
    pins: dict[Path, str] = {}
    active: list[asyncio.Task] = []
    reader_reports: dict[str, ReadingReport] = {}
    coverage_records: dict[Path, IntegrationCoverage | CoverageReview] = {}
    source_coverage_records: dict[Path, SourceCoverageReview] = {}
    obligations = obligations_path = None
    readers = {}
    old_feedback_sources = None
    repair_gap_baseline = None

    def save() -> None:
        state["elapsed_seconds"] = time.monotonic() - started
        usage = {}
        for attempt in state["attempts"]:
            for key, value in attempt.get("usage", {}).items():
                if (isinstance(value, (int, float)) and not isinstance(value, bool)
                        and math.isfinite(value)):
                    usage[key] = usage.get(key, 0) + value
        state["usage"] = usage
        write(run / "state.json", state)

    def pin(path: Path) -> str:
        value = sha(path)
        pins[path] = value
        return value

    def check() -> None:
        for path, expected in pins.items():
            if sha(path) != expected:
                raise ValueError(f"Frozen workflow input/artifact changed: {path}")
        # The copied rendition and the registered original bytes are both checked.
        load_packet(packet_path)
        load_packet(run / "source_packet.json")
        if old_feedback_sources is not None:
            load_packet(repair_feedback_packet)
            load_packet(old_feedback_sources)

    def check_artifact(document, role: str, workflow=None, *, sources=None) -> None:
        sources = packet if sources is None else sources
        errors = validate_evidence(document, sources)
        if role in {"chemistry", "information"}:
            if document.role != role:
                errors.append("Reader role does not match the stage")
            inspected = {(ref.source_id, ref.passage_id) for ref in document.inspected_passages}
            if not inspected:
                errors.append("Reader requires nonempty inspected_passages")
            for finding in document.findings:
                if any((ev.source_id, ev.passage_id) not in inspected for ev in finding.evidence):
                    errors.append(f"Finding {finding.id}: evidence passage was not inspected")
        elif role == "integrator":
            for key in ("method_id", "title", "scope", "variant"):
                if getattr(document, key) != sources[key]:
                    errors.append(f"Workflow {key} differs from frozen source scope")
            if mode not in {"review_only", "repair"}:  # Prior reader lineage is not claimed.
                errors.extend(validate_reader_gaps(document, reader_reports, packet))
            elif mode == "repair" and repair_gap_baseline is not None:
                errors.extend(validate_repair_gap_references(document, repair_gap_baseline))
        elif role == "reviewer":
            errors.extend(validate_review(document, workflow, sources))
        elif role == "source_obligations":
            errors.extend(validate_source_obligations(document, sources))
        if errors:
            raise ValueError("; ".join(errors))

    async def stage(role: str, model, *, inputs: dict[str, Path] | None = None,
                    workflow: Workflow | None = None,
                    coverage: IntegrationCoverage | None = None):
        check()
        index = len(state["attempts"]) + 1
        directory = run / "attempts" / f"{index:03d}_{role}"
        directory.mkdir(parents=True)
        local_inputs = {}
        for name, path in (inputs or {}).items():
            local = directory / f"{name}.json"
            local.write_bytes(path.read_bytes())
            pin(local)
            local_inputs[name] = local
        authoring_kind = None
        if role in {"integrator", "reviewer"}:
            authoring_kind = ("review" if role == "reviewer" else
                              "workflow" if "previous_candidate" in local_inputs else "draft")
        prompt = build_prompt(role, directory, snapshot, packet, schemas[model],
                              inputs=local_inputs,
                              workflow_sha256=digest(workflow) if workflow is not None else None,
                              source_packet_sha256=digest(packet),
                              draft_schema=schemas[WorkflowDraft] if role == "integrator" else None,
                              authoring_schema=(authoring_schemas[authoring_kind]
                                                if authoring_kind else None),
                              review_targets=sorted(target_ids(workflow)) if workflow else (),
                              skill_path=skill_snapshot,
                              coverage_schema=schemas.get(IntegrationCoverage),
                              coverage_review_schema=schemas.get(CoverageReview),
                              coverage_sha256=digest(coverage) if coverage else None,
                              coverage_readers=readers if require_coverage else None,
                              source_coverage_schema=schemas.get(SourceCoverageReview),
                              source_obligations_sha256=(digest(obligations)
                                                         if obligations else None))
        prompt_path = directory / "stage_prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        pin(prompt_path)
        attempt = {"role": role, "directory": str(directory), "revision": state["revision"],
                   "started_at": now(), "status": "running", "inputs": {
                       key: {"path": str(path), "sha256": pins[path]}
                       for key, path in local_inputs.items()}, "prompt_sha256": pins[prompt_path]}
        state["attempts"].append(attempt)
        save()
        stage_started = time.monotonic()
        result = None
        try:
            result = await worker.run(prompt, directory)
            assessment = assess_archived_attempt(directory)
            attempt.update(thread_id=assessment.thread_id, usage=assessment.usage,
                           completed_turns=assessment.completed_turns,
                           transport_complete=assessment.complete)
            if not assessment.complete:
                raise ValueError(assessment.error or "Native transport did not complete")
            if (type(result.exit_code) is not int or result.exit_code != 0
                    or result.error is not None):
                raise ValueError("Native worker reported an unsuccessful attempt")
            if result.thread_id != assessment.thread_id or result.usage != assessment.usage:
                raise ValueError("Native worker result disagrees with archived transport evidence")
            if not assessment.thread_id or any(
                other is not attempt and other.get("thread_id") == assessment.thread_id
                for other in state["attempts"]
            ):
                raise ValueError("Each stage must have a fresh native thread")
            worker_prompt = directory / "prompt.txt"
            if worker_prompt.exists():
                if worker_prompt.read_text(encoding="utf-8") != prompt:
                    raise ValueError("Archived native prompt differs from stage instructions")
                pin(worker_prompt)
            attempt["archive_sha256"] = {
                name: pin(directory / name) for name in
                ("result.json", "events.jsonl", "final.txt", "command.json", "started.json",
                 "stderr.log") if (directory / name).is_file()}
            check()
            artifact = directory / "artifact.json"
            document = model.model_validate_json(artifact.read_bytes())
            check_artifact(document, role, workflow)
            if require_coverage and role in {"integrator", "reviewer"}:
                sidecar = directory / ("coverage.json" if role == "integrator"
                                       else "coverage_review.json")
                sidecar_model = IntegrationCoverage if role == "integrator" else CoverageReview
                extra = sidecar_model.model_validate_json(sidecar.read_bytes())
                if role == "integrator":
                    errors = validate_coverage(extra, document, reader_reports, packet)
                else:
                    errors = validate_coverage_review(extra, coverage, document, workflow,
                                                      reader_reports, packet)
                if errors:
                    raise ValueError("Completion coverage: " + "; ".join(errors))
                sidecar_sha256 = pin(sidecar)
                coverage_records[sidecar] = extra
                key = "coverage" if role == "integrator" else "coverage_review"
                attempt[key] = {"path": str(sidecar), "sha256": sidecar_sha256,
                                "document_sha256": digest(extra)}
                state["artifacts"][key] = attempt[key]
            if require_source_coverage and role == "reviewer":
                sidecar = directory / "source_coverage_review.json"
                extra = SourceCoverageReview.model_validate_json(sidecar.read_bytes())
                errors = validate_source_coverage_review(
                    extra, obligations, document, workflow, packet)
                if errors:
                    raise ValueError("Source completeness: " + "; ".join(errors))
                source_coverage_records[sidecar] = extra
                attempt["source_coverage_review"] = {
                    "path": str(sidecar), "sha256": pin(sidecar),
                    "document_sha256": digest(extra)}
                state["artifacts"]["source_coverage_review"] = attempt["source_coverage_review"]
            attempt.update(status="validated", artifact=str(artifact),
                           artifact_sha256=pin(artifact), document_sha256=digest(document))
            state["artifacts"][role] = {"path": str(artifact), "sha256": pins[artifact]}
            return document, artifact
        except asyncio.CancelledError:
            attempt["status"] = "cancelled"
            raise
        except Exception as exc:
            attempt.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            # The worker writes native result metadata even on timeout/cancellation.
            archive = directory / "result.json"
            if archive.is_file():
                try:
                    metadata = read(archive)
                    if not isinstance(metadata, dict):
                        raise ValueError("Invalid worker metadata")
                    elapsed = metadata.get("elapsed_seconds")
                    if (not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool)
                            or not math.isfinite(elapsed) or elapsed < 0):
                        elapsed = time.monotonic() - stage_started
                    attempt.update(elapsed_seconds=elapsed,
                                   usage=metadata.get("usage") if isinstance(
                                       metadata.get("usage"), dict) else {},
                                   exit_code=metadata.get("exit_code"),
                                   transport_error=metadata.get("error"))
                except (OSError, ValueError, UnicodeDecodeError):
                    pass
            elif result is not None:
                attempt.update(elapsed_seconds=getattr(result, "elapsed_seconds", None),
                               usage=getattr(result, "usage", {}))
            attempt.setdefault("elapsed_seconds", time.monotonic() - stage_started)
            attempt["finished_at"] = now()
            save()

    try:
        packet_sha256 = pin(packet_path)
        packet = load_packet(packet_path)
        snapshot = run / "source_packet.json"
        snapshot.write_bytes(packet_path.read_bytes())
        snapshot_sha256 = pin(snapshot)
        schemas = {}
        models = [ReadingReport, Workflow, WorkflowDraft, WorkflowReview]
        if require_coverage:
            models.extend((IntegrationCoverage, CoverageReview))
        if require_source_coverage:
            models.extend((SourceObligations, SourceCoverageReview))
        for model in models:
            path = run / "schemas" / f"{model.__name__}.schema.json"
            write(path, model.model_json_schema())
            pin(path)
            schemas[model] = path
        authoring_schemas = {}
        for kind in ("draft", "workflow", "review"):
            path = run / "schemas" / f"{kind.capitalize()}Authoring.schema.json"
            write(path, authoring_schema(kind))
            pin(path)
            authoring_schemas[kind] = path
        package = Path(__file__).resolve().parent
        src = package.parents[2]
        implementation = []
        code_files = [*package.glob("*.py"), *(package.parent / name for name in (
            "codex_worker.py", "run_state.py", "document_manifest.py", "workflow_coverage.py",
            "evidence_views.py")),
            package.parent.parent / "core" / "artifacts.py"]
        for path in sorted(code_files):
            original_sha256 = pin(path)
            copy = run / "implementation" / path.relative_to(src)
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(path.read_bytes())
            implementation.append({"path": str(path), "sha256": original_sha256,
                                   "snapshot": str(copy), "snapshot_sha256": pin(copy)})
        skill_snapshot = None
        if require_coverage:
            skill = src.parent / "skills" / "assay-extraction" / "SKILL.md"
            skill_sha256 = pin(skill)
            skill_snapshot = run / "instructions" / "assay_extraction_SKILL.md"
            skill_snapshot.parent.mkdir(parents=True, exist_ok=True)
            skill_snapshot.write_bytes(skill.read_bytes())
            snapshot_skill_sha256 = pin(skill_snapshot)
            implementation.extend((
                {"name": "assay_extraction_skill", "path": str(skill),
                 "sha256": skill_sha256, "snapshot": str(skill_snapshot),
                 "snapshot_sha256": snapshot_skill_sha256},
                {"name": "assay_extraction_skill_snapshot", "path": str(skill_snapshot),
                 "sha256": snapshot_skill_sha256},
            ))
        # Freeze source obligations before copying or passing any seed/candidate.
        # This is one source pass, independent of optional specialist readers.
        obligations_input = None
        if require_source_coverage:
            if source_obligations is None:
                obligations, obligations_path = await stage("source_obligations", SourceObligations)
            else:
                origin = Path(source_obligations).resolve()
                original_digest = pin(origin)
                obligations = SourceObligations.model_validate_json(origin.read_bytes())
                check_artifact(obligations, "source_obligations")
                obligations_path = run / "source_obligations.json"
                obligations_path.write_bytes(origin.read_bytes())
                pin(obligations_path)
                obligations_input = {"path": str(origin), "sha256": original_digest}
            state["artifacts"]["source_obligations"] = {
                "path": str(obligations_path), "sha256": pins[obligations_path],
                "document_sha256": digest(obligations)}
            save()
        seed = None
        if initial_workflow is not None:
            initial_workflow = Path(initial_workflow).resolve()
            initial_sha256 = pin(initial_workflow)
            candidate = run / "initial_workflow.json"
            candidate.write_bytes(initial_workflow.read_bytes())
            seed = {"path": str(initial_workflow), "sha256": initial_sha256,
                    "snapshot": str(candidate), "snapshot_sha256": pin(candidate),
                    "role": "unaccepted_candidate", "prior_transport_verified": False,
                    "prior_review_inherited": False}
            state["artifacts"]["initial_workflow"] = {
                "path": str(candidate), "sha256": pins[candidate]}
        feedback = None
        if repair_review is not None:
            repair_review = Path(repair_review).resolve()
            review_sha256 = pin(repair_review)
            feedback_path = run / "repair_review.json"
            feedback_path.write_bytes(repair_review.read_bytes())
            feedback = {"path": str(repair_review), "sha256": review_sha256,
                        "snapshot": str(feedback_path), "snapshot_sha256": pin(feedback_path),
                        "role": "repair_instructions", "prior_transport_verified": False,
                        "prior_verdict_inherited": False}
            state["artifacts"]["repair_review"] = {
                "path": str(feedback_path), "sha256": pins[feedback_path]}
        feedback_sources = None
        prior_packet = packet
        if repair_feedback_packet is not None:
            repair_feedback_packet = Path(repair_feedback_packet).resolve()
            feedback_packet_sha256 = pin(repair_feedback_packet)
            prior_packet = load_packet(repair_feedback_packet)
            if any(prior_packet[key] != packet[key]
                   for key in ("method_id", "title", "scope", "variant")):
                raise ValueError("Expanded packet differs from feedback method/title/scope/variant")
            current_sources = {source["source_id"]: source["original_sha256"]
                               for source in packet["sources"]}
            if any(current_sources.get(source["source_id"]) != source["original_sha256"]
                   for source in prior_packet["sources"]):
                raise ValueError("Expanded packet removed or replaced a feedback source")
            old_feedback_sources = run / "old_feedback_sources.json"
            old_feedback_sources.write_bytes(repair_feedback_packet.read_bytes())
            feedback_sources = {
                "path": str(repair_feedback_packet), "sha256": feedback_packet_sha256,
                "snapshot": str(old_feedback_sources), "snapshot_sha256": pin(old_feedback_sources),
                "source_packet_digest": digest(prior_packet), "role": "prior_feedback_context",
                "prior_verdict_inherited": False,
                "originals": [{"path": source["original_path"],
                               "sha256": pin(Path(source["original_path"]))}
                              for source in prior_packet["sources"]]}
            state["artifacts"]["old_feedback_sources"] = {
                "path": str(old_feedback_sources), "sha256": pins[old_feedback_sources]}
        config = {"format_version": "workflow_run_config/1.0", "method_id": packet["method_id"],
                  "packet_path": str(packet_path), "packet_sha256": packet_sha256,
                  "source_packet": str(snapshot), "source_packet_sha256": snapshot_sha256,
                  "source_packet_digest": digest(packet), "implementation": implementation,
                  "mode": mode, "max_revisions": max_revisions,
                  "require_coverage": require_coverage,
                  "require_source_coverage": require_source_coverage,
                  "source_obligations_input": obligations_input,
                  "initial_workflow": seed, "repair_review": feedback,
                  "repair_feedback_packet": feedback_sources,
                  "model": getattr(worker, "model", DEFAULT_MODEL),
                  "effort": getattr(worker, "effort", DEFAULT_EFFORT),
                  "timeout_seconds": getattr(worker, "timeout_seconds", None),
                  "web_search": getattr(worker, "web_search", False),
                  "network_access": getattr(worker, "network_access", False),
                  "authoring_schemas": {kind: {"path": str(path), "sha256": pins[path]}
                                        for kind, path in authoring_schemas.items()},
                  "schemas": {model.__name__: {"path": str(path), "sha256": pins[path]}
                              for model, path in schemas.items()}}
        write(run / "config.json", config)
        state["config_sha256"] = pin(run / "config.json")
        state["status"] = "running"
        save()
        check()
        readers = {}
        if mode == "specialists":
            active = [asyncio.create_task(stage(role, ReadingReport))
                      for role in ("chemistry", "information")]
            try:
                reports = await asyncio.gather(*active)
            except BaseException:
                for task in active:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
                raise
            active = []
            readers = {role: output[1] for role, output in
                       zip(("chemistry", "information"), reports, strict=True)}
            reader_reports = {role: output[0] for role, output in
                              zip(("chemistry", "information"), reports, strict=True)}
        if mode in {"review_only", "repair"}:
            workflow = Workflow.model_validate_json(candidate.read_bytes())
            if mode == "repair":
                repair_gap_baseline = workflow
            check_artifact(workflow, "integrator", sources=prior_packet)
            if mode == "repair":
                prior_review = WorkflowReview.model_validate_json(feedback_path.read_bytes())
                check_artifact(prior_review, "reviewer", workflow, sources=prior_packet)
                if review_outcome(prior_review, workflow) != "needs_revision":
                    raise ValueError("Repair feedback must request record revision")
                state["revision"] = 1
                save()
                workflow, candidate = await stage("integrator", Workflow, inputs={
                    "previous_candidate": candidate, "previous_review": feedback_path,
                    **({"old_feedback_sources": old_feedback_sources}
                       if old_feedback_sources is not None else {})})
        else:
            workflow, candidate = await stage("integrator", Workflow, inputs=readers)
        while True:
            state.update(workflow=str(candidate), workflow_sha256=sha(candidate),
                         workflow_digest=digest(workflow))
            save()
            reviewer_inputs = {"candidate": candidate}
            if require_source_coverage:
                reviewer_inputs["source_obligations"] = obligations_path
            coverage = None
            if require_coverage:
                coverage_path = candidate.parent / "coverage.json"
                coverage = coverage_records[coverage_path]
                # Archive every finding; paginated views retain exact claims/evidence.
                pages = [coverage_review_view(coverage, workflow, reader_reports, packet,
                                              start=index, limit=1)
                         for index in range(len(coverage.entries))]
                finding_view = candidate.parent / "finding_view.json"
                write(finding_view, {"format_version": "coverage_finding_pages/1.0",
                                     "pages": pages})
                pin(finding_view)
                reviewer_inputs.update(coverage=coverage_path, finding_view=finding_view)
            review, review_path = await stage("reviewer", WorkflowReview,
                                             inputs=reviewer_inputs, workflow=workflow,
                                             coverage=coverage)
            check()
            check_artifact(workflow, "integrator")
            check_artifact(review, "reviewer", workflow)
            outcome = review_outcome(review, workflow)
            if require_coverage:
                coverage_review_path = review_path.parent / "coverage_review.json"
                coverage_review = coverage_records[coverage_review_path]
                outcome = coverage_review_outcome(coverage_review, review, workflow)
            if require_source_coverage:
                source_coverage_path = review_path.parent / "source_coverage_review.json"
                source_result = source_coverage_outcome(
                    source_coverage_records[source_coverage_path], obligations, review, workflow)
                # Both independent gates must permit support. Research is not a
                # request for a futile candidate-only repair.
                if "research_needed" in (outcome, source_result):
                    outcome = "research_needed"
                elif "needs_revision" in (outcome, source_result):
                    outcome = "needs_revision"
            state.update(review=str(review_path), review_sha256=sha(review_path), status=outcome)
            save()
            if outcome != "needs_revision" or state["revision"] >= max_revisions:
                break
            state.update(revision=state["revision"] + 1, status="running")
            save()
            workflow, candidate = await stage("integrator", Workflow, inputs={
                **readers, "previous_candidate": candidate, "previous_review": review_path,
                **({"previous_coverage": coverage_path,
                    "previous_coverage_review": coverage_review_path} if require_coverage else {}),
                **({"source_obligations": obligations_path,
                    "previous_source_coverage_review": source_coverage_path}
                   if require_source_coverage else {})})
        if outcome == "supported":
            export_candidate(workflow, review, packet, run)
            outputs = (("graph_candidate", "graph_candidate.json"), ("protocol", "protocol.txt"),
                       ("overview", "overview.json"), ("overview_text", "overview.txt"),
                       ("overview_html", "overview.html"))
        elif review_outcome(review, workflow) != "supported":
            export_diagnostic(workflow, review, packet, run)
            outputs = (("graph_diagnostic", "graph_diagnostic.json"),
                       ("protocol_diagnostic", "protocol_diagnostic.txt"),
                       ("overview_diagnostic", "overview_diagnostic.json"),
                       ("overview_diagnostic_text", "overview_diagnostic.txt"),
                       ("overview_diagnostic_html", "overview_diagnostic.html"))
        else:
            # A positive primary review cannot advertise coverage-rejected support.
            outputs = ()
        for key, name in outputs:
            path = run / name
            value = pin(path)
            state.update({key: str(path), key + "_sha256": value})
            state["artifacts"][key] = {"path": str(path), "sha256": value}
        check()
        state["finished_at"] = now()
        save()
        return state
    except asyncio.CancelledError:
        state.update(status="cancelled", finished_at=now(), error="Execution cancelled")
        save()
        raise
    except Exception as exc:
        state.update(status="failed", finished_at=now(), error=f"{type(exc).__name__}: {exc}")
        save()
        return state
