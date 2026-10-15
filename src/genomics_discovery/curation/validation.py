"""Deterministic integrity gates, not a judgment of scientific correctness.

Relative artifact paths are anchored to the extraction file's directory in both
documents. Validation returns the first actionable error and never modifies files.
"""

import hashlib
import json
import math
import os
import re
from pathlib import Path

from .evolution import validate_evolution_claims, validate_evolution_review

ACCEPTED = {"accepted", "accepted_with_explicit_gaps"}
OUTCOMES = ACCEPTED | {"needs_revision", "unresolved_source_gap"}
RELATIONS = {"material", "information", "control", "ordering_requirement"}
EVIDENCE_POLICY = "scientific-source-register/1.0"
SOURCE_ARTIFACT_ROLES = {"source_document", "source_rendition"}
# Defense in depth only: filenames cannot establish authorship or scientific support.
NON_SOURCE_ARTIFACT_NAMES = {
    "extraction.json", "review.json", "prompt.txt", "final.txt", "events.jsonl",
    "input_extraction_snapshot.json", "input_review_snapshot.json", "review_scaffold.json",
    "extraction_scaffold.json",
    "checklist.json", "checklist_scaffold.json", "review_scope.json",
    "review_plan.json", "input.json", "reading_ledger.json", "validation.json", "state.json",
    "config.json", "contracts.json", "result.json",
}
_HASH_CHUNK_BYTES = 1024 * 1024


def _file_signature(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _file_sha256(path: Path) -> str:
    """Hash evidence with bounded memory; reject replacement or mutation while reading."""
    before = _file_signature(path.stat())
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        _require(_file_signature(os.fstat(handle.fileno())) == before,
                 f"Source artifact changed during hashing: {path}")
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
        _require(_file_signature(os.fstat(handle.fileno())) == before,
                 f"Source artifact changed during hashing: {path}")
    _require(_file_signature(path.stat()) == before,
             f"Source artifact changed during hashing: {path}")
    return digest.hexdigest()


class _SourceHashCache:
    """Reuse file hashes only within one caller-owned search/catalogue operation.

    Every lookup checks file identity, size, modification time and change time.
    Expected source hashes are still checked by _sources for every registration.
    This is not a persistent trust cache or a substitute for scientific review.
    """

    def __init__(self):
        self._hashes = {}

    def __call__(self, path: Path) -> str:
        signature = _file_signature(path.stat())
        previous = self._hashes.get(path)
        if previous is not None and previous[0] == signature:
            return previous[1]
        digest = _file_sha256(path)
        _require(_file_signature(path.stat()) == signature,
                 f"Source artifact changed during hashing: {path}")
        self._hashes[path] = (signature, digest)
        return digest


def canonical_hash(value) -> str:
    """Hash the exact JSON value using the atlas's canonical serialization."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _texts(value, *, empty=False):
    return isinstance(value, list) and (empty or bool(value)) and all(map(_text, value))


def _prose(value):
    """Allow labeled, nested prose without treating empty or scalar data as prose."""
    def inspect(item):
        if isinstance(item, str):
            return _text(item), _text(item)
        if isinstance(item, dict):
            if not item or not all(map(_text, item)):
                return False, False
            children = [inspect(v) for v in item.values()]
        elif isinstance(item, list):
            children = [inspect(v) for v in item]
        else:
            return item is None or isinstance(item, (bool, int, float)), False
        return all(valid for valid, _ in children), any(text for _, text in children)

    valid, contains_text = inspect(value)
    return isinstance(value, (str, dict)) and valid and contains_text


def _proses(value, *, empty=False):
    return isinstance(value, list) and (empty or bool(value)) and all(map(_prose, value))


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path):
    def reject_constant(value):
        raise ValueError(f"Non-finite JSON number: {value}")
    def finite_float(value):
        number = float(value)
        _require(math.isfinite(number), f"Non-finite JSON number: {value}")
        return number
    value = json.loads(Path(path).read_text(encoding="utf-8"),
                       object_pairs_hook=_unique_object, parse_constant=reject_constant,
                       parse_float=finite_float)
    _require(isinstance(value, dict), "Document must be a JSON object")
    return value


def _artifact(value, base, roots):
    _require(_text(value), "Missing artifact path")
    path = (base / value).resolve()
    _require(any(path.is_relative_to(root) for root in roots),
             f"Artifact outside allowed source roots: {value}")
    _require(path.is_file(), f"Missing source artifact: {value}")
    return path


def _check_evidence_policy(evidence_policy):
    _require(evidence_policy is None or evidence_policy == EVIDENCE_POLICY,
             f"Unsupported evidence_policy: {evidence_policy}")


def _source_artifact_roles(meta, paths, base, sid, *, required=False):
    """Resolve declared artifact roles; this is not a test of external authorship."""
    supplied = meta.get("artifact_roles")
    if supplied is None and not required:
        return {}
    _require(isinstance(supplied, dict) and supplied, f"Missing artifact_roles: {sid}")
    roles = {}
    for value, role in supplied.items():
        _require(_text(value), f"Malformed artifact role path: {sid}")
        path = (base / value).resolve()
        _require(path in paths, f"Unregistered artifact role path: {sid}/{value}")
        _require(path not in roles, f"Duplicate resolved artifact role path: {sid}/{value}")
        _require(_text(role) and role in SOURCE_ARTIFACT_ROLES,
                 f"Non-scientific or unsupported artifact role: {sid}/{value}")
        roles[path] = role
    _require(set(roles) == set(paths), f"Missing artifact role coverage: {sid}")
    return roles


def _sources(records, base, roots, *, hash_file=None, evidence_policy=None):
    _check_evidence_policy(evidence_policy)
    hash_file = hash_file or _file_sha256
    _require(isinstance(records, list) and records, "Missing source register")
    registers = {}
    for source in records:
        _require(isinstance(source, dict), "Malformed source record")
        sid, meta = source.get("source_id"), source.get("source_metadata")
        _require(_text(sid) and sid not in registers, "Missing or duplicate source_id")
        _require(isinstance(meta, dict), f"Missing source_metadata: {sid}")
        for key in ("title", "primary_url"):
            _require(_text(meta.get(key)), f"Missing source {key}: {sid}")
        inspected = meta.get("inspected_url")
        _require(_text(inspected) or _texts(inspected), f"Missing source inspected_url: {sid}")
        _require(_texts(meta.get("sections_inspected")), f"Missing sections_inspected: {sid}")
        _require(_proses(meta.get("access_limits"), empty=True), f"Missing access_limits: {sid}")
        paths = meta.get("local_evidence")
        paths = [paths] if isinstance(paths, str) else paths
        _require(_texts(paths), f"Missing local_evidence: {sid}")
        if evidence_policy == EVIDENCE_POLICY:
            _require(all(Path(p).name not in NON_SOURCE_ARTIFACT_NAMES for p in paths),
                     f"Agent answer or retrieval diagnostic is not a scientific source: {sid}")
        paths = [_artifact(p, base, roots) for p in paths]
        _require(len(paths) == len(set(paths)), f"Duplicate source artifact: {sid}")
        if evidence_policy == EVIDENCE_POLICY:
            _require(all(p.name not in NON_SOURCE_ARTIFACT_NAMES for p in paths),
                     f"Agent answer or retrieval diagnostic is not a scientific source: {sid}")
            roles = _source_artifact_roles(meta, paths, base, sid, required=True)
            from genomics_discovery.knowledge.retrieval import validate_document_content
            for path in paths:
                if (roles[path] == "source_document"
                        and path.suffix.lower() in {
                            ".html", ".htm", ".xhtml", ".png", ".jpg", ".jpeg"}):
                    validate_document_content(path.read_bytes(), suffix=path.suffix)
        registered = {}

        def register(path, digest, *, registered=registered, sid=sid):
            _require(_digest(digest), f"Malformed registered sha256: {sid}")
            _require(path not in registered or registered[path] == digest,
                     f"Conflicting registered sha256: {sid}")
            registered[path] = digest

        hashes = meta.get("sha256")
        if isinstance(hashes, str):
            register(paths[0], hashes)
            hashes = {}
        _require(isinstance(hashes, dict), f"Missing registered sha256: {sid}")
        extra = meta.get("artifact_hashes", {})
        _require(isinstance(extra, dict), f"Malformed artifact_hashes: {sid}")
        for key, digest in list(hashes.items()) + list(extra.items()):
            _require(_text(key), f"Malformed artifact hash path: {sid}")
            relative = Path(key)
            absolute = (base / relative).resolve()
            matches = [p for p in paths if p == absolute or (
                not relative.is_absolute() and ".." not in relative.parts
                and relative.parts and p.parts[-len(relative.parts):] == relative.parts)]
            _require(len(matches) == 1, f"Ambiguous or unregistered artifact hash: {sid}/{key}")
            register(matches[0], digest)
        _require(set(registered) == set(paths), f"Missing artifact hash coverage: {sid}")
        for path, digest in registered.items():
            _require(hash_file(path) == digest,
                     f"Source artifact sha256 mismatch: {sid}/{path.name}")
        registers[sid] = registered
    return registers


def _refs(refs, sources, context):
    _require(isinstance(refs, list) and refs, f"Missing source references: {context}")
    used = set()
    for ref in refs:
        _require(isinstance(ref, dict), f"Malformed source reference: {context}")
        sid = ref.get("source_id")
        _require(_text(sid) and sid in sources, f"Unknown referenced source: {context}")
        _require(_text(ref.get("locator")), f"Missing source locator: {context}")
        used.add(sid)
    return used


def _profile_sources(value, sources, context="profile"):
    """Check declared references on every profile or review field, including findings."""
    used = set()
    if isinstance(value, dict):
        for key, child in value.items():
            location = f"{context}.{key}"
            if key in {"source_refs", "source_refs_checked"}:
                used |= _refs(child, sources, location)
            else:
                used |= _profile_sources(child, sources, location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            used |= _profile_sources(child, sources, f"{context}[{index}]")
    return used


def _validate_scope_blocking_flags(value, context):
    """Check every explicitly named scope flag without interpreting surrounding prose."""
    if isinstance(value, dict):
        for key, child in value.items():
            location = f"{context}.{key}"
            if key == "blocking_for_declared_scope":
                _require(isinstance(child, bool),
                         f"{location} must be a JSON boolean (true or false)")
            _validate_scope_blocking_flags(child, location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_scope_blocking_flags(child, f"{context}[{index}]")


def _blocks_declared_scope(value):
    """Honor explicit scope blockers without inferring scientific criticality from prose."""
    if isinstance(value, dict):
        return value.get("blocking_for_declared_scope") is True or any(
            _blocks_declared_scope(child) for child in value.values())
    if isinstance(value, list):
        return any(_blocks_declared_scope(child) for child in value)
    return False


def _extraction(path, method_id, roots, *, evidence_policy=None):
    data = _load(path)
    _validate_scope_blocking_flags(data, "extraction")
    _require(data.get("format_version") == "protocol_component_extraction/1.0",
             "Unsupported extraction format_version")
    _require(_prose(data.get("scope")), "Missing extraction scope")
    _require(isinstance(data.get("prompt_provenance"), dict) and data["prompt_provenance"],
             "Missing prompt_provenance")
    profiles = data.get("profiles")
    _require(isinstance(profiles, list) and len(profiles) == 1, "Expected exactly one profile")
    profile = profiles[0]
    _require(isinstance(profile, dict), "Malformed profile")
    _require("verification" not in profile,
             "profile.verification is host-owned; workers must not provide it")
    _require(profile.get("method_ids") == [method_id], "Profile method_ids do not match assignment")
    _require(_text(profile.get("profile_id")), "Missing profile_id")
    _require(profile.get("status") == "source_scoped_protocol_extraction_not_execution_record",
             "Missing or unsupported profile status")
    meta = profile.get("profile_metadata")
    _require(isinstance(meta, dict), "Missing profile_metadata")
    for key in ("name", "record_kind"):
        _require(_text(meta.get(key)), f"Missing profile_metadata.{key}")
    for key in ("implementation_version", "native_specimen",
                "measurement_target", "attribution_unit", "architecture_coverage",
                "recipe_coverage", "directly_recorded_evidence", "instrument_output",
                "quantity_inferred"):
        _require(_prose(meta.get(key)), f"Missing or malformed profile_metadata.{key}")
    _require(_proses(meta.get("key_interfaces")), "Missing or malformed key_interfaces")
    _require(_proses(profile.get("unknowns"), empty=True), "Missing profile unknowns")
    sources = _sources(data.get("sources"), Path(path).resolve().parent, roots,
                       evidence_policy=evidence_policy)
    modules = profile.get("modules")
    _require(isinstance(modules, list) and modules, "Missing modules")
    ids, used = set(), set()
    for module in modules:
        _require(isinstance(module, dict), "Malformed module")
        mid = module.get("module_id")
        _require(_text(mid) and mid not in ids, "Missing or duplicate module_id")
        ids.add(mid)
        for key in ("operation_description", "purpose", "boundary_rationale"):
            _require(_text(module.get(key)), f"Missing {mid}.{key}")
        _require(_prose(module.get("information_effect")), f"Missing {mid}.information_effect")
        _require(_prose(module.get("evidence_scope")), f"Missing {mid}.evidence_scope")
        for key in ("input_state", "output_state"):
            state = module.get(key)
            _require(isinstance(state, dict) and all(_text(state.get(k)) for k in
                     ("description", "scope")), f"Missing {mid}.{key} description/scope")
        for key in ("steps", "function_tags"):
            _require(_texts(module.get(key)), f"Missing {mid}.{key}")
        for key in ("materials", "equipment"):
            values = module.get(key)
            _require(isinstance(values, list) and all(isinstance(v, dict) and
                     _text(v.get("name")) and _text(v.get("role")) for v in values),
                     f"Malformed {mid}.{key}")
        _require(_proses(module.get("unknowns"), empty=True), f"Missing {mid}.unknowns")
        used |= _refs(module.get("source_refs"), sources, mid)
        from .module_annotations import validate_module_annotations
        validate_module_annotations(module, sources)
    used |= _profile_sources(profile, sources)
    topology = profile.get("topology")
    _require(isinstance(topology, dict), "Missing topology")
    order = topology.get("default_order")
    _require(_texts(order) and len(order) == len(set(order)) and set(order) <= ids,
             "default_order must contain unique known module IDs")
    connections = topology.get("connections")
    _require(isinstance(connections, list), "Missing topology connections")
    for edge in connections:
        _require(isinstance(edge, dict), "Malformed topology connection")
        _require(_text(edge.get("source")) and edge["source"] in ids and
                 _text(edge.get("target")) and edge["target"] in ids, "Unknown topology endpoint")
        _require(_text(edge.get("relation")) and edge["relation"] in RELATIONS and
                 _text(edge.get("basis")), "Missing topology relation/basis")
    coverage = profile.get("function_coverage")
    _require(isinstance(coverage, list) and coverage, "Missing function_coverage")
    for item in coverage:
        _require(isinstance(item, dict) and all(_text(item.get(k)) for k in
                 ("function", "status", "reason")), "Malformed function coverage entry")
        mids = item.get("module_ids")
        _require(_texts(mids, empty=True) and set(mids) <= ids and len(mids) == len(set(mids)),
                 "Invalid function coverage module_ids")
    return profile, sources, used


def function_coverage_advice(profile: dict) -> list[dict]:
    """Compare repeated exact function labels in an already validated profile.

    Tags may be finer than checklist categories, so no aliases or new assignments
    are inferred. This is advisory cross-field reconciliation, never an acceptance
    gate or evidence that the scientific function assignment is correct.
    """
    tagged = {}
    for module in profile["modules"]:
        for function in module["function_tags"]:
            tagged.setdefault(function, set()).add(module["module_id"])
    covered = {}
    for item in profile["function_coverage"]:
        covered.setdefault(item["function"], set()).update(item["module_ids"])
    advice = []
    for function in sorted(tagged.keys() & covered.keys()):
        omitted = tagged[function] - covered[function]
        if omitted:
            advice.append({
                "function": function,
                "tagged_module_ids": sorted(tagged[function]),
                "coverage_module_ids": sorted(covered[function]),
                "omitted_tagged_module_ids": sorted(omitted),
                "action": (
                    "Reconcile the modules bearing this exact function label with its "
                    "coverage entry. Correct an omission or explain the intended scope "
                    "difference in the coverage reason. Preserve source-supported "
                    "assignments; do not add a function solely to silence this advice."),
            })
    return advice


def validate_extraction(path: Path, method_id: str,
                        allowed_source_roots: tuple[Path, ...], *,
                        known_method_ids=(), require_evolution=False,
                        evidence_policy=None, review_scopes=None,
                        prepared_sources=None, independent_source_renditions=False,
                        require_connection_reviews=False,
                        require_scientific_detail_reviews=False,
                        source_checklist_policy="independent") -> list[str]:
    try:
        _require(source_checklist_policy in {"independent", "reviewer_source_audit"},
                 "Unsupported source_checklist_policy")
        _require(source_checklist_policy != "reviewer_source_audit" or (
            require_scientific_detail_reviews and require_connection_reviews
            and evidence_policy == EVIDENCE_POLICY and prepared_sources is not None
            and review_scopes is not None),
            "Reviewer source audit requires scoped prepared scientific-detail review")
        _require(isinstance(independent_source_renditions, bool),
                 "independent_source_renditions must be boolean")
        _require(isinstance(require_connection_reviews, bool),
                 "require_connection_reviews must be boolean")
        _require(isinstance(require_scientific_detail_reviews, bool),
                 "require_scientific_detail_reviews must be boolean")
        _require(not require_scientific_detail_reviews or (
            require_connection_reviews and evidence_policy == EVIDENCE_POLICY
            and prepared_sources is not None and review_scopes is not None),
            "Scientific detail extraction requires prepared sources, frozen scope, "
            "scientific evidence policy and per-connection reviews")
        _require(not independent_source_renditions or prepared_sources is not None,
                 "Independent source renditions require frozen prepared_sources")
        _require(_text(method_id), "Missing assigned method_id")
        roots = tuple(Path(p).resolve() for p in allowed_source_roots)
        profile, sources, _ = _extraction(path, method_id, roots, evidence_policy=evidence_policy)
        if require_scientific_detail_reviews or any(
                "internal_handoffs" in module for module in profile["modules"]):
            from .internal_handoffs import validate_internal_handoffs
            validate_internal_handoffs(profile, sources)
        if require_connection_reviews:
            from .connection_review import make_connection_checks
            # Reject alternate edge fields that the graph importer would project
            # but this policy would not enumerate in canonical connections.
            make_connection_checks(profile)
        if prepared_sources is not None:
            from .coverage import _independent_registration_binding, prepared_registration_binding
            bind = (_independent_registration_binding if independent_source_renditions
                    else prepared_registration_binding)
            bind(_load(path), prepared_sources, method_id, roots,
                 base=Path(path).resolve().parent, evidence_policy=evidence_policy)
        if review_scopes is not None:
            from .coverage import scope_binding
            scope_binding(_load(path), method_id, review_scopes)
        return validate_evolution_claims(_load(path), method_id, Path(path).resolve().parent,
                                        roots, known_method_ids=known_method_ids,
                                        require_evaluated=require_evolution)
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, RecursionError) as exc:
        return [f"Extraction validation: {exc}"]


def _check_result(check, context, accepted):
    _require(isinstance(check, dict), f"Missing {context}")
    outcome = check.get("outcome")
    _require(_text(outcome) and outcome in OUTCOMES, f"Invalid {context} outcome")
    _require(_texts(check.get("observations")), f"Missing {context} observations")
    _require(not accepted or outcome in ACCEPTED, f"Unresolved {context} in accepted review")


def validate_review(path: Path, extraction_path: Path, method_id: str,
                    allowed_source_roots: tuple[Path, ...], *,
                    known_method_ids=(), require_evolution=False,
                    evidence_policy=None, review_scopes=None, source_checklist=None,
                    prepared_sources=None, independent_source_renditions=False,
                    require_connection_reviews=False,
                    require_scientific_detail_reviews=False,
                    source_checklist_policy="independent") -> list[str]:
    try:
        _require(source_checklist_policy in {"independent", "reviewer_source_audit"},
                 "Unsupported source_checklist_policy")
        lean = source_checklist_policy == "reviewer_source_audit"
        _require(not lean or (require_scientific_detail_reviews and require_connection_reviews
                 and evidence_policy == EVIDENCE_POLICY and prepared_sources is not None
                 and review_scopes is not None and source_checklist is None),
                 "Reviewer source audit requires scoped prepared scientific-detail review "
                 "without an independent checklist")
        _require(isinstance(independent_source_renditions, bool),
                 "independent_source_renditions must be boolean")
        _require(isinstance(require_connection_reviews, bool),
                 "require_connection_reviews must be boolean")
        _require(isinstance(require_scientific_detail_reviews, bool),
                 "require_scientific_detail_reviews must be boolean")
        _require(not require_scientific_detail_reviews or (
            require_connection_reviews and evidence_policy == EVIDENCE_POLICY
            and prepared_sources is not None and review_scopes is not None
            and (source_checklist is not None or lean)),
            "Scientific detail review requires prepared sources, frozen scope/checklist, "
            "scientific evidence policy and per-connection reviews")
        _require(not independent_source_renditions or (
            prepared_sources is not None and review_scopes is not None
            and (source_checklist is not None or lean)),
            "Independent source renditions require frozen prepared_sources, scope and checklist")
        _require(_text(method_id), "Missing assigned method_id")
        roots = tuple(Path(p).resolve() for p in allowed_source_roots)
        profile, sources, used = _extraction(extraction_path, method_id, roots,
                                            evidence_policy=evidence_policy)
        if any("internal_handoffs" in module for module in profile["modules"]):
            from .internal_handoffs import validate_internal_handoffs
            used |= validate_internal_handoffs(profile, sources)
        extraction = _load(extraction_path)
        if prepared_sources is not None:
            from .coverage import _independent_registration_binding, prepared_registration_binding
            bind = (_independent_registration_binding if independent_source_renditions
                    else prepared_registration_binding)
            bind(extraction, prepared_sources, method_id, roots,
                 base=Path(extraction_path).resolve().parent, evidence_policy=evidence_policy)
        review = _load(path)
        if review_scopes is not None:
            from .coverage import scope_binding, validate_coverage_review
            scope_binding(extraction, method_id, review_scopes)
            if not lean:
                _require(source_checklist is not None, "Review requires a frozen source checklist")
                validate_coverage_review(review, extraction, source_checklist, method_id, roots,
                                         review_scopes[method_id],
                                         extraction_base=Path(extraction_path).resolve().parent,
                                         evidence_policy=evidence_policy,
                                         prepared_sources=prepared_sources,
                                         independent_source_renditions=independent_source_renditions)
        _validate_scope_blocking_flags(review, "review")
        _require(review.get("format_version") == "protocol_component_review/1.0",
                 "Unsupported review format_version")
        _require(review.get("reviewer_role") == "independent_verifier", "Invalid reviewer_role")
        _require(review.get("profile_id") == profile["profile_id"], "Review profile_id mismatch")
        _require(review.get("profile_sha256") == canonical_hash(profile), "Stale profile_sha256")
        additional = review.get("sources", [])
        _require(isinstance(additional, list), "Malformed reviewer source register")
        if additional:
            registered = _sources(additional, Path(extraction_path).resolve().parent, roots,
                                  evidence_policy=evidence_policy)
            _require(not sources.keys() & registered.keys(),
                     "Reviewer source_id collides with extraction source register")
            sources = {**sources, **registered}
        if lean:
            from .coverage import validate_reviewer_source_audit
            used |= validate_reviewer_source_audit(review, extraction, sources,
                         review_scopes[method_id], prepared_sources["methods"][method_id])
        reference_view = review
        if lean and isinstance(review.get("coverage_check"), dict):
            reference_view = {**review, "coverage_check": {
                key: value for key, value in review["coverage_check"].items()
                if key != "source_audit"}}
        if require_connection_reviews and isinstance(review.get("topology_check"), dict):
            # The connection checker separately validates all references and permits
            # an honest nonaccepting row to make no source-reading claim.
            reference_view = {**review, "topology_check": {
                key: value for key, value in review["topology_check"].items()
                if key != "connection_checks"}}
        if require_scientific_detail_reviews:
            reference_view = {key: value for key, value in reference_view.items()
                              if key != "assertion_checks"}
            if isinstance(reference_view.get("topology_check"), dict):
                reference_view["topology_check"] = {
                    key: value for key, value in reference_view["topology_check"].items()
                    if key != "module_handoff_checks"}
        used |= _profile_sources(reference_view, sources, "review")
        scope = review.get("scientific_scope")
        _require(isinstance(scope, dict) and all(_text(scope.get(k)) for k in
                 ("coverage", "limits")), "Missing scientific_scope coverage/limits")
        outcome = review.get("outcome")
        _require(_text(outcome) and outcome in OUTCOMES, "Invalid review outcome")
        accepted = outcome in ACCEPTED
        _require(not accepted or not (
            _blocks_declared_scope(extraction) or _blocks_declared_scope(review)),
            "Accepted review contains blocking_for_declared_scope=True")
        errors = review.get("errors")
        _require(_proses(errors, empty=True), "Missing or malformed review errors")
        _require(not accepted or not errors, "Accepted review must have an empty errors list")
        _require(_proses(review.get("remaining_gaps"), empty=True), "Missing remaining_gaps")
        for key in ("topology_check", "coverage_check"):
            _check_result(review.get(key), key, accepted)
        if require_connection_reviews:
            from .connection_review import validate_connection_checks
            used |= validate_connection_checks(review, profile, sources)
        if require_scientific_detail_reviews:
            from .assertion_review import validate_assertion_checks
            from .internal_handoffs import (
                validate_internal_handoffs,
                validate_module_handoff_checks,
            )
            used |= validate_internal_handoffs(profile, sources)
            used |= validate_assertion_checks(review, profile, sources)
            used |= validate_module_handoff_checks(review, profile, sources)
        checks = review.get("module_checks")
        _require(isinstance(checks, list), "Missing module_checks")
        modules = {m["module_id"]: m for m in profile["modules"]}
        checked = set()
        for check in checks:
            _check_result(check, "module check", accepted)
            mid = check.get("module_id")
            _require(_text(mid) and mid in modules and mid not in checked,
                     "Unknown or duplicate reviewed module_id")
            _require(check.get("module_sha256") == canonical_hash(modules[mid]),
                     f"Stale module_sha256: {mid}")
            refs = _refs(check.get("source_refs_checked"), sources, f"review {mid}")
            _require({r["source_id"] for r in modules[mid]["source_refs"]} <= refs,
                     f"Missing reviewed module sources: {mid}")
            used |= refs
            checked.add(mid)
        _require(not accepted or checked == set(modules), "Missing module review coverage")
        checks = review.get("source_checks")
        _require(isinstance(checks, list), "Missing source_checks")
        covered = {}
        for check in checks:
            _require(isinstance(check, dict), "Malformed source check")
            sid = check.get("source_id")
            _require(_text(sid) and sid in sources and sid not in covered,
                     "Unknown or duplicate checked source_id")
            _require(_texts(check.get("reading_scope")), f"Missing source reading_scope: {sid}")
            files = check.get("files")
            _require(isinstance(files, list) and files, f"Missing checked source files: {sid}")
            covered[sid] = set()
            for file in files:
                _require(isinstance(file, dict), "Malformed source file check")
                for item in (check, file):
                    _require(all(item[k] is True for k in ("matches", "hash_matches") if k in item),
                             f"Failed declared source integrity check: {sid}")
                artifact = _artifact(file.get("path"),
                                     Path(extraction_path).resolve().parent, roots)
                _require(artifact in sources[sid] and artifact not in covered[sid],
                         f"Unregistered or duplicate checked artifact: {sid}")
                _require(file.get("sha256") == sources[sid][artifact],
                         f"Reviewer sha256 mismatch: {sid}")
                covered[sid].add(artifact)
        evolution_errors = validate_evolution_review(
            review, extraction, method_id, Path(extraction_path).resolve().parent, roots,
            known_method_ids=known_method_ids, require_evaluated=require_evolution)
        if evolution_errors:
            return evolution_errors
        used |= {evidence["source_id"] for claim in extraction.get("evolution_claims", [])
                 for evidence in claim["evidence"]}
        _require(not accepted or all(covered.get(sid) == set(sources[sid]) for sid in used),
                 "Missing source artifact review coverage")
        return []
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, RecursionError) as exc:
        return [f"Review validation: {exc}"]
