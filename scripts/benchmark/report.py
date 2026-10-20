"""Offline design-denominated benchmark reporting; never re-audit original sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

EXPECTED_CASES, EXPECTED_STRATA, EXPECTED_COMPARISONS = 12, 6, 8


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def object_digest(value):
    """Canonical JSON binding of an archived object, independent of file layout."""
    return sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode())


class Archive:
    """Read only the selected local phase; mapping is explicit and recorded."""

    def __init__(self, work, original_work_root=None):
        self.work = Path(work).resolve()
        self.original_root = Path(original_work_root) if original_work_root else None
        if self.original_root and not self.original_root.is_absolute():
            raise ValueError("original-work-root must be the exact absolute original phase path")
        self.reads, self.errors, self.originals = {}, [], set()

    def resolve(self, recorded, base=None):
        value = Path(recorded)
        result = {"recorded_path": str(recorded), "local_path": None, "resolution": "unmapped_external"}
        if value.is_absolute():
            if value.is_relative_to(self.work):
                local, mode = value, "exact_local"
            elif self.original_root and value.is_relative_to(self.original_root):
                local, mode = self.work / value.relative_to(self.original_root), "explicit_original_work_root"
            else:
                return result
        else:
            local, mode = (base or self.work) / value, "relative_local"
        local = local.resolve()
        if not local.is_relative_to(self.work):
            result["resolution"] = "outside_local_work"
            return result
        result.update(local_path=str(local), resolution=mode)
        return result

    def read(self, path):
        resolved = self.resolve(path)
        if resolved["local_path"] is None:
            return None
        local = Path(resolved["local_path"])
        if not local.is_file():
            return None
        try:
            raw = local.read_bytes()
            self.reads.setdefault(str(local), sha_bytes(raw))
            return json.loads(raw)
        except (OSError, ValueError, UnicodeError) as exc:
            self.errors.append({"local_path": str(local), "category": "unreadable_artifact",
                                "error": f"{type(exc).__name__}: {exc}"})
            return None

    def linked(self, recorded, base=None):
        resolved = self.resolve(recorded, base)
        return self.read(resolved["local_path"]) if resolved["local_path"] else None

    def pin(self, recorded, expected, base=None):
        result = {**self.resolve(recorded, base), "expected_sha256": expected}
        if str(recorded) in self.originals:
            result["status"] = "source_original_not_audited"
        elif result["local_path"] is None:
            result["status"] = "unavailable_external"
        elif not Path(result["local_path"]).is_file():
            result["status"] = "missing_local_artifact"
        elif not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            result["status"] = "invalid_declared_hash"
        else:
            try:
                actual = sha_bytes(Path(result["local_path"]).read_bytes())
                self.reads.setdefault(result["local_path"], actual)
                result.update(actual_sha256=actual, status="matched" if actual == expected else "mismatch")
            except OSError as exc:
                result.update(status="unreadable_local_artifact", error=str(exc))
        return result


def _rows(document, key):
    value = document.get(key, {}) if isinstance(document, dict) else {}
    return list(value.values()) if isinstance(value, dict) else value if isinstance(value, list) else []


def _review(reference, review):
    if not isinstance(review, dict):
        return {"status": "not_archived", "counts": {}}
    if not isinstance(reference, dict):
        return {"status": "reference_unavailable", "counts": {}}
    counts, valid = Counter(), True
    for original, checked in (("obligations", "obligation_checks"), ("unknowns", "unknown_checks"),
                               ("admissible_decompositions", "decomposition_checks"),
                               ("query_cases", "query_checks")):
        ids = [row.get("id") for row in reference.get(original, [])]
        actual = [row.get("target_id") for row in review.get(checked, [])]
        valid &= len(ids) == len(set(ids)) and len(actual) == len(set(actual)) and set(actual) == set(ids)
        counts.update(row.get("verdict", "unassessed") for row in review.get(checked, []))
    status = ("invalid_accounting" if not valid else "needs_revision" if
        counts["needs_revision"] or review.get("missing_obligations") else "research_needed" if
        counts["uncertain"] else "model_review_supported" if counts and set(counts) == {"supported"}
        else "unassessed")
    return {"status": status, "counts": dict(counts),
            "missing_obligation_count": len(review.get("missing_obligations", []))}


def _workflow_review(workflow, review):
    if not isinstance(review, dict):
        return {"status": "not_archived", "counts": {}}
    if not isinstance(workflow, dict):
        return {"status": "workflow_unavailable", "counts": {}}
    expected = {f"{kind}:{row['id']}" for kind, field in
        (("state", "states"), ("operation", "operations"), ("flow", "flows"))
        for row in workflow.get(field, []) if "id" in row}
    expected |= {f"resolution:{row['reader_role']}:{row['gap_id']}"
                 for row in workflow.get("gap_resolutions", [])}
    actual = [row.get("target_id") for row in review.get("checks", [])]
    counts = Counter(row.get("verdict", "unassessed") for row in review.get("checks", []))
    omissions = review.get("omissions", [])
    status = ("invalid_accounting" if not expected or len(actual) != len(set(actual)) or set(actual) != expected
        else "needs_revision" if counts["unsupported"] or any(row.get("action") == "revise_record" for row in omissions)
        else "research_needed" if counts["unresolved"] or any(row.get("blocking") for row in
            [*omissions, *workflow.get("unresolved", [])]) else "model_review_supported" if
        counts and set(counts) == {"supported"} else "unassessed")
    return {"status": status, "counts": dict(counts), "omissions": omissions}


def _obligations(reference, assessment, workflow):
    reference = reference if isinstance(reference, dict) else {}
    expected = reference.get("obligations", [])
    rows = assessment.get("obligations", []) if isinstance(assessment, dict) else []
    ids = Counter(row.get("obligation_id") for row in rows)
    by_id = {row.get("obligation_id"): row for row in rows}
    outcomes = {row["id"]: by_id.get(row["id"], {}).get("outcome", "unassessed")
                if ids[row["id"]] == 1 else "unassessed" for row in expected}
    allowed = {"covered", "partial", "missing", "unassessed"}
    witnesses_valid = all(row.get("outcome") in allowed and
        not (set(row.get("targets", [])) - _benchmark_targets(workflow)) and
        (row.get("outcome") not in {"covered", "partial"} or
         bool(row.get("targets")) and bool(row.get("evidence"))) for row in rows)
    valid = bool(expected) and len({row["id"] for row in expected}) == len(expected) and witnesses_valid and (
        set(ids) <= {row["id"] for row in expected}) and all(count == 1 for count in ids.values())
    critical = [row["id"] for row in expected if row.get("criticality") == "critical"
                and outcomes[row["id"]] != "covered"]
    required = [row["id"] for row in expected if outcomes[row["id"]] != "covered"]
    counts = Counter(outcomes.values())
    return {"denominator": len(expected) if expected else None,
            "status": "full" if valid and not required else "partial" if expected and rows else "not_assessed",
            "covered_fraction_of_declared_obligations": counts["covered"] / len(expected) if expected and rows else None,
            "counts": {key: counts[key] for key in ("covered", "partial", "missing", "unassessed")},
            "critical_not_covered": critical, "required_not_fully_covered": required,
            "accounting_valid": valid, "extra_obligation_ids": sorted(set(ids) - set(outcomes)),
            "unknown_outcome_ids": [key for key, value in outcomes.items() if value not in allowed],
            "details": [{**row, "assessment": by_id.get(row["id"]), "assessed_outcome": outcomes[row["id"]]}
                        for row in expected]}


def _targets(workflow):
    """Extraction-review targets; module boundaries and retained gaps are excluded."""
    if not isinstance(workflow, dict):
        return set()
    result = {f"{kind}:{row['id']}" for kind, field in
        (("state", "states"), ("operation", "operations"), ("flow", "flows"))
        for row in workflow.get(field, []) if "id" in row}
    return result | {f"resolution:{row['reader_role']}:{row['gap_id']}"
                     for row in workflow.get("gap_resolutions", [])}


def _benchmark_targets(workflow):
    """Benchmark witnesses additionally include authored modules and retained gaps."""
    if not isinstance(workflow, dict):
        return set()
    return _targets(workflow) | {f"{kind}:{row['id']}" for kind, field in
        (("module", "modules"), ("gap", "unresolved"))
        for row in workflow.get(field, []) if "id" in row}


def _unknown_preservation(reference, assessment, workflow, packet):
    """Inspect archived 1.1 judgments; never infer retention from legacy outputs."""
    expected = reference.get("unknowns", []) if isinstance(reference, dict) else []
    version = assessment.get("format_version", "protocol_benchmark_assessment/1.0") if isinstance(assessment, dict) else None
    if version != "protocol_benchmark_assessment/1.1" or not isinstance(reference, dict):
        return {"status": "not_assessed", "denominator": len(expected) if isinstance(reference, dict) else None,
            "counts": {}, "strict_retained_fraction": None, "accounting_valid": None,
            "details": [], "boundary": "Legacy/missing assessment has no graph-unknown preservation judgment."}
    rows = assessment.get("unknown_checks", [])
    ids = Counter(row.get("unknown_id") for row in rows)
    by_id = {row.get("unknown_id"): row for row in rows}
    allowed = ("retained", "partial", "missing", "reference_revision_needed", "unassessed")
    outcomes = {item["id"]: by_id.get(item["id"], {}).get("outcome", "unassessed") for item in expected}
    valid = ("unknown_checks" in assessment and len(outcomes) == len(expected) and
             set(ids) == set(outcomes) and all(n == 1 for n in ids.values()))
    inspected = {(item.get("source_id"), item.get("passage_id")) for item in assessment.get("inspected_passages", [])}
    passages = {(source.get("source_id"), passage.get("passage_id")): passage.get("text", "")
        for source in (packet or {}).get("sources", []) for passage in source.get("passages", [])}
    errors = []
    for unknown in expected:
        row = by_id.get(unknown["id"], {})
        targets, evidence = row.get("targets", []), row.get("evidence", [])
        scope = {(item.get("source_id"), item.get("passage_id")) for item in unknown.get("inspected_passages", [])}
        if (row.get("outcome") not in allowed or not row.get("rationale") or len(targets) != len(set(targets)) or
                set(targets) - _benchmark_targets(workflow) or
                row.get("outcome") in {"retained", "partial"} and not targets or
                row.get("outcome") == "reference_revision_needed" and not evidence or
                row.get("outcome") != "unassessed" and (not scope or scope - inspected or scope - set(passages))):
            errors.append(unknown["id"] + ": invalid judgment, witnesses or inspected scope")
        for ev in evidence:
            ref = (ev.get("source_id"), ev.get("passage_id"))
            if ref not in inspected or ref not in passages or not ev.get("quote") or ev["quote"] not in passages[ref]:
                errors.append(unknown["id"] + ": invalid archived evidence reference/quote")
    counts = Counter(outcomes.values())
    valid = bool(valid and not errors)
    not_retained = [key for key, value in outcomes.items() if value != "retained"]
    return {"status": "invalid_accounting" if not valid else "not_applicable" if not expected else
            "retained" if not not_retained else "incomplete", "denominator": len(expected),
        "counts": {key: counts[key] for key in allowed}, "accounting_valid": valid,
        "strict_retained_fraction": counts["retained"] / len(expected) if expected and valid else None,
        "not_fully_retained_ids": not_retained, "errors": errors,
        "missing_unknown_ids": sorted(set(outcomes) - set(ids)),
        "extra_unknown_ids": sorted(set(ids) - set(outcomes)),
        "details": [{**item, "assessment": by_id.get(item["id"])} for item in expected],
        "boundary": "Declared uncertainty-preservation judgments only. Local quotation integrity does not establish source entailment or resolve the reference unknown."}


def _factual_audit(assessment, workflow):
    rows = assessment.get("factual_audits", []) if isinstance(assessment, dict) else []
    counts = Counter(row.get("verdict", "unassessed") for row in rows)
    declared = assessment.get("factual_audit_scope", "not_archived") if isinstance(assessment, dict) else "not_archived"
    targets = _benchmark_targets(workflow)
    audited = {target for row in rows for target in row.get("targets", [])}
    judged = {target for row in rows if row.get("verdict") in {"supported", "unsupported"}
              for target in row.get("targets", [])}
    return {"denominator": len(rows), "counts": dict(counts), "declared_scope": declared,
            "unsupported_factual_audit_ids": [row.get("id") for row in rows if row.get("verdict") == "unsupported"],
            "supported_claim_precision": None, "whole_graph_precision": "withheld",
            "declared_row_supported_fraction": counts["supported"] / len(rows) if rows else None,
            "workflow_target_count": len(targets) if isinstance(workflow, dict) else None,
            "unaudited_target_ids": sorted(targets - audited),
            "unaudited_target_count": len(targets - audited) if isinstance(workflow, dict) else None,
            "unjudged_target_ids": sorted(targets - judged),
            "unjudged_target_count": len(targets - judged) if isinstance(workflow, dict) else None,
            "foreign_audit_target_ids": sorted(audited - targets),
            "boundary": "Whole-graph precision is withheld regardless of complete_declared. Fractions describe only declared audit rows; abstentions stay in the denominator. Unmatched outputs remain unjudged, never automatically false. These model judgments do not establish scientific accuracy."}


def _graph_history(state):
    """Render declared correction rounds; attempt counts do not establish history."""
    rounds = state.get("graph_rounds")
    repairs, selected = state.get("graph_repairs_used"), state.get("selected_graph_round")
    recorded = isinstance(rounds, list) and type(repairs) is int
    chosen = next((row for row in rounds if isinstance(row, dict) and row.get("round") == selected), {}) if recorded else {}
    status = ("not_assessed" if not recorded else
        "first_pass" if selected == 1 and repairs == 0 else
        "after_one_data_correction" if selected == 2 and repairs == 1 else
        "first_pass_retained_after_failed_correction" if selected == 1 and repairs == 1 else
        "not_selected" if selected is None else "inconsistent_declared_history")
    return {"graph_round_history_status": status, "graph_repairs_used": repairs,
        "selected_graph_round": selected, "graph_rounds_count": len(rounds) if isinstance(rounds, list) else None,
        "graph_rounds": rounds if isinstance(rounds, list) else None,
        "selected_graph_round_hashes": {key: chosen[key] for key in
            ("workflow_sha256", "workflow_review_sha256", "assessment_sha256", "scores_sha256") if key in chosen}}


def _artifact(archive, case_dir, state, filename, role=None):
    value = archive.read(case_dir / filename)
    if value is not None or role is None:
        return value
    attempts = [row for row in state.get("attempts", []) if row.get("role") == role]
    if attempts:
        located = archive.resolve(attempts[-1]["attempt"], case_dir)
        if located["local_path"]:
            return archive.read(Path(located["local_path"]) / "artifact.json")
    return None


def _case(archive, design, source_rows, manifests):
    cid, mid = design["case_id"], design.get("method_id")
    source = source_rows.get(cid, source_rows.get(mid, {}))
    folder = archive.work / "references/cases" / cid
    state = archive.read(folder / "state.json") or {}
    inputs = archive.read(folder / "input.json") or {}
    declarations = [(row, base) for rows, base in manifests for row in rows if row.get("case_id") == cid]
    if source.get("packet"):
        declarations.append((source, archive.work / "sources"))
    if inputs:
        declarations.append((inputs, folder))
    hashes = {row["packet_sha256"] for row, _ in declarations if row.get("packet_sha256")}
    packet_checks, packet = [], None
    for row, base in declarations:
        if row.get("packet"):
            packet_checks.append(archive.pin(row["packet"], row.get("packet_sha256"), base))
            packet = packet or archive.linked(row["packet"], base)
    if not packet and source.get("packet"):
        packet = archive.linked(source["packet"], archive.work / "sources")
    if isinstance(packet, dict):
        archive.originals.update(row["original_path"] for row in packet.get("sources", []) if row.get("original_path"))
    reference = _artifact(archive, folder, state, "reference.json", "reference_annotator")
    review = _artifact(archive, folder, state, "reference_review.json", "reference_reviewer")
    workflow = archive.read(folder / "workflow.json")
    graph_review = _artifact(archive, folder, state, "workflow_review.json", "graph_reviewer")
    assessment = archive.read(folder / "assessment.json")
    if isinstance(graph_review, dict) and "workflow_review" in graph_review:
        assessment = assessment or graph_review.get("assessment")
        graph_review = graph_review["workflow_review"]
    reference_review, workflow_review = _review(reference, review), _workflow_review(workflow, graph_review)
    obligations = _obligations(reference, assessment, workflow)
    unknowns = _unknown_preservation(reference, assessment, workflow, packet)
    factual_audit = _factual_audit(assessment, workflow)
    bindings = archive.read(folder / "bindings.json") or {}
    pins = [archive.pin(path, expected, folder) for path, expected in bindings.items()]
    bad_pins = [row for row in [*pins, *packet_checks] if row["status"] in {
        "mismatch", "invalid_declared_hash", "missing_local_artifact", "unreadable_local_artifact"}]
    matched_paths = {row["local_path"] for row in pins if row["status"] == "matched"}
    essentials = [folder / name for name in ("input.json", "reference.json", "reference_review.json", "workflow.json",
                                              "workflow_review.json", "assessment.json")]
    byte_verified = all(str(path) in matched_paths for path in essentials)
    manifest_valid = bool(declarations) and len(hashes) == 1 and bool(packet_checks) and all(
        row["status"] == "matched" for row in packet_checks)
    metadata_conflicts = [key for key in ("case_id", "method_id", "scope", "variant")
        if isinstance(reference, dict) and key in design and reference.get(key) != design[key]]
    binding_checks = []
    for label, artifact, fields in (
        ("reference", reference, {"source_packet_sha256": packet}),
        ("reference_review", review, {"reference_sha256": reference, "source_packet_sha256": packet}),
        ("workflow_review", graph_review, {"workflow_sha256": workflow, "source_packet_sha256": packet}),
        ("assessment", assessment, {"reference_sha256": reference, "workflow_sha256": workflow,
                                    "source_packet_sha256": packet}),
    ):
        if isinstance(artifact, dict):
            for field, bound in fields.items():
                actual = object_digest(bound) if isinstance(bound, dict) else None
                binding_checks.append({"artifact": label, "field": field,
                    "declared": artifact.get(field), "archived_object_sha256": actual,
                    "status": "matched" if actual and artifact.get(field) == actual else "unverifiable" if
                              actual is None else "mismatch"})
    failures = {key: [] for key in ("source", "annotation", "reference_review", "graph")}
    if source.get("status") not in {None, "registered_for_source_first_annotation"}:
        failures["source"].append({"stage": "source_preparation", "category": source.get("status"),
                                    "message": source.get("reason", source.get("acquisition_status"))})
    issue_stage = state.get("issue_stage")
    if issue_stage:
        category = ("source" if issue_stage == "source" else "annotation" if issue_stage == "reference_annotator"
                    else "reference_review" if issue_stage == "reference_reviewer" else "graph")
        failures[category].append({"stage": issue_stage, "category": state.get("error_category"),
                                   "message": state.get("error")})
    if any(pin["status"] in {"mismatch", "invalid_declared_hash"} for pin in packet_checks) or len(hashes) > 1:
        failures["source"].append({"stage": "archived_packet_binding", "category": "packet_pin_conflict",
                                    "message": "Declared packet bytes disagree or an available local byte pin failed."})
    for label, judgment in (("reference_review", reference_review), ("graph", workflow_review)):
        if judgment["status"] in {"needs_revision", "research_needed", "invalid_accounting"}:
            failures[label].append({"stage": label, "category": judgment["status"],
                                    "message": "Recorded review judgment or review accounting blocks supported candidate coverage."})
    if obligations["status"] == "partial":
        failures["graph"].append({"stage": "obligation_assessment", "category": "critical_not_covered" if
            obligations["critical_not_covered"] else "required_not_fully_covered",
            "message": "Declared reference obligations are not all covered by the assessed graph witnesses."})
    if unknowns["status"] in {"incomplete", "invalid_accounting"}:
        failures["graph"].append({"stage": "unknown_preservation", "category": unknowns["status"],
            "message": "Reference unknown preservation is incomplete or invalid in assessment 1.1."})
    if factual_audit["unsupported_factual_audit_ids"]:
        failures["graph"].append({"stage": "factual_audit", "category": "factual_assertions_unsupported",
            "message": "Explicit unsupported factual-audit judgments block positive candidate coverage: "
                + str(factual_audit["unsupported_factual_audit_ids"])})
    source_count = len(packet.get("sources", [])) if isinstance(packet, dict) else source.get("sources")
    supplement_count = sum(row.get("role") == "supplement" for row in packet.get("sources", [])) if (
        isinstance(packet, dict)) else source.get("supplement_count")
    source_status = source.get("status", "source_report_pending_or_not_archived")
    eligible = (state.get("status") == "model_reviewed_reference_candidate" and
        source_status == "registered_for_source_first_annotation" and manifest_valid and
        isinstance(reference, dict) and reference_review["status"] == "model_review_supported" and
        workflow_review["status"] == "model_review_supported" and obligations["status"] == "full" and
        not bad_pins and not metadata_conflicts and byte_verified and binding_checks and
        all(row["status"] == "matched" for row in binding_checks) and
        unknowns["status"] not in {"incomplete", "invalid_accounting"} and
        not factual_audit["unsupported_factual_audit_ids"])
    return {"case_id": cid, "method_id": mid, "family": design.get("family", "unspecified"),
        "split": design.get("split", "unspecified"), "title": design.get("title", cid),
        "source": {"status": source_status, "source_count": source_count,
            "supplement_count": supplement_count, "acquisition_status": source.get("acquisition_status"),
            "manifest_packet_checks": packet_checks, "declared_packet_hashes": sorted(hashes),
            "manifest_pins_agree": manifest_valid},
        "annotation": {"status": reference.get("annotation_status", "artifact_present") if isinstance(reference, dict)
            else "not_archived", "obligation_count": len(reference.get("obligations", [])) if isinstance(reference, dict) else None},
        "reference_review": reference_review, "workflow_review": workflow_review,
        "obligations": obligations, "factual_audit": factual_audit,
        "unknowns": reference.get("unknowns", []) if isinstance(reference, dict) else None,
        "unresolved_reference_unknown_count": len(reference.get("unknowns", [])) if isinstance(reference, dict) else None,
        "graph_unknown_preservation": unknowns["status"], "unknown_preservation": unknowns,
        "source_coverage": reference.get("source_coverage", []) if isinstance(reference, dict) else None,
        "failures": failures, "recorded_runtime_status": state.get("status", "not_archived"),
        "recorded_stage": state.get("stage"), "repairs_used": state.get("repairs_used", 0),
        **_graph_history(state),
        "attempt_records": state.get("attempts", []), "metadata_conflicts": metadata_conflicts,
        "artifact_pin_checks": pins, "archived_object_binding_checks": binding_checks,
        "archived_object_digests": {key: object_digest(value) if isinstance(value, dict) else None
            for key, value in (("reference", reference), ("workflow", workflow), ("packet", packet))},
        "essential_artifact_bytes_verified": byte_verified,
        "local_pin_failures": bad_pins, "full_model_candidate_coverage": eligible,
        "authority": "Model-authored/model-reviewed candidate; no human adjudication established."}


def _comparisons(archive, design, reference_cases):
    results = []
    for row in design.get("comparisons", []):
        cid = row["comparison_id"]
        folder = archive.work / "comparisons/cases" / cid
        state = archive.read(folder / "state.json") or {}
        reference = archive.read(folder / "reference.json") or archive.read(folder / "comparison.json")
        review = archive.read(folder / "review.json")
        bindings = archive.read(folder / "bindings.json") or {}
        pins = [archive.pin(path, expected, folder) for path, expected in bindings.items()]
        matched = {pin["local_path"] for pin in pins if pin["status"] == "matched"}
        byte_verified = all(str(folder / name) in matched for name in
                            ("input.json", "reference.json", "review.json", "scores.json", "relations.json"))
        bad_pins = [pin for pin in pins if pin["status"] in {"mismatch", "invalid_declared_hash",
                    "missing_local_artifact", "unreadable_local_artifact"}]
        claims = reference.get("claims", []) if isinstance(reference, dict) else []
        expected = {claim.get("id") for claim in claims}
        checks = review.get("claim_checks", []) if isinstance(review, dict) else []
        verdicts = Counter(check.get("verdict", "unassessed") for check in checks)
        units = review.get("unit_checks", []) if isinstance(review, dict) else []
        supported = bool(review) and len(units) == 2 and {item.get("side") for item in units} == {"left", "right"} and all(
            item.get("verdict") == "supported" for item in units) and len(checks) == len(expected) and {
            item.get("claim_id") for item in checks} == expected and all(
            item.get("verdict") == "supported" and set(item.get("checked_fields", [])) == {
                "text", "scope", "judgment", "evidence", "preserved_differences", "counterexamples", "unknowns"}
            for item in checks)
        if not claims:
            supported &= bool(reference) and bool(reference.get("no_claims_reason")) and review.get("no_claims_check") == "supported" if review else False
        conflicts = []
        if isinstance(reference, dict):
            if reference.get("comparison_id") != cid or reference.get("task_sha256") != object_digest(row):
                conflicts.append("comparison/task binding")
            for side in ("left", "right"):
                if reference.get(side, {}).get("case_id") != row.get(side + "_case_id"):
                    conflicts.append(side + " case_id")
                bound_case = reference_cases.get(row.get(side + "_case_id"), {})
                digests = bound_case.get("archived_object_digests", {})
                for field, kind in (("reference_sha256", "reference"), ("source_packet_sha256", "packet")):
                    if not digests.get(kind) or reference.get(side, {}).get(field) != digests[kind]:
                        conflicts.append(side + " archived " + kind)
                workflow_hash = reference.get(side, {}).get("unit", {}).get("workflow_sha256")
                if workflow_hash is not None and workflow_hash != digests.get("workflow"):
                    conflicts.append(side + " archived workflow")
            if isinstance(review, dict):
                author_session, reviewer_session = reference.get("annotation_session_id"), review.get("review_session_id")
                if (not isinstance(author_session, str) or not author_session.strip()
                        or not isinstance(reviewer_session, str) or not reviewer_session.strip()
                        or author_session.strip() == reviewer_session.strip()):
                    conflicts.append("comparison author/reviewer session separation")
                expected_bindings = {"comparison_sha256": object_digest(reference),
                    "task_sha256": reference.get("task_sha256")}
                for side in ("left", "right"):
                    for field in ("reference_sha256", "source_packet_sha256"):
                        expected_bindings[side + "_" + field] = reference.get(side, {}).get(field)
                conflicts += [field for field, value in expected_bindings.items()
                              if value is None or review.get(field) != value]
        eligible_cases = all(reference_cases.get(row.get(side + "_case_id"), {}).get(
            "full_model_candidate_coverage", False) for side in ("left", "right"))
        eligible = bool(supported and byte_verified and not bad_pins and not conflicts and eligible_cases and
                        state.get("status") == "supported_comparison_candidate")
        results.append({"comparison_id": cid, "left_case_id": row.get("left_case_id"),
            "right_case_id": row.get("right_case_id"), "split": row.get("split"),
            "annotation_present": bool(reference), "review_present": bool(review),
            "reviewed_candidate_recorded": eligible, "declared_review_supported": bool(supported),
            "review_counts": dict(verdicts), "authored_claim_count": len(claims),
            "essential_artifact_bytes_verified": byte_verified, "artifact_pin_checks": pins,
            "prerequisite_case_gates_recorded": eligible_cases,
            "local_pin_failures": bad_pins, "metadata_conflicts": conflicts,
            "claim_judgment_counts": dict(Counter(claim.get("judgment", "unassessed") for claim in claims)),
            "recorded_runtime_status": state.get("status", "pending_or_not_archived"),
            "recorded_stage": state.get("stage"), "error_category": state.get("error_category"),
            "error": state.get("error"), "attempt_records": state.get("attempts", []),
            "boundary": "No expected positive relation. Not established and unannotated questions are not false labels."})
    return {"denominator": EXPECTED_COMPARISONS, "designed_question_count": len(results),
            "annotated": sum(row["annotation_present"] for row in results),
            "reviewed_candidates_recorded": sum(row["reviewed_candidate_recorded"] for row in results),
            "cases": results}


def _sessions(archive, cases):
    records, usage, threads = [], Counter(), Counter()
    for path in sorted(archive.work.rglob("result.json")):
        data = archive.read(path)
        if not isinstance(data, dict) or not isinstance(data.get("usage"), dict) or "exit_code" not in data:
            continue
        numeric = {key: value for key, value in data["usage"].items()
                   if isinstance(value, (int, float)) and not isinstance(value, bool)}
        usage.update(numeric)
        if data.get("thread_id"):
            threads[data["thread_id"]] += 1
        matched_cases = [case for case in cases if case["case_id"] in path.parts or case["method_id"] in path.parts]
        case = matched_cases[0] if len(matched_cases) == 1 else {}
        records.append({"result_path": str(path), "thread_id": data.get("thread_id"),
                        "case_id": case.get("case_id"), "family": case.get("family"),
                        "elapsed_seconds": data.get("elapsed_seconds"), "usage": numeric,
                        "exit_code": data.get("exit_code"), "error": data.get("error")})
    elapsed = [row["elapsed_seconds"] for row in records if isinstance(row["elapsed_seconds"], (int, float))]
    strata = {}
    for case in cases:
        family = case["family"]
        if family not in strata:
            selected = [row for row in records if row["family"] == family]
            totals = Counter()
            for row in selected:
                totals.update(row["usage"])
            strata[family] = {"session_count": len(selected), "usage": dict(totals),
                "summed_session_elapsed_seconds": sum(row["elapsed_seconds"] for row in selected
                    if isinstance(row["elapsed_seconds"], (int, float)))}
    return {"result_record_count": len(records), "distinct_reported_threads": len(threads),
        "duplicate_reported_thread_ids": [key for key, count in threads.items() if count > 1],
        "usage": dict(usage), "summed_session_elapsed_seconds": sum(elapsed),
        "max_session_elapsed_seconds": max(elapsed) if elapsed else None,
        "declared_data_repairs": sum(row["repairs_used"] or 0 for row in cases),
        "by_stratum": strata, "unassigned_session_count": sum(row["case_id"] is None for row in records),
        "transport_retries": "not_inferred_from_events_or_state",
        "sessions": records, "boundary": "Usage/latency come only from archived per-session result.json metadata, including failures. Provider item events are not parsed; transport retries, liveness and currency cost are not inferred. Duplicate thread IDs are disclosed."}


def build_report(design_path, work, *, comparisons_path=None, original_work_root=None):
    design_path = Path(design_path).resolve()
    design = json.loads(design_path.read_bytes())
    rows = design.get("cases", [])
    if not rows or len({row["case_id"] for row in rows}) != len(rows) or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", row["case_id"]) for row in rows):
        raise ValueError("Design must contain distinct cases; never substitute the source-ready manifest")
    archive = Archive(work, original_work_root)
    source_report = archive.read(archive.work / "sources/source_report.json") or {}
    source_rows = {row.get("case_id"): row for row in _rows(source_report, "cases")}
    source_rows.update({row.get("method_id", key): row for key, row in
        source_report.get("cases", {}).items()})
    source_manifest = archive.read(archive.work / "sources/annotation_manifest.json") or {}
    ref_manifest = archive.read(archive.work / "references/manifest.json") or {}
    manifests = [(_rows(source_manifest, "cases"), archive.work / "sources"),
                 (_rows(ref_manifest, "cases"), archive.work / "references")]
    cases = [_case(archive, row, source_rows, manifests) for row in rows]
    declared_design_hash = source_manifest.get("case_design_sha256")
    design_pin = {"declared_sha256": declared_design_hash, "actual_sha256": sha_bytes(design_path.read_bytes()),
                  "status": "not_declared" if not declared_design_hash else "matched" if
                  declared_design_hash == sha_bytes(design_path.read_bytes()) else "mismatch"}
    comparisons_path = Path(comparisons_path) if comparisons_path else design_path.with_name("comparisons.json")
    comparison_design = json.loads(comparisons_path.read_bytes()) if comparisons_path.is_file() else {"comparisons": []}
    comparison_rows = comparison_design.get("comparisons", [])
    if len({row["comparison_id"] for row in comparison_rows}) != len(comparison_rows) or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", row["comparison_id"]) for row in comparison_rows):
        raise ValueError("Comparison design requires distinct safe comparison IDs")
    comparisons = _comparisons(archive, comparison_design, {row["case_id"]: row for row in cases})
    comparisons["design_path"] = str(comparisons_path)
    comparisons["design_sha256"] = sha_bytes(comparisons_path.read_bytes()) if comparisons_path.is_file() else None
    strata = {}
    for row in cases:
        group = strata.setdefault(row["family"], {"designed": 0, "source_ready": 0,
            "annotations": 0, "reference_reviewed": 0, "workflow_reviewed": 0, "full_coverage": 0,
            "unknown_preservation_assessed": 0, "unknown_preserved_or_na": 0})
        group["designed"] += 1
        group["source_ready"] += row["source"]["status"] == "registered_for_source_first_annotation"
        group["annotations"] += row["annotation"]["status"] != "not_archived"
        group["reference_reviewed"] += row["reference_review"]["status"] == "model_review_supported"
        group["workflow_reviewed"] += row["workflow_review"]["status"] == "model_review_supported"
        group["full_coverage"] += row["full_model_candidate_coverage"]
        group["unknown_preservation_assessed"] += row["graph_unknown_preservation"] != "not_assessed"
        group["unknown_preserved_or_na"] += row["graph_unknown_preservation"] in {"retained", "not_applicable"}
    expected_cases, expected_strata = EXPECTED_CASES, EXPECTED_STRATA
    all_cases = len(rows) == expected_cases and len(strata) == expected_strata and all(
        row["full_model_candidate_coverage"] for row in cases)
    unknown_statuses = {row["graph_unknown_preservation"] for row in cases}
    unknown_status = ("incomplete" if unknown_statuses & {"incomplete", "invalid_accounting"} else
        "not_applicable" if unknown_statuses == {"not_applicable"} else
        "retained" if unknown_statuses <= {"retained", "not_applicable"} else
        "partially_assessed" if unknown_statuses - {"not_assessed"} else "not_assessed")
    comparisons_done = comparisons["designed_question_count"] == comparisons["denominator"] > 0 and comparisons[
        "reviewed_candidates_recorded"] == comparisons["denominator"]
    source_report_declaration = source_manifest.get("source_report", {})
    source_report_pin = archive.pin(source_report_declaration["path"], source_report_declaration.get("sha256"),
        archive.work / "sources") if source_report_declaration.get("path") else {"status": "not_declared"}
    sessions = _sessions(archive, cases)
    changed = []
    for name, expected in archive.reads.items():
        local = archive.resolve(name)["local_path"]
        try:
            if not local or sha_bytes(Path(local).read_bytes()) != expected:
                changed.append(name)
        except OSError:
            changed.append(name)
    unexpected = {label: sorted({row.get("case_id") for row in entries if row.get("case_id")} -
        {row["case_id"] for row in rows}) for label, entries in
        (("source_manifest", manifests[0][0]), ("reference_manifest", manifests[1][0]))}
    stage_counts = {"denominator": expected_cases, "source_ready": sum(group["source_ready"] for group in strata.values()),
        "annotation_present": sum(group["annotations"] for group in strata.values()),
        "reference_review_supported": sum(group["reference_reviewed"] for group in strata.values()),
        "workflow_review_supported": sum(group["workflow_reviewed"] for group in strata.values()),
        "unknown_preservation_assessed": sum(group["unknown_preservation_assessed"] for group in strata.values()),
        "unknown_preserved_or_na": sum(group["unknown_preserved_or_na"] for group in strata.values()),
        "declared_obligations_assessed": sum(row["obligations"]["status"] != "not_assessed" for row in cases),
        "full_declared_candidate_coverage": sum(row["full_model_candidate_coverage"] for row in cases)}
    return {"format_version": "protocol_benchmark_archived_report/1.0",
        "as_of_read_utc": datetime.now(timezone.utc).isoformat(), "work": str(archive.work),
        "original_work_root": str(original_work_root) if original_work_root else None,
        "coverage_status": "all_designed_model_candidate_stages_recorded" if all_cases and comparisons_done and
            unknown_status in {"retained", "not_applicable"} and
            design_pin["status"] == "matched" and source_report_pin["status"] == "matched" and
            not changed and not archive.errors and not any(unexpected.values()) else "pending_or_incomplete",
        "scientific_reference_completeness": "not_established",
        "graph_unknown_preservation": unknown_status, "human_gold": False,
        "coverage_criteria_version": "protocol_benchmark_assessment/1.1",
        "design": {"path": str(design_path), "case_count": len(rows), "expected_case_count": expected_cases,
                   "stratum_count": len(strata), "expected_stratum_count": expected_strata, "pin": design_pin},
        "source_report_pin": source_report_pin, "unexpected_manifest_cases": unexpected,
        "cases": cases, "stage_counts": stage_counts, "strata_unweighted_case_counts": strata,
        "comparisons": comparisons,
        "sessions": sessions, "artifact_read_errors": archive.errors,
        "changed_during_read": changed,
        "boundary": "Archived-only report as of read; phases may still be pending. State JSON does not establish process liveness. Only available local artifact bytes are checked against declared pins, using explicit mapping when supplied. Original source files are not re-read, source completeness/identity/entailment are not re-audited, and model-reviewed candidates are not human gold. Graph construction from references is self-consistency, not blind harness performance."}


def markdown(report):
    def cell(value):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        return text.replace("|", "\\|").replace("\n", "<br>")

    lines = ["# Protocol benchmark walkthrough", "", f"As of {report['as_of_read_utc']}", "",
             f"Coverage: **{report['coverage_status']}**. Designed cases: {report['design']['case_count']}; "
             f"sampling strata: {report['design']['stratum_count']}; comparison questions: {report['comparisons']['denominator']}.",
             "", report["boundary"], "", "Generated view: exact archived requirements, evidence and judgments are rendered below without new scientific annotation. Full obligation coverage means only the declared obligations. Reference unknowns remain unresolved; assessment1.1 separately judges their graph preservation, and legacy1.0 does not assess it. Whole-graph precision is withheld. No pooled benchmark accuracy is computed.",
             "", "## Unweighted case counts by stratum", "",
             "| Stratum | Designed | Source ready | Annotation | Reference reviewed | Workflow reviewed | Full coverage | Unknowns assessed | Unknowns retained/N/A |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, row in report["strata_unweighted_case_counts"].items():
        lines.append(f"| {name} | " + " | ".join(str(value) for value in row.values()) + " |")
    for row in report["cases"]:
        source, obligations = row["source"], row["obligations"]
        lines += ["", f"## {row['case_id']}", "", f"{row['title']} — {row['family']}; {row['split']}.", "",
            f"1. Sources: {source['status']}; registered sources {source['source_count']}, supplements {source['supplement_count']}.",
            f"2. Annotation: {row['annotation']['status']}; reference review: {row['reference_review']['status']}.",
            f"3. Workflow review: {row['workflow_review']['status']}; obligation coverage: {obligations['status']}.",
            f"4. Actual obligation judgments: {obligations['counts']}; denominator {obligations['denominator']}.",
            f"Critical not covered: {obligations['critical_not_covered'] or 'none recorded'}. "
            f"Required not fully covered: {obligations['required_not_fully_covered'] or 'none recorded'}.",
            f"Local essential artifact byte pins verified: {row['essential_artifact_bytes_verified']}; "
            f"manifest packet pins agree: {source['manifest_pins_agree']}."]
        lines += ["", f"Declared graph history: {row['graph_round_history_status']}; "
            f"data corrections used: {row['graph_repairs_used']}; reviewed rounds: {row['graph_rounds_count']}; "
            f"selected round: {row['selected_graph_round']}. History is not inferred from native attempt counts."]
        for graph_round in row["graph_rounds"] or []:
            lines += [f"- Round {cell(graph_round.get('round'))}: {cell(graph_round.get('status'))}; "
                f"declared canonical hashes: {cell({key: value for key, value in graph_round.items() if key.endswith('_sha256')})}."]
        for stage, failures in row["failures"].items():
            for failure in failures:
                lines += ["", f"{stage} issue — {failure['stage']} / {failure['category']}: {failure['message']}"]
        if obligations["details"]:
            lines += ["", "### Exact reference obligations and graph witnesses", "",
                "| ID | Criticality | Requirement | Why needed | Assessed coverage | Target IDs | Source evidence |",
                "|---|---|---|---|---|---|---|"]
            for item in obligations["details"]:
                assessment = item.get("assessment") or {}
                values = (item["id"], item.get("criticality"), item.get("text"), item.get("why_needed"),
                          item["assessed_outcome"], assessment.get("targets", []), item.get("evidence", []))
                lines.append("| " + " | ".join(cell(value) for value in values) + " |")
                if assessment:
                    lines += ["", f"Assessment for {cell(item['id'])}: {cell(assessment.get('rationale'))}. "
                              f"Assessment evidence: {cell(assessment.get('evidence', []))}."]
        for unknown in row["unknowns"] or []:
            lines += ["", f"Unresolved reference unknown {cell(unknown.get('id'))}: {cell(unknown.get('question'))} "
                       f"Consequence: {cell(unknown.get('consequence'))}. Next evidence: {cell(unknown.get('next_evidence'))}. "
                       f"Inspected passages: {cell(unknown.get('inspected_passages', []))}."]
        preservation = row["unknown_preservation"]
        lines += ["", f"Graph unknown preservation: {preservation['status']}; denominator {preservation['denominator']}; "
                  f"judgment counts {preservation['counts']}; strict retained fraction {preservation['strict_retained_fraction']}."]
        for item in preservation["details"]:
            judgment = item.get("assessment") or {}
            lines += [f"- {cell(item['id'])}: outcome {cell(judgment.get('outcome', 'unassessed'))}; "
                      f"witnesses {cell(judgment.get('targets', []))}; rationale {cell(judgment.get('rationale'))}; "
                      f"evidence {cell(judgment.get('evidence', []))}."]
        if row["source_coverage"]:
            lines += ["", "Declared source inspection: " + cell(row["source_coverage"]) + "."]
        audit = row["factual_audit"]
        lines += ["", f"Factual-audit rows: {audit['denominator']}; declared scope: {audit['declared_scope']}; "
                  f"row verdict counts: {audit['counts']}; supported fraction of all declared rows: "
                  f"{audit['declared_row_supported_fraction']}. Unaudited targets: {audit['unaudited_target_count']}; "
                  f"targets without supported/unsupported judgment: {audit['unjudged_target_count']}. "
                  f"Explicit unsupported audit IDs: {audit['unsupported_factual_audit_ids']}. "
                  "Whole-graph precision is withheld. These are recorded model judgments, not scientific accuracy."]
    lines += ["", "## Separate comparison coverage", ""]
    for row in report["comparisons"]["cases"]:
        lines.append(f"- {row['comparison_id']}: annotation={row['annotation_present']}, "
                     f"review={row['review_present']}, reviewed candidate recorded={row['reviewed_candidate_recorded']}.")
    sessions = report["sessions"]
    lines += ["", "## Session metadata", "", f"Result records: {sessions['result_record_count']}; "
        f"reported distinct threads: {sessions['distinct_reported_threads']}; declared data repairs: {sessions['declared_data_repairs']}.",
        "", f"Usage: {sessions['usage']}. Summed session elapsed seconds: {sessions['summed_session_elapsed_seconds']}.",
        "", sessions["boundary"], ""]
    for family, row in sessions["by_stratum"].items():
        lines += [f"- {family}: {row['session_count']} sessions; usage {row['usage']}; "
                  f"summed session seconds {row['summed_session_elapsed_seconds']}."]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparisons", type=Path)
    parser.add_argument("--original-work-root", type=Path)
    args = parser.parse_args(argv)
    report = build_report(args.design, args.work, comparisons_path=args.comparisons,
                          original_work_root=args.original_work_root)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True,
                                                       allow_nan=False) + "\n")
    (args.output / "walkthrough.md").write_text(markdown(report))
    print(json.dumps({"output": str(args.output.resolve()), "coverage_status": report["coverage_status"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
