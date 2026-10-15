"""Opt-in scope binding and provisional source-to-record review integrity.

The checklist is an independent source reading, not an adjudicated reference.
These gates check identities and coverage declarations, never entailment.
"""

import hashlib
from pathlib import Path

from .validation import (
    ACCEPTED,
    _check_result,
    _load,
    _prose,
    _proses,
    _refs,
    _require,
    _source_artifact_roles,
    _sources,
    _text,
    _texts,
    _validate_scope_blocking_flags,
    canonical_hash,
)

SCOPE_FORMAT = "protocol_review_scope/1.0"
CHECKLIST_FORMAT = "protocol_source_checklist/1.0"
CHECKLIST_STATUS = "provisional_source_checklist_not_gold_standard"
ITEM_OUTCOMES = {"represented", "missing", "unresolved", "outside_declared_scope"}
REVIEWER_SOURCE_AUDIT = "reviewer_source_audit"


def validate_scope(scope):
    _require(isinstance(scope, dict), "Review scope must be an object")
    for key in ("implementation", "coverage"):
        _require(_text(scope.get(key)), f"Missing review scope.{key}")
    _require(_texts(scope.get("required_outputs")), "Missing review scope.required_outputs")
    for key in ("included_branches", "excluded_branches"):
        _require(_texts(scope.get(key), empty=True), f"Malformed review scope.{key}")
    _require(not set(scope["included_branches"]) & set(scope["excluded_branches"]),
             "Review scope includes and excludes the same branch")
    _validate_scope_blocking_flags(scope, "review_scope")


def make_reviewer_source_audit(scope, prepared_method_sources):
    """Pin the operator's scope; supply no scientific requirements or verdicts."""
    validate_scope(scope)
    audit = {
        "policy": REVIEWER_SOURCE_AUDIT,
        "scope_sha256": canonical_hash(scope),
        "prepared_sources_sha256": canonical_hash(prepared_method_sources),
        "omissions": [], "uninspected_dependencies": [],
    }
    for kind, field in (("output", "required_outputs"), ("branch", "included_branches")):
        audit[kind + "_checks"] = [{
            kind + "_index": index, kind + "_sha256": canonical_hash(value),
            "outcome": "pending_fresh_review", "observations": [],
            "source_refs_checked": [], "record_paths": [],
        } for index, value in enumerate(scope[field])]
    return audit


def validate_reviewer_source_audit(review, extraction, sources, scope,
                                   prepared_method_sources):
    """Check source-to-record judgments, not blind reading or scientific entailment."""
    validate_scope(scope)
    audit = review.get("coverage_check", {}).get("source_audit")
    _require(isinstance(audit, dict), "Missing reviewer source_audit")
    _require(audit.get("policy") == REVIEWER_SOURCE_AUDIT, "Wrong source_audit policy")
    _require(audit.get("scope_sha256") == canonical_hash(scope), "Stale source_audit scope")
    _require(audit.get("prepared_sources_sha256") == canonical_hash(prepared_method_sources),
             "Stale source_audit prepared sources")
    _require("source_to_record_check" not in review,
             "Reviewer source audit cannot claim an independent source checklist")
    accepted = review.get("outcome") in ACCEPTED
    used = set()
    for kind, field in (("output", "required_outputs"), ("branch", "included_branches")):
        rows = audit.get(kind + "_checks")
        _require(isinstance(rows, list), f"Missing source_audit {kind}_checks")
        seen = set()
        for row in rows:
            _require(isinstance(row, dict), f"Malformed source_audit {kind} check")
            index = row.get(kind + "_index")
            _require(type(index) is int and 0 <= index < len(scope[field]) and index not in seen,
                     f"Unknown or duplicate source_audit {kind}_index")
            seen.add(index)
            _require(row.get(kind + "_sha256") == canonical_hash(scope[field][index]),
                     f"Stale source_audit {kind} requirement")
            _check_result(row, f"source_audit {kind} {index}", accepted)
            row_accepted = row["outcome"] in ACCEPTED
            refs = row.get("source_refs_checked")
            _require(isinstance(refs, list), f"Missing source_audit {kind} refs")
            checked = _refs(refs, sources, f"source_audit {kind}") if refs else set()
            _require(not row_accepted or bool(checked),
                     f"Accepted source_audit {kind} needs inspected passages")
            used |= checked
            paths = row.get("record_paths")
            _require(_texts(paths, empty=not row_accepted),
                     f"Accepted source_audit {kind} needs substantive record_paths")
            for pointer in paths:
                _record_pointer(extraction, pointer, allow_unknown=not row_accepted)
        _require(not accepted or seen == set(range(len(scope[field]))),
                 f"Missing source_audit {kind} coverage")
    omissions = audit.get("omissions")
    _require(isinstance(omissions, list), "Missing source_audit omissions")
    _require(not accepted or not omissions, "Accepted review has source-backed omissions")
    for omission in omissions:
        _require(isinstance(omission, dict) and all(_text(omission.get(key))
                 for key in ("affected_field", "detail")), "Malformed source_audit omission")
        used |= _refs(omission.get("source_refs_checked"), sources, "source_audit omission")
    dependencies = audit.get("uninspected_dependencies")
    _require(isinstance(dependencies, list), "Missing source_audit uninspected_dependencies")
    for dependency in dependencies:
        _require(isinstance(dependency, dict) and all(_text(dependency.get(key)) for key in
                 ("affected_field", "detail", "requested_evidence", "scope_reason"))
                 and type(dependency.get("blocking_for_declared_scope")) is bool,
                 "Malformed source_audit uninspected dependency")
        _require(not accepted or not dependency["blocking_for_declared_scope"],
                 "Accepted source_audit leaves a blocking dependency uninspected")
    return used


def load_scopes(path, method_ids):
    document = _load(Path(path))
    _require(document.get("format_version") == SCOPE_FORMAT, "Unsupported review scope format")
    scopes = document.get("methods")
    _require(isinstance(scopes, dict) and set(scopes) == set(method_ids),
             "Review scope must cover exactly the selected method IDs")
    for scope in scopes.values():
        validate_scope(scope)
    return document


def scope_binding(extraction, method_id, review_scopes):
    if review_scopes is not None:
        scope = review_scopes[method_id]
        validate_scope(scope)
        _require(extraction.get("review_scope") == scope,
                 "Extraction review_scope differs from frozen declared scope")


def checklist_document(path, method_id, roots, scope, *, evidence_policy=None,
                       prepared_sources=None):
    document = _load(Path(path))
    _validate_scope_blocking_flags(document, "source_checklist")
    _require(document.get("format_version") == CHECKLIST_FORMAT,
             "Unsupported source checklist format")
    _require(document.get("status") == CHECKLIST_STATUS, "Checklist must remain provisional")
    _require(document.get("method_id") == method_id, "Checklist method_id mismatch")
    validate_scope(scope)
    _require(document.get("review_scope") == scope, "Checklist differs from frozen review scope")
    sources = _sources(document.get("sources"), Path(path).resolve().parent, roots,
                       evidence_policy=evidence_policy)
    if prepared_sources is not None:
        _independent_registration_binding(document, prepared_sources, method_id, roots,
                                          base=Path(path).resolve().parent,
                                          evidence_policy=evidence_policy)
    items = document.get("items")
    _require(isinstance(items, list) and 0 < len(items) <= 200,
             "Checklist needs 1..200 focused source requirements")
    ids = set()
    for item in items:
        _require(isinstance(item, dict), "Malformed source checklist item")
        iid = item.get("item_id")
        _require(_text(iid) and iid not in ids, "Missing or duplicate checklist item_id")
        ids.add(iid)
        for key in ("requirement", "scope_reason"):
            _require(_text(item.get(key)), f"Missing checklist {iid}.{key}")
        _refs(item.get("source_refs"), sources, f"checklist {iid}")
    _require(_proses(document.get("remaining_gaps"), empty=True),
             "Missing checklist remaining_gaps")
    gap_ids = set()
    for gap in document["remaining_gaps"]:
        _require(isinstance(gap, dict) and _text(gap.get("gap_id"))
                 and gap["gap_id"] not in gap_ids, "Missing or duplicate checklist gap_id")
        gap_ids.add(gap["gap_id"])
        _require(isinstance(gap.get("blocking_for_declared_scope"), bool),
                 "Checklist gap needs boolean blocking_for_declared_scope")
        for key in ("affected_field", "detail", "scope_reason"):
            _require(_text(gap.get(key)), f"Missing checklist gap.{key}")
        _require(_prose(gap.get("requested_evidence")), "Missing checklist gap.requested_evidence")
    return document, sources


def validate_checklist(path, method_id, roots, scope, *, evidence_policy=None,
                       prepared_sources=None):
    try:
        checklist_document(path, method_id, roots, scope, evidence_policy=evidence_policy,
                           prepared_sources=prepared_sources)
        return []
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, RecursionError) as exc:
        return [f"Source checklist validation: {exc}"]


def _record_pointer(document, pointer, *, allow_unknown=False):
    _require(_text(pointer) and pointer.startswith("/"), "Record path must be a JSON pointer")
    value = document
    parts = []
    for part in pointer[1:].split("/"):
        # Reject invalid escapes instead of silently targeting another field.
        _require(all(i + 1 < len(part) and part[i + 1] in "01"
                     for i, char in enumerate(part) if char == "~"), "Invalid JSON pointer escape")
        key = part.replace("~1", "/").replace("~0", "~")
        parts.append(key)
        if isinstance(value, list):
            _require(key.isascii() and key.isdigit() and str(int(key)) == key,
                     "Invalid JSON pointer array index")
            _require(int(key) < len(value), "Record path targets missing array item")
            value = value[int(key)]
        else:
            _require(isinstance(value, dict) and key in value, "Record path targets missing field")
            value = value[key]
    _require(value is not None and value != "" and value != [] and value != {},
             "Record path targets empty content")
    # IDs, source pointers, labels and presentation order cannot discharge a
    # scientific requirement. Meaning still needs independent source review.
    scientific = False
    if parts[:2] == ["profiles", "0"] and len(parts) >= 3:
        field = parts[2]
        if field == "modules":
            scientific = len(parts) <= 4 or parts[4] in {
                "operation_description", "purpose", "boundary_rationale", "input_state",
                "output_state", "steps", "materials", "equipment", "information_effect"}
            if len(parts) >= 5 and parts[4] == "internal_handoffs":
                # A whole handoff or its substantive assertion can discharge a
                # requirement; occurrence IDs/hashes/citations alone cannot.
                scientific = len(parts) <= 6 or parts[6] in {
                    "basis", "condition", "data_or_material_product"}
            if allow_unknown and len(parts) >= 5 and parts[4] == "unknowns":
                scientific = True
        elif field == "topology":
            scientific = (len(parts) >= 4 and parts[3] == "connections"
                          and (len(parts) <= 5 or parts[5] in {
                              "basis", "flow", "condition", "data_or_material_product"}))
        elif field == "function_coverage":
            scientific = len(parts) <= 4 or parts[4] == "reason"
        elif field == "profile_metadata":
            scientific = len(parts) == 3 or parts[3] in {
                "implementation_version", "native_specimen", "measurement_target",
                "attribution_unit", "architecture_coverage", "recipe_coverage",
                "directly_recorded_evidence", "instrument_output", "quantity_inferred",
                "key_interfaces"}
        elif field == "unknowns":
            scientific = allow_unknown
    _require(scientific, "Record path must target substantive scientific content, not identifiers")


def _source_extension(original_meta, original_files, current_meta, current_files, *,
                      original_base, current_base, sid, context="Checklist"):
    """Preserve frozen identity while allowing hash-bound declared conversions.

    Parent/path/hash provenance is integrity evidence, not proof that the parser
    faithfully represents a passage; independent source review still checks that.
    """
    label = context + " source identity differs from current record: " + sid
    for key in ("title", "primary_url"):
        _require(original_meta[key] == current_meta[key], label + "; changed " + key)
    original_roles = _source_artifact_roles(
        original_meta, original_files, original_base, sid, required=True)
    current_roles = _source_artifact_roles(
        current_meta, current_files, current_base, sid, required=True)
    missing = set(original_files) - set(current_files)
    _require(not missing,
        label + "; omitted frozen artifacts: " + ", ".join(sorted(p.name for p in missing)))
    _require(all(current_files[path] == original_files[path]
                 and current_roles[path] == original_roles[path]
                 for path in set(original_files) & set(current_files)),
             label + "; changed shared artifact bytes or roles")
    added = set(current_files) - set(original_files)
    _require(all(current_roles[path] == "source_rendition" for path in added),
             "Added checklist artifact must be a source_rendition: " + sid)
    if not added:
        return
    declared = []
    group = current_meta.get("rendition_provenance")
    if group is not None:
        _require(isinstance(group, dict) and _texts(group.get("paths")),
                 "Malformed additional rendition provenance: " + sid)
        declared.extend({**group, "path": path} for path in group["paths"])
    text = current_meta.get("text_renditions", [])
    _require(isinstance(text, list), "Malformed additional text rendition provenance: " + sid)
    declared.extend(text)
    images = current_meta.get("image_renditions", [])
    _require(isinstance(images, list), "Malformed additional image rendition provenance: " + sid)
    declared.extend(images)
    covered = set()
    for rendition in declared:
        _require(isinstance(rendition, dict) and _text(rendition.get("path")),
                 "Malformed additional rendition provenance: " + sid)
        path = (Path(current_base) / rendition["path"]).resolve()
        _require(path in current_files and current_roles[path] == "source_rendition",
                 "Unregistered additional rendition provenance artifact: " + sid)
        if path not in added:
            continue
        _require(path not in covered and _text(rendition.get("source_path")),
                 "Missing or duplicate additional rendition parent: " + sid)
        parent = (Path(current_base) / rendition["source_path"]).resolve()
        _require(parent in original_files and original_roles[parent] == "source_document"
                 and rendition.get("source_sha256") == original_files[parent],
                 "Additional rendition must bind a frozen source_document parent: " + sid)
        _require(all(_text(rendition.get(key)) for key in ("parser", "parser_version")),
                 "Missing additional rendition parser provenance: " + sid)
        _require("sha256" not in rendition or rendition["sha256"] == current_files[path],
                 "Additional rendition provenance sha256 mismatch: " + sid)
        covered.add(path)
    _require(covered == added, "Missing additional rendition provenance coverage: " + sid)


def _sibling_source_identity(original_meta, original_files, current_meta, current_files, *,
                             original_base, current_base, sid):
    """Compare independent registrations without requiring identical conversions.

    Each worker separately preserves supplied files and binds its new conversions.
    Siblings must agree on original documents and any paths they both register.
    """
    label = "Checklist source identity differs from current record: " + sid
    for key in ("title", "primary_url"):
        _require(original_meta[key] == current_meta[key], label + "; changed " + key)
    original_roles = _source_artifact_roles(
        original_meta, original_files, original_base, sid, required=True)
    current_roles = _source_artifact_roles(
        current_meta, current_files, current_base, sid, required=True)
    original_documents = {path: digest for path, digest in original_files.items()
                          if original_roles[path] == "source_document"}
    current_documents = {path: digest for path, digest in current_files.items()
                         if current_roles[path] == "source_document"}
    _require(original_documents == current_documents,
             label + "; changed original source_document registration")
    _require(all(current_files[path] == original_files[path]
                 and current_roles[path] == original_roles[path]
                 for path in set(original_files) & set(current_files)),
             label + "; changed shared artifact bytes or roles")


def _prepared_artifact_role_bindings(packet):
    """Bind supplied roles and allowed bibliographic owners across source IDs."""
    paths, hashes, path_owners, hash_owners = {}, {}, {}, {}
    for leads in packet["methods"].values():
        for lead in leads:
            for file in lead["files"]:
                path, digest, role = Path(file["path"]).resolve(), file["sha256"], file["role"]
                binding = (digest, role)
                _require(path not in paths or paths[path] == binding,
                         "Conflicting prepared artifact hash/role for resolved path: " + str(path))
                _require(digest not in hashes or hashes[digest] == role,
                         "Conflicting prepared artifact roles for identical SHA256: " + digest)
                paths[path], hashes[digest] = binding, role
                owner = (lead["title"], lead["url"])
                path_owners.setdefault(path, set()).add(owner)
                hash_owners.setdefault(digest, set()).add(owner)
    return paths, hashes, path_owners, hash_owners


def _independent_registration_binding(document, packet, method_id, roots, *, base,
                                      evidence_policy=None):
    """Preserve prepared files and bind newly registered sources' conversions.

    Supplied renditions keep their frozen registration; this does not retroactively
    require parser declarations for inputs prepared under an earlier contract.
    """
    from .prepared_sources import FORMAT

    _require(isinstance(packet, dict) and packet.get("format_version") == FORMAT,
             "Unsupported independent prepared_sources format_version")
    _require(isinstance(packet.get("methods"), dict) and method_id in packet["methods"],
             "Independent prepared_sources must contain the assigned method_id")
    prepared_paths, prepared_hashes, path_owners, hash_owners = (
        _prepared_artifact_role_bindings(packet))
    prepared_registration_binding(document, packet, method_id, roots, base=base,
                                  evidence_policy=evidence_policy)
    supplied = {lead["source_id"] for lead in packet["methods"][method_id]}
    registered = _sources(document.get("sources"), base, roots,
                          evidence_policy=evidence_policy)
    for source in document["sources"]:
        sid = source["source_id"]
        meta, files = source["source_metadata"], registered[sid]
        roles = _source_artifact_roles(meta, files, base, sid, required=True)
        owner = (meta["title"], meta["primary_url"])
        for path, digest in files.items():
            _require(path not in prepared_paths or prepared_paths[path] == (digest, roles[path]),
                     "Prepared artifact identity/role differs across source IDs: " + sid
                     + "; " + path.name)
            _require(digest not in prepared_hashes or prepared_hashes[digest] == roles[path],
                     "Prepared artifact role differs across source IDs for identical bytes: "
                     + sid + "; " + path.name)
            _require(path not in path_owners or owner in path_owners[path],
                     "Prepared bibliographic identity differs across source IDs for path: "
                     + sid + "; " + path.name)
            _require(digest not in hash_owners or owner in hash_owners[digest],
                     "Prepared bibliographic identity differs across source IDs for identical "
                     "bytes: " + sid + "; " + path.name)
        if sid in supplied:
            continue
        originals = {path: digest for path, digest in files.items()
                     if roles[path] == "source_document"}
        original_meta = {**meta, "artifact_roles": {
            str(path): "source_document" for path in originals}}
        _source_extension(original_meta, originals, meta, files, original_base=base,
                          current_base=base, sid=sid, context="Independent")


def validate_coverage_review(review, extraction, checklist_path, method_id, roots, scope,
                             *, extraction_base, evidence_policy=None, prepared_sources=None,
                             independent_source_renditions=False):
    _require(isinstance(independent_source_renditions, bool),
             "independent_source_renditions must be boolean")
    _require(not independent_source_renditions or prepared_sources is not None,
             "Independent source renditions require frozen prepared_sources")
    checklist, sources = checklist_document(checklist_path, method_id, roots, scope,
                                           evidence_policy=evidence_policy,
                                           prepared_sources=(prepared_sources if
                                               independent_source_renditions else None))
    check = review.get("source_to_record_check")
    _require(isinstance(check, dict), "Missing independent source_to_record_check")
    _require(check.get("checklist_sha256") == hashlib.sha256(
        Path(checklist_path).read_bytes()).hexdigest(), "Stale source checklist_sha256")
    _require(check.get("scope_sha256") == canonical_hash(scope), "Stale review scope_sha256")
    items = {item["item_id"]: item for item in checklist["items"]}
    checks = check.get("item_checks")
    _require(isinstance(checks, list), "Missing source checklist item_checks")
    seen = set()
    accepted = review.get("outcome") in ACCEPTED
    for item_check in checks:
        _require(isinstance(item_check, dict), "Malformed source checklist item check")
        iid = item_check.get("item_id")
        _require(iid in items and iid not in seen,
                 "Unknown or duplicate reviewed checklist item_id")
        seen.add(iid)
        outcome = item_check.get("outcome")
        _require(outcome in ITEM_OUTCOMES, f"Invalid checklist outcome: {iid}")
        _require(_texts(item_check.get("observations")), f"Missing checklist observations: {iid}")
        _require(_text(item_check.get("scope_reason")), f"Missing checklist scope_reason: {iid}")
        refs = _refs(item_check.get("source_refs_checked"), sources, f"checklist review {iid}")
        _require({r["source_id"] for r in items[iid]["source_refs"]} <= refs,
                 f"Missing reviewed checklist source: {iid}")
        pointers = item_check.get("record_paths")
        _require(_texts(pointers, empty=outcome != "represented"),
                 f"Missing represented record_paths: {iid}")
        for pointer in pointers:
            _record_pointer(extraction, pointer, allow_unknown=outcome == "unresolved")
        _require(not accepted or outcome in {"represented", "outside_declared_scope"},
                 f"Accepted review leaves source requirement {outcome}: {iid}")
    _require(seen == set(items), "Missing source-to-record checklist coverage")
    gaps = {g["gap_id"]: g for g in checklist["remaining_gaps"]}
    gap_checks = check.get("gap_checks")
    _require(isinstance(gap_checks, list), "Missing provisional checklist gap_checks")
    all_sources = dict(sources)
    identities = {s["source_id"]: (s["source_metadata"], Path(checklist_path).resolve().parent)
                  for s in checklist["sources"]}
    for register in (extraction.get("sources", []), review.get("sources", [])):
        if not register:
            continue
        if independent_source_renditions:
            _independent_registration_binding({"sources": register}, prepared_sources,
                                              method_id, roots, base=extraction_base,
                                              evidence_policy=evidence_policy)
        registered = _sources(register, Path(extraction_base).resolve(), roots,
                              evidence_policy=evidence_policy)
        metadata = {s["source_id"]: s["source_metadata"] for s in register}
        for sid, files in registered.items():
            if sid in all_sources:
                original_meta, original_base = identities[sid]
                compare = (_sibling_source_identity if independent_source_renditions
                           else _source_extension)
                compare(original_meta, all_sources[sid], metadata[sid], files,
                        original_base=original_base, current_base=extraction_base, sid=sid)
            all_sources[sid] = files
            identities[sid] = (metadata[sid], Path(extraction_base).resolve())
    seen_gaps = set()
    for gap_check in gap_checks:
        _require(isinstance(gap_check, dict), "Malformed provisional gap check")
        gid = gap_check.get("gap_id")
        _require(gid in gaps and gid not in seen_gaps, "Unknown or duplicate reviewed gap_id")
        seen_gaps.add(gid)
        outcome = gap_check.get("outcome")
        _require(outcome in {"resolved", "unresolved", "outside_declared_scope"},
                 "Invalid provisional gap outcome")
        _require(_texts(gap_check.get("observations")) and _text(gap_check.get("scope_reason")),
                 "Missing provisional gap observations/scope_reason")
        if outcome in {"resolved", "outside_declared_scope"}:
            _refs(gap_check.get("source_refs_checked"), all_sources, "resolved gap " + gid)
        if outcome == "resolved":
            _require(_texts(gap_check.get("record_paths")), "Resolved gap needs record_paths")
            for pointer in gap_check["record_paths"]:
                _record_pointer(extraction, pointer)
        if outcome == "unresolved":
            _require(not accepted or not gaps[gid]["blocking_for_declared_scope"],
                     "Accepted review leaves essential source gap unresolved: " + gid)
            _require(any(isinstance(g, dict) and g.get("gap_id") == gid
                         and g.get("blocking_for_declared_scope") is
                         gaps[gid]["blocking_for_declared_scope"]
                         for g in review.get("remaining_gaps", [])),
                     "Unresolved checklist gap must remain in review.remaining_gaps: " + gid)
    _require(seen_gaps == set(gaps), "Missing provisional source gap assessment")


def prepared_registration_binding(extraction, packet, method_id, roots, *, base,
                                  evidence_policy=None):
    """Check supplied identities before review, without independent checklist prose.

    Unused discovery leads need not be registered as inspected. A source registered
    under a supplied ID must preserve its frozen files and bind added conversions.
    This checks integrity, not reading or entailment.
    """
    registered = _sources(extraction.get("sources"), base, roots,
                          evidence_policy=evidence_policy)
    metadata = {s["source_id"]: s["source_metadata"] for s in extraction["sources"]}
    for lead in packet["methods"][method_id]:
        sid = lead["source_id"]
        if sid not in registered:
            continue
        files = {Path(f["path"]).resolve(): f["sha256"] for f in lead["files"]}
        original = {"title": lead["title"], "primary_url": lead["url"],
                    "artifact_roles": {f["path"]: f["role"] for f in lead["files"]}}
        _source_extension(original, files, metadata[sid], registered[sid],
                          original_base=base, current_base=base, sid=sid, context="Prepared")
