"""Optional, source-bound evolution candidates; no graph mutation or inferred ancestry.

Claims are identified independently of their endpoints, allowing parallel typed
assertions. Extraction never declares a claim supported: that export disposition
requires a separate hash-bound review. A passage match proves occurrence only.
The harness must enforce reviewer independence and supply trusted method IDs.

Character locators address decoded UTF-8 text, before normalization. The only
normalization collapses Unicode whitespace; words, punctuation and case remain
unchanged. PDF-derived text requires a registered rendition and provenance.
XML/HTML artifacts can be independently retrieved alongside a PDF; co-location
and filename extensions do not establish their origin, which needs source review.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Collection
from pathlib import Path

FIELDS = ("before", "after", "design_move", "bottleneck", "tradeoff")
CRITICAL_FIELDS = {"before", "after", "design_move"}
RELATIONS = {
    "method_development": {
        "extends",
        "adapts",
        "combines",
        "replaces_step",
        "scales",
        "adds_measurement",
    },
    "component_reuse": {"uses_component", "adapts", "combines", "replaces_step",
                        "scales", "adds_measurement"},
    "comparison": {"compares"},
    "analogy": {"analogous_to"},
}
OPERATORS = {
    "method_development": {
        "add",
        "replace",
        "remove",
        "reorder",
        "combine",
        "scale",
        "change_material",
        "change_readout",
        "adapt",
    },
    "component_reuse": {"reuse", "add", "replace", "remove", "reorder", "combine",
                        "scale", "change_material", "change_readout", "adapt"},
    "comparison": {"compare"},
    "analogy": {"analogy"},
}
CANDIDATE_STATUSES = {"awaiting_review", "awaiting_evidence", "not_established", "withheld"}
REVIEW_STATUSES = {"supported", "awaiting_evidence", "not_established", "withheld"}
FIELD_STATUSES = {"supported", "pending", "not_reported", "not_applicable", "inferred"}


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _member(value, choices):
    return isinstance(value, str) and value in choices


def _ids(value, label, *, empty=False):
    _require(
        isinstance(value, list) and (empty or value) and all(_text(v) for v in value),
        f"Missing or malformed {label}",
    )
    _require(len(value) == len(set(value)), f"Duplicate {label}")
    return set(value)


def _identifier(value, label):
    _require(
        isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._/-]{0,239}", value),
        f"Missing or malformed stable {label}",
    )
    return value


def _field(value, evidence_ids, label):
    _require(isinstance(value, dict), f"Missing {label}")
    status = value.get("evidence_status")
    _require(_member(status, FIELD_STATUSES), f"Invalid {label}.evidence_status")
    refs = _ids(value.get("evidence_ids"), f"{label}.evidence_ids", empty=status != "supported")
    _require(refs <= evidence_ids, f"Unknown evidence ID in {label}")
    if status == "supported":
        _require(_text(value.get("text")), f"Missing supported {label}.text")
    else:
        _require(_text(value.get("reason")), f"Missing explicit gap/inference reason in {label}")
        _require(
            value.get("text") is None or isinstance(value.get("text"), str),
            f"Malformed {label}.text",
        )
    return status, refs


def _sources(extraction, base, roots):
    # Lazy import lets validation.py call this module without an import cycle.
    from .validation import _sources as source_register

    return source_register(extraction.get("sources"), base, roots)


def _anchor(evidence, sources, metadata, base):
    _require(isinstance(evidence, dict), "Malformed evolution evidence")
    eid = _identifier(evidence.get("evidence_id"), "evidence_id")
    sid = evidence.get("source_id")
    _require(_text(sid) and sid in sources, f"Unregistered evidence source_id: {eid}")
    _require(_text(evidence.get("path")), f"Missing evidence path: {eid}")
    path = (base / evidence["path"]).resolve()
    _require(path in sources[sid], f"Evidence path belongs to wrong/unregistered source: {eid}")
    _require(evidence.get("sha256") == sources[sid][path], f"Evidence sha256 mismatch: {eid}")
    raw = path.read_bytes()
    _require(
        hashlib.sha256(raw).hexdigest() == evidence["sha256"],
        f"Evidence bytes changed while validating: {eid}",
    )
    _require(
        path.suffix.lower() != ".pdf" and not raw.startswith(b"%PDF-") and b"\0" not in raw,
        f"Evidence must anchor UTF-8 text, not a PDF/binary artifact: {eid}",
    )
    text = raw.decode("utf-8")
    pdfs = {p for p in sources[sid] if p.suffix.lower() == ".pdf"}
    renditions = metadata[sid].get("text_renditions", [])
    _require(isinstance(renditions, list), f"Malformed text rendition provenance: {eid}")
    matches = [
        r
        for r in renditions
        if isinstance(r, dict) and _text(r.get("path")) and (base / r["path"]).resolve() == path
    ]
    # A publisher XML/HTML artifact need not have been produced from its PDF.
    # This exception permits independent artifacts; it does not certify origin.
    native_markup = path.suffix.lower() in {".xml", ".html", ".htm", ".xhtml"}
    if matches or (pdfs and not native_markup):
        _require(
            len(matches) == 1, f"PDF evidence needs registered text rendition provenance: {eid}"
        )
        rendition = matches[0]
        _require(_text(rendition.get("source_path")), f"Missing rendition source_path: {eid}")
        original = (base / rendition["source_path"]).resolve()
        _require(
            original in sources[sid]
            and original != path
            and rendition.get("sha256") == sources[sid][path]
            and rendition.get("source_sha256") == sources[sid][original]
            and _text(rendition.get("parser"))
            and _text(rendition.get("parser_version")),
            f"Invalid text rendition provenance: {eid}",
        )
    locator = evidence.get("locator")
    _require(
        isinstance(locator, dict) and _text(locator.get("label")),
        f"Missing exact evidence locator: {eid}",
    )
    start, end = locator.get("start_char"), locator.get("end_char")
    _require(
        type(start) is int and type(end) is int and 0 <= start < end <= len(text),
        f"Invalid character evidence locator: {eid}",
    )
    _require(evidence.get("normalization") == "whitespace", f"Unsupported normalization: {eid}")
    excerpt = evidence.get("excerpt")
    _require(_text(excerpt), f"Empty evidence excerpt: {eid}")
    _require(
        " ".join(text[start:end].split()) == " ".join(excerpt.split()),
        f"Evidence excerpt does not match located source span: {eid}",
    )
    return eid


def _cycle(edges):
    adjacency = defaultdict(set)
    for source, target in edges:
        adjacency[source].add(target)
    visiting, visited = set(), set()

    def visit(node):
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(child) for child in adjacency.get(node, ())):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in list(adjacency))


def _claims(
    extraction,
    method_id,
    base,
    roots,
    known_method_ids,
    require_evaluated,
    existing_claim_ids,
    existing_development_edges,
):
    _require(isinstance(extraction, dict), "Extraction must be an object")
    if "evolution_claims" not in extraction:
        _require(
            not require_evaluated, "Missing required evolution_claims evaluation (use [] if none)"
        )
        return [], {}, {}
    claims = extraction["evolution_claims"]
    _require(isinstance(claims, list), "evolution_claims must be a list")
    if not claims:
        return claims, {}, {}
    _require(_text(method_id), "Missing assigned method_id")
    known = set(known_method_ids) | {method_id}
    sources = _sources(extraction, base, roots)
    metadata = {s["source_id"]: s["source_metadata"] for s in extraction["sources"]}
    profiles = extraction.get("profiles")
    _require(
        isinstance(profiles, list) and len(profiles) == 1 and isinstance(profiles[0], dict),
        "Evolution claims need one assigned profile",
    )
    profile = profiles[0]
    _require(profile.get("method_ids") == [method_id], "Evolution profile assignment mismatch")
    modules = {
        m["module_id"]: m
        for m in profile.get("modules", [])
        if isinstance(m, dict) and _text(m.get("module_id"))
    }
    seen, evidence_seen = set(existing_claim_ids), set()
    development = list(existing_development_edges)
    for claim in claims:
        _require(isinstance(claim, dict), "Malformed evolution claim")
        cid = _identifier(claim.get("claim_id"), "claim_id")
        _require(cid not in seen, f"Duplicate/colliding claim_id: {cid}")
        seen.add(cid)
        source, target = claim.get("source_method_id"), claim.get("target_method_id")
        _require(
            _text(source) and source in known and _text(target) and target in known,
            f"Unregistered method endpoint: {cid}",
        )
        _require(target == method_id, f"Evolution target must be assigned method: {cid}")
        _require(source != target, f"Self-edge is not an evolution assertion: {cid}")
        kind = claim.get("relationship_kind")
        _require(_member(kind, RELATIONS), f"Invalid relationship_kind: {cid}")
        _require(
            _member(claim.get("relation"), RELATIONS[kind]),
            f"relation does not match relationship_kind: {cid}",
        )
        _require(
            _member(claim.get("change_operator"), OPERATORS[kind]),
            f"change_operator does not match relationship_kind: {cid}",
        )
        _require(
            _member(claim.get("status"), CANDIDATE_STATUSES),
            f"Invalid candidate status (extraction cannot self-promote): {cid}",
        )
        mids = _ids(claim.get("module_ids"), f"{cid}.module_ids")
        _require(mids <= modules.keys(), f"Unknown linked module_id: {cid}")
        evidence = claim.get("evidence")
        _require(isinstance(evidence, list), f"Missing evidence list: {cid}")
        eids = set()
        for item in evidence:
            eid = _anchor(item, sources, metadata, base)
            _require(eid not in evidence_seen, f"Duplicate/colliding evidence_id: {eid}")
            eids.add(eid)
            evidence_seen.add(eid)
        if claim["status"] == "awaiting_review":
            _require(eids, f"Empty evidence cannot await support review: {cid}")
        evidence_sources = {e["source_id"] for e in evidence}
        for mid in mids:
            refs = modules[mid].get("source_refs", [])
            _require(
                not eids
                or evidence_sources & {r.get("source_id") for r in refs if isinstance(r, dict)},
                f"Evidence lacks source link to module {mid}: {cid}",
            )
        for field in FIELDS:
            status, _ = _field(claim.get(field), eids, f"{cid}.{field}")
            if claim["status"] == "awaiting_review" and field in CRITICAL_FIELDS:
                _require(
                    status == "supported",
                    f"awaiting_review requires supported critical field: {cid}.{field}; "
                    "use awaiting_evidence for unresolved support",
                )
        if kind == "method_development" and claim["status"] == "awaiting_review":
            development.append((source, target))
    _require(not _cycle(development), "Method-development cycle requires resolution before support")
    return claims, sources, modules


def validate_evolution_claims(
    extraction: dict,
    method_id: str,
    base: Path,
    source_roots: tuple[Path, ...],
    *,
    known_method_ids: Collection[str] = (),
    require_evaluated: bool = False,
    existing_claim_ids: Collection[str] = (),
    existing_development_edges: Collection[tuple[str, str]] = (),
) -> list[str]:
    """Return integrity errors; explicit pending claims and no claims are valid."""
    try:
        _claims(
            extraction,
            method_id,
            Path(base).resolve(),
            tuple(Path(p).resolve() for p in source_roots),
            known_method_ids,
            require_evaluated,
            existing_claim_ids,
            existing_development_edges,
        )
        return []
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, RecursionError) as exc:
        return [f"Evolution validation: {exc}"]


def _review(
    review,
    extraction,
    method_id,
    base,
    roots,
    known_method_ids,
    require_evaluated,
    existing_claim_ids,
    existing_development_edges,
):
    claims, sources, modules = _claims(
        extraction,
        method_id,
        base,
        roots,
        known_method_ids,
        require_evaluated,
        existing_claim_ids,
        existing_development_edges,
    )
    _require(isinstance(review, dict), "Review must be an object")
    checks = review.get("evolution_checks", [])
    _require(isinstance(checks, list), "evolution_checks must be a list")
    _require(
        not require_evaluated or "evolution_checks" in review,
        "Missing required evolution_checks evaluation (use [] if none)",
    )
    indexed = {c["claim_id"]: c for c in claims}
    checked = {}
    for check in checks:
        _require(isinstance(check, dict), "Malformed evolution check")
        cid = check.get("claim_id")
        _require(
            _text(cid) and cid in indexed and cid not in checked,
            "Unknown or duplicate reviewed claim_id",
        )
        claim = indexed[cid]
        _require(
            review.get("reviewer_role") == "independent_verifier", "Invalid evolution reviewer_role"
        )
        _require(check.get("claim_sha256") == _hash(claim), f"Stale claim_sha256: {cid}")
        outcome = check.get("outcome")
        _require(_member(outcome, REVIEW_STATUSES), f"Invalid evolution review outcome: {cid}")
        _require(_text(check.get("rationale")), f"Missing relationship support rationale: {cid}")
        for key in ("relationship_kind", "relation", "change_operator"):
            _require(check.get(key) == claim[key], f"Reviewed {key} differs from claim: {cid}")
        mids = _ids(check.get("module_ids_checked"), f"{cid}.module_ids_checked")
        _require(mids == set(claim["module_ids"]), f"Missing reviewed module links: {cid}")
        eids = _ids(check.get("evidence_ids_checked"), f"{cid}.evidence_ids_checked", empty=True)
        _require(
            eids == {e["evidence_id"] for e in claim["evidence"]},
            f"Missing or unknown reviewed evidence passages: {cid}",
        )
        field_checks = check.get("field_checks")
        _require(
            isinstance(field_checks, dict) and set(field_checks) == set(FIELDS),
            f"Missing field-level evolution checks: {cid}",
        )
        for field in FIELDS:
            value = field_checks[field]
            _require(
                isinstance(value, dict) and _text(value.get("reason")),
                f"Missing field review reason: {cid}.{field}",
            )
            _require(
                "text" not in value or value["text"] == claim[field].get("text"),
                f"Review field text differs from hashed claim: {cid}.{field}",
            )
            status, refs = _field(
                {**value, "text": claim[field].get("text")}, eids, f"review.{cid}.{field}"
            )
            _require(
                set(claim[field]["evidence_ids"]) <= refs,
                f"Field review omits cited evidence: {cid}.{field}",
            )
            if outcome == "supported":
                _require(
                    status == claim[field]["evidence_status"],
                    f"Review field status requires claim revision: {cid}.{field}",
                )
                _require(
                    status != "pending" and (field not in CRITICAL_FIELDS or status == "supported"),
                    f"Critical/pending evidence cannot be promoted: {cid}.{field}",
                )
        if outcome == "supported":
            _require(
                claim["status"] == "awaiting_review" and eids,
                f"Pending/withheld claim cannot be promoted without revision: {cid}",
            )
            _require(
                review.get("outcome") in {"accepted", "accepted_with_explicit_gaps"},
                f"Supported claim requires accepted profile review: {cid}",
            )
            profile = extraction["profiles"][0]
            _require(
                review.get("profile_sha256") == _hash(profile),
                f"Stale evolution-linked profile_sha256: {cid}",
            )
            module_checks = {
                c.get("module_id"): c
                for c in review.get("module_checks", [])
                if isinstance(c, dict) and _text(c.get("module_id"))
            }
            for mid in mids:
                mc = module_checks.get(mid, {})
                _require(
                    mc.get("module_sha256") == _hash(modules[mid])
                    and mc.get("outcome") in {"accepted", "accepted_with_explicit_gaps"},
                    f"Missing accepted hash-bound linked module review: {mid}",
                )
            for evidence in claim["evidence"]:
                sid, path = evidence["source_id"], (base / evidence["path"]).resolve()
                matched = [
                    c
                    for c in review.get("source_checks", [])
                    if isinstance(c, dict) and c.get("source_id") == sid
                ]
                _require(
                    len(matched) == 1
                    and any(
                        isinstance(f, dict)
                        and _text(f.get("path"))
                        and (base / f["path"]).resolve() == path
                        and f.get("sha256") == sources[sid][path]
                        for f in matched[0].get("files", [])
                    ),
                    f"Missing reviewer source artifact binding: {cid}/{sid}",
                )
        checked[cid] = outcome
    _require(set(checked) == set(indexed), "Missing evolution claim review coverage")
    return checked


def validate_evolution_review(
    review: dict,
    extraction: dict,
    method_id: str,
    base: Path,
    source_roots: tuple[Path, ...],
    *,
    known_method_ids: Collection[str] = (),
    require_evaluated: bool = False,
    existing_claim_ids: Collection[str] = (),
    existing_development_edges: Collection[tuple[str, str]] = (),
) -> list[str]:
    """Validate a separate review; pending claims may coexist with accepted profiles."""
    try:
        _review(
            review,
            extraction,
            method_id,
            Path(base).resolve(),
            tuple(Path(p).resolve() for p in source_roots),
            known_method_ids,
            require_evaluated,
            existing_claim_ids,
            existing_development_edges,
        )
        return []
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, RecursionError) as exc:
        return [f"Evolution review validation: {exc}"]


def evolution_export_status(
    review: dict,
    extraction: dict,
    method_id: str,
    base: Path,
    source_roots: tuple[Path, ...],
    *,
    known_method_ids: Collection[str] = (),
    require_evaluated: bool = False,
    existing_claim_ids: Collection[str] = (),
    existing_development_edges: Collection[tuple[str, str]] = (),
) -> dict:
    """Report candidate dispositions; never modify or automatically import the atlas.

    Recheck collisions/cycles against the destination snapshot at actual import.
    Without that snapshot, this validates only the submitted candidate batch.
    """
    errors = validate_evolution_review(
        review,
        extraction,
        method_id,
        base,
        source_roots,
        known_method_ids=known_method_ids,
        require_evaluated=require_evaluated,
        existing_claim_ids=existing_claim_ids,
        existing_development_edges=existing_development_edges,
    )
    statuses = (
        {c["claim_id"]: c["outcome"] for c in review.get("evolution_checks", [])}
        if not errors
        else {}
    )
    return {
        "evaluated": "evolution_claims" in extraction and "evolution_checks" in review,
        "valid": not errors,
        "claim_statuses": statuses,
        "supported_claim_ids": sorted(
            cid for cid, status in statuses.items() if status == "supported"
        ),
        "pending_claim_ids": sorted(
            cid for cid, status in statuses.items() if status == "awaiting_evidence"
        ),
        "errors": errors,
        "requires_separate_graph_import_review": True,
        "scope": (
            "Traceable reviewed candidates; exact passage occurrence is not semantic entailment."
        ),
    }
