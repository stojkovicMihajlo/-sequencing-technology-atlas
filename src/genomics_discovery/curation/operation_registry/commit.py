"""Atomic cohort commit: every admitted assignment passes source and utility gates."""

import json
import sqlite3

from genomics_discovery.curation.workflow_extraction.models import digest
from .admission import validate_batch
from .integrity import validate_snapshot
from .models import normalized_label


def commit_batch(path, proposal, review, occurrences, packets, provenance=None):
    from .store import BOUNDARY
    checks = {r.decision_sha256: r for r in review.reviews}
    with sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=30) as db:
        db.execute("BEGIN IMMEDIATE")
        snapshot = json.loads(db.execute("SELECT payload FROM registry WHERE id=1").fetchone()[0])
        validate_snapshot(snapshot)
        base_revision = digest(snapshot)
        # All proposals see this same snapshot. A stale new concept requires reconciliation.
        outcomes = validate_batch(proposal, review, snapshot, occurrences, packets)
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
        db.execute("UPDATE registry SET payload=? WHERE id=1", (json.dumps(snapshot),))
    return {"registry_revision": digest(snapshot), "outcomes": outcomes, "boundary": BOUNDARY}
