"""Archived-only benchmark reporting with synthetic artifacts; no model runs."""

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/benchmark/report.py"
SPEC = importlib.util.spec_from_file_location("benchmark_report", SCRIPT)
reporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reporter)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    return path


def file_hash(path):
    return reporter.sha_bytes(path.read_bytes())


class Phase:
    def __init__(self, tmp_path, *, remote=None):
        self.work, self.remote = tmp_path / "phase", remote
        self.work.mkdir()
        self.design = write(tmp_path / "design/cases.json", {"selection": {"cases": 12, "families": 6},
            "cases": [{"case_id": f"c{i:02}", "method_id": f"m{i:02}", "family": f"f{i // 2}",
                "split": "development" if i < 8 else "future_tuning_holdout", "title": f"Paper {i}",
                "scope": "The exact reported assay.", "variant": "Original variant."} for i in range(12)]})
        self.comparisons = write(self.design.with_name("comparisons.json"), {"comparisons": [
            {"comparison_id": f"pair{i}", "left_case_id": f"c{i:02}", "right_case_id": f"c{i + 1:02}",
             "split": "development", "question": "A source-scoped question."} for i in range(8)]})
        self.sources, self.manifest = {}, []

    def recorded(self, path):
        return str(Path(self.remote) / path.relative_to(self.work)) if self.remote else str(path)

    def refresh(self):
        source = write(self.work / "sources/source_report.json", {"cases": self.sources})
        write(self.work / "sources/annotation_manifest.json", {
            "case_design_sha256": file_hash(self.design), "cases": self.manifest,
            "source_report": {"path": self.recorded(source), "sha256": file_hash(source)}})
        write(self.work / "references/manifest.json", {"cases": self.manifest})

    def case(self, index=0, *, partial=False, critical_missing=False, source_status=None):
        design = json.loads(self.design.read_bytes())["cases"][index]
        cid, mid = design["case_id"], design["method_id"]
        if source_status:
            self.sources[mid] = {"case_id": cid, "status": source_status, "sources": 1,
                "supplement_count": 0, "reason": "Required supplement unavailable."}
            self.refresh()
            return
        folder = self.work / "references/cases" / cid
        original = self.work / "originals" / f"{cid}.txt"
        original.parent.mkdir(exist_ok=True)
        original.write_text("Do not freshly audit this original source.\n")
        packet = write(self.work / "sources/packets" / f"{cid}.json", {"sources": [
            {"source_id": "main", "role": "paper", "original_path": self.recorded(original),
             "passages": [{"passage_id": "p1", "text": "Exact quoted source."}]},
            {"source_id": "supp", "role": "supplement", "original_path": "/remote/unavailable/supp.pdf"}]})
        packet_data = json.loads(packet.read_bytes())
        evidence = [{"source_id": "main", "passage_id": "p1", "quote": "Exact quoted source."}]
        ref = {**{key: design[key] for key in ("case_id", "method_id", "scope", "variant")},
            "source_packet_sha256": reporter.object_digest(packet_data),
            "annotation_status": "model_authored_reference_candidate", "measurement": "An observed quantity.",
            "obligations": [{"id": "critical", "criticality": "critical", "kind": "material",
                "text": "Retain the source-defined material.", "why_needed": "Defines the assay input.",
                "evidence": evidence}, {"id": "required", "criticality": "required", "kind": "operation",
                "text": "Preserve the reported route.", "why_needed": "Defines the reported transformation.",
                "evidence": evidence}], "unknowns": [{"id": "u1", "question": "Unreported association?",
                "consequence": "Cannot infer association.", "next_evidence": "Acquire cited supplement.",
                "inspected_passages": [{"source_id": "main", "passage_id": "p1"}]}],
            "admissible_decompositions": [{"id": "d1", "description": "One acceptable decomposition.",
                "obligation_ids": ["critical", "required"]}], "query_cases": [], "source_coverage": [
                {"source_id": "main", "inspected_passage_ids": ["p1"], "role_in_assay": "Methods",
                 "notes": "Declared inspected scope."}]}
        workflow = {"states": [{"id": "in"}, {"id": "out"}], "operations": [{"id": "transform"}],
                    "flows": [{"id": "material-flow"}], "gap_resolutions": []}
        ref_review = {"reference_sha256": reporter.object_digest(ref),
            "source_packet_sha256": reporter.object_digest(packet_data), "missing_obligations": [],
            **{field: [{"target_id": row["id"], "verdict": "supported"} for row in ref[original_field]]
               for original_field, field in (("obligations", "obligation_checks"), ("unknowns", "unknown_checks"),
                   ("admissible_decompositions", "decomposition_checks"), ("query_cases", "query_checks"))}}
        workflow_review = {"workflow_sha256": reporter.object_digest(workflow),
            "source_packet_sha256": reporter.object_digest(packet_data), "omissions": [],
            "checks": [{"target_id": target, "verdict": "supported"} for target in sorted(reporter._targets(workflow))]}
        assessment = {"reference_sha256": reporter.object_digest(ref), "workflow_sha256": reporter.object_digest(workflow),
            "source_packet_sha256": reporter.object_digest(packet_data), "obligations": [
                {"obligation_id": row["id"], "outcome": "missing" if critical_missing and row["id"] == "critical"
                 else "partial" if partial and row["id"] == "required" else "covered",
                 "targets": ["operation:transform"], "rationale": "Recorded source comparison.", "evidence": evidence}
                for row in ref["obligations"]], "factual_audit_scope": "complete_declared", "factual_audits": [
                {"id": "audit1", "claim": "Only this scoped claim.", "verdict": "supported",
                 "targets": ["operation:transform"], "evidence": evidence}]}
        inputs = {"case_id": cid, "method_id": mid, "packet": self.recorded(packet),
                  "packet_sha256": file_hash(packet), "source_packet_sha256": reporter.object_digest(packet_data)}
        artifacts = {"input.json": inputs, "reference.json": ref, "reference_review.json": ref_review,
                     "workflow.json": workflow, "workflow_review.json": workflow_review, "assessment.json": assessment,
                     "scores.json": {"supported_claim_precision": 1, "obligation_completeness": "complete"}}
        for name, data in artifacts.items():
            write(folder / name, data)
        self.repin(folder, extra={self.recorded(original): "0" * 64})
        write(folder / "state.json", {"case_id": cid, "status": "model_reviewed_reference_candidate",
                                      "stage": "done", "repairs_used": 0, "attempts": []})
        self.sources[mid] = {"case_id": cid, "status": "registered_for_source_first_annotation",
            "packet": self.recorded(packet), "packet_sha256": file_hash(packet), "sources": 2, "supplement_count": 1}
        self.manifest.append({"case_id": cid, "packet": self.recorded(packet), "packet_sha256": file_hash(packet)})
        self.refresh()
        return folder

    def repin(self, folder, *, extra=None):
        pins = {self.recorded(path): file_hash(path) for path in folder.glob("*.json")
                if path.name not in {"state.json", "bindings.json"}}
        pins.update(extra or {})
        write(folder / "bindings.json", pins)

    def v2(self, index=0, *, outcome="retained", zero_unknowns=False):
        folder = self.work / "references/cases" / f"c{index:02}"
        names = ("reference", "reference_review", "workflow", "workflow_review", "assessment")
        values = {name: json.loads((folder / (name + ".json")).read_bytes()) for name in names}
        reference, workflow, assessment = (values[name] for name in ("reference", "workflow", "assessment"))
        if zero_unknowns:
            reference["unknowns"] = []
            values["reference_review"]["unknown_checks"] = []
        else:
            workflow["unresolved"] = [{"id": "source-unknown", "description": reference["unknowns"][0]["question"],
                "blocking": False, "next_evidence": reference["unknowns"][0]["next_evidence"]}]
        assessment.update(format_version="protocol_benchmark_assessment/1.1",
            inspected_passages=[{"source_id": "main", "passage_id": "p1"}], unknown_checks=[])
        if not zero_unknowns:
            assessment["unknown_checks"] = [{"unknown_id": "u1", "outcome": outcome,
                "targets": ["gap:source-unknown"] if outcome in {"retained", "partial"} else [],
                "rationale": "The declared reviewer assessed this scoped unknown.",
                "evidence": reference["obligations"][0]["evidence"] if outcome == "reference_revision_needed" else []}]
        assessment["reference_sha256"] = values["reference_review"]["reference_sha256"] = reporter.object_digest(reference)
        assessment["workflow_sha256"] = values["workflow_review"]["workflow_sha256"] = reporter.object_digest(workflow)
        for name, data in values.items():
            write(folder / (name + ".json"), data)
        self.repin(folder)
        return folder

    def compare(self, index=0):
        task = json.loads(self.comparisons.read_bytes())["comparisons"][index]
        folder = self.work / "comparisons/cases" / task["comparison_id"]
        reference = {"comparison_id": task["comparison_id"], "task_sha256": reporter.object_digest(task),
            "claims": [], "no_claims_reason": "No claim established in inspected scope.",
            **{side: {"case_id": task[side + "_case_id"], "reference_sha256": "a" * 64,
                      "source_packet_sha256": "b" * 64} for side in ("left", "right")}}
        for side in ("left", "right"):
            ref_path = self.work / "references/cases" / task[side + "_case_id"] / "reference.json"
            if ref_path.is_file():
                ref = json.loads(ref_path.read_bytes())
                reference[side].update(reference_sha256=reporter.object_digest(ref),
                    source_packet_sha256=ref["source_packet_sha256"])
        review = {"comparison_sha256": reporter.object_digest(reference), "task_sha256": reference["task_sha256"],
            "claim_checks": [], "unit_checks": [{"side": side, "verdict": "supported"} for side in ("left", "right")],
            "no_claims_check": "supported", **{side + "_" + field: reference[side][field]
                for side in ("left", "right") for field in ("reference_sha256", "source_packet_sha256")}}
        for name, data in (("input.json", {}), ("reference.json", reference), ("review.json", review),
                           ("scores.json", {}), ("relations.json", {"relations": []})):
            write(folder / name, data)
        write(folder / "state.json", {"status": "supported_comparison_candidate", "attempts": []})
        self.repin(folder)
        return folder

    def report(self, *, mapping=True):
        return reporter.build_report(self.design, self.work,
            original_work_root=self.remote if mapping else None)


def test_missing_cases_remain_in_twelve_case_denominator_and_six_strata(tmp_path):
    phase = Phase(tmp_path)
    phase.case()
    result = phase.report()
    assert len(result["cases"]) == result["stage_counts"]["denominator"] == 12
    assert len(result["strata_unweighted_case_counts"]) == 6
    assert result["stage_counts"]["source_ready"] == 1
    assert result["cases"][0]["full_model_candidate_coverage"]
    assert result["cases"][1]["obligations"]["status"] == "not_assessed"
    assert result["cases"][1]["obligations"]["covered_fraction_of_declared_obligations"] is None
    assert result["comparisons"]["denominator"] == 8
    assert result["coverage_status"] == "pending_or_incomplete"


@pytest.mark.parametrize("fault,expected", [("partial", "required"), ("critical_missing", "critical")])
def test_runtime_complete_label_cannot_hide_partial_or_missing_obligation(tmp_path, fault, expected):
    phase = Phase(tmp_path)
    phase.case(**{fault: True})
    row = phase.report()["cases"][0]
    assert row["recorded_runtime_status"] == "model_reviewed_reference_candidate"
    assert row["obligations"]["status"] == "partial"
    assert expected in row["obligations"]["required_not_fully_covered"]
    assert not row["full_model_candidate_coverage"]
    assert row["failures"]["graph"][0]["stage"] == "obligation_assessment"


def test_empty_archive_has_no_zero_scientific_scores_or_precision(tmp_path):
    result = Phase(tmp_path).report()
    assert result["stage_counts"]["source_ready"] == 0
    assert all(row["obligations"]["denominator"] is None for row in result["cases"])
    assert all(row["factual_audit"]["declared_row_supported_fraction"] is None for row in result["cases"])
    assert result["comparisons"]["denominator"] == 8
    assert result["sessions"]["result_record_count"] == 0
    assert result["coverage_status"] == "pending_or_incomplete"


def test_source_ready_subset_cannot_redefine_the_benchmark_denominator(tmp_path):
    phase = Phase(tmp_path)
    phase.case()
    document = json.loads(phase.design.read_bytes())
    document.update(cases=document["cases"][:1], selection={"cases": 1, "families": 1})
    write(phase.design, document)
    phase.refresh()
    result = phase.report()
    assert result["design"]["case_count"] == 1
    assert result["design"]["expected_case_count"] == result["stage_counts"]["denominator"] == 12
    assert result["coverage_status"] == "pending_or_incomplete"


def test_wrong_packet_hash_is_custody_failure_separate_from_source_judgment(tmp_path):
    phase = Phase(tmp_path)
    phase.case()
    phase.manifest[0]["packet_sha256"] = "0" * 64
    phase.refresh()
    row = phase.report()["cases"][0]
    assert not row["source"]["manifest_pins_agree"]
    assert any(pin["status"] == "mismatch" for pin in row["source"]["manifest_packet_checks"])
    assert row["failures"]["source"][0]["category"] == "packet_pin_conflict"
    assert row["reference_review"]["status"] == "model_review_supported"
    assert not row["full_model_candidate_coverage"]


def test_acquisition_failure_never_becomes_graph_failure_or_extraction_zero(tmp_path):
    phase = Phase(tmp_path)
    phase.case(source_status="source_acquisition_needed")
    folder = phase.work / "references/cases/c01"
    write(folder / "state.json", {"status": "graph_constructor_failed", "issue_stage": "graph_constructor",
        "stage": "graph_constructor", "error_category": "invalid_artifact", "error": "Invalid graph artifact."})
    result = phase.report()
    source, graph = result["cases"][:2]
    assert source["failures"]["source"][0]["category"] == "source_acquisition_needed"
    assert source["failures"]["graph"] == []
    assert source["obligations"]["status"] == "not_assessed"
    assert graph["failures"]["source"] == []
    assert graph["failures"]["graph"][0]["stage"] == "graph_constructor"


def test_complete_declared_narrow_audit_never_yields_whole_graph_precision(tmp_path):
    phase = Phase(tmp_path)
    phase.case()
    row = phase.report()["cases"][0]
    audit = row["factual_audit"]
    assert audit["declared_scope"] == "complete_declared"
    assert audit["declared_row_supported_fraction"] == 1
    assert audit["supported_claim_precision"] is None
    assert audit["unaudited_target_count"] == audit["unjudged_target_count"] == 3
    assert row["unresolved_reference_unknown_count"] == 1
    assert row["graph_unknown_preservation"] == "not_assessed"


@pytest.mark.parametrize("verdict,eligible", [("unsupported", False), ("uncertain", True), ("unassessed", True)])
def test_explicit_unsupported_audit_blocks_candidate_without_changing_obligation_counts(tmp_path, verdict, eligible):
    phase = Phase(tmp_path)
    folder = phase.case()
    assessment = json.loads((folder / "assessment.json").read_bytes())
    assessment["factual_audits"][0]["verdict"] = verdict
    write(folder / "assessment.json", assessment)
    phase.repin(folder)
    result = phase.report()
    row = result["cases"][0]
    assert row["workflow_review"]["status"] == "model_review_supported"
    assert row["obligations"]["status"] == "full"
    assert row["obligations"]["counts"] == {"covered": 2, "missing": 0, "partial": 0, "unassessed": 0}
    assert row["full_model_candidate_coverage"] is eligible
    assert result["stage_counts"]["full_declared_candidate_coverage"] == int(eligible)
    assert row["factual_audit"]["counts"] == {verdict: 1}
    assert row["factual_audit"]["unsupported_factual_audit_ids"] == (["audit1"] if verdict == "unsupported" else [])
    if verdict == "unsupported":
        assert row["failures"]["graph"][0]["category"] == "factual_assertions_unsupported"


@pytest.mark.parametrize("selected,repairs,status", [(1, 0, "first_pass"),
    (2, 1, "after_one_data_correction"), (1, 1, "first_pass_retained_after_failed_correction")])
def test_declared_graph_correction_history_and_round_hashes_are_visible(tmp_path, selected, repairs, status):
    phase = Phase(tmp_path)
    folder = phase.case()
    state = json.loads((folder / "state.json").read_bytes())
    rounds = [{"round": number, "status": "graph_needs_revision" if number == 1 and repairs else
        "model_reviewed_reference_candidate", "workflow_sha256": str(number) * 64,
        "workflow_review_sha256": "a" * 64, "assessment_sha256": "b" * 64, "scores_sha256": "c" * 64}
        for number in range(1, selected + 1)]
    state.update(graph_repairs_used=repairs, selected_graph_round=selected, graph_rounds=rounds)
    write(folder / "state.json", state)
    result = phase.report()
    row = result["cases"][0]
    assert row["graph_round_history_status"] == status
    assert row["graph_repairs_used"] == repairs and row["selected_graph_round"] == selected
    assert row["graph_rounds_count"] == len(rounds) and row["graph_rounds"] == rounds
    assert row["selected_graph_round_hashes"]["workflow_sha256"] == str(selected) * 64
    text = reporter.markdown(result)
    assert status in text and str(selected) * 64 in text


def test_legacy_graph_history_is_not_inferred_from_attempt_count(tmp_path):
    phase = Phase(tmp_path)
    folder = phase.case()
    state = json.loads((folder / "state.json").read_bytes())
    state["attempts"] = [{"role": "graph_constructor"}, {"role": "graph_reviewer"}] * 3
    write(folder / "state.json", state)
    row = phase.report()["cases"][0]
    assert row["graph_round_history_status"] == "not_assessed"
    assert row["graph_repairs_used"] is row["selected_graph_round"] is row["graph_rounds_count"] is None
    assert row["graph_rounds"] is None and row["selected_graph_round_hashes"] == {}


@pytest.mark.parametrize("use_module_gap_witnesses", [True, False])
def test_benchmark_module_gap_targets_are_distinct_from_extraction_review_targets(
        tmp_path, use_module_gap_witnesses):
    phase = Phase(tmp_path)
    folder = phase.case()
    workflow = json.loads((folder / "workflow.json").read_bytes())
    workflow.update(modules=[{"id": "reported-unit"}], unresolved=[{"id": "context-gap", "blocking": False}])
    assessment = json.loads((folder / "assessment.json").read_bytes())
    review = json.loads((folder / "workflow_review.json").read_bytes())
    review["workflow_sha256"] = assessment["workflow_sha256"] = reporter.object_digest(workflow)
    benchmark_only_targets = ["module:reported-unit", "gap:context-gap"]
    if use_module_gap_witnesses:
        for obligation, target in zip(assessment["obligations"], benchmark_only_targets):
            obligation["targets"] = [target]
        assessment["factual_audits"][0]["targets"] = benchmark_only_targets
    for filename, data in (("workflow.json", workflow), ("workflow_review.json", review),
                           ("assessment.json", assessment)):
        write(folder / filename, data)
    phase.repin(folder)
    row = phase.report()["cases"][0]
    assert row["workflow_review"]["status"] == "model_review_supported"
    assert row["obligations"]["accounting_valid"]
    assert row["full_model_candidate_coverage"]
    audit = row["factual_audit"]
    assert audit["workflow_target_count"] == 6
    assert audit["foreign_audit_target_ids"] == []
    if use_module_gap_witnesses:
        assert audit["unaudited_target_count"] == audit["unjudged_target_count"] == 4
        assert not set(benchmark_only_targets) & set(audit["unaudited_target_ids"])
    else:
        assert audit["unaudited_target_count"] == audit["unjudged_target_count"] == 5
        assert set(benchmark_only_targets) <= set(audit["unaudited_target_ids"])


def test_abstentions_remain_in_declared_row_denominator(tmp_path):
    phase = Phase(tmp_path)
    folder = phase.case()
    assessment = json.loads((folder / "assessment.json").read_bytes())
    assessment["factual_audits"].append({"id": "abstain", "verdict": "abstained", "targets": ["state:out"]})
    write(folder / "assessment.json", assessment)
    audit = phase.report()["cases"][0]["factual_audit"]
    assert audit["denominator"] == 2
    assert audit["declared_row_supported_fraction"] == .5
    assert audit["supported_claim_precision"] is None
    assert audit["unjudged_target_count"] == 3


def test_original_source_files_are_not_read_even_when_local_and_pinned(tmp_path, monkeypatch):
    phase = Phase(tmp_path)
    phase.case()
    original = phase.work / "originals/c00.txt"
    read_bytes = Path.read_bytes

    def bounded_read(path):
        if path == original:
            raise AssertionError("Reporter must not audit original source files")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", bounded_read)
    row = phase.report()["cases"][0]
    original_pin = next(pin for pin in row["artifact_pin_checks"] if pin["recorded_path"] == str(original))
    assert original_pin["status"] == "source_original_not_audited"
    assert row["full_model_candidate_coverage"]


def test_archive_path_mapping_is_explicit_and_keeps_original_strings(tmp_path):
    phase = Phase(tmp_path, remote="/remote/immutable/phase")
    phase.case()
    unmapped = phase.report(mapping=False)["cases"][0]
    mapped = phase.report()["cases"][0]
    assert not unmapped["full_model_candidate_coverage"]
    assert mapped["full_model_candidate_coverage"]
    assert all(pin["recorded_path"].startswith("/remote/immutable/phase/")
               for pin in mapped["source"]["manifest_packet_checks"])
    assert all(pin["resolution"] == "explicit_original_work_root"
               for pin in mapped["source"]["manifest_packet_checks"])


def test_modified_artifact_or_foreign_review_digest_blocks_candidate(tmp_path):
    phase = Phase(tmp_path)
    folder = phase.case()
    review = json.loads((folder / "reference_review.json").read_bytes())
    review["reference_sha256"] = "f" * 64
    write(folder / "reference_review.json", review)
    first = phase.report()["cases"][0]
    assert any(pin["status"] == "mismatch" for pin in first["local_pin_failures"])
    phase.repin(folder)
    second = phase.report()["cases"][0]
    assert not second["local_pin_failures"]
    assert not second["full_model_candidate_coverage"]
    assert any(check["status"] == "mismatch" for check in second["archived_object_binding_checks"])


def test_session_totals_use_result_metadata_not_provider_item_events(tmp_path):
    phase = Phase(tmp_path)
    folder = phase.case()
    attempt = folder / "attempts/001_reference_annotator"
    write(attempt / "result.json", {"exit_code": 0, "thread_id": "session1", "elapsed_seconds": 12.5,
                                   "usage": {"input_tokens": 10, "output_tokens": 4}})
    write(attempt / "events.json", {"usage": {"input_tokens": 999999}})
    state = json.loads((folder / "state.json").read_bytes())
    state["repairs_used"] = 1
    write(folder / "state.json", state)
    sessions = phase.report()["sessions"]
    assert sessions["usage"] == {"input_tokens": 10, "output_tokens": 4}
    assert sessions["summed_session_elapsed_seconds"] == 12.5
    assert sessions["declared_data_repairs"] == 1
    assert sessions["by_stratum"]["f0"]["session_count"] == 1


def test_empty_independently_reviewed_comparison_counts_without_forced_claim(tmp_path):
    phase = Phase(tmp_path)
    phase.case(0)
    phase.case(1)
    phase.compare()
    comparisons = phase.report()["comparisons"]
    assert comparisons["denominator"] == 8
    assert comparisons["reviewed_candidates_recorded"] == 1
    assert comparisons["cases"][0]["authored_claim_count"] == 0
    assert comparisons["cases"][0]["claim_judgment_counts"] == {}
    assert not comparisons["cases"][1]["reviewed_candidate_recorded"]


def test_orphaned_comparison_cannot_satisfy_prerequisite_coverage(tmp_path):
    phase = Phase(tmp_path)
    phase.compare()
    comparison = phase.report()["comparisons"]["cases"][0]
    assert comparison["declared_review_supported"]
    assert not comparison["prerequisite_case_gates_recorded"]
    assert not comparison["reviewed_candidate_recorded"]
    assert "left archived reference" in comparison["metadata_conflicts"]


def test_all_legacy_stages_still_leave_new_uncertainty_criterion_not_assessed(tmp_path):
    phase = Phase(tmp_path)
    for index in range(12):
        phase.case(index)
    for index in range(8):
        phase.compare(index)
    result = phase.report()
    assert result["coverage_status"] == "pending_or_incomplete"
    assert result["comparisons"]["reviewed_candidates_recorded"] == 8
    assert result["stage_counts"]["full_declared_candidate_coverage"] == 12
    assert result["scientific_reference_completeness"] == "not_established"
    assert result["graph_unknown_preservation"] == "not_assessed"
    assert result["human_gold"] is False


@pytest.mark.parametrize("outcome,expected", [("retained", "retained"), ("partial", "incomplete"),
    ("missing", "incomplete"), ("unassessed", "incomplete"), ("reference_revision_needed", "incomplete")])
def test_report_shows_v2_unknown_judgments_separate_from_known_obligations(tmp_path, outcome, expected):
    phase = Phase(tmp_path)
    phase.case()
    phase.v2(outcome=outcome)
    result = phase.report()
    row = result["cases"][0]
    assert row["obligations"]["status"] == "full"
    assert row["graph_unknown_preservation"] == expected
    assert row["unknown_preservation"]["counts"][outcome] == 1
    assert row["unknown_preservation"]["denominator"] == row["unresolved_reference_unknown_count"] == 1
    assert row["factual_audit"]["supported_claim_precision"] is None
    assert row["full_model_candidate_coverage"] == (outcome == "retained")
    if outcome == "retained":
        assert "gap:source-unknown" in reporter.markdown(result)


@pytest.mark.parametrize("fault", ["missing_check", "unknown_witness", "quote", "workflow_hash"])
def test_report_does_not_trust_corrupted_v2_unknown_accounting(tmp_path, fault):
    phase = Phase(tmp_path)
    phase.case()
    folder = phase.v2()
    assessment = json.loads((folder / "assessment.json").read_bytes())
    if fault == "missing_check":
        assessment["unknown_checks"] = []
    elif fault == "unknown_witness":
        assessment["unknown_checks"][0]["targets"] = ["gap:foreign-workflow"]
    elif fault == "quote":
        assessment["unknown_checks"][0]["evidence"] = [
            {"source_id": "main", "passage_id": "p1", "quote": "Invented source quote."}]
    else:
        assessment["workflow_sha256"] = "0" * 64
    write(folder / "assessment.json", assessment)
    phase.repin(folder)
    row = phase.report()["cases"][0]
    assert not row["full_model_candidate_coverage"]
    if fault != "workflow_hash":
        assert row["unknown_preservation"]["status"] == "invalid_accounting"


def test_zero_unknown_v2_report_is_not_applicable_without_perfect_fraction(tmp_path):
    phase = Phase(tmp_path)
    phase.case()
    phase.v2(zero_unknowns=True)
    row = phase.report()["cases"][0]
    assert row["unknown_preservation"]["status"] == "not_applicable"
    assert row["unknown_preservation"]["denominator"] == 0
    assert row["unknown_preservation"]["strict_retained_fraction"] is None


def test_all_v2_stages_with_retained_unknowns_still_are_not_scientific_gold(tmp_path):
    phase = Phase(tmp_path)
    for index in range(12):
        phase.case(index)
        phase.v2(index)
    for index in range(8):
        phase.compare(index)
    result = phase.report()
    assert result["coverage_status"] == "all_designed_model_candidate_stages_recorded"
    assert result["graph_unknown_preservation"] == "retained"
    assert result["human_gold"] is False
    assert result["scientific_reference_completeness"] == "not_established"


def test_exact_generated_obligation_view_preserves_evidence_and_unknowns(tmp_path):
    phase = Phase(tmp_path)
    phase.case(partial=True)
    text = reporter.markdown(phase.report())
    for literal in ("Retain the source-defined material.", "Defines the assay input.", "operation:transform",
                    "Exact quoted source.", "Unreported association?", "Acquire cited supplement.",
                    "Whole-graph precision is withheld", "No pooled benchmark accuracy"):
        assert literal in text


def test_cli_writes_new_report_only_and_refuses_overwrite(tmp_path):
    phase = Phase(tmp_path)
    phase.case()
    output = tmp_path / "report"
    before = {path: file_hash(path) for path in phase.work.rglob("*") if path.is_file()}
    argv = ["--design", str(phase.design), "--work", str(phase.work), "--output", str(output)]
    assert reporter.main(argv) == 0
    assert json.loads((output / "report.json").read_bytes())["design"]["case_count"] == 12
    assert (output / "walkthrough.md").is_file()
    assert before == {path: file_hash(path) for path in phase.work.rglob("*") if path.is_file()}
    with pytest.raises(FileExistsError):
        reporter.main(argv)
