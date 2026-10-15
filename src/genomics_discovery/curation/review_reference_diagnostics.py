"""Bounded row-local reference diagnostics, separate from the acceptance gate.

These checks explain declared citation coverage. They neither authenticate source
bytes nor infer scientific support, missing knowledge, aliases or new citations.
The caller retains its authoritative validation result unchanged.
"""

from __future__ import annotations

from collections import Counter

from .assertion_review import make_assertion_checks
from .validation import ACCEPTED, _profile_sources, _refs, canonical_hash

_MAX_ROWS = 4096
_MAX_SOURCE_IDS = 64
_MAX_SOURCE_ID_CHARS = 256


def _bounded_source_ids(ids):
    named = [sid for sid in sorted(ids)[:_MAX_SOURCE_IDS] if len(sid) <= _MAX_SOURCE_ID_CHARS]
    return named, len(ids) - len(named)


def missing_source_ids_text(ids):
    """Name exact short IDs; explicitly count IDs omitted from bounded gate text."""
    named, omitted = _bounded_source_ids(ids)
    message = ", ".join(named) or "(no IDs displayed within output limits)"
    if omitted:
        message += f"; {omitted} source IDs omitted by output limits"
    return message


def review_reference_diagnostics(review, profile, sources, *, connections=False,
                                 detail=False, limit=128):
    """Collect independent checked-reference problems from identifiable current rows.

    Sources are declared IDs supplied by the caller, not an authentication claim.
    Stale, duplicate or malformed target identities in the inspected portion are
    skipped; the ordinary gate reports those failures. Uninspected rows can contain
    further identities. Valid refs on another row or in source_checks cannot
    satisfy a row's source_refs_checked. Nonaccepting handoff/edge rows may honestly
    have no inspected refs. Assertion rows retain their existing passage rule;
    no original-source subset requirement is invented for assertion groups.
    """
    if type(limit) is not int or not 1 <= limit <= 1024:
        raise ValueError("Reference diagnostic limit must be an integer from 1 to 1024")
    issues, skipped, found, examined, omitted_rows = [], 0, 0, 0, 0
    omitted_source_ids, omitted_refs = 0, 0

    def missing_ids(ids):
        nonlocal omitted_source_ids
        named, omitted = _bounded_source_ids(ids)
        omitted_source_ids += omitted
        return {"missing_source_ids": named, "missing_source_id_count": len(ids),
                "missing_source_ids_omitted": omitted}

    def add(kind, path, problem, **fields):
        nonlocal found
        found += 1
        if len(issues) < limit:
            issues.append({"kind": kind, "review_field_path": path, "problem": problem,
                           **fields})

    def inspect(kind, path, row, original=None, *, require_refs=True):
        nonlocal examined, omitted_refs
        examined += 1
        refs = row.get("source_refs_checked")
        checked = set()
        required = set()
        if original is not None:
            try:
                required = _profile_sources(original, sources, path)
            except (ValueError, KeyError, TypeError, RecursionError):
                add(kind, path, "invalid_original_reference_context")
                return
        if not isinstance(refs, list):
            add(kind, path, "malformed_source_refs_checked")
            refs = []
        for index, ref in enumerate(refs[:_MAX_ROWS]):
            try:
                checked |= _refs([ref], sources, path)
            except (ValueError, KeyError, TypeError):
                add(kind, path + f"/source_refs_checked/{index}", "invalid_checked_reference")
        if len(refs) > _MAX_ROWS:
            # A later valid ref might cover a source: do not infer its absence.
            omitted_refs += len(refs) - _MAX_ROWS
            add(kind, path, "checked_reference_scan_truncated",
                checked_references_omitted=len(refs) - _MAX_ROWS)
            return
        if require_refs and not checked:
            add(kind, path, "missing_inspected_reference", **missing_ids(required))
        elif missing := required - checked:
            add(kind, path, "missing_source_id_coverage", **missing_ids(missing))

    def rows(values, targets, key, hash_key, prefix):
        nonlocal skipped, omitted_rows
        if not isinstance(values, list):
            skipped += 1
            return
        if len(values) > _MAX_ROWS:
            omitted_rows += len(values) - _MAX_ROWS
            values = values[:_MAX_ROWS]
        identities = Counter()
        for value in values:
            identity = value.get(key) if isinstance(value, dict) else None
            if isinstance(identity, (str, int)) and not isinstance(identity, bool):
                identities[identity] += 1
        for index, row in enumerate(values):
            if not isinstance(row, dict):
                skipped += 1
                continue
            identity = row.get(key)
            if (not isinstance(identity, (str, int)) or isinstance(identity, bool)
                    or identity not in targets or identities[identity] != 1):
                skipped += 1
                continue
            original = targets[identity]
            if hash_key:
                try:
                    current = row.get(hash_key) == canonical_hash(original)
                except (ValueError, TypeError, RecursionError):
                    current = False
                if not current:
                    skipped += 1
                    continue
            yield prefix + f"/{index}", row, original

    def result(**fields):
        return {"scope": "Artifact reference integrity diagnostics only; "
                         "not source authentication, scientific support, "
                         "missing-knowledge judgment or acceptance.",
                "issues": issues, "issues_detected": found,
                "truncated": found > len(issues)
                or bool(omitted_rows or omitted_source_ids or omitted_refs),
                "contexts_examined": examined, "contexts_skipped": skipped,
                "rows_omitted_by_resource_limit": omitted_rows,
                "source_ids_omitted_by_resource_limit": omitted_source_ids,
                "checked_references_omitted_by_resource_limit": omitted_refs,
                "limit": limit, **fields}

    if not all(isinstance(value, dict) for value in (review, profile, sources)):
        return result(inspection_unavailable="Review, profile and declared sources must be objects")
    module_list = profile.get("modules")
    module_list = module_list if isinstance(module_list, list) else []
    module_ids = Counter(module.get("module_id") for module in module_list
                         if isinstance(module, dict) and isinstance(module.get("module_id"), str))
    modules = {module["module_id"]: module for module in module_list
               if isinstance(module, dict) and isinstance(module.get("module_id"), str)
               and module_ids[module["module_id"]] == 1}
    # The ordinary module gate requires its declared top-level sources on every
    # submitted module check, including a nonaccepting check.
    for path, row, module in rows(review.get("module_checks"), modules, "module_id",
                                  "module_sha256", "/module_checks"):
        inspect("module_check", path, row, {"source_refs": module.get("source_refs")})

    topology = review.get("topology_check")
    topology = topology if isinstance(topology, dict) else {}
    if connections:
        original_topology = profile.get("topology")
        original_topology = original_topology if isinstance(original_topology, dict) else {}
        edges = original_topology.get("connections")
        targets = dict(enumerate(edges)) if isinstance(edges, list) else {}
        for path, row, edge in rows(topology.get("connection_checks"), targets,
                                    "connection_index", "connection_sha256",
                                    "/topology_check/connection_checks"):
            accepted = isinstance(row.get("outcome"), str) and row["outcome"] in ACCEPTED
            inspect("connection_check", path, row, edge if accepted else None,
                    require_refs=accepted)
    if detail:
        try:
            targets = {row["assertion_id"]: row for row in make_assertion_checks(profile)}
        except (ValueError, KeyError, TypeError, RecursionError):
            targets = {}
            skipped += 1
        for path, row, original in rows(review.get("assertion_checks"), targets,
                                        "assertion_id", None, "/assertion_checks"):
            if row.get("field_bindings") != original["field_bindings"]:
                skipped += 1
                continue
            inspect("assertion_check", path, row,
                    require_refs=isinstance(row.get("context_outcome"), str)
                    and row["context_outcome"] in {"supported", "unsupported"})
        for path, row, module in rows(topology.get("module_handoff_checks"), modules,
                                      "module_id", "module_sha256",
                                      "/topology_check/module_handoff_checks"):
            accepted = isinstance(row.get("outcome"), str) and row["outcome"] in ACCEPTED
            inspect("module_handoff_scan", path, row, module if accepted else None,
                    require_refs=accepted)
            handoffs = module.get("internal_handoffs", [])
            targets = dict(enumerate(handoffs)) if isinstance(handoffs, list) else {}
            for inner_path, inner, handoff in rows(row.get("internal_handoff_checks"), targets,
                    "handoff_index", "handoff_sha256", path + "/internal_handoff_checks"):
                accepted = isinstance(inner.get("outcome"), str) and inner["outcome"] in ACCEPTED
                inspect("internal_handoff_check", inner_path, inner, handoff if accepted else None,
                        require_refs=accepted)
    return result()
