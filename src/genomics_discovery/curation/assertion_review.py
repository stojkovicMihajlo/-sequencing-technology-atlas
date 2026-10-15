"""Opt-in scientific-detail accountability, not an entailment checker.

The inventory binds compact groups of recorded actions, interfaces and qualifiers
to exact profile field values. A reviewer still judges passage support in context.
Grouping does not atomize prose or guarantee that every critical assertion was
identified. Source-to-record omissions remain the independent provisional source
checklist's separate denominator. The caller validates profile/source identity and
enables the policy only for a new full review; historical reviews are unchanged.
"""

from __future__ import annotations

from .validation import (
    ACCEPTED,
    OUTCOMES,
    _check_result,
    _profile_sources,
    _refs,
    _require,
    _text,
    _texts,
    canonical_hash,
)

ASSERTION_POLICY = "scientific-detail/1.0"
CONTEXT_OUTCOMES = {"supported", "unsupported", "not_inspected"}
PROFILE_FIELDS = (
    "implementation_version", "native_specimen", "measurement_target", "attribution_unit",
    "architecture_coverage", "recipe_coverage", "directly_recorded_evidence",
    "instrument_output", "quantity_inferred", "key_interfaces",
)
MODULE_GROUPS = {
    "actions_and_conditions": ("operation_description", "steps", "materials", "equipment"),
    "interfaces_and_retained_fraction": ("input_state", "output_state", "information_effect"),
    "scope_and_qualifiers": (
        "evidence_scope", "unknowns", "purpose", "boundary_rationale", "function_tags"),
}


def _bindings(value, prefix, fields):
    _require(isinstance(value, dict), f"Malformed assertion target: {prefix}")
    _require(all(field in value for field in fields), f"Missing assertion field: {prefix}")
    return [{"field_pointer": f"{prefix}/{field}", "value_sha256": canonical_hash(value[field])}
            for field in fields]


def make_assertion_checks(profile: dict) -> list[dict]:
    """Create pointer/value identities with no reading or scientific assertions.

    Arrays/objects are reviewed as groups. A negative judgment must name actual
    nested fields, making specific contradictions actionable without splitting
    every prose sentence. These selected groups are a review policy, not a proof
    of completeness or a preferred module partition.

    Purpose, boundary rationale and function tags include curator interpretations;
    they need contextual review, not literal matches to paper category names.
    Open additional fields and function_coverage are not inventoried here and
    remain the separate whole-record/coverage review's responsibility.
    """
    _require(isinstance(profile, dict), "Missing scientific-detail profile")
    groups = [("profile_context", _bindings(
        profile.get("profile_metadata"), "/profile_metadata", PROFILE_FIELDS))]
    modules = profile.get("modules")
    _require(isinstance(modules, list) and modules, "Missing scientific-detail modules")
    for index, module in enumerate(modules):
        for name, fields in MODULE_GROUPS.items():
            groups.append((f"module:{index}:{name}", _bindings(
                module, f"/modules/{index}", fields)))
        optional = tuple(key for key in (
            "input_requirements", "mechanisms", "operation_concepts") if key in module)
        if optional:
            groups.append((f"module:{index}:contextual_annotations", _bindings(
                module, f"/modules/{index}", optional)))
    return [{"assertion_id": aid, "field_bindings": bindings,
             "outcome": "pending_fresh_review", "observations": [],
             "context_outcome": "not_inspected", "affected_field_paths": [],
             "source_refs_checked": []} for aid, bindings in groups]


def _resolve_pointer(profile, pointer):
    _require(_text(pointer) and pointer.startswith("/"), "Invalid assertion field pointer")
    value = profile
    for part in pointer[1:].split("/"):
        _require(all(index + 1 < len(part) and part[index + 1] in "01"
                     for index, char in enumerate(part) if char == "~"),
                 "Invalid assertion pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            _require(key.isascii() and key.isdigit() and str(int(key)) == key,
                     "Invalid assertion pointer array index")
            _require(int(key) < len(value), "Assertion pointer targets missing array item")
            value = value[int(key)]
        else:
            _require(isinstance(value, dict) and key in value,
                     "Assertion pointer targets missing field")
            value = value[key]
    return value


def validate_assertion_checks(review: dict, profile: dict, sources: dict) -> set[str]:
    """Bind scoped passage judgments to current targets; return referenced IDs.

    Registered passage locators are scoped by their row. Their specificity and
    entailment are scientific judgments, not inferred from their spelling. A
    valid citation with a declared wrong branch/specimen/version cannot receive
    an accepted row. Honest uninspected work can finish as a nonaccepting review.
    The caller must exclude this subtree from generic recursive reference checks,
    which otherwise reject empty refs on honest nonaccepting rows.
    """
    _require(isinstance(review, dict), "Missing scientific-detail review")
    _require(review.get("scientific_detail_policy") == ASSERTION_POLICY,
             "Missing or unsupported scientific_detail_policy")
    _require(_text(review.get("outcome")) and review["outcome"] in OUTCOMES,
             "Invalid scientific-detail review outcome")
    _require(isinstance(sources, dict), "Missing scientific-detail sources")
    accepted = review["outcome"] in ACCEPTED
    expected = {row["assertion_id"]: row for row in make_assertion_checks(profile)}
    checks = review.get("assertion_checks")
    _require(isinstance(checks, list), "Missing assertion_checks")
    seen, used = set(), set()
    for check in checks:
        _require(isinstance(check, dict), "Malformed assertion check")
        aid = check.get("assertion_id")
        _require(_text(aid) and aid in expected and aid not in seen,
                 "Unknown or duplicate assertion_id")
        seen.add(aid)
        bindings = check.get("field_bindings")
        _require(bindings == expected[aid]["field_bindings"],
                 f"Stale or out-of-scope assertion field_bindings: {aid}")
        _check_result(check, f"assertion check {aid}", accepted)
        context = check.get("context_outcome")
        _require(_text(context) and context in CONTEXT_OUTCOMES,
                 f"Invalid assertion context_outcome: {aid}")
        row_accepted = check["outcome"] in ACCEPTED
        _require(row_accepted == (context == "supported"),
                 f"Assertion outcome disagrees with contextual support: {aid}")
        paths = check.get("affected_field_paths")
        _require(_texts(paths, empty=True) and len(paths) == len(set(paths)),
                 f"Malformed affected_field_paths: {aid}")
        _require(not (context == "supported" and paths),
                 f"Supported assertion has unresolved affected fields: {aid}")
        _require(context != "unsupported" or bool(paths),
                 f"Unsupported assertion must identify affected fields: {aid}")
        for path in paths:
            _require(any(path == field["field_pointer"] or path.startswith(
                field["field_pointer"] + "/") for field in bindings),
                f"Affected assertion field outside bound group: {aid}")
            _resolve_pointer(profile, path)
        refs = check.get("source_refs_checked")
        _require(isinstance(refs, list), f"Missing assertion passage refs: {aid}")
        checked = _refs(refs, sources, f"assertion {aid}") if refs else set()
        _require(context != "supported" or bool(checked),
                 f"Supported assertion has no inspected passage: {aid}")
        _require(context != "unsupported" or bool(checked),
                 f"Unsupported assertion has no inspected passage: {aid}")
        # Empty primary refs mean no inspected passage. Additional typed refs in
        # observations still obey the ordinary registered-source policy.
        used |= checked | _profile_sources(
            {key: value for key, value in check.items() if key != "source_refs_checked"},
            sources, f"assertion {aid}")

    omitted = set(expected) - seen
    _require(not accepted or not omitted, "Accepted review lacks scientific-detail coverage")
    if omitted or "uninspected_assertion_ids" in review:
        ids = review.get("uninspected_assertion_ids")
        _require(_texts(ids, empty=True) and len(ids) == len(set(ids)) and set(ids) == omitted,
                 "Uninspected assertion IDs must equal omitted checks")
    return used
