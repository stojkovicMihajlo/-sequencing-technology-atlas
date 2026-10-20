"""Persistent pending proposals and overlapping candidate lookup.

Multiple processes may submit or review concurrently. Proposal and review records
are immutable; the publication service alone changes admitted registry contents.
"""

import json
from pathlib import Path

from genomics_discovery.curation.run_state import sha
from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import load_occurrences
from .admission import VocabularyProposal, CurrentVocabularyReview, validate_decision, validate_batch
from .candidate_index import SMALL_CATALOGUE, index_definition, lookup_owners
from .definition_comparison import ComparisonReport, validate_report
from .semantic_candidates import (
    SEMANTIC_LIMIT, retrieval_policy, validate_retrieval_policy, candidate_binding, retrieve,
)
from .storage import connect, generation


def _comparison_profile_files(manifest):
    paths = {str(Path(row["comparison_profile"]).resolve()) for row in manifest.values()
             if row.get("comparison_profile")}
    try:
        return {path: sha(Path(path)) for path in sorted(paths)}
    except OSError as exc:
        raise ValueError("Comparison profile file is missing or unreadable") from exc


def enqueue(path, proposal, catalogue, manifest, *, embeddings=None, semantic_limit=SEMANTIC_LIMIT):
    occurrences, packets = load_occurrences(manifest)
    if {d.occurrence_id for d in proposal.decisions} != occurrences.keys():
        raise ValueError("A queued proposal must cover all supplied selections")
    for decision in proposal.decisions:
        validate_decision(decision, catalogue, occurrences, packets)
    payload = {"proposal": proposal.model_dump(mode="json"), "catalogue": catalogue, "manifest": manifest}
    if embeddings is not None:
        payload["retrieval_policy"] = retrieval_policy(embeddings, semantic_limit)
    if profiles := _comparison_profile_files(manifest):
        payload["comparison_profile_files"] = profiles
    key = digest(payload)
    with connect(path, write=True) as db:
        generation(db)
        exists = db.execute("SELECT payload FROM submissions WHERE id=?", (key,)).fetchone()
        if exists:
            if json.loads(exists[0]) != payload:
                raise ValueError("Submission identity collision")
            return key
        db.execute("INSERT INTO submissions(id,payload) VALUES (?,?)", (key, json.dumps(payload)))
        for decision in proposal.decisions:
            if decision.assignment:
                index_definition(db, f"proposal:{key}:{decision.assignment.definition.id}", decision.assignment.concept())
        db.execute("UPDATE registry_meta SET generation=generation+1 WHERE id=1")
    return key


def submission(db, key):
    row = db.execute("SELECT payload,review,result FROM submissions WHERE id=?", (key,)).fetchone()
    if row is None:
        raise ValueError("Unknown submission")
    payload = json.loads(row[0])
    if digest(payload) != key:
        raise ValueError("Queued submission changed after registration")
    if (payload.get("comparison_profile_files", {}) != _comparison_profile_files(payload["manifest"])):
        raise ValueError("Comparison profile files changed after registration")
    review = json.loads(row[1]) if row[1] else None
    if review and review.get("document_sha256") != digest(review["document"]):
        raise ValueError("Stored source review changed")
    return {**payload, "review": review,
            "result": json.loads(row[2]) if row[2] else None}


def attach_review(path, key, review, *, provenance=None):
    with connect(path) as db:
        saved = submission(db, key)
    occurrences, packets = load_occurrences(saved["manifest"])
    proposal = VocabularyProposal.model_validate(saved["proposal"])
    outcomes = validate_batch(proposal, review, saved["catalogue"], occurrences, packets)
    value = {"document": review.model_dump(mode="json"), "document_sha256": digest(review), "provenance": provenance or {
        "kind": "supplied_review_artifact", "independent_execution_verified": False},
        "outcomes": outcomes, "outcomes_sha256": digest(outcomes)}
    with connect(path, write=True) as db:
        current = submission(db, key)
        if current["review"] and any(value.get(k) != v for k, v in current["review"].items()):
            raise ValueError("Source review is immutable; submit a revised proposal separately")
        if not current["review"]:
            db.execute("UPDATE submissions SET review=? WHERE id=?", (json.dumps(value), key))
            db.execute("UPDATE registry_meta SET generation=generation+1 WHERE id=1")


def reviewed_targets(saved):
    """Eligible draft decisions, not a cohort-level approximation of review."""
    if not saved["review"]:
        return None  # Unreviewed pending proposals remain visible for discovery.
    review = saved["review"]
    if "outcomes" in review:
        if digest(review["outcomes"]) != review.get("outcomes_sha256"):
            raise ValueError("Stored source outcomes changed")
        outcomes = review["outcomes"]
    else:
        # Read historical records without rewriting an immutable source review.
        occurrences, packets = load_occurrences(saved["manifest"])
        outcomes = validate_batch(VocabularyProposal.model_validate(saved["proposal"]),
            CurrentVocabularyReview.model_validate(review["document"]), saved["catalogue"], occurrences, packets)
    return {r["decision_sha256"] for r in outcomes if r["status"] == "admit"}


def definitions(db, *, include_inactive=False):
    result = {}
    for cid, payload in db.execute("SELECT id,payload FROM concepts"):
        concept = json.loads(payload)
        result["concept:" + cid] = concept
    for key, payload, terminal in db.execute("SELECT id,payload,result FROM submissions"):
        if not include_inactive and terminal and json.loads(terminal).get("status") in {"withdrawn", "completed_without_admission"}:
            continue
        value = submission(db, key)
        eligible = None if include_inactive else reviewed_targets(value)
        for decision in value["proposal"]["decisions"]:
            if decision["assignment"] and (eligible is None or digest(decision) in eligible):
                assignment = decision["assignment"]
                result[f"proposal:{key}:{assignment['definition']['id']}"] = {
                    k: assignment[k] for k in ("definition", "relation", "aliases", "scope_note")}
    return result


def make_plan(db, key, *, embeddings=None, semantic_limit=None):
    saved = submission(db, key)
    policy = saved.get("retrieval_policy")
    if policy is not None:
        validate_retrieval_policy(policy, embeddings, semantic_limit)
    elif embeddings is not None or semantic_limit is not None:
        raise ValueError("Semantic retrieval must be configured in a fresh submission")
    eligible = reviewed_targets(saved)
    candidates = definitions(db)
    pairs, refs = {}, {}
    for owner, concept in candidates.items():
        refs.setdefault(digest(concept), []).append(owner)
    for decision in saved["proposal"]["decisions"]:
        if eligible is not None and digest(decision) not in eligible:
            continue
        if not (assignment := decision["assignment"]):
            continue
        query = {k: assignment[k] for k in ("definition", "relation", "aliases", "scope_note")}
        routes = lookup_owners(db, query)
        rankings = {}
        if policy is not None:
            routes, rankings = retrieve(query, candidates, routes, embeddings, policy["semantic_limit"])
        if len(refs) <= SMALL_CATALOGUE:
            for owner in candidates:
                routes.setdefault(owner, []).append("exhaustive_small_catalogue")
        for owner, selected_routes in routes.items():
            if owner not in candidates:
                continue  # Withdrawn/non-admitted drafts retain an audit record, not a candidate.
            target = candidates[owner]
            qhash, thash = digest(query), digest(target)
            # Same exact definition object needs no paraphrase comparison.
            if qhash == thash:
                continue
            pair_id = digest([qhash, thash])
            pair = pairs.setdefault(pair_id, {"pair_id": pair_id, "query_sha256": qhash,
                "target_sha256": thash, "query": query, "target": target, "routes": []})
            pair["routes"] = sorted(set(pair["routes"]) | set(selected_routes))
            if policy is not None:
                pair["retrieval_ranking"] = rankings.get(thash, {"channels": [], "rrf_score": 0.0})
    result = {"format_version": "definition_comparison_plan/1.0", "submission_id": key,
            "candidate_policy": {"kind": "overlapping_field_indexes", "exhaustive_threshold": SMALL_CATALOGUE,
                                 "semantic_retriever": False, "recall_guaranteed": False},
            "pairs": sorted(pairs.values(), key=lambda p: p["pair_id"])}
    if policy is not None:
        result["candidate_policy"] = {"kind": "overlapping_field_indexes_plus_semantic",
            "exhaustive_threshold": SMALL_CATALOGUE, "semantic_retriever": True,
            "recall_guaranteed": False, "retrieval_policy": policy, **candidate_binding(candidates)}
        result["pairs"].sort(key=lambda p: (-p["retrieval_ranking"]["rrf_score"], p["pair_id"]))
    return result


def plan(path, key, *, embeddings=None, semantic_limit=None):
    with connect(path) as db:
        return make_plan(db, key, embeddings=embeddings, semantic_limit=semantic_limit)


def validate_candidate_policy(db, comparison_plan, *, embeddings=None, require_current=True):
    """Validate a current hybrid plan or an exact pair subset from its claims.

    Historical lexical plans retain their original immutable-definition checks.
    Publication binds the active catalogue. An in-flight report may instead
    retain its exact archived candidates while later publication refreshes them.
    """
    saved = submission(db, comparison_plan["submission_id"])
    configured = saved.get("retrieval_policy") is not None
    hybrid = comparison_plan.get("candidate_policy", {}).get("semantic_retriever") is True
    if not configured and not hybrid:
        return
    validate_retrieval_policy(saved.get("retrieval_policy"), embeddings)
    if not require_current:
        policy = comparison_plan.get("candidate_policy", {})
        bindings = policy.get("definitions", {})
        archived = definitions(db, include_inactive=True)
        if any(owner not in archived for owner in bindings):
            raise ValueError("Semantic candidate metadata contains an unknown definition")
        bound = {owner: archived[owner] for owner in bindings}
        expected = {"kind": "overlapping_field_indexes_plus_semantic",
            "exhaustive_threshold": SMALL_CATALOGUE, "semantic_retriever": True,
            "recall_guaranteed": False, "retrieval_policy": saved["retrieval_policy"], **candidate_binding(bound)}
        if policy != expected:
            raise ValueError("Semantic candidate metadata changed")
        cached = {}
        bound_hashes = {digest(concept) for concept in bound.values()}
        for pair in comparison_plan["pairs"]:
            qhash, thash = digest(pair["query"]), digest(pair["target"])
            if qhash not in bound_hashes:
                raise ValueError("Semantic candidate metadata omits its query definition")
            if qhash not in cached:
                routes, rankings = retrieve(pair["query"], bound, lookup_owners(db, pair["query"]),
                    embeddings, saved["retrieval_policy"]["semantic_limit"])
                if len({digest(c) for c in bound.values()}) <= SMALL_CATALOGUE:
                    for owner in bound:
                        routes.setdefault(owner, []).append("exhaustive_small_catalogue")
                cached[qhash] = routes, rankings
            routes, rankings = cached[qhash]
            selected = sorted({route for owner, concept in bound.items() if digest(concept) == thash
                               for route in routes.get(owner, [])})
            if (not selected or pair.get("routes") != selected or pair.get("retrieval_ranking") !=
                rankings.get(thash, {"channels": [], "rrf_score": 0.0})):
                raise ValueError("Semantic candidate pair metadata changed")
        return
    current = make_plan(db, comparison_plan["submission_id"], embeddings=embeddings)
    if comparison_plan.get("candidate_policy") != current["candidate_policy"]:
        raise ValueError("Semantic candidate metadata changed; regenerate the comparison plan")
    expected = {pair["pair_id"]: pair for pair in current["pairs"]}
    if any(expected.get(pair["pair_id"]) != pair for pair in comparison_plan["pairs"]):
        raise ValueError("Semantic candidate pair metadata changed")


def attach_comparisons(path, key, plan, report, *, provenance=None, embeddings=None):
    validate_report(report, plan)
    if plan["submission_id"] != key:
        raise ValueError("Comparison plan belongs to another submission")
    with connect(path) as db:
        validate_candidate_policy(db, plan, embeddings=embeddings, require_current=False)
        # The report concerns immutable definitions. A rejected/withdrawn draft
        # may leave the candidate index while its comparison is in flight. Keep
        # that valid historical report; publication uses only current candidates.
        saved = submission(db, key)
        queries = {digest({k: d["assignment"][k] for k in ("definition", "relation", "aliases", "scope_note")})
                   for d in saved["proposal"]["decisions"] if d["assignment"]}
        archived = {digest(v) for v in definitions(db, include_inactive=True).values()}
        for pair in plan["pairs"]:
            qhash, thash = digest(pair["query"]), digest(pair["target"])
            if (qhash not in queries or thash not in archived
                or qhash != pair["query_sha256"] or thash != pair["target_sha256"]
                or pair["pair_id"] != digest([qhash, thash])):
                raise ValueError("Comparison plan contains an unknown or changed definition")
    bundle = {"plan": plan, "report": report.model_dump(mode="json"), "provenance": provenance or {
        "kind": "supplied_comparison_artifact", "independent_execution_verified": False}}
    with connect(path, write=True) as db:
        cursor = db.execute("INSERT OR IGNORE INTO comparison_reports VALUES (?,?,?)", (digest(bundle), key, json.dumps(bundle)))
        if cursor.rowcount:
            db.execute("UPDATE registry_meta SET generation=generation+1 WHERE id=1")


def comparisons(db, key, *, comparison_plan=None, embeddings=None):
    # Definition comparisons depend on their immutable definitions, not the job
    # that requested them. Reuse exact pairs, including the inverse direction;
    # conflicting judgments become unresolved rather than two competing truths.
    current = comparison_plan if comparison_plan is not None else make_plan(db, key, embeddings=embeddings)
    if current["submission_id"] != key:
        raise ValueError("Comparison plan belongs to another submission")
    wanted = {p["pair_id"] for p in current["pairs"]}
    records = {}
    inverse = {"broader": "narrower", "narrower": "broader"}
    for ident, payload in db.execute("SELECT id,payload FROM comparison_reports"):
        value = json.loads(payload)
        if digest(value) != ident:
            raise ValueError("Stored comparison report changed")
        report = ComparisonReport.model_validate(value["report"])
        outcomes = validate_report(report, value["plan"])
        for pair in value["plan"]["pairs"]:
            result = outcomes[pair["pair_id"]]
            reverse_id = digest([pair["target_sha256"], pair["query_sha256"]])
            for pair_id, verdict in ((pair["pair_id"], result), (reverse_id, inverse.get(result, result))):
                if pair_id in wanted:
                    records.setdefault(pair_id, set()).add(verdict)
    return {key: (next(iter(values)) if len(values) == 1 else
                  "non_equivalent" if values <= {"distinct", "non_equivalent"} else "unresolved")
            for key, values in records.items()}


def missing_plan(path, key, *, embeddings=None, semantic_limit=None):
    with connect(path) as db:
        full = make_plan(db, key, embeddings=embeddings, semantic_limit=semantic_limit)
        known = comparisons(db, key, comparison_plan=full)
    return {**full, "pairs": [p for p in full["pairs"] if p["pair_id"] not in known]}


def withdraw(path, key, reason):
    """Retire an uncommitted draft without deleting its evidence or comparisons."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Withdrawal requires a reason")
    with connect(path, write=True) as db:
        saved = submission(db, key)
        if saved["result"]:
            if saved["result"]["status"] == "withdrawn":
                return saved["result"]
            raise ValueError("A completed submission cannot be withdrawn")
        result = {"status": "withdrawn", "submission_id": key, "reason": reason}
        db.execute("UPDATE submissions SET result=? WHERE id=?", (json.dumps(result), key))
        db.execute("UPDATE registry_meta SET generation=generation+1 WHERE id=1")
    return result
