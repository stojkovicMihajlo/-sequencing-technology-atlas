"""Validate outside write locks, then atomically publish unchanged-version rows."""

from copy import deepcopy

from genomics_discovery.curation.workflow_extraction.models import digest
from .admission import validate_batch
from .integrity import validate_snapshot
from .models import normalized_label
from .storage import connect, generation, read_snapshot, write_snapshot


def prepare_commit(snapshot, proposal, review, occurrences, outcomes, provenance=None):
    snapshot = deepcopy(snapshot)
    checks = {r.decision_sha256: r for r in review.reviews}
    base_revision = digest(snapshot)
    pending = {}
    for decision, outcome in zip(proposal.decisions, outcomes):
        if outcome["status"] != "admit":
            continue
        assignment = decision.assignment
        cid, concept = assignment.definition.id, assignment.concept()
        if cid in pending and pending[cid] != concept:
            raise ValueError("Conflicting definitions for one concept ID in the batch")
        for other_id, other in pending.items():
            if other_id == cid or other["relation"] != concept["relation"]:
                continue
            fields = set(concept["definition"]) - {"id", "name"}
            if (normalized_label(other["definition"]["name"]) ==
                normalized_label(concept["definition"]["name"])
                or all(other["definition"][k] == concept["definition"][k] for k in fields)):
                raise ValueError("Duplicate concept definitions/labels in one batch")
        pending[cid] = concept
    snapshot["concepts"].update(pending)
    for decision, outcome in zip(proposal.decisions, outcomes):
        key, check = digest(decision), checks[digest(decision)]
        occurrence = occurrences[decision.occurrence_id]
        # Retain properties-only and provisional decisions even without a concept.
        record = {"decision": decision.model_dump(mode="json"),
                  "review": check.model_dump(mode="json"), "review_sha256": digest(check),
                  "occurrence": occurrence.model_dump(mode="json"),
                  "occurrence_sha256": digest(occurrence), "outcome": outcome,
                  "batch_sha256": digest(proposal), "commit_base_revision": base_revision}
        snapshot.setdefault("decisions", {})[key] = record
        if outcome["status"] != "admit":
            continue
        assignment = decision.assignment
        member = assignment.members[0]
        predecessors = {aid: row for aid, row in snapshot["assignments"].items()
                        if row["concept_id"] == assignment.definition.id
                        and row["occurrence_sha256"] == member.occurrence_sha256}
        if any(r.get("admission_decision_sha256") for r in predecessors.values()):
            raise ValueError("This exact occurrence already has this concept")
        aid = digest(assignment)
        if aid in snapshot["assignments"]:
            raise ValueError("Reassess a legacy assignment as reuse; preserve the original artifact")
        snapshot["assignments"][aid] = {
            "concept_id": assignment.definition.id, "occurrence_sha256": digest(occurrence),
            "occurrence": occurrence.model_dump(mode="json"),
            "proposal": assignment.model_dump(mode="json"),
            "review": check.source_review.model_dump(mode="json"),
            "review_sha256": digest(check.source_review),
            "commit_base_revision": base_revision, "lookup_results": outcome["lookup_results"],
            "admission_decision_sha256": key,
            "supersedes_assignment_ids": list(predecessors),
            "review_execution": provenance or {
                "kind": "supplied_review_artifact", "independent_execution_verified": False},
        }
        outcome["assignment_id"] = aid
        outcome["concept_id"] = assignment.definition.id
    validate_snapshot(snapshot)

    return snapshot


def commit_batch(path, proposal, review, occurrences, packets, provenance=None):
    from .store import BOUNDARY
    for _ in range(16):
        with connect(path) as db:
            version, snapshot = generation(db), read_snapshot(db)
        validate_snapshot(snapshot)
        outcomes = validate_batch(proposal, review, snapshot, occurrences, packets)
        updated = prepare_commit(snapshot, proposal, review, occurrences, outcomes, provenance)
        with connect(path, write=True) as db:
            if generation(db) != version:
                continue
            write_snapshot(db, updated)
            return {"registry_revision": digest(updated), "outcomes": outcomes, "boundary": BOUNDARY}
    raise ValueError("Concurrent registry changes; retry the commit without repeating model review")
