"""Synthetic integrity controls; no assay or reader claim is scientifically accepted."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from genomics_discovery.curation.workflow_coverage import (
    CoverageReview,
    IntegrationCoverage,
    MAX_VIEW_CHARS,
    coverage_review_outcome,
    coverage_review_read,
    coverage_review_view,
    main,
    validate_coverage,
    validate_coverage_review,
)
from genomics_discovery.curation.workflow_extraction.models import (
    ReadingReport,
    Workflow,
    WorkflowReview,
    digest,
    target_ids,
)
from genomics_discovery.curation.workflow_extraction.sources import build_packet


def evidence(quote="Tag DNA."):
    return {"source_id": "paper", "passage_id": "p00001", "quote": quote,
            "support": "reported", "rationale": "Synthetic contextual source citation."}


def disposition(kind="represented", **kwargs):
    result = {"kind": kind, "covers": "The stated finding.",
              "rationale": "Account for this finding within the declared variant.",
              "evidence": [evidence()], **kwargs}
    if kind == "represented":
        result.setdefault("targets", ["operation:assay"])
    return result


@pytest.fixture
def records(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Tag DNA. Read tags. A validation experiment compares two cell lines.\n\n"
                      "A separate control is described.")
    packet = build_packet(
        method_id="synthetic", title="Synthetic assay", scope="Published tag-to-read route",
        variant="original", sources=[{"source_id": "paper", "title": "Synthetic source",
                                     "url": "https://example.org", "role": "primary_paper",
                                     "path": str(source)}],
    )
    gap = {"id": "unknown_setting", "description": "Internal setting unavailable.",
           "blocking": False, "next_evidence": "Acquire the original internal recipe."}
    workflow = Workflow.model_validate({
        **{key: packet[key] for key in ("method_id", "title", "scope", "variant")},
        "states": [{"id": "input", "kind": "material", "description": "Starting DNA",
                    "evidence": [evidence()]},
                   {"id": "reads", "kind": "data", "description": "Sequence reads",
                    "evidence": [evidence("Read tags.")]}],
        "operations": [{"id": "assay", "name": "Tag and read", "action": "Tag and read DNA",
                        "inputs": ["input"], "outputs": ["reads"], "resources": [],
                        "information_effect": "Produce tag reads", "evidence": [evidence()]}],
        "flows": [{"id": "input_flow", "source": "input", "target": "assay",
                   "kind": "material_input", "basis": "Input route", "evidence": [evidence()]},
                  {"id": "read_flow", "source": "assay", "target": "reads",
                   "kind": "data_output", "basis": "Read route", "evidence": [evidence()]}],
        "modules": [{"id": "module", "name": "An assay-defined grouping",
                     "operation_ids": ["assay"], "boundary_rationale": "Source-defined boundary"}],
        "unresolved": [{**gap, "id": "chemistry:unknown_setting"}],
    })
    reports = {
        role: ReadingReport.model_validate({
            "role": role, "inspected_passages": [{"source_id": "paper", "passage_id": "p00001"}],
            "findings": [{"id": "route", "description": "Tag-to-read transformation",
                          "evidence": [evidence()]}] + ([{
                              "id": "comparison", "description": "A study comparison",
                              "evidence": [evidence(
                                  "A validation experiment compares two cell lines.",
                              )],
                          }] if role == "chemistry" else []),
            "unresolved": [gap] if role == "chemistry" else [],
        }) for role in ("chemistry", "information")
    }
    coverage = {
        "source_packet_sha256": digest(packet), "workflow_sha256": digest(workflow),
        "readers": [{"role": role, "report_sha256": digest(report)}
                    for role, report in reports.items()],
        "entries": [{"reader_role": role, "finding_id": finding.id,
                     "dispositions": [disposition("excluded", basis="outside_scope")
                                      if finding.id == "comparison" else disposition()]}
                    for role, report in reports.items() for finding in report.findings],
    }
    return packet, workflow, reports, coverage


def check(records, data=None):
    packet, workflow, reports, coverage = records
    return validate_coverage(IntegrationCoverage.model_validate(data or coverage),
                             workflow, reports, packet)


def test_complete_shared_target_accounting_does_not_require_operation_counts(records):
    assert check(records) == []
    assert len(records[1].operations) == 1


def test_omitted_or_unknown_finding_fails(records):
    data = deepcopy(records[3])
    data["entries"].pop()
    assert any("Missing finding disposition" in error for error in check(records, data))
    data["entries"][0]["finding_id"] = "invented"
    assert any("Unknown reader finding" in error for error in check(records, data))


@pytest.mark.parametrize("roles", [(), ("chemistry",), ("information",)])
def test_empty_or_partial_reader_lineage_fails(records, roles):
    packet, workflow, reports, data = records
    reports = {role: reports[role] for role in roles}
    assert "lineage requires exactly" in validate_coverage(
        IntegrationCoverage.model_validate(data), workflow, reports, packet,
    )[0]


@pytest.mark.parametrize("binding", ["packet", "workflow", "reader"])
def test_digest_mismatch_fails(records, binding):
    data = deepcopy(records[3])
    if binding == "reader":
        data["readers"][0]["report_sha256"] = "0" * 64
    else:
        data["source_packet_sha256" if binding == "packet" else "workflow_sha256"] = "0" * 64
    assert any("does not match" in error for error in check(records, data))


def test_duplicate_reader_binding_and_role_mismatch_fail(records):
    packet, workflow, reports, data = records
    data = deepcopy(data)
    data["readers"][1] = deepcopy(data["readers"][0])
    assert any("one binding per reader role" in error for error in check(records, data))
    swapped = {"chemistry": reports["information"], "information": reports["chemistry"]}
    assert any("role does not match" in error for error in validate_coverage(
        IntegrationCoverage.model_validate(records[3]), workflow, swapped, packet,
    ))


@pytest.mark.parametrize("target", ["operation:missing", "state:missing", "flow:missing",
                                    "module:module", "resolution:chemistry:unknown_setting"])
def test_dangling_or_wrong_kind_target_fails(records, target):
    data = deepcopy(records[3])
    data["entries"][0]["dispositions"][0]["targets"] = [target]
    assert any("unknown/nonmaterialized target" in error for error in check(records, data))


def test_actual_gap_required(records):
    data = deepcopy(records[3])
    data["entries"][0]["dispositions"] = [disposition("scoped_gap", workflow_gap_id="missing")]
    assert any("unknown scoped gap" in error for error in check(records, data))
    data["entries"][0]["dispositions"][0]["workflow_gap_id"] = "chemistry:unknown_setting"
    assert check(records, data) == []


def test_quote_mismatch_and_uninspected_reader_evidence_fail(records):
    packet, workflow, reports, data = records
    data = deepcopy(data)
    data["entries"][0]["dispositions"][0]["evidence"][0]["quote"] = "Invent an adapter."
    assert any("quote is not present" in error for error in check(records, data))
    raw = reports["chemistry"].model_dump(mode="json")
    raw["inspected_passages"] = []
    reports = {**reports, "chemistry": ReadingReport.model_validate(raw)}
    assert any("inspected" in error for error in validate_coverage(
        IntegrationCoverage.model_validate(records[3]), workflow, reports, packet,
    ))


def test_duplicate_reader_findings_and_entries_fail(records):
    packet, workflow, reports, data = records
    raw = reports["chemistry"].model_dump(mode="json")
    raw["findings"].append(deepcopy(raw["findings"][0]))
    reports = {**reports, "chemistry": ReadingReport.model_validate(raw)}
    data = deepcopy(data)
    data["readers"][0]["report_sha256"] = digest(reports["chemistry"])
    assert any("duplicate finding ID" in error for error in validate_coverage(
        IntegrationCoverage.model_validate(data), workflow, reports, packet,
    ))
    data["entries"].append(deepcopy(data["entries"][0]))
    assert any("Duplicate coverage entry" in error for error in check(records, data))


def test_source_refuted_claim_is_accounted_without_copying_reader_as_truth(records):
    packet, workflow, reports, data = records
    raw = reports["chemistry"].model_dump(mode="json")
    raw["findings"][0]["description"] = "The assay tags RNA rather than DNA."
    reports = {**reports, "chemistry": ReadingReport.model_validate(raw)}
    data = deepcopy(data)
    data["readers"][0]["report_sha256"] = digest(reports["chemistry"])
    data["entries"][0]["dispositions"] = [disposition(
        "excluded", basis="source_refuted",
        rationale="The reader asserts RNA tagging; the source instructs tagging DNA.",
    )]
    assert validate_coverage(
        IntegrationCoverage.model_validate(data), workflow, reports, packet,
    ) == []
    assert workflow.operations[0].action == "Tag and read DNA"


def test_compound_finding_can_have_represented_excluded_and_gap_parts(records):
    packet, workflow, reports, data = records
    raw = reports["chemistry"].model_dump(mode="json")
    raw["findings"][0]["description"] = (
        "Tag and read DNA; compare cell lines in a validation experiment; "
        "the internal setting is unavailable."
    )
    reports = {**reports, "chemistry": ReadingReport.model_validate(raw)}
    data = deepcopy(records[3])
    data["readers"][0]["report_sha256"] = digest(reports["chemistry"])
    data["entries"][0]["dispositions"] = [
        disposition(covers="The material and readout route.",
                    targets=["state:input", "operation:assay", "flow:read_flow"]),
        disposition("excluded", covers="The study-only comparison.", basis="outside_scope"),
        disposition("scoped_gap", covers="The missing internal setting.",
                    workflow_gap_id="chemistry:unknown_setting"),
    ]
    assert validate_coverage(
        IntegrationCoverage.model_validate(data), workflow, reports, packet,
    ) == []


def test_workflow_scope_identity_checked_even_when_digest_rebound(records):
    packet, workflow, reports, data = records
    raw = workflow.model_dump(mode="json")
    raw["variant"] = "later_variant"
    workflow = Workflow.model_validate(raw)
    data = deepcopy(data)
    data["workflow_sha256"] = digest(workflow)
    assert any("Workflow variant differs" in error for error in validate_coverage(
        IntegrationCoverage.model_validate(data), workflow, reports, packet,
    ))


@pytest.mark.parametrize(
    "mutation", ["missing_field", "bad_role", "forged_text", "changed_original"],
)
def test_malformed_or_unfrozen_source_record_fails(records, mutation):
    packet, workflow, reports, data = records
    packet = deepcopy(packet)
    source = packet["sources"][0]
    if mutation == "missing_field":
        del source["original_sha256"]
    elif mutation == "bad_role":
        source["role"] = {}
    elif mutation == "forged_text":
        source["passages"][0]["text"] = "Forged source."
    else:
        Path(source["original_path"]).write_text("Changed original.")
    assert "Invalid source packet/original" in validate_coverage(
        IntegrationCoverage.model_validate(data), workflow, reports, packet,
    )[0]


def test_existing_reader_gap_contract_is_reused(records):
    packet, workflow, reports, data = records
    raw = workflow.model_dump(mode="json")
    raw["unresolved"] = []
    workflow = Workflow.model_validate(raw)
    data = deepcopy(data)
    data["workflow_sha256"] = digest(workflow)
    assert any("retain or resolve exactly once" in error for error in validate_coverage(
        IntegrationCoverage.model_validate(data), workflow, reports, packet,
    ))


def test_schema_and_check_cli_report_integrity_only(records, tmp_path, capsys):
    packet, workflow, reports, data = records
    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["additionalProperties"] is False
    with pytest.raises(ValidationError, match="Extra inputs"):
        IntegrationCoverage.model_validate({**data, "accepted": True})
    paths = {}
    for name, record in {"packet": packet, "workflow": workflow.model_dump(mode="json"),
                         "coverage": data, **{role: report.model_dump(mode="json")
                                              for role, report in reports.items()}}.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(record))
    argv = ["check", str(paths["coverage"]), "--packet", str(paths["packet"]),
            "--workflow", str(paths["workflow"])]
    assert main(argv) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["integrity_valid"] is False
    assert "Integrity only" in result["boundary"]
    assert "semantic/source review is still required" in result["boundary"]
    for role in reports:
        argv += ["--reader", str(paths[role])]
    assert main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["integrity_valid"] is True and result["errors"] == []
    assert "accepted" not in result
    assert main(argv + ["--reader", str(paths["chemistry"])]) == 1
    assert "Duplicate input reader role" in json.loads(capsys.readouterr().out)["errors"]


@pytest.fixture
def review_records(records):
    packet, workflow, reports, data = records
    coverage = IntegrationCoverage.model_validate(data)
    workflow_review = WorkflowReview.model_validate({
        "workflow_sha256": digest(workflow), "source_packet_sha256": digest(packet),
        "inspected_passages": [{"source_id": "paper", "passage_id": "p00001"}],
        "checks": [{"target_id": target, "verdict": "supported",
                    "rationale": "Synthetic source check, not experimental acceptance.",
                    "evidence": [evidence()]} for target in sorted(target_ids(workflow))],
        "omissions": [], "notes": [],
    })
    review = {
        "coverage_sha256": digest(coverage),
        "workflow_review_sha256": digest(workflow_review),
        "inspected_passages": [{"source_id": "paper", "passage_id": "p00001"}],
        "checks": [{"reader_role": entry.reader_role, "finding_id": entry.finding_id,
                    "verdict": "supported", "rationale": "Independently assess the proposal.",
                    "evidence": [evidence()]} for entry in coverage.entries],
        "omissions": [],
    }
    return packet, workflow, reports, coverage, workflow_review, review


def review_check(records, data=None, *, coverage=None, workflow_review=None):
    packet, workflow, reports, original, source_review, raw = records
    model = CoverageReview.model_validate(raw if data is None else data)
    return validate_coverage_review(model, coverage or original, workflow_review or source_review,
                                    workflow, reports, packet)


def test_complete_coverage_review_and_old_model_hashes_are_unchanged(review_records):
    packet, workflow, reports, coverage, source_review, data = review_records
    hashes = digest(workflow), digest(source_review), digest(coverage), digest(reports["chemistry"])
    review = CoverageReview.model_validate(data)
    assert review_check(review_records) == []
    assert coverage_review_outcome(review, source_review, workflow) == "supported"
    assert hashes == (digest(workflow), digest(source_review), digest(coverage),
                      digest(reports["chemistry"]))
    assert "format_version" not in source_review.model_dump()
    assert "coverage" not in workflow.model_dump()


@pytest.mark.parametrize("binding", ["coverage_sha256", "workflow_review_sha256"])
def test_coverage_review_rejects_wrong_canonical_binding(review_records, binding):
    data = deepcopy(review_records[-1])
    data[binding] = "0" * 64
    assert any("does not match" in error for error in review_check(review_records, data))


def test_raw_sidecar_without_model_defaults_is_not_canonical_digest(review_records, records):
    data = deepcopy(review_records[-1])
    data["coverage_sha256"] = digest(records[-1])
    assert data["coverage_sha256"] != digest(review_records[3])
    assert any("coverage_sha256" in error for error in review_check(review_records, data))


def test_review_rejects_swapped_or_stale_sidecar_and_source_review(review_records):
    packet, workflow, reports, coverage, source_review, data = review_records
    raw = coverage.model_dump(mode="json")
    raw["entries"][0]["dispositions"][0]["rationale"] = "A different integration proposal."
    swapped = IntegrationCoverage.model_validate(raw)
    assert any("coverage_sha256" in error for error in review_check(
        review_records, coverage=swapped,
    ))
    source = source_review.model_dump(mode="json")
    source["checks"][0]["rationale"] = "A different source review."
    assert any("workflow_review_sha256" in error for error in review_check(
        review_records, workflow_review=WorkflowReview.model_validate(source),
    ))
    raw["readers"][0]["report_sha256"] = "0" * 64
    swapped = IntegrationCoverage.model_validate(raw)
    rebound = {**data, "coverage_sha256": digest(swapped)}
    assert any("report_sha256" in error for error in review_check(
        review_records, rebound, coverage=swapped,
    ))


@pytest.mark.parametrize("change", ["missing", "duplicate", "unknown"])
def test_exactly_one_review_check_per_original_finding_pair(review_records, change):
    data = deepcopy(review_records[-1])
    if change == "missing":
        data["checks"].pop()
        message = "Missing coverage review check"
    elif change == "duplicate":
        data["checks"].append(deepcopy(data["checks"][0]))
        message = "Duplicate coverage review check"
    else:
        data["checks"][0]["finding_id"] = "invented"
        message = "Unknown coverage review finding"
    assert any(message in error for error in review_check(review_records, data))


def test_rebound_review_still_validates_both_original_input_contracts(review_records):
    packet, workflow, reports, coverage, source_review, data = review_records
    raw = source_review.model_dump(mode="json")
    raw["checks"].pop()
    source_review = WorkflowReview.model_validate(raw)
    rebound = {**data, "workflow_review_sha256": digest(source_review)}
    assert any("Review missing targets" in error for error in review_check(
        review_records, rebound, workflow_review=source_review,
    ))
    raw = coverage.model_dump(mode="json")
    raw["entries"].pop()
    coverage = IntegrationCoverage.model_validate(raw)
    rebound = {**data, "coverage_sha256": digest(coverage)}
    assert any("Missing finding disposition" in error for error in review_check(
        review_records, rebound, coverage=coverage,
    ))


def test_malformed_packet_returns_errors_before_review_quote_walk(review_records):
    packet, workflow, reports, coverage, source_review, data = review_records
    packet = deepcopy(packet)
    # A forged nontext rendition must not reach the quotation walk's string operations.
    packet["sources"][0]["passages"][0]["text"] = {}
    errors = validate_coverage_review(CoverageReview.model_validate(data), coverage,
                                      source_review, workflow, reports, packet)
    assert len(errors) == 1
    assert errors[0].startswith("Invalid source packet/original:")
    assert "Stored rendition/identity differs from original" in errors[0]


def test_check_citations_can_independently_differ_from_original_proposal(review_records):
    data = deepcopy(review_records[-1])
    data["inspected_passages"].append({"source_id": "paper", "passage_id": "p00002"})
    data["checks"][0]["evidence"] = [{**evidence("A separate control is described."),
                                     "passage_id": "p00002"}]
    assert review_check(review_records, data) == []


def test_quoted_evidence_must_be_true_to_passage_and_independently_inspected(review_records):
    data = deepcopy(review_records[-1])
    data["checks"][0]["evidence"][0]["quote"] = "A fabricated instruction."
    assert any("quote is not present" in error for error in review_check(review_records, data))
    data = deepcopy(review_records[-1])
    data["inspected_passages"] = [{"source_id": "paper", "passage_id": "p00002"}]
    assert any("was not inspected" in error for error in review_check(review_records, data))


@pytest.mark.parametrize("change", ["empty_inspected", "empty_evidence", "extra", "bad_version"])
def test_coverage_review_is_strict_and_requires_review_evidence(review_records, change):
    data = deepcopy(review_records[-1])
    if change == "empty_inspected":
        data["inspected_passages"] = []
    elif change == "empty_evidence":
        data["checks"][0]["evidence"] = []
    elif change == "extra":
        data["accepted"] = True
    else:
        data["format_version"] = "coverage_review/2.0"
    with pytest.raises(ValidationError):
        CoverageReview.model_validate(data)


def test_false_exclusion_is_a_semantic_review_question_and_can_block_old_supported(review_records):
    packet, workflow, reports, coverage, source_review, data = review_records
    raw = coverage.model_dump(mode="json")
    raw["entries"][0]["dispositions"] = [disposition("excluded", basis="outside_scope")]
    coverage = IntegrationCoverage.model_validate(raw)
    data = {**deepcopy(data), "coverage_sha256": digest(coverage)}
    # Existence and a real quote do not let code decide whether this scope exclusion is true.
    assert validate_coverage(coverage, workflow, reports, packet) == []
    assert review_check(review_records, data, coverage=coverage) == []
    view = coverage_review_view(coverage, workflow, reports, packet)
    assert view["entries"][0]["original_finding"]["description"] == "Tag-to-read transformation"
    assert view["entries"][0]["proposed_dispositions"][0]["kind"] == "excluded"
    data["checks"][0].update(verdict="unsupported", rationale=(
        "The general published route includes this transformation; the scope exclusion is false."
    ))
    assert review_check(review_records, data, coverage=coverage) == []
    assert coverage_review_outcome(CoverageReview.model_validate(data), source_review,
                                   workflow) == "needs_revision"


@pytest.mark.parametrize(
    "action,blocking,outcome",
    [("revise_record", False, "needs_revision"), ("revise_record", True, "needs_revision"),
     ("retrieve_evidence", True, "research_needed"), ("retrieve_evidence", False, "supported")],
)
def test_coverage_omission_actions_keep_existing_gate_semantics(review_records, action, blocking,
                                                               outcome):
    data = deepcopy(review_records[-1])
    data["omissions"] = [{"description": "Source-backed disposition correction or research need.",
                          "blocking": blocking, "action": action, "evidence": [evidence()]}]
    assert review_check(review_records, data) == []
    assert coverage_review_outcome(CoverageReview.model_validate(data), review_records[4],
                                   review_records[1]) == outcome


def test_negative_omission_citations_are_inspected_and_repair_requires_source(review_records):
    data = deepcopy(review_records[-1])
    omission = {"description": "Correct a proposed exclusion.", "blocking": False,
                "action": "revise_record", "evidence": []}
    data["omissions"] = [omission]
    with pytest.raises(ValidationError, match="nonempty source evidence"):
        CoverageReview.model_validate(data)
    omission["evidence"] = [{**evidence("A separate control is described."),
                             "passage_id": "p00002"}]
    assert any("was not inspected" in error for error in review_check(review_records, data))


@pytest.mark.parametrize("source_verdict,coverage_verdict,outcome", [
    ("unsupported", "supported", "needs_revision"),
    ("unresolved", "supported", "research_needed"),
    ("supported", "unresolved", "research_needed"),
    ("unresolved", "unsupported", "needs_revision"),
])
def test_old_source_outcome_and_new_coverage_outcome_both_gate(review_records, source_verdict,
                                                              coverage_verdict, outcome):
    packet, workflow, reports, coverage, source_review, data = review_records
    source = source_review.model_dump(mode="json")
    source["checks"][0]["verdict"] = source_verdict
    source_review = WorkflowReview.model_validate(source)
    data = {**deepcopy(data), "workflow_review_sha256": digest(source_review)}
    data["checks"][0]["verdict"] = coverage_verdict
    assert review_check(review_records, data, workflow_review=source_review) == []
    assert coverage_review_outcome(CoverageReview.model_validate(data), source_review,
                                   workflow) == outcome


def test_finding_view_withholds_gap_opinions_but_binds_full_original_reports(review_records):
    packet, workflow, reports, coverage, source_review, data = review_records
    raw = reports["chemistry"].model_dump(mode="json")
    raw["unresolved"][0]["description"] = "WITHHELD previous reader gap-severity opinion."
    reports = {**reports, "chemistry": ReadingReport.model_validate(raw)}
    raw_coverage = coverage.model_dump(mode="json")
    raw_coverage["readers"][0]["report_sha256"] = digest(reports["chemistry"])
    coverage = IntegrationCoverage.model_validate(raw_coverage)
    first = coverage_review_view(coverage, workflow, reports, packet, limit=1)
    assert first["start"] == 0 and first["total_entries"] == 3 and first["next_start"] == 1
    assert first["claim_status"].startswith("unaccepted")
    assert first["coverage_sha256"] == digest(coverage)
    assert first["workflow_sha256"] == digest(workflow)
    assert first["readers"][0]["report_sha256"] == digest(reports["chemistry"])
    assert first["entries"][0]["original_finding"] == reports["chemistry"].findings[0].model_dump(
        mode="json")
    assert "WITHHELD" not in json.dumps(first)
    assert "unresolved" not in json.dumps(first) and "verdict" not in json.dumps(first)
    remaining = coverage_review_view(coverage, workflow, reports, packet, start=1, limit=2)
    assert remaining["next_start"] is None
    assert len(first["entries"] + remaining["entries"]) == first["total_entries"]


@pytest.mark.parametrize("kwargs", [{"start": -1}, {"start": 4}, {"limit": 0},
                                    {"limit": 9}, {"limit": True}])
def test_view_is_bounded_and_does_not_silently_truncate(review_records, kwargs):
    packet, workflow, reports, coverage, source_review, data = review_records
    with pytest.raises(ValueError):
        coverage_review_view(coverage, workflow, reports, packet, **kwargs)


def test_oversized_finding_has_bound_exact_read_access_without_relaxing_budget(review_records):
    packet, workflow, reports, coverage, source_review, data = review_records
    raw = reports["chemistry"].model_dump(mode="json")
    raw["findings"][0]["description"] = "Original finding " * 2500
    reports = {**reports, "chemistry": ReadingReport.model_validate(raw)}
    with pytest.raises(ValueError, match="report_sha256"):
        coverage_review_view(coverage, workflow, reports, packet)
    raw_coverage = coverage.model_dump(mode="json")
    raw_coverage["readers"][0]["report_sha256"] = digest(reports["chemistry"])
    coverage = IntegrationCoverage.model_validate(raw_coverage)
    view = coverage_review_view(coverage, workflow, reports, packet, limit=1)
    assert view["format_version"] == "coverage_review_view/1.1"
    entry = view["entries"][0]
    assert entry["content_mode"] == "bound_reference"
    assert len(json.dumps(view, ensure_ascii=False)) <= MAX_VIEW_CHARS
    original = reports["chemistry"].findings[0].description
    chunks, start = [], 0
    while True:
        part = coverage_review_read(coverage, workflow, reports, packet,
            reader_role="chemistry", finding_id=entry["finding_id"],
            pointer="/original_finding/description", start=start)
        assert part["entry_sha256"] == entry["entry_sha256"]
        assert part["total_characters"] == len(original)
        assert len(json.dumps(part, ensure_ascii=False)) <= MAX_VIEW_CHARS
        chunks.append(part["text"])
        if part["next_start"] is None:
            break
        start = part["next_start"]
    assert "".join(chunks) == original


def test_oversized_proposals_retain_all_dispositions_and_evidence(review_records):
    packet, workflow, reports, coverage, source_review, data = review_records
    raw = coverage.model_dump(mode="json")
    original = "Proposed contextual explanation " * 2000
    raw["entries"][0]["dispositions"][0]["rationale"] = original
    coverage = IntegrationCoverage.model_validate(raw)
    original = coverage.entries[0].dispositions[0].rationale
    view = coverage_review_view(coverage, workflow, reports, packet, limit=1)
    assert view["entries"][0]["content_mode"] == "bound_reference"
    context = dict(reader_role="chemistry", finding_id=coverage.entries[0].finding_id)
    root = coverage_review_read(coverage, workflow, reports, packet, **context)
    assert {child["pointer"] for child in root["children"]} == {
        "/reader_role", "/finding_id", "/original_finding", "/proposed_dispositions"}
    container = coverage_review_read(coverage, workflow, reports, packet, **context,
                                    pointer="/proposed_dispositions")
    assert container["children"] == [{"pointer": "/proposed_dispositions/0", "kind": "object"}]
    quote = coverage_review_read(coverage, workflow, reports, packet, **context,
        pointer="/proposed_dispositions/0/evidence/0/quote")
    assert quote["text"] == coverage.entries[0].dispositions[0].evidence[0].quote
    chunks, start = [], 0
    while True:
        page = coverage_review_read(coverage, workflow, reports, packet, **context,
            pointer="/proposed_dispositions/0/rationale", start=start)
        chunks.append(page["text"])
        if page["next_start"] is None:
            break
        start = page["next_start"]
    assert "".join(chunks) == original


@pytest.mark.parametrize("change", [
    {"reader_role": "unknown"}, {"finding_id": "absent"}, {"pointer": "/unresolved"},
    {"pointer": "/original_finding/~2"}, {"start": -1}, {"start": True},
    {"limit": 9}, {"chars": 4001}, {"chars": True},
])
def test_bounded_finding_read_rejects_unknown_records_gap_access_and_bad_windows(review_records,
                                                                              change):
    packet, workflow, reports, coverage, source_review, data = review_records
    arguments = {"reader_role": "chemistry", "finding_id": coverage.entries[0].finding_id,
                 **change}
    with pytest.raises(ValueError):
        coverage_review_read(coverage, workflow, reports, packet, **arguments)


def test_bounded_read_rejects_stale_lineage_and_never_exposes_old_gap_opinions(review_records):
    packet, workflow, reports, coverage, source_review, data = review_records
    arguments = dict(reader_role="chemistry", finding_id=coverage.entries[0].finding_id)
    raw = reports["chemistry"].model_dump(mode="json")
    raw["unresolved"][0]["description"] = "WITHHELD prior gap severity"
    reports = {**reports, "chemistry": ReadingReport.model_validate(raw)}
    with pytest.raises(ValueError, match="report_sha256"):
        coverage_review_read(coverage, workflow, reports, packet, **arguments)
    raw_coverage = coverage.model_dump(mode="json")
    raw_coverage["readers"][0]["report_sha256"] = digest(reports["chemistry"])
    coverage = IntegrationCoverage.model_validate(raw_coverage)
    result = coverage_review_read(coverage, workflow, reports, packet, **arguments)
    assert "WITHHELD" not in json.dumps(result)
    assert all("unresolved" not in child["pointer"] for child in result["children"])


def test_multi_entry_view_still_requires_a_smaller_page(review_records):
    packet, workflow, reports, coverage, source_review, data = review_records
    raw = reports["chemistry"].model_dump(mode="json")
    raw["findings"][0]["description"] = "Long exact finding " * 2500
    reports = {**reports, "chemistry": ReadingReport.model_validate(raw)}
    raw_coverage = coverage.model_dump(mode="json")
    raw_coverage["readers"][0]["report_sha256"] = digest(reports["chemistry"])
    coverage = IntegrationCoverage.model_validate(raw_coverage)
    with pytest.raises(ValueError, match="smaller batch"):
        coverage_review_view(coverage, workflow, reports, packet)


def test_review_schema_cli_and_valid_negative_review_report_outcome(review_records, tmp_path,
                                                                  capsys):
    assert main(["schema", "--kind", "review"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["additionalProperties"] is False and schema["title"] == "CoverageReview"
    packet, workflow, reports, coverage, source_review, data = review_records
    data = deepcopy(data)
    data["checks"][0]["verdict"] = "unsupported"
    paths = {}
    documents = {"packet": packet, "workflow": workflow.model_dump(mode="json"),
                 "coverage": coverage.model_dump(mode="json"),
                 "workflow_review": source_review.model_dump(mode="json"), "review": data,
                 **{role: report.model_dump(mode="json") for role, report in reports.items()}}
    for name, document in documents.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(document))
    context = ["--packet", str(paths["packet"]), "--workflow", str(paths["workflow"])]
    for role in reports:
        context += ["--reader", str(paths[role])]
    args = ["check", str(paths["review"]), "--kind", "review", *context]
    assert main(args) == 1
    assert "requires --workflow-review" in json.loads(capsys.readouterr().out)["errors"][0]
    args += ["--workflow-review", str(paths["workflow_review"]),
             "--coverage", str(paths["coverage"])]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["integrity_valid"] is True and result["review_outcome"] == "needs_revision"
    assert "accepted" not in result
    assert main(["view", str(paths["coverage"]), *context, "--limit", "1"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["total_entries"] == 3 and len(view["entries"]) == 1
    assert main(["read", str(paths["coverage"]), *context, "--reader-role", "chemistry",
                 "--finding-id", coverage.entries[0].finding_id,
                 "--pointer", "/original_finding/description", "--chars", "5"]) == 0
    part = json.loads(capsys.readouterr().out)
    assert part["text"] == reports["chemistry"].findings[0].description[:5]
    assert part["next_start"] == 5
