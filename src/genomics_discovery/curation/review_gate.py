"""One review gate shared by execution, inspection, recovery and export."""

from __future__ import annotations

from pathlib import Path

from .run_state import read, sha
from .validation import EVIDENCE_POLICY, canonical_hash, validate_review


def validation_options(run: Path, config: dict) -> dict:
    """Only the frozen catalogue, never worker output, resolves graph endpoints."""
    policy = config.get("source_checklist_policy", "independent")
    if not isinstance(policy, str) or policy not in {"independent", "reviewer_source_audit"}:
        raise ValueError("Unsupported source checklist policy")
    options = {"source_checklist_policy": policy}
    if config.get("evidence_policy") is not None:
        options["evidence_policy"] = config["evidence_policy"]
    if config.get("review_scope_sha256") is not None:
        options["review_scopes"] = read(run / "review_scope.json")["methods"]
    if config.get("prepared_registration_policy") == "preserve-supplied-sources/1.0":
        options["prepared_sources"] = read(run / "prepared_sources.json")
    if "independent_source_renditions" in config:
        if not isinstance(config["independent_source_renditions"], bool):
            raise ValueError("Independent source-rendition policy must be boolean")
        options["independent_source_renditions"] = config["independent_source_renditions"]
        if config["independent_source_renditions"] and not (
                "review_scopes" in options and "prepared_sources" in options):
            raise ValueError("Independent source renditions require frozen scope and sources")
    if "connection_review_policy" in config:
        if config["connection_review_policy"] != "per-connection/1.0":
            raise ValueError("Unsupported connection review policy")
        options["require_connection_reviews"] = True
    if "scientific_detail_policy" in config:
        if config["scientific_detail_policy"] != "scientific-detail/1.0":
            raise ValueError("Unsupported scientific detail policy")
        if not (config.get("connection_review_policy") and
                config.get("review_scope_sha256") and
                config.get("prepared_sources_sha256") and config.get("source_only")
                and config.get("evidence_policy") == EVIDENCE_POLICY
                and not config.get("incremental_reviews")):
            raise ValueError("Scientific detail review requires scoped prepared sources "
                             "and fresh connection review")
        options["require_scientific_detail_reviews"] = True
    if policy == "reviewer_source_audit" and not (
            config.get("source_only") and config.get("evidence_policy") == EVIDENCE_POLICY
            and config.get("review_scope_sha256") and config.get("prepared_sources_sha256")
            and config.get("prepared_registration_policy") == "preserve-supplied-sources/1.0"
            and config.get("independent_source_renditions") is True
            and options.get("require_connection_reviews")
            and options.get("require_scientific_detail_reviews")
            and not config.get("incremental_reviews")):
        raise ValueError("Reviewer source audit requires scoped prepared sources, "
                         "source-only extraction and fresh scientific detail/connection reviews")
    if config.get("with_evolution"):
        registry = read(run / "method_registry.json")
        options.update(known_method_ids=tuple(m["id"] for m in registry["methods"]),
                       require_evolution=True)
    return options


def review_policy(config: dict, job: dict, implementation: dict, evidence_hash=None) -> dict:
    """Host-owned review context. A changed contract/context disables check reuse."""
    policy = {"version": "incremental-review/1.0", "contracts": config["contracts_sha256"],
            "registry": config.get("method_registry_sha256"),
            "input": job["input_sha256"], "model": config["model"],
            "effort": config["effort"], "source_only": config.get("source_only", False),
            "evidence_index": evidence_hash,
            "implementation": {name: implementation.get(name) for name in
                               ("prompts.py", "validation.py", "evolution.py", "incremental.py")}}
    if implementation.get("scaffolds.py"):
        # Older attempts retain their recorded policy shape; new scaffold code
        # participates in the context fingerprint for subsequent check reuse.
        policy["implementation"]["scaffolds.py"] = implementation["scaffolds.py"]
    # Keep legacy context fingerprints reproducible. A new evidence contract
    # changes the host policy and therefore cannot inherit legacy acceptance.
    if config.get("evidence_policy") is not None:
        policy["evidence_policy"] = config["evidence_policy"]
    if config.get("prepared_sources_sha256") is not None:
        policy["prepared_sources_sha256"] = config["prepared_sources_sha256"]
    if config.get("prepared_registration_policy") is not None:
        policy["prepared_registration_policy"] = config["prepared_registration_policy"]
        policy["implementation"]["coverage.py"] = implementation.get("coverage.py")
    if config.get("review_scope_sha256") is not None:
        policy["review_scope_sha256"] = config["review_scope_sha256"]
        if config.get("source_checklist_policy", "independent") == "independent":
            policy["source_checklist_sha256"] = job.get("source_checklist_sha256")
        policy["implementation"]["coverage.py"] = implementation.get("coverage.py")
    if "source_checklist_policy" in config:
        policy["source_checklist_policy"] = config["source_checklist_policy"]
    if "independent_source_renditions" in config:
        policy["independent_source_renditions"] = config["independent_source_renditions"]
    if "connection_review_policy" in config:
        policy["connection_review_policy"] = config["connection_review_policy"]
        policy["implementation"]["connection_review.py"] = implementation.get(
            "connection_review.py")
    if "scientific_detail_policy" in config:
        policy["scientific_detail_policy"] = config["scientific_detail_policy"]
        for name in ("assertion_review.py", "internal_handoffs.py"):
            policy["implementation"][name] = implementation.get(name)
    return policy


def _source_checklist_path(run: Path, config: dict, job: dict, method_id: str) -> Path | None:
    if config.get("source_checklist_policy", "independent") == "reviewer_source_audit":
        validation_options(run, config)
        if (job.get("source_checklist") or job.get("source_checklist_sha256")
                or any(a["role"] == "source_checklist" for a in job["attempts"])):
            raise ValueError("Reviewer source audit forbids independent checklist artifacts")
        return None
    if config.get("review_scope_sha256") is None:
        return None
    path = Path(job.get("source_checklist", "")).resolve()
    if (not path.is_relative_to(run / "jobs" / method_id) or not path.is_file()
            or path.name != "checklist.json"
            or sha(path) != job.get("source_checklist_sha256")):
        raise ValueError("Missing or changed frozen source checklist")
    attempts = job["attempts"]
    index = next((i for i, a in enumerate(attempts)
                  if Path(a["directory"]).resolve() == path.parent
                  and a["role"] == "source_checklist"), None)
    if index is None:
        origin = job.get("seed", {}).get("source_checklist")
        if origin is None:
            raise ValueError("Source checklist must precede every current-run extractor")
        from .continuation import validate_seed
        validate_seed(job["seed"])
        if (sha(path) != origin["sha256"]
                or origin["source_packet_method_sha256"] != canonical_hash(
                    read(run / "prepared_sources.json")["methods"][method_id])
                or origin["scope_sha256"] != canonical_hash(
                    read(run / "review_scope.json")["methods"][method_id])):
            raise ValueError("Reused source checklist requires identical sources and scope")
    elif any(a["role"] == "extractor" for a in attempts[:index]):
        raise ValueError("Source checklist must precede every current-run extractor")
    from .coverage import validate_checklist
    errors = validate_checklist(path, method_id, (Path(config["atlas"]), run),
                                read(run / "review_scope.json")["methods"][method_id],
                                evidence_policy=config.get("evidence_policy"),
                                prepared_sources=(read(run / "prepared_sources.json")
                                    if config.get("independent_source_renditions") else None))
    if errors:
        raise ValueError(str(errors))
    return path


def validate_job_review(run: Path, config: dict, job: dict, review: Path,
                        extraction: Path, method_id: str, *, seen=None) -> list[str]:
    """One gate for execution, self-check, recovery and export, including reuse lineage."""
    roots, options = (Path(config["atlas"]), run), validation_options(run, config)
    try:
        review, extraction = review.resolve(), extraction.resolve()
        seen = set() if seen is None else set(seen)
        if review in seen or len(seen) >= 20:
            return ["Cyclic or excessive incremental review ancestry"]
        seen.add(review)
        attempt = next((a for a in job["attempts"]
                        if Path(a["directory"]).resolve() == review.parent), {})
        pin = attempt.get("review_plan")
        document = read(review)
        if not isinstance(document, dict):
            return ["Review must be a JSON object"]
        marked = "incremental_review" in document
        marker = document.get("incremental_review")
        if config.get("source_checklist_policy") == "reviewer_source_audit" and (
                pin is not None or marked):
            return ["Reviewer source audit requires a fresh full review without reused checks"]
        if pin is None and not marked:
            return validate_review(review, extraction, method_id, roots,
                                   source_checklist=_source_checklist_path(
                                       run, config, job, method_id), **options)
        if pin is None or not isinstance(marker, dict):
            return ["Incremental review requires both a host-pinned plan and output provenance"]
        plan_path = Path(pin["path"]).resolve()
        if plan_path != review.parent / "review_plan.json" or sha(plan_path) != pin["sha256"]:
            return ["Host-pinned review plan changed or is outside its attempt"]
        plan = read(plan_path)
        snapshot_hash = attempt.get("evidence_index_sha256")
        if (snapshot_hash is not None
                and sha(review.parent / "shared_sources.json") != snapshot_hash):
            return ["Pinned source index snapshot changed"]
        current_index = run / "shared_sources/index.json"
        current_hash = sha(current_index) if current_index.is_file() else None
        if (len(seen) == 1 and (plan["reused_module_ids"] or plan["reused_source_ids"])
                and current_hash != snapshot_hash):
            return ["Source index changed; reused checks require a fresh full review"]
        if plan["extraction"] != {"path": str(extraction), "sha256": sha(extraction)}:
            return ["Review plan targets a different extraction than the current job"]
        expected_policy = review_policy(config, job, attempt["implementation_hashes"],
                                        attempt.get("evidence_index_sha256"))
        if plan["policy"] != expected_policy or attempt.get("review_policy") != expected_policy:
            return ["Review plan does not match the host's scientific policy"]
        # Validate ancestors through this same gate. A raw legacy validator alone
        # would not detect forged or missing provenance in an incremental baseline.
        errors = validate_job_review(run, config, job, Path(plan["base_review"]["path"]),
                                     Path(plan["base_extraction"]["path"]), method_id, seen=seen)
        if errors:
            return ["Invalid baseline review: " + e for e in errors]
        from .incremental import validate_incremental_review
        return validate_incremental_review(review, plan_path, method_id, roots,
                                           expected_plan_sha256=pin["sha256"], **options)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"Review lineage error: {exc}"]

