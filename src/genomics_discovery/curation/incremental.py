"""Conservative reuse of hash-bound scientific checks; never a claim of rereading.

The host freezes a plan and prepares a normal full-review scaffold. A new verifier
fills fresh checks and reconsiders the complete record. Integrity checks establish
lineage and coverage, not the scientific truth of the verifier's observations.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

from .validation import (
    ACCEPTED,
    _load,
    _profile_sources,
    _sources,
    canonical_hash,
    validate_extraction,
    validate_review,
)

POLICY = "incremental-review/1.1"
LEGACY_POLICY = "incremental-review/1.0"
_SUPPORTED_PLAN_VERSIONS = {LEGACY_POLICY, POLICY}
_ADMIN_FIELDS = {"prompt_provenance", "file_hashes"}
_GLOBAL_CHECKS = {"topology_check", "coverage_check"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pin(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _sha(path)}


def _refs(value):
    if isinstance(value, dict):
        result = {r["source_id"] for key in ("source_refs", "source_refs_checked")
                  for r in value.get(key, []) if isinstance(r, dict) and "source_id" in r}
        for key, child in value.items():
            if key not in {"source_refs", "source_refs_checked"}:
                result |= _refs(child)
        return result
    if isinstance(value, list):
        return set().union(*(_refs(v) for v in value)) if value else set()
    return set()


def _source_checks(review, registers, base):
    """Only complete, independently checked artifact sets are reusable."""
    complete = {}
    for check in review["source_checks"]:
        sid = check["source_id"]
        actual = {(base / f["path"]).resolve(): f["sha256"] for f in check["files"]}
        if (actual == registers.get(sid)
                and check.get("reading_mode", "fresh") == "fresh"):
            normalized = deepcopy(check)
            for file in normalized["files"]:
                file["path"] = str((base / file["path"]).resolve())
            complete[sid] = normalized
    return complete


def _review_sources(records, registers, base):
    """Retain reviewer evidence when the new extraction has another directory."""
    output = deepcopy(records)
    for record in output:
        meta = record["source_metadata"]
        registered = registers[record["source_id"]]
        meta["local_evidence"] = [str(p) for p in registered]
        meta["sha256"] = {str(p): digest for p, digest in registered.items()}
        meta.pop("artifact_hashes", None)
        if isinstance(meta.get("artifact_roles"), dict):
            meta["artifact_roles"] = {
                str((base / path).resolve()): role
                for path, role in meta["artifact_roles"].items()
            }
        for rendition in meta.get("text_renditions", []):
            for key in ("path", "source_path"):
                if key in rendition:
                    rendition[key] = str((base / rendition[key]).resolve())
    return output


def _findings(review):
    return [{"finding_id": f"{kind}:{index}:{canonical_hash(finding)}",
             "kind": kind, "finding": deepcopy(finding)}
            for kind in ("errors", "remaining_gaps")
            for index, finding in enumerate(review[kind])]


def build_review_plan(base_extraction, base_review, extraction, method_id,
                      allowed_source_roots, *, policy=POLICY, baseline_policy=None,
                      format_version=POLICY,
                      **validation_options):
    """Build a deterministic plan. Missing/mismatched policy disables all reuse.

    `policy` and `baseline_policy` are host-supplied scientific-contract fingerprints.
    They may be strings or JSON objects. No network research or artifact writes occur.
    `format_version` preserves archived plan contracts during deterministic validation;
    new plans use the current format by default.
    """
    _require(format_version in _SUPPORTED_PLAN_VERSIONS, "Unsupported incremental policy")
    base_extraction, base_review, extraction = map(
        lambda p: Path(p).resolve(), (base_extraction, base_review, extraction))
    roots = tuple(Path(p).resolve() for p in allowed_source_roots)
    for label, errors in (
        ("baseline extraction", validate_extraction(base_extraction, method_id, roots,
                                                    **validation_options)),
        ("baseline review", validate_review(base_review, base_extraction, method_id, roots,
                                           **validation_options)),
        ("current extraction", validate_extraction(extraction, method_id, roots,
                                                  **validation_options)),
    ):
        _require(not errors, f"Invalid {label}: {errors}")
    old, prior, new = map(_load, (base_extraction, base_review, extraction))
    op, np = old["profiles"][0], new["profiles"][0]
    om = {m["module_id"]: m for m in op["modules"]}
    nm = {m["module_id"]: m for m in np["modules"]}
    changed = {mid for mid in om.keys() | nm.keys() if om.get(mid) != nm.get(mid)}
    reasons = []
    if "incremental_review" in prior:
        reasons.append("Incremental baseline requires fresh review; transitive reuse is disabled")
    if baseline_policy is None or policy != baseline_policy:
        reasons.append("Scientific review policy is missing or changed")
    if old["sources"] != new["sources"]:
        reasons.append("Source register or source-version metadata changed")
    for key in (op.keys() | np.keys()) - {"modules", "topology", "function_coverage"}:
        if op.get(key) != np.get(key):
            reasons.append(f"Global profile field changed: {key}")
    for key in (old.keys() | new.keys()) - _ADMIN_FIELDS - {
            "profiles", "sources", "evolution_claims", "evolution_source_leads"}:
        if old.get(key) != new.get(key):
            reasons.append(f"Global extraction field changed: {key}")
    evidence_policy = validation_options.get("evidence_policy")
    old_sources = _sources(old["sources"], base_extraction.parent, roots,
                           evidence_policy=evidence_policy)
    new_sources = _sources(new["sources"], extraction.parent, roots,
                           evidence_policy=evidence_policy)
    if old_sources != new_sources:
        reasons.append("Resolved source artifacts changed")
    additional = prior.get("sources", [])
    reviewer_sources = (_sources(additional, base_extraction.parent, roots,
                                 evidence_policy=evidence_policy) if additional else {})
    all_old_sources = {**old_sources, **reviewer_sources}
    retained_sources = [s for s in additional if s["source_id"] not in new_sources]
    all_sources = {**new_sources, **{sid: files for sid, files in reviewer_sources.items()
                                  if sid not in new_sources}}
    checked_sources = _source_checks(prior, all_old_sources, base_extraction.parent)
    prior_checks = {c["module_id"]: c for c in prior["module_checks"]}
    invalid = set(changed)
    old_edges, new_edges = op["topology"]["connections"], np["topology"]["connections"]
    for edge in old_edges + new_edges:
        if edge not in old_edges or edge not in new_edges:
            invalid.update((edge["source"], edge["target"]))
    # Unrecognized topology changes can alter branching/order globally.
    topology_keys = (op["topology"].keys() | np["topology"].keys()) - {"connections"}
    if any(op["topology"].get(k) != np["topology"].get(k) for k in topology_keys):
        reasons.append("Topology order or routing semantics changed")
    if op["function_coverage"] != np["function_coverage"]:
        reasons.append("Function coverage changed")
    while True:
        expanded = invalid | {e["target"] for e in old_edges + new_edges
                              if e["source"] in invalid}
        if expanded == invalid:
            break
        invalid = expanded
    for mid in nm:
        check = prior_checks.get(mid)
        if (not check or check["outcome"] not in ACCEPTED
                or not (_refs(check) | _refs(nm[mid])) <= checked_sources.keys()):
            invalid.add(mid)
    for finding in prior["errors"]:
        affected = finding.get("module_id") if isinstance(finding, dict) else None
        if isinstance(affected, str) and affected in nm:
            invalid.add(affected)
        else:
            reasons.append("Prior scientific error has no unambiguous module assignment")
    if reasons:
        invalid.update(nm)
    fresh = sorted(set(nm) & invalid)
    reused = sorted(set(nm) - invalid)
    # All source support needed by fresh modules/findings/global statements is fresh.
    global_content = {k: v for k, v in np.items() if k != "modules"}
    fresh_sources = set().union(*(_refs(nm[mid]) for mid in fresh)) if fresh else set()
    fresh_sources |= _refs(global_content) | _refs(prior["errors"]) | _refs(prior["remaining_gaps"])
    fresh_sources |= _refs(new.get("evolution_claims", []))
    fresh_sources |= {e["source_id"] for claim in new.get("evolution_claims", [])
                      for e in claim.get("evidence", [])}
    if old.get("evolution_source_leads") != new.get("evolution_source_leads"):
        fresh_sources |= _refs(new.get("evolution_source_leads", []))
    fresh_sources |= set(all_sources) - checked_sources.keys()
    if reasons:
        fresh_sources = set(all_sources)
    reused_sources = sorted(set(all_sources) - fresh_sources)
    options = deepcopy(validation_options)
    if "known_method_ids" in options:
        options["known_method_ids"] = sorted(options["known_method_ids"])
    return {
        "format_version": format_version, "method_id": method_id,
        "base_extraction": _pin(base_extraction), "base_review": _pin(base_review),
        "extraction": _pin(extraction), "allowed_source_roots": [str(p) for p in roots],
        "policy": deepcopy(policy), "baseline_policy": deepcopy(baseline_policy),
        "validation_options": options, "full_review_reasons": sorted(set(reasons)),
        "changed_module_ids": sorted(changed), "fresh_module_ids": fresh,
        "reused_module_ids": reused, "fresh_source_ids": sorted(fresh_sources),
        "reused_source_ids": reused_sources,
        "reused_module_checks": [deepcopy(prior_checks[mid]) for mid in reused],
        "reused_source_checks": [checked_sources[sid] for sid in reused_sources],
        "source_check_templates": [{"source_id": sid, "reading_scope": [],
                                    "files": [{"path": str(p), "sha256": digest}
                                              for p, digest in all_sources[sid].items()]}
                                   for sid in sorted(fresh_sources)],
        "reviewer_sources": _review_sources(retained_sources, reviewer_sources,
                                            base_extraction.parent),
        "prior_findings": _findings(prior),
        "profile_id": np["profile_id"], "profile_sha256": canonical_hash(np),
        "module_sha256": {mid: canonical_hash(nm[mid]) for mid in sorted(nm)},
        "evolution_claim_ids": [c["claim_id"] for c in new.get("evolution_claims", [])],
    }


def _attestation_template(plan):
    """Require reuse assertions only when the host plan actually carries checks."""
    version = plan.get("format_version")
    _require(version in _SUPPORTED_PLAN_VERSIONS, "Unsupported incremental policy")
    flags = {"whole_record_scanned": False}
    if version == LEGACY_POLICY:
        # Do not reinterpret or migrate previously signed review contracts.
        flags.update(carried_checks_applicable=False, no_unexpected_evidence=False,
                     not_fresh_reread=False)
    elif plan["reused_module_ids"] or plan["reused_source_ids"]:
        flags.update(carried_checks_applicable=False,
                     no_evidence_invalidating_carried_checks=False,
                     carried_checks_not_claimed_as_fresh=False)
    return flags


def make_review_scaffold(plan):
    """Return the established full review shape; fresh placeholders cannot validate."""
    checks = deepcopy(plan["reused_module_checks"])
    checks += [{"module_id": mid, "module_sha256": plan["module_sha256"][mid],
                "outcome": "pending_fresh_review", "observations": [],
                "source_refs_checked": []} for mid in plan["fresh_module_ids"]]
    sources = deepcopy(plan["reused_source_checks"])
    for check in sources:
        check["reading_mode"] = "carried_from_prior_review"
    for check in deepcopy(plan["source_check_templates"]):
        check["reading_mode"] = "fresh"
        sources.append(check)
    provenance = {
        "plan_sha256": canonical_hash(plan),
        "baseline_review_sha256": plan["base_review"]["sha256"],
        "module_provenance": {c["module_id"]: {
            "mode": "carried_from_prior_review", "check_sha256": canonical_hash(c)}
            for c in plan["reused_module_checks"]},
        "source_provenance": {c["source_id"]: {
            "mode": "carried_from_prior_review", "check_sha256": canonical_hash(c)}
            for c in plan["reused_source_checks"]},
        "attestation": _attestation_template(plan),
        "finding_dispositions": [{"finding_id": f["finding_id"], "status": "pending",
                                  "observations": [], "fresh_checks": []}
                                 for f in plan["prior_findings"]],
    }
    return {
        "format_version": "protocol_component_review/1.0", "reviewer_role": "independent_verifier",
        "profile_id": plan["profile_id"], "profile_sha256": plan["profile_sha256"],
        "scientific_scope": {"coverage": "", "limits": ""}, "outcome": "pending_fresh_review",
        "sources": deepcopy(plan["reviewer_sources"]), "module_checks": checks,
        "source_checks": sources,
        "topology_check": {"outcome": "pending_fresh_review", "observations": []},
        "coverage_check": {"outcome": "pending_fresh_review", "observations": []},
        "evolution_checks": [], "errors": [], "remaining_gaps": [],
        "incremental_review": provenance,
    }


def validate_incremental_review(review_path, plan_path, method_id, allowed_source_roots,
                                *, expected_plan_sha256=None, **validation_options):
    """Validate host-pinned lineage and complete coverage, never scientific truth."""
    try:
        if expected_plan_sha256 is not None:
            _require(_sha(plan_path) == expected_plan_sha256, "Host-pinned plan changed")
        plan = _load(plan_path)
        _require(plan.get("format_version") in _SUPPORTED_PLAN_VERSIONS,
                 "Unsupported incremental policy")
        _require(plan["method_id"] == method_id, "Plan method mismatch")
        for key in ("base_extraction", "base_review", "extraction"):
            _require(_sha(plan[key]["path"]) == plan[key]["sha256"], f"Pinned {key} changed")
        computed = build_review_plan(
            plan["base_extraction"]["path"], plan["base_review"]["path"],
            plan["extraction"]["path"], method_id, allowed_source_roots,
            policy=plan["policy"], baseline_policy=plan["baseline_policy"],
            format_version=plan["format_version"], **validation_options)
        _require(computed == plan, "Review plan invalidation or lineage mismatch")
        review = _load(review_path)
        errors = validate_review(review_path, Path(plan["extraction"]["path"]), method_id,
                                 allowed_source_roots, **validation_options)
        _require(not errors, str(errors))
        scaffold = make_review_scaffold(plan)
        meta = review.get("incremental_review")
        _require(isinstance(meta, dict), "Missing incremental review provenance")
        for key in ("plan_sha256", "baseline_review_sha256", "module_provenance",
                    "source_provenance"):
            _require(meta.get(key) == scaffold["incremental_review"][key],
                     f"Altered incremental provenance: {key}")
        attestation = meta.get("attestation")
        required_flags = scaffold["incremental_review"]["attestation"]
        _require(isinstance(attestation, dict), "Missing review attestation object")
        if plan["format_version"] != LEGACY_POLICY:
            _require(attestation.keys() == required_flags.keys(),
                     "Review attestation keys must match the host-plan scaffold")
        _require(all(attestation.get(k) is True for k in required_flags),
                 "Required whole-record/reuse attestation was not completed")
        existing_sources = {s["source_id"]: s for s in plan["reviewer_sources"]}
        review_sources = {s["source_id"]: s for s in review.get("sources", [])}
        _require(all(review_sources.get(sid) == record
                     for sid, record in existing_sources.items()),
                 "Historical reviewer source registration was removed or altered")
        added_sources = review_sources.keys() - existing_sources.keys()
        fully_fresh = not plan["reused_module_ids"] and not plan["reused_source_ids"]
        _require(fully_fresh or not added_sources,
                 "Unexpected reviewer evidence requires a new full review")
        modules = {c["module_id"]: c for c in review["module_checks"]}
        _require(set(modules) == set(plan["module_sha256"]), "Missing complete module coverage")
        for cached in plan["reused_module_checks"]:
            _require(modules[cached["module_id"]] == cached, "Altered carried module check")
        source_checks = {c["source_id"]: c for c in review["source_checks"]}
        expected_sources = set(plan["fresh_source_ids"] + plan["reused_source_ids"])
        _require(set(source_checks) == expected_sources | added_sources,
                 "Missing complete source coverage")
        base = Path(plan["extraction"]["path"]).parent
        roots = tuple(Path(p).resolve() for p in allowed_source_roots)
        evidence_policy = validation_options.get("evidence_policy")
        current_sources = _sources(_load(plan["extraction"]["path"])["sources"], base, roots,
                                   evidence_policy=evidence_policy)
        if review_sources:
            current_sources.update(_sources(list(review_sources.values()), base, roots,
                                            evidence_policy=evidence_policy))
        for check in scaffold["source_checks"]:
            actual = source_checks[check["source_id"]]
            if check["reading_mode"] == "carried_from_prior_review":
                _require(actual == check, "Altered carried source check or reading claim")
            else:
                _require(actual.get("reading_mode") == "fresh", "Missing fresh source reading mode")
                expected = {(f["path"], f["sha256"]) for f in check["files"]}
                observed = {(str((base / f["path"]).resolve()), f["sha256"])
                            for f in actual["files"]}
                _require(observed == expected, "Incomplete fresh source artifact coverage")
        for sid in added_sources:
            check = source_checks[sid]
            _require(check.get("reading_mode") == "fresh", "New evidence needs fresh reading mode")
            observed = {(base / f["path"]).resolve(): f["sha256"] for f in check["files"]}
            _require(observed == current_sources[sid], "Incomplete new source artifact coverage")
        dispositions = meta.get("finding_dispositions")
        _require(isinstance(dispositions, list), "Missing prior finding dispositions")
        _require(len(dispositions) == len(plan["prior_findings"]),
                 "Missing prior finding disposition")
        by_id = {d["finding_id"]: d for d in dispositions}
        _require(set(by_id) == {f["finding_id"] for f in plan["prior_findings"]},
                 "Duplicate or unknown prior finding disposition")
        available_fresh_checks = set(plan["fresh_module_ids"]) | _GLOBAL_CHECKS
        # The ordinary gate has validated each actual, freshly written check.
        # An empty evolution list is not evidence of a resolution.
        if review.get("evolution_checks"):
            available_fresh_checks.add("evolution_checks")
        for prior in plan["prior_findings"]:
            disposition = by_id[prior["finding_id"]]
            _require(isinstance(disposition.get("observations"), list)
                     and bool(disposition["observations"])
                     and all(isinstance(v, str) and v.strip()
                             for v in disposition["observations"]), "Missing finding observations")
            if disposition.get("status") == "resolved":
                fresh_checks = disposition.get("fresh_checks")
                _require(isinstance(fresh_checks, list) and bool(fresh_checks)
                         and set(fresh_checks) <= available_fresh_checks,
                         "Finding resolution needs fresh checks")
                _require(bool(disposition.get("source_refs")),
                         "Finding resolution needs source support")
                referenced = _profile_sources(disposition, current_sources, "finding disposition")
                _require(referenced <= set(plan["fresh_source_ids"]) | added_sources,
                         "Finding resolution requires fresh source inspection")
            else:
                _require(disposition.get("status") == "still_open", "Invalid finding disposition")
                _require(prior["finding"] in review[prior["kind"]],
                         "Unresolved prior error/gap was dropped or downgraded")
        return []
    except (ValueError, TypeError, OSError, RuntimeError, RecursionError, KeyError) as exc:
        return [f"Incremental review validation: {exc}"]
