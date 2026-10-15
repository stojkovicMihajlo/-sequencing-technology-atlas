"""Host-computed artifact identities with deliberately unfilled scientific checks.

These are starting files, never worker outputs or evidence of source inspection.
An extraction still needs its source-scoped contents; a fresh reviewer still needs
to independently inspect the sources and supply every scientific judgment.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .validation import _extraction, canonical_hash


def make_extraction_scaffold(task: dict, prompt_path: Path, prompt: str | None = None) -> dict:
    """Copy prepared file identities without making any reading/content claim."""
    sources = []
    for lead in task.get("prepared_sources", []):
        files = lead["files"]
        sources.append({"source_id": lead["source_id"], "source_metadata": {
            "title": lead["title"], "primary_url": lead["url"],
            "inspected_url": "", "local_evidence": [f["path"] for f in files],
            "sha256": {f["path"]: f["sha256"] for f in files},
            "artifact_roles": {f["path"]: f["role"] for f in files},
            "sections_inspected": [], "access_limits": [],
        }})
    metadata = {key: "" for key in (
        "name", "record_kind", "implementation_version", "native_specimen",
        "measurement_target", "attribution_unit", "architecture_coverage", "recipe_coverage",
        "directly_recorded_evidence", "instrument_output", "quantity_inferred",
    )}
    metadata["key_interfaces"] = []
    document = {
        "format_version": "protocol_component_extraction/1.0", "scope": "",
        "prompt_provenance": {"prompt_path": str(prompt_path)},
        "sources": sources,
        "profiles": [{
            "profile_id": "", "method_ids": [task["id"]], "profile_metadata": metadata,
            "modules": [], "topology": {"default_order": [], "connections": []},
            "function_coverage": [], "unknowns": [], "status": "pending_source_extraction",
        }],
    }
    if prompt is not None:
        document["prompt_provenance"]["sha256"] = hashlib.sha256(
            prompt.encode("utf-8")).hexdigest()
    if task.get("evolution_required"):
        document["evolution_claims"] = []
    if task.get("review_scope") is not None:
        document["review_scope"] = task["review_scope"]
    return document


def make_checklist_scaffold(task: dict) -> dict:
    """Prepared source identities only; no requirements or inspection claims."""
    from .coverage import CHECKLIST_FORMAT, CHECKLIST_STATUS
    sources = make_extraction_scaffold(task, Path("unused"))["sources"]
    return {"format_version": CHECKLIST_FORMAT, "status": CHECKLIST_STATUS,
            "method_id": task["id"], "review_scope": task["review_scope"],
            "sources": sources, "items": [], "remaining_gaps": []}


def make_fresh_review_scaffold(extraction_path: Path, method_id: str,
                              allowed_source_roots: tuple[Path, ...], *,
                              evidence_policy=None, source_checklist: Path | None = None,
                              require_connection_reviews: bool = False,
                              require_scientific_detail_reviews: bool = False,
                              source_checklist_policy="independent",
                              prepared_sources=None) -> dict:
    """Pin validated profile/module/file identities; leave all reading claims empty.

    Reuse the existing source-register resolver, including legacy relative paths
    and hash maps, rather than assuming a single source-metadata representation.
    This file carries no earlier review conclusions or review-reuse attestations.
    """
    profile, sources, _ = _extraction(
        extraction_path, method_id, allowed_source_roots, evidence_policy=evidence_policy)
    document = {
        "format_version": "protocol_component_review/1.0", "reviewer_role": "independent_verifier",
        "profile_id": profile["profile_id"], "profile_sha256": canonical_hash(profile),
        "scientific_scope": {"coverage": "", "limits": ""}, "outcome": "pending_fresh_review",
        "sources": [],
        "module_checks": [{
            "module_id": module["module_id"], "module_sha256": canonical_hash(module),
            "outcome": "pending_fresh_review", "observations": [], "source_refs_checked": [],
        } for module in profile["modules"]],
        "source_checks": [{
            "source_id": sid, "files": [{"path": str(path), "sha256": digest}
                                         for path, digest in registered.items()],
            "reading_scope": [],
        } for sid, registered in sources.items()],
        "topology_check": {"outcome": "pending_fresh_review", "observations": []},
        "coverage_check": {"outcome": "pending_fresh_review", "observations": []},
        "evolution_checks": [], "errors": [], "remaining_gaps": [],
    }
    if require_connection_reviews:
        from .connection_review import make_connection_checks
        document["topology_check"]["connection_checks"] = make_connection_checks(profile)
    if require_scientific_detail_reviews:
        from .assertion_review import ASSERTION_POLICY, make_assertion_checks
        from .internal_handoffs import make_module_handoff_checks
        document["scientific_detail_policy"] = ASSERTION_POLICY
        document["assertion_checks"] = make_assertion_checks(profile)
        document["topology_check"]["module_handoff_checks"] = make_module_handoff_checks(profile)
    if source_checklist is not None:
        from .coverage import checklist_document
        from .validation import _load
        scope = _load(extraction_path)["review_scope"]
        checklist, _ = checklist_document(source_checklist, method_id, allowed_source_roots,
                                          scope, evidence_policy=evidence_policy)
        document["source_to_record_check"] = {
            "checklist_sha256": hashlib.sha256(source_checklist.read_bytes()).hexdigest(),
            "scope_sha256": canonical_hash(scope),
            "gap_checks": [{"gap_id": gap["gap_id"], "outcome": "pending",
                            "observations": [], "scope_reason": "",
                            "source_refs_checked": [], "record_paths": []}
                           for gap in checklist["remaining_gaps"]],
            "item_checks": [{"item_id": item["item_id"], "outcome": "pending",
                             "record_paths": [], "observations": [], "scope_reason": "",
                             "source_refs_checked": []} for item in checklist["items"]]}
    if source_checklist_policy == "reviewer_source_audit":
        from .coverage import make_reviewer_source_audit
        from .validation import _load
        if (source_checklist is not None or prepared_sources is None
                or not require_connection_reviews or not require_scientific_detail_reviews):
            raise ValueError("Reviewer source audit scaffold needs scoped prepared detail review")
        scope = _load(extraction_path)["review_scope"]
        document["coverage_check"]["source_audit"] = make_reviewer_source_audit(
            scope, prepared_sources["methods"][method_id])
    elif source_checklist_policy != "independent":
        raise ValueError("Unsupported source_checklist_policy")
    return document
