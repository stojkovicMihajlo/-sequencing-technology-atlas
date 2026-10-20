"""Four fresh source-bound stages for reference construction, not blind evaluation."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from genomics_discovery.curation.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.assemble import assemble_workflow
from genomics_discovery.curation.workflow_extraction.models import (
    Workflow, WorkflowDraft, WorkflowReview, digest, review_outcome, validate_evidence, validate_review,
)
from genomics_discovery.curation.workflow_extraction.projection import export_candidate
from genomics_discovery.curation.workflow_extraction.sources import load_packet

from .models import GraphReviewAssessmentV2, ProtocolReference, ReferenceReview, parse_assessment
from .prompts import DEFAULT_DIRECTORY, ROLES, build_prompt
from .scoring import score_assessment
from .validation import (
    reference_review_outcome, validate_assessment, validate_reference, validate_reference_review,
)

MODELS = {"reference_annotator": ProtocolReference, "reference_reviewer": ReferenceReview,
          "graph_constructor": WorkflowDraft, "graph_reviewer": GraphReviewAssessmentV2}
BOUNDARY = (
    "Model-reviewed reference construction and self-consistency on fixed source packets. "
    "The graph constructor sees the reference; this is not blind extraction-harness "
    "performance, expert-adjudicated ground truth or production atlas publication."
)
ARCHIVES = ("artifact.json", "prompt.txt", "command.json", "started.json", "events.jsonl",
            "stderr.log", "final.txt", "result.json")


class StageFailure(ValueError):
    def __init__(self, category, message):
        self.category = category
        super().__init__(message)


class DataIssue(StageFailure):
    pass


def verify_pins(pins):
    for name, expected in pins.items():
        path = Path(name)
        if not path.is_file() or sha(path) != expected:
            raise StageFailure("frozen_input_changed", f"Frozen file changed: {path}")


def _usage(attempts):
    total = Counter()
    for attempt in attempts:
        if attempt.get("transport_complete"):
            total.update({key: value for key, value in attempt.get("usage", {}).items()
                          if isinstance(value, (int, float)) and not isinstance(value, bool)})
    return dict(total)


class CaseSession:
    def __init__(self, row, run, pins, prompts, used_threads, worker_factory, prepare_only):
        self.row, self.run, self.pins = row, run, dict(pins)
        self.prompts, self.used_threads = prompts, used_threads
        self.worker_factory, self.prepare_only = worker_factory, prepare_only
        self.state = {"case_id": row["case_id"], "status": "preparing", "stage": "source",
                      "started_at": now(), "attempts": [], "repairs_used": 0,
                      "graph_repairs_used": 0, "graph_rounds": [], "boundary": BOUNDARY}

    def persist(self):
        self.state["usage"] = _usage(self.state["attempts"])
        self.state["completed_native_sessions"] = sum(
            bool(attempt.get("transport_complete")) for attempt in self.state["attempts"])
        write(self.run / "state.json", self.state)
        write(self.run / "bindings.json", self.pins)

    def freeze(self, name, value):
        path = self.run / name
        if path.exists():
            raise FileExistsError(f"Refuse to replace a pilot artifact: {path}")
        write(path, value)
        self.pins[str(path)] = sha(path)
        return path

    async def stage(self, role, inputs, *, repair=False):
        self.state["stage"] = role
        verify_pins(self.pins)
        attempt = self.run / "attempts" / f"{len(self.state['attempts']) + 1:03d}_{role}"
        attempt.mkdir(parents=True)
        schema = self.run / "schemas" / f"{role}.json"
        bindings = {"source_packet_sha256": digest(self.packet)}
        if "reference" in inputs:
            reference = ProtocolReference.model_validate_json(Path(inputs["reference"]).read_bytes())
            bindings["reference_sha256"] = digest(reference)
        if "workflow" in inputs:
            workflow = Workflow.model_validate_json(Path(inputs["workflow"]).read_bytes())
            bindings["workflow_sha256"] = digest(workflow)
        if role == "graph_constructor" and repair:
            for field, parser in (("previous_artifact", WorkflowDraft.model_validate_json),
                ("previous_workflow", Workflow.model_validate_json), ("previous_workflow_review", WorkflowReview.model_validate_json),
                ("previous_assessment", parse_assessment), ("previous_scores", json.loads)):
                bindings[field + "_sha256"] = digest(parser(Path(inputs[field]).read_bytes()))
        prompt = build_prompt(role, directory=self.prompts, attempt=attempt, schema=schema,
            packet=Path(self.row["packet"]), case_id=self.row["case_id"],
            metadata=json.dumps({key: self.packet[key] for key in
                ("method_id", "title", "scope", "variant")}, ensure_ascii=False),
            inputs=inputs, bindings=bindings, repair=repair)
        self.freeze(attempt.relative_to(self.run) / "instruction.json",
                    {"prompt": prompt, "canonical_object_digests": bindings})
        self.freeze(attempt.relative_to(self.run) / "input_bindings.json", self.pins)
        record = {"role": role, "attempt": str(attempt), "started_at": now(),
                  "status": "running", "transport_complete": False, "data_repair": repair}
        self.state["attempts"].append(record)
        self.persist()
        try:
            worker = self.worker_factory() if self.worker_factory else CodexWorker(
                model="gpt-6.1-sol", effort="xhigh", timeout_seconds=3600,
                web_search=False, network_access=False)
            if isinstance(worker, CodexWorker) and (worker.web_search or worker.network_access):
                raise StageFailure("network_policy", "Native benchmark work uses a fixed source corpus")
            result = await worker.run(prompt, attempt)
            checked = assess_archived_attempt(attempt)
            if (not checked.complete or not checked.thread_id or result.exit_code != 0
                    or result.error is not None or result.thread_id != checked.thread_id
                    or result.usage != checked.usage or checked.thread_id in self.used_threads
                    or (attempt / "prompt.txt").read_text() != prompt):
                raise StageFailure("native_transport", "Expected a complete, unchanged, fresh native archive")
            self.used_threads.add(checked.thread_id)
            record.update(transport_complete=True, thread_id=checked.thread_id,
                          usage=checked.usage, elapsed_seconds=result.elapsed_seconds)
            verify_pins(self.pins)
            if not (attempt / "artifact.json").is_file():
                raise StageFailure("missing_artifact", "A final message cannot replace the JSON artifact")
            if any(not (attempt / name).is_file() for name in ARCHIVES):
                raise StageFailure("native_archive_missing", "Native transport archive is incomplete")
            self.pins.update({str(attempt / name): sha(attempt / name) for name in ARCHIVES})
            try:
                model = MODELS[role].model_validate_json((attempt / "artifact.json").read_bytes())
            except ValidationError as exc:
                raise DataIssue("artifact_schema", json.dumps(
                    exc.errors(include_input=False, include_url=False), default=str)) from exc
            record.update(status="complete", artifact_sha256=sha(attempt / "artifact.json"),
                          artifact_digest=digest(model))
            return model, attempt / "artifact.json"
        except BaseException as exc:
            record.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                          error_category=getattr(exc, "category", "execution"),
                          error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            record["finished_at"] = now()
            self.persist()

    async def reference(self):
        previous, feedback = None, None
        while True:
            inputs = {"previous_artifact": previous, "feedback": feedback} if feedback else {}
            try:
                reference, artifact = await self.stage("reference_annotator", inputs,
                                                        repair=feedback is not None)
                errors = validate_reference(reference, self.packet)
                if reference.case_id != self.row["case_id"]:
                    errors.append("Reference case_id differs from assigned case")
                if errors:
                    raise DataIssue("reference_integrity", "; ".join(errors))
            except DataIssue as exc:
                if self.state["repairs_used"]:
                    raise
                self.state["repairs_used"] = 1
                previous = Path(self.state["attempts"][-1]["attempt"]) / "artifact.json"
                feedback = self.freeze("repair_feedback.json", {
                    "category": exc.category, "integrity_errors": [str(exc)],
                    "instruction": "Correct the published schema/integrity issues using the same sources."})
                continue
            review, review_path = await self.stage("reference_reviewer", {"reference": artifact})
            if errors := validate_reference_review(review, reference, self.packet):
                raise DataIssue("reference_review_integrity", "; ".join(errors))
            outcome = reference_review_outcome(review)
            self.state["reference_review_outcome"] = outcome
            if outcome == "needs_revision" and not self.state["repairs_used"]:
                self.state["repairs_used"] = 1
                previous = artifact
                feedback = self.freeze("repair_feedback.json", {
                    "category": "reference_review_needs_revision", "review": str(review_path),
                    "instruction": "Assess the review against the same sources and correct justified issues."})
                continue
            if outcome != "supported_reference_candidate":
                self.state["status"] = "reference_needs_revision" if outcome == "needs_revision" else "reference_uncertain"
                return None
            reference_path = self.freeze("reference.json", reference.model_dump(mode="json"))
            self.freeze("reference_review.json", review.model_dump(mode="json"))
            self.state.update(reference_sha256=digest(reference),
                              reference_file_sha256=sha(reference_path),
                              reference_annotation_status="model_reviewed_reference_candidate")
            return reference, reference_path

    async def graph(self, reference, reference_path):
        """One source-bound correction, followed by a new independent review."""
        last = None
        inputs = {"reference": reference_path}
        try:
            for number in (1, 2):
                draft, draft_path = await self.stage("graph_constructor", inputs, repair=number == 2)
                try:
                    workflow = assemble_workflow(draft, require_bindings=True)
                except ValueError as exc:
                    raise DataIssue("graph_assembly", str(exc)) from exc
                errors = validate_evidence(workflow, self.packet)
                errors.extend(f"Workflow {field} differs from frozen packet" for field in
                    ("method_id", "title", "scope", "variant") if getattr(workflow, field) != self.packet[field])
                if errors:
                    raise DataIssue("graph_integrity", "; ".join(errors))
                directory = Path("graph_rounds") / f"{number:03d}"
                workflow_path = self.freeze(directory / "workflow.json", workflow.model_dump(mode="json"))
                combined, _ = await self.stage("graph_reviewer", {"reference": reference_path, "workflow": workflow_path})
                errors = validate_review(combined.workflow_review, workflow, self.packet)
                errors.extend(validate_assessment(combined.assessment, reference, workflow, self.packet))
                if errors:
                    raise DataIssue("graph_review_integrity", "; ".join(errors))
                review_path = self.freeze(directory / "workflow_review.json", combined.workflow_review.model_dump(mode="json"))
                assessment_path = self.freeze(directory / "assessment.json", combined.assessment.model_dump(mode="json"))
                scores = score_assessment(combined.assessment, reference, workflow, self.packet)
                scores_path = self.freeze(directory / "scores.json", scores)
                outcome = review_outcome(combined.workflow_review, workflow)
                critical = scores["obligation_coverage"]["critical_not_covered"]
                unknowns = scores["unknown_preservation"]
                revision = bool(unknowns["counts"]["reference_revision_needed"])
                unsupported = [item.id for item in combined.assessment.factual_audits
                               if item.verdict == "unsupported"]
                status = ("reference_unknown_revision_needed" if revision else
                    "factual_assertions_unsupported" if unsupported else
                    "graph_needs_revision" if outcome == "needs_revision" else
                    "graph_uncertain" if outcome != "supported" else
                    "critical_obligations_not_covered" if critical else
                    "reference_unknowns_not_retained" if unknowns["status"] not in {"retained", "not_applicable"}
                    else "model_reviewed_reference_candidate")
                self.state.update(status=status, workflow_review_outcome=outcome, critical_not_covered=critical,
                    unsupported_factual_audit_ids=unsupported,
                    unknown_preservation=unknowns["status"], unknowns_not_fully_retained=unknowns["not_fully_retained_ids"])
                self.state["graph_rounds"].append({"round": number, "directory": str(self.run / directory),
                    "status": status, "unsupported_factual_audit_ids": unsupported,
                    "workflow_sha256": digest(workflow), "assessment_sha256": digest(combined.assessment),
                    "workflow_review_sha256": digest(combined.workflow_review), "scores_sha256": digest(scores)})
                last = workflow, combined, scores, number
                self.persist()
                # Unassessed alone is evaluator incompleteness, not a graph defect.
                # A reference revision needs new adjudication, never a graph patch.
                correctable = (bool(unsupported) or outcome == "needs_revision" or any(item.obligation_id in critical
                    and item.outcome in {"missing", "partial"} for item in combined.assessment.obligations)
                    or any(item.outcome in {"missing", "partial"} for item in combined.assessment.unknown_checks))
                if number == 2 or revision or (outcome == "research_needed" and not unsupported) or not correctable:
                    break
                self.state["graph_repairs_used"] = 1
                feedback = self.freeze("graph_correction_feedback.json", {"category": "graph_correction",
                    "workflow_review_outcome": outcome, "critical_not_covered": critical,
                    "unsupported_factual_audit_ids": unsupported,
                    "unknown_preservation": unknowns,
                    "instruction": "Assess the exact prior draft/workflow/review/assessment/scores against the unchanged sources and reference. Correct justified graph defects or remove/qualify unsupported claims. Preserve supported obligations and genuine uncertainty; do not invent missing facts or tailor the record for approval. One fresh independent graph review is required."})
                inputs = {"reference": reference_path, "previous_artifact": draft_path,
                    "previous_workflow": workflow_path, "previous_workflow_review": review_path,
                    "previous_assessment": assessment_path, "previous_scores": scores_path, "feedback": feedback}
        finally:
            # Even a failed correction keeps the last fully reviewed negative
            # round available to existing reporters; no canonical file is replaced.
            if last is not None:
                workflow, combined, scores, number = last
                for name, value in (("workflow", workflow), ("workflow_review", combined.workflow_review),
                        ("assessment", combined.assessment), ("scores", scores)):
                    self.freeze(name + ".json", value if isinstance(value, dict) else value.model_dump(mode="json"))
                self.state["selected_graph_round"] = number
        return last

    async def execute(self):
        self.run.mkdir(parents=True, exist_ok=False)
        self.persist()
        try:
            verify_pins(self.pins)
            packet_path = Path(self.row["packet"])
            if sha(packet_path) != self.row["packet_sha256"]:
                raise StageFailure("packet_byte_hash", "Declared source packet byte hash differs")
            self.packet = load_packet(packet_path)
            self.pins[str(packet_path)] = sha(packet_path)
            for source in self.packet["sources"]:
                original = Path(source["original_path"])
                self.pins[str(original)] = source["original_sha256"]
            verify_pins(self.pins)
            self.freeze("input.json", {"case_id": self.row["case_id"], "packet": str(packet_path),
                "packet_sha256": self.row["packet_sha256"], "source_packet_sha256": digest(self.packet),
                **{key: self.packet[key] for key in ("method_id", "title", "scope", "variant")}})
            for role, model in MODELS.items():
                self.freeze(Path("schemas") / f"{role}.json", model.model_json_schema())
            if self.prepare_only:
                self.state.update(status="prepared", stage="prepared")
                return self.state
            accepted = await self.reference()
            if accepted is None:
                return self.state
            reference, reference_path = accepted
            workflow, combined, _, _ = await self.graph(reference, reference_path)
            if self.state["status"] == "model_reviewed_reference_candidate":
                verify_pins(self.pins)
                export_candidate(workflow, combined.workflow_review, self.packet, self.run / "projection")
                for name in ("graph_candidate.json", "protocol.txt"):
                    path = self.run / "projection" / name
                    self.pins[str(path)] = sha(path)
                self.state.update(status="model_reviewed_reference_candidate",
                    workflow_sha256=digest(workflow), assessment_sha256=digest(combined.assessment),
                    obligation_completeness=("complete" if all(item.outcome == "covered" for item in
                        combined.assessment.obligations) else "partial"),
                    projection=str(self.run / "projection"))
            verify_pins(self.pins)
        except asyncio.CancelledError:
            self.state.update(status="cancelled", issue_stage=self.state["stage"])
            raise
        except Exception as exc:
            self.state.update(status=f"{self.state['stage']}_failed", issue_stage=self.state["stage"],
                error_category=getattr(exc, "category", "execution"),
                error=f"{type(exc).__name__}: {exc}")
        finally:
            self.state["finished_at"] = now()
            self.persist()
        return self.state


async def run_batch(manifest_path, output, *, concurrency=4, case_ids=(), prepare_only=False,
                    worker_factory=None, prompt_directory=DEFAULT_DIRECTORY, driver_path=None):
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError("Use positive integer concurrency")
    if not prepare_only and worker_factory is None and not os.environ.get("SLURM_JOB_ID"):
        raise RuntimeError("Native reference construction requires a Sherlock Slurm allocation")
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    manifest = json.loads(manifest_path.read_bytes())
    rows = manifest.get("cases")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Manifest requires a nonempty cases list")
    ids = [row.get("case_id") for row in rows]
    if (any(not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", ident)
            for ident in ids) or len(set(ids)) != len(ids)):
        raise ValueError("Manifest case IDs must be distinct safe path components")
    if set(case_ids) - set(ids):
        raise ValueError("Requested case IDs are absent from the manifest")
    rows = [dict(row) for row in rows]
    report = manifest.get("source_report")
    report_pins = {}
    if report is not None:
        if (not isinstance(report, dict) or not isinstance(report.get("path"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", str(report.get("sha256", "")))):
            raise ValueError("Declared source report requires a path and file-byte SHA-256")
        report_path = (manifest_path.parent / report["path"]).resolve()
        report_pins[str(report_path)] = report["sha256"]
        verify_pins(report_pins)
        if any(not row.get("source_report") or
               (manifest_path.parent / row["source_report"]).resolve() != report_path for row in rows):
            raise ValueError("Each case source_report must match the globally bound source report")
        report = {"path": str(report_path), "sha256": report["sha256"]}
        for row in rows:
            row["source_report"] = str(report_path)
    rows = [row for row in rows if not case_ids or row["case_id"] in case_ids]
    for row in rows:
        if not isinstance(row.get("packet_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["packet_sha256"]):
            raise ValueError("Each case requires a declared packet file-byte SHA-256")
        row["packet"] = str((manifest_path.parent / row["packet"]).resolve())
    output.mkdir(parents=True, exist_ok=False)
    templates = Path(prompt_directory).resolve()
    root = Path(__file__).resolve().parents[4]
    code = [*root.joinpath("src/genomics_discovery").rglob("*.py"),
            Path(driver_path).resolve() if driver_path else root / "scripts/benchmark/run_reference_batch.py"]
    files = [manifest_path, *code, *(templates / name for name in ROLES.values())]
    pins = {str(path): sha(path) for path in files}
    pins.update(report_pins)
    write(output / "manifest.json", {"cases": rows, **({"source_report": report} if report is not None else {})})
    pins[str(output / "manifest.json")] = sha(output / "manifest.json")
    write(output / "bindings.json", pins)
    state = {"status": "running", "started_at": now(), "concurrency": concurrency,
             "model": "gpt-6.1-sol", "effort": "xhigh", "cases": {}, "boundary": BOUNDARY,
             "job_id": os.environ.get("SLURM_JOB_ID"), "prepare_only": prepare_only}
    state["source_report_binding"] = "declared_sha256_verified" if report_pins else "legacy_unbound"
    write(output / "state.json", state)
    semaphore, used_threads = asyncio.Semaphore(concurrency), set()

    async def case(row):
        async with semaphore:
            session = CaseSession(row, output / "cases" / row["case_id"], pins, templates,
                                  used_threads, worker_factory, prepare_only)
            state["cases"][row["case_id"]] = await session.execute()
            write(output / "state.json", state)

    tasks = [asyncio.create_task(case(row)) for row in rows]
    try:
        await asyncio.gather(*tasks)
        verify_pins(pins)
        statuses = Counter(item["status"] for item in state["cases"].values())
        good = "prepared" if prepare_only else "model_reviewed_reference_candidate"
        state.update(status=("prepared" if prepare_only else "completed") if set(statuses) == {good}
                     else "finished_with_failures", counts=dict(statuses))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        state["status"] = "cancelled_or_batch_integrity_failure"
        raise
    finally:
        attempts = [attempt for case in state["cases"].values() for attempt in case["attempts"]]
        state.update(finished_at=now(), usage=_usage(attempts),
            completed_native_sessions=sum(bool(attempt.get("transport_complete")) for attempt in attempts),
            distinct_native_threads=len(used_threads),
            usage_scope="Sum of separately assessed completed native sessions, including invalid data artifacts.")
        write(output / "state.json", state)
        write(output / "summary.json", state)
    return state
