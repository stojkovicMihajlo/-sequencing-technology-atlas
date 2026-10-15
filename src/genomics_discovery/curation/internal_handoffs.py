"""Optional internal handoffs and fresh module scans for scientific-detail/1.0.

Contract: a validated profile and authenticated source register are inputs. A
module may stay broad or be split; only explicitly recorded internal transfers
bind existing step occurrences and exact values. No step order, module count,
chronological dependency, chemistry validity or evidence entailment is inferred.

The independent reviewer scans every module, including modules without internal
records. A supported boundary may appear as an external connection or an internal
handoff; missing/unread boundaries remain nonaccepting. "No consequential handoff
identified" is an inspection claim with limits, never proof of independence or
exhaustiveness. Citations and hashes bind claims, not the truth of those claims.

The caller opts into this policy, authenticates source files, validates ordinary
modules/connections, and runs the existing per-connection and source-coverage
checks. This helper neither projects a graph nor inherits prior review acceptance.
"""

from __future__ import annotations

from .review_reference_diagnostics import missing_source_ids_text
from .validation import (
    ACCEPTED,
    OUTCOMES,
    RELATIONS,
    _check_result,
    _profile_sources,
    _refs,
    _require,
    _text,
    _texts,
    canonical_hash,
)

POLICY = "scientific-detail/1.0"
DISPOSITIONS = {
    "represented_external", "represented_internal", "represented_both",
    "no_consequential_handoff_identified", "missing_or_unresolved",
}


def _modules(profile):
    _require(isinstance(profile, dict), "Missing internal-handoff profile")
    modules = profile.get("modules")
    _require(isinstance(modules, list) and modules, "Missing internal-handoff modules")
    ids = set()
    for module in modules:
        _require(isinstance(module, dict), "Malformed internal-handoff module")
        mid = module.get("module_id")
        _require(_text(mid) and mid not in ids, "Missing or duplicate handoff module_id")
        ids.add(mid)
        steps = module.get("steps")
        _require(_texts(steps), f"Missing handoff module steps: {mid}")
        handoffs = module.get("internal_handoffs", [])
        _require(isinstance(handoffs, list), f"Malformed internal_handoffs: {mid}")
        for index, handoff in enumerate(handoffs):
            label = f"internal handoff {mid}/{index}"
            _require(isinstance(handoff, dict), f"Malformed {label}")
            occurrences = []
            for endpoint in ("source", "target"):
                step = handoff.get(endpoint + "_step_index")
                _require(type(step) is int and 0 <= step < len(steps),
                         f"Invalid {label}.{endpoint}_step_index")
                _require(handoff.get(endpoint + "_step_sha256") == canonical_hash(steps[step]),
                         f"Stale {label}.{endpoint}_step_sha256")
                occurrences.append(step)
            _require(occurrences[0] != occurrences[1],
                     f"{label} needs distinct producer/consumer step occurrences")
            _require(_text(handoff.get("relation")) and handoff["relation"] in RELATIONS,
                     f"Invalid {label}.relation")
            for field in ("data_or_material_product", "basis"):
                _require(_text(handoff.get(field)), f"Missing {label}.{field}")
            _require("condition" in handoff and (handoff["condition"] is None
                     or _text(handoff["condition"])), f"Missing or malformed {label}.condition")
    return modules


def validate_internal_handoffs(profile: dict, sources: dict) -> set[str]:
    """Validate explicit occurrences/references; null condition means unrecorded.

    Arbitrary additional branch/uncertainty fields are retained and hash-bound.
    Every recorded internal assertion needs typed citations, whose identity and
    locator validity do not establish support for the asserted relation.
    """
    _require(isinstance(sources, dict), "Missing internal-handoff sources")
    used = set()
    for module in _modules(profile):
        for index, handoff in enumerate(module.get("internal_handoffs", [])):
            label = f"internal handoff {module['module_id']}/{index}"
            used |= _refs(handoff.get("source_refs"), sources, label)
            used |= _profile_sources(handoff, sources, label)
    return used


def make_module_handoff_checks(profile: dict) -> list[dict]:
    """Produce exact identity scaffolds, without claiming inspection or absence."""
    return [{
        "module_id": module["module_id"], "module_sha256": canonical_hash(module),
        "outcome": "pending_fresh_review", "observations": [],
        "disposition": "pending_fresh_review", "source_refs_checked": [],
        "external_connection_indices": [],
        "internal_handoff_checks": [{
            "handoff_index": index, "handoff_sha256": canonical_hash(handoff),
            "outcome": "pending_fresh_review", "observations": [], "source_refs_checked": [],
        } for index, handoff in enumerate(module.get("internal_handoffs", []))],
    } for module in _modules(profile)]


def _checked_refs(check, original, sources, label, *, module_scan=False):
    refs = check.get("source_refs_checked")
    _require(isinstance(refs, list), f"Missing checked handoff refs: {label}")
    checked = _refs(refs, sources, label) if refs else set()
    skipped = {"source_refs_checked"} | ({"internal_handoff_checks"} if module_scan else set())
    used = _profile_sources({key: value for key, value in check.items() if key not in skipped},
                            sources, label)
    if check["outcome"] in ACCEPTED:
        _require(bool(checked), f"Accepted handoff scan has no inspected refs: {label}")
        missing = _profile_sources(original, sources, label) - checked
        _require(not missing, f"Missing reviewed handoff sources: {label}; "
                 f"missing source IDs: {missing_source_ids_text(missing)}")
    return used | checked


def _indices(value, count, label):
    _require(isinstance(value, list) and all(type(index) is int and 0 <= index < count
             for index in value), f"Invalid {label}")
    _require(len(value) == len(set(value)), f"Duplicate {label}")
    return set(value)


def validate_module_handoff_checks(review: dict, profile: dict, sources: dict) -> set[str]:
    """Require a fresh, current module scan and exact internal occurrence coverage.

    A complete nonaccepting review may describe unread sources with empty refs
    and omitted internal checks with exact uninspected indices. Every module still
    needs a scan row; unseen modules cannot silently become absence observations.
    Existing per-connection review owns the scientific checks of external edges.
    """
    validate_internal_handoffs(profile, sources)
    modules = {module["module_id"]: module for module in _modules(profile)}
    _require(isinstance(review, dict), "Missing module-handoff review")
    _require(_text(review.get("outcome")) and review["outcome"] in OUTCOMES,
             "Invalid module-handoff review outcome")
    accepted = review["outcome"] in ACCEPTED
    topology = review.get("topology_check")
    _check_result(topology, "topology check", accepted)
    recorded_topology = profile.get("topology")
    _require(isinstance(recorded_topology, dict), "Missing handoff external topology")
    connections = recorded_topology.get("connections")
    _require(isinstance(connections, list) and all(isinstance(edge, dict)
             for edge in connections), "Missing handoff external connections")
    checks = topology.get("module_handoff_checks")
    _require(isinstance(checks, list), "Missing module_handoff_checks")
    seen, used = set(), set()
    for check in checks:
        _require(isinstance(check, dict), "Malformed module handoff check")
        mid = check.get("module_id")
        _require(_text(mid) and mid in modules and mid not in seen,
                 "Unknown or duplicate handoff scan module_id")
        seen.add(mid)
        module = modules[mid]
        _require(check.get("module_sha256") == canonical_hash(module),
                 f"Stale handoff scan module_sha256: {mid}")
        _check_result(check, f"module handoff scan {mid}", accepted)
        if topology["outcome"] in ACCEPTED:
            _require(check["outcome"] in ACCEPTED,
                     f"Accepted topology contains unresolved module handoff scan: {mid}")
        used |= _checked_refs(check, module, sources, mid, module_scan=True)
        external = _indices(check.get("external_connection_indices"), len(connections),
                            f"external_connection_indices: {mid}")
        _require(all(connections[index].get("source") == mid
                     or connections[index].get("target") == mid for index in external),
                 f"External connection does not touch reviewed module: {mid}")
        handoffs = module.get("internal_handoffs", [])
        disposition = check.get("disposition")
        _require(_text(disposition) and disposition in DISPOSITIONS,
                 f"Invalid handoff disposition: {mid}")
        if disposition == "missing_or_unresolved":
            _require(check["outcome"] not in ACCEPTED,
                     f"Accepted module handoff scan has missing/unresolved boundary: {mid}")
        else:
            expected = ("represented_both" if external and handoffs else
                        "represented_external" if external else
                        "represented_internal" if handoffs else
                        "no_consequential_handoff_identified")
            _require(disposition == expected, f"Handoff disposition differs from records: {mid}")
        internal = check.get("internal_handoff_checks")
        _require(isinstance(internal, list), f"Missing internal_handoff_checks: {mid}")
        inspected = set()
        for item in internal:
            _require(isinstance(item, dict), f"Malformed internal handoff check: {mid}")
            index = item.get("handoff_index")
            _require(type(index) is int and 0 <= index < len(handoffs),
                     f"Invalid handoff_index: {mid}")
            _require(index not in inspected, f"Duplicate handoff_index: {mid}")
            inspected.add(index)
            handoff = handoffs[index]
            _require(item.get("handoff_sha256") == canonical_hash(handoff),
                     f"Stale handoff_sha256: {mid}/{index}")
            _check_result(item, f"internal handoff check {mid}/{index}",
                          check["outcome"] in ACCEPTED)
            used |= _checked_refs(item, handoff, sources, f"{mid}/{index}")
        omitted = set(range(len(handoffs))) - inspected
        _require(check["outcome"] not in ACCEPTED or not omitted,
                 f"Accepted scan lacks internal handoff coverage: {mid}")
        if omitted or "uninspected_handoff_indices" in check:
            uninspected = _indices(check.get("uninspected_handoff_indices"), len(handoffs),
                                   f"uninspected_handoff_indices: {mid}")
            _require(uninspected == omitted, f"Uninspected handoff indices differ: {mid}")
    _require(seen == set(modules), "Missing fresh module handoff scan coverage")
    return used
