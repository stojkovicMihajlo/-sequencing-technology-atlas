"""Source-occurrence census and reviewed method-level work planning.

Name matches nominate questions; they never admit identity or schedule extraction.
The archive is a metadata index. Reviewers must inspect registered source content
at the preserved attempt paths before approving source/implementation bindings.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from genomics_discovery.core.artifacts import StrictModel
from genomics_discovery.illumina.models import Evidence

Text = Annotated[str, Field(min_length=1)]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def search_key(name):
    """Lossy retrieval key ONLY. CapSeq/CAP-seq collisions remain explicit questions."""
    return re.sub(r"[\s‐‑–—_-]+", "", name).casefold()


def source_record(record):
    card, sources = record.get("card") or {}, record.get("sources") or {}
    labels = card.get("source_method_labels") or [card.get("source_title") or record["title"]]
    category = card.get("source_category_path") or []
    reasons = []
    if "Transcriptome" in category:
        reasons.append("website_transcriptome_category")
    if "rna_poster" in category:
        reasons.append("rna_poster")
    if any(k.startswith("collection:rna:page:") for k in sources):
        reasons.append("registered_rna_collection_association_unverified")
    return {
        "record_id": record["record_id"], "title": record["title"],
        "source_method_labels": labels, "source_category": category,
        "source_page_url": card.get("source_page_url"),
        "scope_nomination": "rna_context_candidate" if reasons else "screen_for_rna_or_joint_measurement",
        "nomination_reasons": reasons,
        "archived_outcome": record["status"],
        "registered_sources": sources,
        "listed_references": card.get("references") or [],
        "source_registry_path": str(Path(record["attempt_directory"]) / "source_registry.json"),
        "card_path": str(Path(record["attempt_directory"]) / "card.json"),
        "source_status": "archive_metadata_only; inspect_original_registered_content",
    }


def prepare(archive):
    """Preserve every occurrence, including cross-poster rows and supporting panels."""
    rows = []
    with gzip.open(archive, "rt") as handle:
        for line in handle:
            if line.strip():
                rows.append(source_record(json.loads(line)))
    ids = [r["record_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate archive record ID")
    pairs = []
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            names = sorted({search_key(x) for x in left["source_method_labels"]} &
                           {search_key(x) for x in right["source_method_labels"]})
            if names:
                pairs.append({"left": left["record_id"], "right": right["record_id"],
                              "reason": "overlapping_lossy_name_key; not_method_identity",
                              "retrieval_keys": names})
    return {
        "format_version": "transcriptomics_source_census/1",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "status": "planning_input_not_extraction_worklist",
        "selection_policy": "All source occurrences require a disposition. RNA context nominates scope; other categories are screened for RNA/joint measurements and supporting components.",
        "rows": rows, "candidate_identity_pairs": pairs,
        "counts": {"all_source_occurrences": len(rows),
                   "rna_context_candidates": sum(bool(r["nomination_reasons"]) for r in rows),
                   "remaining_scope_checks": sum(not r["nomination_reasons"] for r in rows),
                   "candidate_identity_pairs": len(pairs)},
    }


def subset(census, record_ids):
    """A bounded review includes all nominated identity neighbors, not a new census."""
    available = {r["record_id"] for r in census["rows"]}
    selected = set(record_ids)
    if not selected or selected - available:
        raise ValueError("Choose existing source records for the bounded review")
    while True:
        additions = {x for p in census["candidate_identity_pairs"]
                     if {p["left"], p["right"]} & selected for x in (p["left"], p["right"])}
        if additions <= selected: break
        selected |= additions
    result = {**census, "parent_census_sha256": digest(census),
              "status": "bounded_planning_input_not_full_transcriptomics_coverage",
              "rows": [r for r in census["rows"] if r["record_id"] in selected],
              "candidate_identity_pairs": [p for p in census["candidate_identity_pairs"]
                                           if p["left"] in selected and p["right"] in selected]}
    result["counts"] = {"source_occurrences": len(result["rows"]),
                        "candidate_identity_pairs": len(result["candidate_identity_pairs"])}
    return result


class SourceEvidence(Evidence):
    record_id: Text
    finding: Text = Field(description="What inspected source content establishes about method, variant, or scope. A title match alone does not establish implementation identity.")


class Disposition(StrictModel):
    record_id: Text
    decision: Literal["extract", "support_only", "outside_transcriptomics", "identity_research_needed"]
    reason: Text
    evidence: Annotated[tuple[SourceEvidence, ...], Field(min_length=1)]


class SourceBinding(StrictModel):
    record_id: Text
    selected_scope: Text = Field(description="The method/variant or supporting component used from this depiction; grouped source names are not presumed aliases.")
    role: Literal["primary_depiction", "additional_depiction", "supporting_context"]
    evidence: Annotated[tuple[SourceEvidence, ...], Field(min_length=1)]


class WorkUnit(StrictModel):
    unit_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,99}$")]
    method_name: Text
    implementation_scope: Text
    bindings: Annotated[tuple[SourceBinding, ...], Field(min_length=1)]
    distinction_from_other_units: Text


class IdentityDecision(StrictModel):
    left: Text
    right: Text
    decision: Literal["same_scoped_method", "distinct_implementations", "distinct_methods", "grouped_members", "unresolved", "outside_scope"]
    reason: Text
    evidence: Annotated[tuple[SourceEvidence, ...], Field(min_length=1)]


class WorkPlan(StrictModel):
    format_version: Literal["transcriptomics_work_plan/1"] = "transcriptomics_work_plan/1"
    census_sha256: Text
    dispositions: tuple[Disposition, ...]
    identity_decisions: tuple[IdentityDecision, ...]
    work_units: tuple[WorkUnit, ...]


class Assessment(StrictModel):
    target: Text
    decision: Literal["accept", "reject"]
    reason: Text
    evidence: Annotated[tuple[SourceEvidence, ...], Field(min_length=1)]


class PlanReview(StrictModel):
    format_version: Literal["transcriptomics_work_plan_review/1"] = "transcriptomics_work_plan_review/1"
    plan_sha256: Text
    assessments: tuple[Assessment, ...]


def pair_key(left, right):
    return "pair:" + "|".join(sorted((left, right)))


def validate_plan(census, plan):
    """Mechanical admission; does not replace independent source interpretation."""
    if plan.census_sha256 != digest(census):
        raise ValueError("Plan refers to changed source census")
    rows = {r["record_id"]: r for r in census["rows"]}
    dispositions = {d.record_id: d for d in plan.dispositions}
    if len(dispositions) != len(plan.dispositions) or set(dispositions) != set(rows):
        raise ValueError("Exactly one disposition is required for every source occurrence")
    if any(d.record_id not in {e.record_id for e in d.evidence} for d in plan.dispositions):
        raise ValueError("Each source disposition requires evidence from that occurrence")
    units = {u.unit_id: u for u in plan.work_units}
    if len(units) != len(plan.work_units):
        raise ValueError("Duplicate work unit ID")
    seen_keys, bound = set(), {r: set() for r in rows}
    for unit in plan.work_units:
        key = (unit.method_name.casefold().strip(), unit.implementation_scope.casefold().strip())
        if key in seen_keys:
            raise ValueError("Duplicate method and implementation scope")
        seen_keys.add(key)
        binding_ids = [b.record_id for b in unit.bindings]
        if len(set(binding_ids)) != len(binding_ids) or set(binding_ids) - set(rows):
            raise ValueError("Duplicate or unknown source binding")
        if sum(b.role == "primary_depiction" for b in unit.bindings) != 1:
            raise ValueError("Each unit requires exactly one primary depiction")
        for binding in unit.bindings:
            if binding.record_id not in {e.record_id for e in binding.evidence}:
                raise ValueError("Each source binding requires evidence from that occurrence")
            disposition = dispositions[binding.record_id].decision
            if disposition not in {"extract", "support_only"}:
                raise ValueError("Excluded or unresolved source cannot enter an extraction unit")
            if disposition == "support_only" and binding.role != "supporting_context":
                raise ValueError("Support-only source cannot be an extracted depiction")
            if binding.role != "supporting_context":
                bound[binding.record_id].add(unit.unit_id)
    for record_id, disposition in dispositions.items():
        if disposition.decision == "extract" and not bound[record_id]:
            raise ValueError("Extract source missing from work units")
    expected_pairs = {pair_key(p["left"], p["right"]) for p in census["candidate_identity_pairs"]}
    decisions = {pair_key(d.left, d.right): d for d in plan.identity_decisions}
    if len(decisions) != len(plan.identity_decisions) or set(decisions) != expected_pairs:
        raise ValueError("Every nominated identity pair needs exactly one decision")
    for d in plan.identity_decisions:
        if {d.left, d.right} - {e.record_id for e in d.evidence}:
            raise ValueError("Identity decisions require evidence from both source occurrences")
        active = bool(bound[d.left] or bound[d.right])
        if active and d.decision == "unresolved":
            raise ValueError("Unresolved potential duplicates cannot be dispatched")
        if d.decision == "same_scoped_method" and active:
            if len(bound[d.left]) != 1 or bound[d.left] != bound[d.right]:
                raise ValueError("Same scoped method must share one work unit assignment")
        if d.decision in {"distinct_implementations", "distinct_methods"} and bound[d.left] & bound[d.right]:
            raise ValueError("Distinct methods/implementations were merged into one unit")
        if d.decision == "outside_scope" and active:
            raise ValueError("Outside-scope identity decision cannot contain extraction depictions")
    payload = plan.model_dump(mode="json")
    def evidence_walk(value):
        if isinstance(value, dict):
            if {"record_id", "source_id", "locator", "finding"} <= value.keys():
                if value["record_id"] not in rows or value["source_id"] not in rows[value["record_id"]]["registered_sources"]:
                    raise ValueError("Identity evidence refers to unregistered source")
            for child in value.values(): evidence_walk(child)
        elif isinstance(value, list):
            for child in value: evidence_walk(child)
    evidence_walk(payload)
    return {"source:" + x for x in rows} | {"unit:" + x for x in units} | expected_pairs


def admit(census, plan, review):
    targets = validate_plan(census, plan)
    if review.plan_sha256 != digest(plan.model_dump(mode="json")):
        raise ValueError("Review does not bind the exact proposed work plan")
    assessments = {a.target: a for a in review.assessments}
    if len(assessments) != len(review.assessments) or set(assessments) != targets:
        raise ValueError("Fresh review must cover every source, identity decision, and work unit")
    if any(a.decision != "accept" for a in review.assessments):
        raise ValueError("Rejected planning assessment blocks worklist publication")
    rows = {r["record_id"]: r for r in census["rows"]}
    required_sources = {"source:" + r: {r} for r in rows}
    required_sources.update({"unit:" + u.unit_id: {b.record_id for b in u.bindings} for u in plan.work_units})
    required_sources.update({pair_key(d.left, d.right): {d.left, d.right} for d in plan.identity_decisions})
    for a in review.assessments:
        if required_sources[a.target] - {e.record_id for e in a.evidence}:
            raise ValueError("Review assessment omits evidence from an affected source occurrence")
        for e in a.evidence:
            if e.record_id not in rows or e.source_id not in rows[e.record_id]["registered_sources"]:
                raise ValueError("Review evidence refers to unregistered source")
    return {"format_version": "transcriptomics_reviewed_worklist/1",
            "status": "model_reviewed_method_worklist; extraction_pilot_gate_required_separately",
            "census_sha256": digest(census), "plan_sha256": review.plan_sha256,
            "review_sha256": digest(review.model_dump(mode="json")),
            "units": [{**u.model_dump(mode="json"),
                       "source_registry_paths": [rows[b.record_id]["source_registry_path"] for b in u.bindings]}
                      for u in plan.work_units],
            "boundary": "Source pointers require custody validation on Sherlock. This gate checks plan coverage and matching review, not biological truth or source-file accessibility."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("archive", type=Path); p.add_argument("output", type=Path)
    p = sub.add_parser("schemas"); p.add_argument("output", type=Path)
    p = sub.add_parser("subset")
    p.add_argument("census", type=Path); p.add_argument("output", type=Path)
    p.add_argument("--record-id", action="append", required=True)
    p = sub.add_parser("admit")
    for key in ("census", "plan", "review", "output"): p.add_argument(key, type=Path)
    args = parser.parse_args()
    if args.command == "schemas":
        args.output.mkdir(parents=True, exist_ok=True)
        for name, model in (("plan.schema.json", WorkPlan), ("review.schema.json", PlanReview)):
            (args.output / name).write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
        return
    if args.command == "prepare": result = prepare(args.archive)
    elif args.command == "subset": result = subset(json.loads(args.census.read_text()), args.record_id)
    else:
        result = admit(json.loads(args.census.read_text()), WorkPlan.model_validate_json(args.plan.read_text()),
                       PlanReview.model_validate_json(args.review.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    if args.command == "prepare": print(json.dumps(result["counts"], indent=2))


if __name__ == "__main__": main()
