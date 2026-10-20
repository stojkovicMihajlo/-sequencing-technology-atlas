"""Resolve admission against current candidates without holding model-time locks."""

import json

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import load_occurrences
from .admission import VocabularyProposal, CurrentVocabularyReview, validate_batch, validate_decision
from .consistency import check_batch_consistency
from .commit import prepare_commit
from .integrity import validate_snapshot
from .definition_comparison import OUTCOME_POLICY
from .proposal_queue import submission, make_plan, comparisons
from .storage import connect, generation, read_snapshot, write_snapshot


def publish(path, key, *, embeddings=None):
    for _ in range(16):
        with connect(path) as db:
            version, snapshot = generation(db), read_snapshot(db)
            saved = submission(db, key)
            if saved["result"]:
                validate_snapshot(snapshot)
                return {**saved["result"], "idempotent_replay": True}
            current_plan = make_plan(db, key, embeddings=embeddings)
            known = comparisons(db, key, comparison_plan=current_plan, embeddings=embeddings)
        validate_snapshot(snapshot)
        if not saved["review"]:
            return {"status": "awaiting_source_review", "submission_id": key}
        proposal = VocabularyProposal.model_validate(saved["proposal"])
        review = CurrentVocabularyReview.model_validate(saved["review"]["document"])
        occurrences, packets = load_occurrences(saved["manifest"])
        # Revalidate evidence and the original scientific review before considering publication.
        original = validate_batch(proposal, review, saved["catalogue"], occurrences, packets)
        active = {digest(d.assignment.concept()) for d, result in zip(proposal.decisions, original)
                  if d.assignment and result["status"] == "admit"}
        relevant = [p for p in current_plan["pairs"] if p["query_sha256"] in active]
        missing = [p["pair_id"] for p in relevant if p["pair_id"] not in known]
        if missing:
            return {"status": "awaiting_comparisons", "submission_id": key, "missing_pair_ids": missing}
        unknown = [p["pair_id"] for p in relevant if known[p["pair_id"]] == "unresolved"]
        if unknown:
            return {"status": "unresolved", "submission_id": key, "unresolved_pair_ids": unknown}
        targets, contextual = {}, {}
        # Resolve only against admitted definitions or other definitions in this cohort.
        cohort = {d.assignment.definition.id: d.assignment.concept() for d in proposal.decisions
                  if d.assignment and digest(d.assignment.concept()) in active}
        for decision, result in zip(proposal.decisions, original):
            a = decision.assignment
            if not a or result["status"] != "admit" or a.decision == "reuse":
                continue
            query_hash = digest(a.concept())
            equivalent = []
            for cid, target in {**cohort, **snapshot["concepts"]}.items():
                if cid == a.definition.id and cid not in snapshot["concepts"]:
                    continue
                relation = ("equivalent_scope" if digest(target) == query_hash else
                            known.get(digest([query_hash, digest(target)])))
                if relation == "equivalent_scope" and (cid in snapshot["concepts"] or cid < a.definition.id):
                    equivalent.append((cid, target))
                elif relation in {"broader", "narrower", "overlap_without_equivalence"} and (
                    cid in snapshot["concepts"] and cid not in decision.case.existing_concept_ids):
                    contextual.setdefault(a.definition.id, []).append(cid)
            if equivalent:
                equivalent.sort(key=lambda item: (item[0] not in snapshot["concepts"], item[0]))
                targets[a.definition.id] = equivalent[0][1]
        if targets:
            return {"status": "reuse_review_required", "submission_id": key, "targets": targets,
                    "reason": "Check membership against the canonical definition; the original source review is not rewritten."}
        if contextual:
            return {"status": "context_review_required", "submission_id": key, "nearby_concepts": contextual,
                    "reason": "Review whether existing definitions plus properties suffice; broader/narrower is not equivalence."}
        # All source claims were just revalidated against their original review
        # context. Only admitted decisions need current-catalogue collision checks;
        # a rejected draft is retained as history, not proposed for publication.
        outcomes = [dict(row) for row in original]
        for decision, evaluated in zip(proposal.decisions, outcomes):
            if evaluated["status"] == "admit":
                evaluated["lookup_results"] = validate_decision(
                    decision, snapshot, occurrences, packets, allow_stale_new=True)
        check_batch_consistency(proposal, review, snapshot, outcomes)
        updated = prepare_commit(snapshot, proposal, review, occurrences, outcomes,
                                 saved["review"]["provenance"])
        result = {"status": "committed" if any(r["status"] == "admit" for r in outcomes)
                  else "completed_without_admission", "submission_id": key,
                  "registry_revision": digest(updated), "outcomes": outcomes,
                  "comparison_outcome_policy": OUTCOME_POLICY,
                  "comparison_outcomes": {p["pair_id"]: known[p["pair_id"]] for p in relevant},
                  "boundary": "Current recorded checks; not a guarantee of semantic uniqueness or experimental compatibility."}
        with connect(path, write=True) as db:
            if generation(db) != version:
                continue  # Recompute targeted dependencies; never rerun an LLM inside this lock.
            if previous := submission(db, key)["result"]:
                return {**previous, "idempotent_replay": True}
            write_snapshot(db, updated)
            db.execute("UPDATE submissions SET result=? WHERE id=?", (json.dumps(result), key))
            return result
    return {"status": "retry_publication", "submission_id": key,
            "reason": "Concurrent writes continued; reuse the saved scientific reviews."}


def reuse_proposal(saved, targets, catalogue):
    """Create a new review target; never transplant an old positive source verdict."""
    proposal = VocabularyProposal.model_validate(saved["proposal"])
    decisions = []
    for decision in proposal.decisions:
        a = decision.assignment
        if a:
            target = targets.get(a.definition.id)
            changes = {"registry_revision": catalogue["registry_revision"]}
            if target:
                cid = target["definition"]["id"]
                admitted = catalogue["concepts"].get(cid)
                changes.update({**target, "decision": "reuse" if admitted else "new",
                                "lookup_queries": (*a.lookup_queries, cid),
                                "distinction": "Scope comparison requested this canonical definition. Fresh source review is required."})
            a = type(a).model_validate({**a.model_dump(mode="json"), **changes})
            case_changes = {"existing_concept_ids": tuple(sorted(
                set(decision.case.existing_concept_ids) | ({a.definition.id} if a.decision == "reuse" else set())))}
            decision_changes = {"assignment": a}
            if target:
                # Rebuild the admission argument for the current catalogue. An
                # old new-label justification can assert an empty catalogue or
                # absent alternatives; it is not a justification for reuse.
                # Scientific question, contextual properties and evidence stay
                # intact, and the original argument remains in its submission.
                state = "already admitted" if a.decision == "reuse" else "proposed within this cohort"
                case_changes.update(
                    existing_plus_filters=f"The canonical definition {a.definition.id} is {state}. "
                        "Assess whether this definition with the recorded use-specific properties answers the retained question.",
                    filters_sufficient=a.decision == "reuse",
                    lost_distinction_if_merged="The revised request groups only the scoped transformation. "
                        "Original operations, evidence, inputs, outputs and contextual properties remain separate; "
                        "verify that using the canonical definition does not erase a consequential distinction.",
                    revision_requests=())
                decision_changes["explanation"] = (
                    "Revised after definition comparison to request membership in the canonical definition "
                    f"{a.definition.id}. Reassess source support and usefulness against the current catalogue. "
                    "The original proposal and its earlier catalogue justification remain archived separately.")
            decision_changes["case"] = decision.case.model_copy(update=case_changes)
            decision = decision.model_copy(update=decision_changes)
        decisions.append(decision)
    return VocabularyProposal(decisions=tuple(decisions))
