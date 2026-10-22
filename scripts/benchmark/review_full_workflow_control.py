"""Challenge the actual Workflow reviewer once; expected defects stay host-only."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from genomics_discovery.curation.codex_worker import CodexWorker, assess_archived_attempt
from genomics_discovery.curation.run_state import now, sha, write
from genomics_discovery.curation.workflow_extraction.extraction_pipeline import execute
from genomics_discovery.curation.workflow_extraction.field_review import WorkflowFieldReview, validate_field_review
from genomics_discovery.curation.workflow_extraction.models import Workflow, WorkflowReview, digest
from genomics_discovery.curation.workflow_extraction.obligations import (
    SourceObligations, SourceCoverageReview, validate_source_coverage_review, validate_source_obligations,
)
from genomics_discovery.curation.workflow_extraction.sources import load_packet


def score_control(receipt, state, field_review, coverage_review, omission_obligations,
                  *, native_complete: bool, supported_export: bool) -> dict:
    """Exact affected-field and omission accounting; no prose-based rescue."""
    fields = {row.group_id: row for row in field_review.checks} if field_review else {}
    coverage = {row.obligation_id: row for row in coverage_review.checks} if coverage_review else {}
    changed = []
    for mutation in receipt["field_mutations"]:
        pointer = mutation["candidate_field_pointer"]
        row = fields.get(mutation["target_id"])
        detected = (native_complete and row is not None
                    and row.verdict in {"unsupported", "not_established"}
                    and any(pointer == affected or pointer.startswith(affected + "/")
                            for affected in row.affected_fields))
        changed.append({"target_id": mutation["target_id"], "field_pointer": pointer,
                        "detected": bool(detected), "verdict": row.verdict if row else None})
    omissions = []
    for ident in omission_obligations:
        row = coverage.get(ident)
        detected = (native_complete and row is not None and row.selected
                    and row.disposition in {"missing", "partial"})
        omissions.append({"obligation_id": ident, "detected": bool(detected),
                          "disposition": row.disposition if row else None,
                          "selected": row.selected if row else None})
    checks = {"native_complete": native_complete,
              "all_seeded_fields_detected": bool(changed) and all(row["detected"] for row in changed),
              "all_seeded_omissions_detected": bool(omissions) and all(row["detected"] for row in omissions),
              "withheld_as_needs_revision": state.get("status") == "needs_revision",
              "no_supported_export": not supported_export}
    return {"format_version": "full_workflow_control_report/1.0", "passed": all(checks.values()),
            "checks": checks, "field_controls": changed, "omission_controls": omissions,
            "actual_extraction_status": state.get("status"),
            "boundary": "One deliberately flawed full Workflow reviewed with the production prompt. This measures these seeded defects only, not general accuracy, supported-content retention or experimental validity."}


def _pinned(path, expected):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or sha(path) != expected:
        raise ValueError(f"Input/artifact pin differs: {path}")
    return path.resolve()


async def run_control(candidate, packet_path, obligations_path, receipt_path, output,
                      omission_obligations, *, expected_receipt_sha256,
                      expected_obligations_sha256, executor=None, worker=None):
    """One review_only call, no correction/retry and no expected answer in inputs."""
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Full Workflow model controls require Sherlock Slurm")
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refuse to overwrite full-review control: {output}")
    receipt_path = _pinned(receipt_path, expected_receipt_sha256)
    receipt = json.loads(receipt_path.read_text())
    candidate = _pinned(candidate, receipt["candidate_raw_sha256"])
    packet_path = _pinned(packet_path, receipt["source_packet_raw_sha256"])
    obligations_path = _pinned(obligations_path, expected_obligations_sha256)
    workflow = Workflow.model_validate_json(candidate.read_bytes())
    packet = load_packet(packet_path)
    obligations = SourceObligations.model_validate_json(obligations_path.read_bytes())
    if digest(workflow) != receipt["candidate_workflow_sha256"] or digest(packet) != receipt["source_packet_sha256"]:
        raise ValueError("Canonical candidate/source bindings differ from host receipt")
    if errors := validate_source_obligations(obligations, packet):
        raise ValueError("; ".join(errors))
    if (not omission_obligations or len(set(omission_obligations)) != len(omission_obligations)
            or set(omission_obligations) - {row.id for row in obligations.obligations}):
        raise ValueError("Supply distinct existing omission-obligation IDs")
    pins = {str(receipt_path): expected_receipt_sha256, str(candidate): receipt["candidate_raw_sha256"],
            str(packet_path): receipt["source_packet_raw_sha256"],
            str(obligations_path): expected_obligations_sha256}
    output.mkdir(parents=True)
    write(output / "host_input_bindings.json", {"started_at": now(), "pins": pins,
          "omission_obligations": omission_obligations, "reviewer_receives_expected_answers": False,
          "runner_path": str(Path(__file__).resolve()), "runner_sha256": sha(Path(__file__)),
          "model": "gpt-6.1-sol", "effort": "xhigh", "timeout_seconds": None})
    native_run = output / "execution"
    worker = worker or CodexWorker(model="gpt-6.1-sol", effort="xhigh", timeout_seconds=None,
                                   web_search=False, network_access=False)
    state, field, coverage, native_complete, error = {}, None, None, False, None
    try:
        # No receipt, labels, mutation IDs or expected findings are passed here.
        state = await (executor or execute)(packet_path, native_run, worker=worker,
            mode="review_only", initial_workflow=candidate, max_revisions=0,
            require_field_review=True, require_source_coverage=True, source_obligations=obligations_path)
        write(output / "returned_state.json", state)
        for path, expected in pins.items():
            _pinned(path, expected)
        attempts = state.get("attempts", [])
        if len(attempts) != 1 or attempts[0].get("role") != "reviewer":
            raise ValueError("Expected exactly one production reviewer attempt")
        attempt = attempts[0]
        assessment = assess_archived_attempt(Path(attempt["directory"]))
        if (state.get("status") in {"failed", "cancelled"} or attempt.get("status") != "validated"
                or not attempt.get("transport_complete") or not assessment.complete
                or assessment.thread_id != attempt.get("thread_id")
                or assessment.usage != attempt.get("usage")):
            raise ValueError("Production review did not complete validated native transport")
        for name, expected in attempt.get("archive_sha256", {}).items():
            _pinned(Path(attempt["directory"]) / name, expected)
        artifacts = state["artifacts"]
        def document(key, model):
            item = artifacts[key]
            path = _pinned(item["path"], item["sha256"])
            if not path.is_relative_to(native_run):
                raise ValueError("Reviewer artifact lies outside this run")
            return model.model_validate_json(path.read_bytes())
        review = document("reviewer", WorkflowReview)
        field = document("field_review", WorkflowFieldReview)
        coverage = document("source_coverage_review", SourceCoverageReview)
        errors = validate_field_review(field, workflow, packet)
        errors.extend(validate_source_coverage_review(coverage, obligations, review, workflow, packet))
        if errors:
            raise ValueError("; ".join(errors))
        native_complete = True
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        # The production execute function preserves native attempts/state even
        # when it returns failed; no failed transport receives detection credit.
        if not state:
            state = {"status": "failed", "error": error}
        write(output / "returned_state.json", state)
    supported_export = any((native_run / name).exists() for name in
        ("graph_candidate.json", "protocol.txt", "overview.json", "overview.txt", "overview.html"))
    supported_export |= bool(state.get("graph_candidate") or state.get("artifacts", {}).get("graph_candidate"))
    report = score_control(receipt, state, field, coverage, omission_obligations,
                           native_complete=native_complete, supported_export=supported_export)
    report.update(finished_at=now(), operational_error=error, execution_directory=str(native_run),
                  input_pins=pins, runner_sha256=sha(Path(__file__)))
    write(output / "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate", "packet", "obligations", "receipt", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-receipt-sha256", required=True)
    parser.add_argument("--expected-obligations-sha256", required=True)
    parser.add_argument("--omission-obligation", action="append", required=True)
    args = parser.parse_args(argv)
    report = asyncio.run(run_control(args.candidate, args.packet, args.obligations, args.receipt,
        args.output, args.omission_obligation, expected_receipt_sha256=args.expected_receipt_sha256,
        expected_obligations_sha256=args.expected_obligations_sha256))
    print(json.dumps({"passed": report["passed"], "actual_extraction_status": report["actual_extraction_status"],
                      "report": str(args.output / "report.json")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
