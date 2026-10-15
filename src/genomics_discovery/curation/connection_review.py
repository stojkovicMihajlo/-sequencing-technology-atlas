"""Identity and inspection coverage for opt-in ``per-connection/1.0`` review.

These checks bind inspection claims to exact edge occurrences. They do not prove
that a passage entails a transfer, decision, condition, or claimed prerequisite.
The caller validates the profile and source files and opts into this policy;
legacy reviews need not use these helpers.
"""

from __future__ import annotations

from .review_reference_diagnostics import missing_source_ids_text
from .validation import (
    ACCEPTED,
    OUTCOMES,
    _check_result,
    _profile_sources,
    _refs,
    _require,
    _text,
    canonical_hash,
)


def _connections(profile: dict) -> list[dict]:
    _require(isinstance(profile, dict), "Missing connection-review profile")
    topology = profile.get("topology")
    _require(isinstance(topology, dict), "Missing connection-review topology")
    for alias in ("edges", "control_edges"):
        if alias in topology:
            _require(isinstance(topology[alias], list) and not topology[alias],
                     f"Per-connection review requires canonical connections; unsupported {alias}")
    connections = topology.get("connections")
    _require(isinstance(connections, list), "Missing reviewed connections")
    _require(all(isinstance(edge, dict) for edge in connections),
             "Malformed reviewed connection")
    return connections


def make_connection_checks(profile: dict) -> list[dict]:
    """Pin every edge, without observations, citations, or an inspection verdict.

    A pending placeholder cannot pass review validation. Identical parallel edges
    still receive distinct indices; the hash also binds every recorded attribute.
    """
    return [{"connection_index": index, "connection_sha256": canonical_hash(edge),
             "outcome": "pending_fresh_review", "observations": [],
             "source_refs_checked": []}
            for index, edge in enumerate(_connections(profile))]


def validate_connection_checks(review: dict, profile: dict, sources: dict) -> set[str]:
    """Validate per-edge claims and return checked IDs for file-coverage checks.

    Accepting verdicts require complete accepted edge coverage. A nonaccepting
    review may omit checks only when ``uninspected_connection_indices`` names
    exactly those omissions and the aggregate topology verdict is nonaccepting.
    Submitted unresolved checks may honestly have no source refs; accepted checks
    require inspected refs and every explicitly typed original edge source.

    Source names embedded in legacy ``basis`` prose are not guessed or parsed.
    The caller must delegate this subtree before generic recursive ref validation,
    which does not allow empty refs for unresolved inspection claims.
    """
    edges = _connections(profile)
    _require(isinstance(review, dict), "Missing connection-review document")
    _require(_text(review.get("outcome")) and review["outcome"] in OUTCOMES,
             "Invalid connection-review outcome")
    _require(isinstance(sources, dict), "Missing connection-review sources")
    accepted = review["outcome"] in ACCEPTED
    topology = review.get("topology_check")
    _check_result(topology, "topology check", accepted)
    checks = topology.get("connection_checks")
    _require(isinstance(checks, list), "Missing topology connection_checks")

    seen, used = set(), set()
    for check in checks:
        _require(isinstance(check, dict), "Malformed connection check")
        index = check.get("connection_index")
        _require(type(index) is int and 0 <= index < len(edges),
                 "Invalid connection_index")
        _require(index not in seen, "Duplicate connection_index")
        seen.add(index)
        edge = edges[index]
        _require(check.get("connection_sha256") == canonical_hash(edge),
                 f"Stale connection_sha256: {index}")
        _check_result(check, f"connection check {index}", accepted)
        # Only the primary reading-ref array can be empty for unresolved work.
        # Rich observations/findings still follow the ordinary recursive policy.
        used |= _profile_sources({key: value for key, value in check.items()
                                  if key != "source_refs_checked"},
                                 sources, f"connection check {index}")
        original_refs = _profile_sources(edge, sources, f"connection {index}")
        refs = check.get("source_refs_checked")
        _require(isinstance(refs, list), f"Missing checked connection refs: {index}")
        checked_refs = _refs(refs, sources, f"connection check {index}") if refs else set()
        if check["outcome"] in ACCEPTED:
            _require(bool(checked_refs), f"Accepted connection has no inspected refs: {index}")
            missing = original_refs - checked_refs
            _require(not missing, f"Missing reviewed connection sources: {index}; "
                     f"missing source IDs: {missing_source_ids_text(missing)}")
        used |= checked_refs

    omitted = set(range(len(edges))) - seen
    _require(not accepted or not omitted, "Accepted review lacks connection coverage")
    if omitted:
        _require(topology["outcome"] not in ACCEPTED,
                 "Partial connection coverage cannot accept topology")
    if topology["outcome"] in ACCEPTED:
        _require(all(check["outcome"] in ACCEPTED for check in checks),
                 "Accepted topology contains unresolved connection checks")
    if omitted or "uninspected_connection_indices" in topology:
        uninspected = topology.get("uninspected_connection_indices")
        _require(isinstance(uninspected, list) and all(
            type(index) is int and 0 <= index < len(edges) for index in uninspected),
            "Invalid uninspected_connection_indices")
        _require(len(uninspected) == len(set(uninspected)) and set(uninspected) == omitted,
                 "Uninspected connection indices must equal omitted checks")
    return used
