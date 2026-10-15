"""Digest-bound accounting and source review for known reader findings.

These sidecars leave existing workflow and source-review models unchanged.
Target existence, exact quotes and complete accounting do not establish
representation, scope, refutation, source completeness or justified gap severity.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.curation.workflow_extraction.models import (
    Evidence,
    Omission,
    PassageRef,
    ReadingReport,
    Text,
    Workflow,
    WorkflowReview,
    digest,
    review_outcome,
    target_ids,
    validate_evidence,
    validate_reader_gaps,
    validate_review,
)
from genomics_discovery.curation.workflow_extraction.sources import validate_packet

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Role = Literal["chemistry", "information"]
ROLES = frozenset({"chemistry", "information"})
MAX_VIEW_ENTRIES = 8
MAX_VIEW_CHARS = 32_000
BOUNDARY = (
    "Integrity only: accounting, bindings and provenance. Target existence does not prove "
    "representation; semantic/source review is still required."
)


class ReaderBinding(StrictModel):
    role: Role
    report_sha256: Digest


class _Disposition(StrictModel):
    covers: Text
    rationale: Text
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


class Represented(_Disposition):
    kind: Literal["represented"]
    targets: Annotated[tuple[Text, ...], Field(min_length=1)]


class Excluded(_Disposition):
    kind: Literal["excluded"]
    basis: Literal["source_refuted", "outside_scope"]


class ScopedGap(_Disposition):
    kind: Literal["scoped_gap"]
    workflow_gap_id: Text


Disposition = Annotated[Represented | Excluded | ScopedGap, Field(discriminator="kind")]


class CoverageEntry(StrictModel):
    reader_role: Role
    finding_id: Text
    dispositions: Annotated[tuple[Disposition, ...], Field(min_length=1)]


class IntegrationCoverage(StrictModel):
    format_version: Literal["integration_coverage/1.0"] = "integration_coverage/1.0"
    digest_profile: Literal["workflow_extraction.canonical_model_json/1"] = (
        "workflow_extraction.canonical_model_json/1"
    )
    source_packet_sha256: Digest
    workflow_sha256: Digest
    readers: Annotated[tuple[ReaderBinding, ...], Field(min_length=2, max_length=2)]
    entries: Annotated[tuple[CoverageEntry, ...], Field(min_length=1)]


class CoverageCheck(StrictModel):
    reader_role: Role
    finding_id: Text
    verdict: Literal["supported", "unsupported", "unresolved"]
    rationale: Text
    evidence: Annotated[tuple[Evidence, ...], Field(min_length=1)]


class CoverageReview(StrictModel):
    """The same source-review turn separately judges each proposed disposition."""

    format_version: Literal["coverage_review/1.0"] = "coverage_review/1.0"
    coverage_sha256: Digest
    workflow_review_sha256: Digest
    inspected_passages: Annotated[tuple[PassageRef, ...], Field(min_length=1)]
    checks: Annotated[tuple[CoverageCheck, ...], Field(min_length=1)]
    omissions: tuple[Omission, ...]
    notes: tuple[Text, ...] = ()


def validate_coverage(
    coverage: IntegrationCoverage,
    workflow: Workflow,
    reports: dict[str, ReadingReport],
    packet: dict,
) -> list[str]:
    """Return mechanical errors for the supplied full specialist input lineage.

    The caller supplies the intended reader inputs; this does not authenticate
    their origin or replace independent source review. Digests use models.digest,
    including validated model defaults, rather than runtime file-byte hashes.
    """
    try:
        validate_packet(packet)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return [f"Invalid source packet/original: {exc}"]
    if set(reports) != ROLES:
        return ["Reader lineage requires exactly chemistry and information reports"]

    errors = validate_evidence(workflow, packet) + validate_evidence(coverage, packet)
    if coverage.source_packet_sha256 != digest(packet):
        errors.append("Coverage source_packet_sha256 does not match the source packet")
    if coverage.workflow_sha256 != digest(workflow):
        errors.append("Coverage workflow_sha256 does not match the workflow")
    for field in ("method_id", "title", "scope", "variant"):
        if getattr(workflow, field) != packet[field]:
            errors.append(f"Workflow {field} differs from frozen source identity/scope")

    bindings = Counter(binding.role for binding in coverage.readers)
    if set(bindings) != ROLES or any(count != 1 for count in bindings.values()):
        errors.append("Coverage requires exactly one binding per reader role")
    for binding in coverage.readers:
        if binding.report_sha256 != digest(reports[binding.role]):
            errors.append(f"Reader {binding.role}: report_sha256 does not match")

    expected = set()
    for role, report in reports.items():
        if report.role != role:
            errors.append(f"Reader report role does not match: {role}")
        errors.extend(validate_evidence(report, packet))
        inspected = {(ref.source_id, ref.passage_id) for ref in report.inspected_passages}
        if not inspected:
            errors.append(f"Reader {role}: requires nonempty inspected_passages")
        counts = Counter(finding.id for finding in report.findings)
        for ident, count in counts.items():
            if count != 1:
                errors.append(f"Reader {role}: duplicate finding ID {ident}")
        for finding in report.findings:
            expected.add((role, finding.id))
            if any((ev.source_id, ev.passage_id) not in inspected for ev in finding.evidence):
                errors.append(f"Reader {role}, finding {finding.id}: evidence not inspected")
    errors.extend(validate_reader_gaps(workflow, reports, packet))

    entries = Counter((entry.reader_role, entry.finding_id) for entry in coverage.entries)
    for key, count in entries.items():
        if count != 1:
            errors.append(f"Duplicate coverage entry: {key}")
    for key in sorted(expected - entries.keys()):
        errors.append(f"Missing finding disposition: {key}")
    for key in sorted(entries.keys() - expected):
        errors.append(f"Unknown reader finding: {key}")

    actual_targets = {
        target for target in target_ids(workflow)
        if target.split(":", 1)[0] in {"state", "operation", "flow"}
    }
    actual_gaps = {gap.id for gap in workflow.unresolved}
    for entry in coverage.entries:
        key = (entry.reader_role, entry.finding_id)
        for disposition in entry.dispositions:
            if isinstance(disposition, Represented):
                if len(disposition.targets) != len(set(disposition.targets)):
                    errors.append(f"Finding {key}: duplicate represented targets")
                for target in disposition.targets:
                    if target not in actual_targets:
                        errors.append(f"Finding {key}: unknown/nonmaterialized target {target}")
            elif isinstance(disposition, ScopedGap):
                if disposition.workflow_gap_id not in actual_gaps:
                    errors.append(
                        f"Finding {key}: unknown scoped gap {disposition.workflow_gap_id}"
                    )
    return errors


def validate_coverage_review(
    review: CoverageReview,
    coverage: IntegrationCoverage,
    workflow_review: WorkflowReview,
    workflow: Workflow,
    reports: dict[str, ReadingReport],
    packet: dict,
) -> list[str]:
    """Validate lineage, review accounting and citations, not scientific entailment."""
    try:
        validate_packet(packet)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return [f"Invalid source packet/original: {exc}"]
    errors = validate_coverage(coverage, workflow, reports, packet)
    try:
        errors.extend(validate_review(workflow_review, workflow, packet))
        errors.extend(validate_evidence(review, packet))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        errors.append(f"Invalid coverage-review input: {exc}")
    if review.coverage_sha256 != digest(coverage):
        errors.append("Coverage review coverage_sha256 does not match the coverage sidecar")
    if review.workflow_review_sha256 != digest(workflow_review):
        errors.append("Coverage review workflow_review_sha256 does not match the source review")

    expected = {(role, finding.id) for role, report in reports.items()
                for finding in report.findings}
    checked = Counter((check.reader_role, check.finding_id) for check in review.checks)
    for key, count in checked.items():
        if count != 1:
            errors.append(f"Duplicate coverage review check: {key}")
    for key in sorted(expected - checked.keys()):
        errors.append(f"Missing coverage review check: {key}")
    for key in sorted(checked.keys() - expected):
        errors.append(f"Unknown coverage review finding: {key}")

    inspected = {(ref.source_id, ref.passage_id) for ref in review.inspected_passages}
    if not inspected:
        errors.append("Coverage review requires nonempty inspected_passages")
    for check in review.checks:
        if not check.evidence:
            errors.append(f"Coverage review check {(check.reader_role, check.finding_id)}: "
                          "requires nonempty evidence")
    for item in (*review.checks, *review.omissions):
        if any((ev.source_id, ev.passage_id) not in inspected for ev in item.evidence):
            errors.append("Coverage review evidence passage was not inspected")
    return errors


def coverage_review_outcome(
    review: CoverageReview,
    workflow_review: WorkflowReview,
    workflow: Workflow,
) -> str:
    """Combine outcomes only after separate full integrity validation has passed."""
    source_outcome = review_outcome(workflow_review, workflow)
    if source_outcome == "needs_revision" or any(
        check.verdict == "unsupported" for check in review.checks
    ) or any(omission.action == "revise_record" for omission in review.omissions):
        return "needs_revision"
    if source_outcome == "research_needed" or any(
        check.verdict == "unresolved" for check in review.checks
    ) or any(omission.blocking and omission.action == "retrieve_evidence"
             for omission in review.omissions):
        return "research_needed"
    return "supported"


def coverage_review_view(
    coverage: IntegrationCoverage,
    workflow: Workflow,
    reports: dict[str, ReadingReport],
    packet: dict,
    *,
    start: int = 0,
    limit: int = MAX_VIEW_ENTRIES,
) -> dict:
    """Page original unaccepted findings and proposals without prior gap opinions.

    Original descriptions and evidence are never truncated or rewritten. Oversized
    batches fail explicitly; callers can reduce the page size rather than silently
    omit claims. Original full reader reports remain canonically bound even though
    their unresolved-gap opinions are withheld from this scientific review view.
    """
    errors = validate_coverage(coverage, workflow, reports, packet)
    if errors:
        raise ValueError("Invalid coverage lineage: " + "; ".join(errors))
    if type(start) is not int or not 0 <= start <= len(coverage.entries):
        raise ValueError("View start must be an integer within the coverage entry range")
    if type(limit) is not int or not 1 <= limit <= MAX_VIEW_ENTRIES:
        raise ValueError(f"View limit must be an integer from 1 to {MAX_VIEW_ENTRIES}")
    findings = {(role, finding.id): finding for role, report in reports.items()
                for finding in report.findings}
    stop = min(start + limit, len(coverage.entries))
    view = {
        "format_version": "coverage_review_view/1.0",
        "claim_status": "unaccepted_reader_findings_and_integration_proposals",
        "digest_profile": coverage.digest_profile,
        "source_packet_sha256": digest(packet),
        "workflow_sha256": digest(workflow),
        "coverage_sha256": digest(coverage),
        "readers": [binding.model_dump(mode="json") for binding in coverage.readers],
        "scope": workflow.scope,
        "variant": workflow.variant,
        "start": start,
        "limit": limit,
        "total_entries": len(coverage.entries),
        "next_start": stop if stop < len(coverage.entries) else None,
        "entries": [{
            "reader_role": entry.reader_role,
            "finding_id": entry.finding_id,
            "original_finding": findings[(entry.reader_role, entry.finding_id)].model_dump(
                mode="json"),
            "proposed_dispositions": [part.model_dump(mode="json")
                                      for part in entry.dispositions],
        } for entry in coverage.entries[start:stop]],
        "boundary": BOUNDARY,
    }
    if len(json.dumps(view, ensure_ascii=False)) > MAX_VIEW_CHARS:
        raise ValueError(f"Coverage review view exceeds {MAX_VIEW_CHARS} characters; "
                         "request a smaller batch or inspect the bound originals")
    return view


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    schema = commands.add_parser("schema", help="Print the selected sidecar JSON schema")
    schema.add_argument("--kind", choices=("coverage", "review"), default="coverage")
    check = commands.add_parser("check", help="Check sidecar integrity; source review is required")
    check.add_argument("document", type=Path)
    check.add_argument("--kind", choices=("coverage", "review"), default="coverage")
    check.add_argument("--workflow-review", type=Path)
    check.add_argument("--coverage", type=Path, dest="integration_coverage")
    view = commands.add_parser("view", help="Read a bounded source-review finding/proposal view")
    view.add_argument("document", type=Path)
    view.add_argument("--start", type=int, default=0)
    view.add_argument("--limit", type=int, default=MAX_VIEW_ENTRIES)
    for command in (check, view):
        command.add_argument("--packet", type=Path, required=True)
        command.add_argument("--workflow", type=Path, required=True)
        command.add_argument("--reader", type=Path, action="append", default=[],
                             help="Repeat for both chemistry and information reports")
    args = parser.parse_args(argv)
    if args.command == "schema":
        model = CoverageReview if args.kind == "review" else IntegrationCoverage
        print(json.dumps(model.model_json_schema(), indent=2))
        return 0

    outcome = None
    try:
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
        workflow = Workflow.model_validate_json(args.workflow.read_bytes())
        readers = [ReadingReport.model_validate_json(path.read_bytes()) for path in args.reader]
        errors = []
        if len({reader.role for reader in readers}) != len(readers):
            errors.append("Duplicate input reader role")
        reports = {reader.role: reader for reader in readers}
        if args.command == "view" or args.kind == "coverage":
            coverage = IntegrationCoverage.model_validate_json(args.document.read_bytes())
            errors.extend(validate_coverage(coverage, workflow, reports, packet))
            if args.command == "view" and not errors:
                result = coverage_review_view(coverage, workflow, reports, packet,
                                              start=args.start, limit=args.limit)
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 0
        else:
            if args.workflow_review is None or args.integration_coverage is None:
                raise ValueError("Review checking requires --workflow-review and --coverage")
            review = CoverageReview.model_validate_json(args.document.read_bytes())
            workflow_review = WorkflowReview.model_validate_json(args.workflow_review.read_bytes())
            coverage = IntegrationCoverage.model_validate_json(
                args.integration_coverage.read_bytes())
            errors.extend(validate_coverage_review(
                review, coverage, workflow_review, workflow, reports, packet,
            ))
            if not errors:
                outcome = coverage_review_outcome(review, workflow_review, workflow)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        errors = [str(exc)]
    result = {"integrity_valid": not errors, "errors": errors, "boundary": BOUNDARY}
    if outcome is not None:
        result["review_outcome"] = outcome
    print(json.dumps(result,
                     indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
