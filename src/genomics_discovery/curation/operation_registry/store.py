"""SQLite serializes host commits; agents propose against a frozen JSON snapshot."""

import json
import re
import sqlite3
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import digest
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipReview, review_membership, validate_membership,
)

from .models import Assignment, normalized_label
from .integrity import FORMAT, validate_snapshot

BOUNDARY = ("Reviewed membership in the stated definition; not experimental compatibility, "
            "historical ancestry or scientific novelty. Names and search scores do not assign labels.")


def empty_snapshot() -> dict:
    return {"format_version": FORMAT, "concepts": {}, "assignments": {}}


def revision(snapshot: dict) -> str:
    return snapshot.get("registry_revision") or digest(snapshot)


def catalogue(snapshot: dict) -> dict:
    """Expose definitions for lookup, not accumulated cuts, quotes or review opinions."""
    return {"format_version": "operation_concept_catalogue/1.0",
            "registry_revision": digest(snapshot), "concepts": snapshot["concepts"],
            "admission_status": {cid: ("admitted" if any(
                r["concept_id"] == cid and r.get("admission_decision_sha256")
                for r in snapshot["assignments"].values()) else "legacy_unassessed")
                for cid in snapshot["concepts"]},
            "review_contracts": {cid: sorted({snapshot.get("decisions", {}).get(
                row.get("admission_decision_sha256"), {}).get("review", {}).get(
                    "format_version", "concept_admission_review/1.0" if row.get(
                        "admission_decision_sha256") else "source_only")
                for row in snapshot["assignments"].values() if row["concept_id"] == cid})
                for cid in snapshot["concepts"]}}


def search(snapshot: dict, query: str, *, limit: int = 10) -> dict:
    terms = set(re.findall(r"\w+", normalized_label(query)))
    if not terms or type(limit) is not int or limit < 1:
        raise ValueError("Use a nonempty query and positive limit")
    hits = []
    for cid, concept in snapshot["concepts"].items():
        definition = concept["definition"]
        labels = [cid, definition["name"], *concept["aliases"]]
        text = " ".join([*labels, concept["scope_note"],
                         *(v for v in definition.values() if isinstance(v, str))])
        matched = terms & set(re.findall(r"\w+", normalized_label(text)))
        exact = normalized_label(query) in {normalized_label(s) for s in labels}
        if matched or exact:
            hits.append({"concept_id": cid, "concept": concept, "exact_label_hit": exact,
                         "admission_status": snapshot.get("admission_status", {}).get(cid, "unassessed"),
                         "matched_terms": sorted(matched), "score": len(matched) / len(terms)})
    hits.sort(key=lambda h: (-int(h["exact_label_hit"]), -h["score"], h["concept_id"]))
    return {"registry_revision": revision(snapshot), "query": query, "hits": hits[:limit],
            "total": len(hits), "truncated": len(hits) > limit,
            "boundary": "Candidate lookup only. An empty result does not establish a new concept."}


def check_proposal(snapshot, proposal, occurrences, packets, *, allow_stale_reuse=False,
                   allow_stale_new=False) -> list[dict]:
    if (proposal.registry_revision != revision(snapshot)
        and not (allow_stale_reuse and proposal.decision == "reuse") and not allow_stale_new):
        raise ValueError("Stale registry revision; look up again and re-review the decision")
    errors = validate_membership(proposal, occurrences, packets)
    if errors:
        raise ValueError("; ".join(errors))
    cid, concept = proposal.definition.id, proposal.concept()
    existing = snapshot["concepts"].get(cid)
    if proposal.decision == "reuse":
        if existing is None or existing != concept:
            raise ValueError("Reuse requires the exact existing concept; definitions are immutable")
    else:
        if existing is not None:
            raise ValueError("Concept ID already exists; inspect and reuse it or choose a distinct ID")
        for other_id, other in snapshot["concepts"].items():
            if other["relation"] != concept["relation"]:
                continue
            if normalized_label(other["definition"]["name"]) == normalized_label(proposal.definition.name):
                raise ValueError(f"Preferred label already exists at {other_id}; reuse or qualify "
                                 "the label to express the actual distinction")
            fields = set(type(proposal.definition).model_fields) - {"id", "name"}
            if all(other["definition"][k] == concept["definition"][k] for k in fields):
                raise ValueError(f"Identical transformation definition already exists at {other_id}")
    # Recompute the queries rather than accepting agent-authored hit lists.
    lookups = [search(snapshot, query, limit=max(1, len(snapshot["concepts"])))
               for query in proposal.lookup_queries]
    if proposal.decision == "reuse" and not any(
        hit["concept_id"] == cid for result in lookups for hit in result["hits"]
    ):
        raise ValueError("Recorded lookup queries do not retrieve the reused concept")
    return lookups


class Registry:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()

    def initialize(self) -> dict:
        from .storage import create, normalized
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            legacy = db.execute("SELECT 1 FROM sqlite_master WHERE name='registry'").fetchone()
            if not normalized(db) and not legacy:
                create(db, empty_snapshot())
        return self.snapshot()

    def snapshot(self) -> dict:
        from .storage import connect, read_snapshot
        with connect(self.path) as db:
            value = read_snapshot(db)
        validate_snapshot(value)
        return value

    def apply_batch(self, proposal, review, occurrences, packets, *, provenance=None):
        from .commit import commit_batch
        return commit_batch(self.path, proposal, review, occurrences, packets, provenance)

    def apply(self, proposal: Assignment, review: MembershipReview, occurrences: dict,
              packets: dict, *, admission_decision=None, admission_review=None, provenance=None):
        """Single-assignment adapter. New writes require a bound usefulness review."""
        from .admission import VocabularyProposal, VocabularyReview
        if admission_decision is None or admission_review is None:
            raise ValueError("Source review alone cannot admit a label; provide an admission decision/review")
        if admission_decision.assignment != proposal or admission_review.source_review != review:
            raise ValueError("Admission and source artifacts disagree")
        batch = VocabularyProposal(decisions=(admission_decision,))
        checked = VocabularyReview(proposal_sha256=digest(batch), reviews=(admission_review,))
        selected = {proposal.members[0].occurrence_id: occurrences[proposal.members[0].occurrence_id]}
        # Other selections may serve as boundary examples; use the batch entry point for a cohort.
        result = self.apply_batch(batch, checked, selected, packets, provenance=provenance)
        outcome = result["outcomes"][0]
        return {**result, **outcome}


def uses(snapshot: dict, concept_id: str, *, offset: int = 0, limit: int = 10) -> dict:
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 1:
        raise ValueError("Use a nonnegative offset and positive limit")
    concept = snapshot["concepts"][concept_id]
    rows = []
    for ident, row in snapshot["assignments"].items():
        if row["concept_id"] != concept_id:
            continue
        occurrence = row["occurrence"]
        rows.append({"assignment_id": ident, "method_id": occurrence["method_id"],
                     "variant": occurrence["variant"], "workflow_sha256": occurrence["workflow_sha256"],
                     "operation_ids": [op["id"] for op in occurrence["operations"]],
                     "inputs": occurrence["inputs"], "outputs": occurrence["outputs"],
                     "workflow_review_status": occurrence["workflow_review_status"],
                     "admission_status": "admitted" if row.get("admission_decision_sha256") else "legacy_unassessed",
                     "review_sha256": row["review_sha256"]})
    end = offset + limit
    return {"concept": concept, "uses": rows[offset:end], "total": len(rows),
            "next_offset": end if end < len(rows) else None, "boundary": BOUNDARY}


def graph_overlay(snapshot: dict) -> dict:
    """A separate overlay; the exact source selections remain intact."""
    validate_snapshot(snapshot)
    nodes, edges = {}, []
    admitted = {key: row for key, row in snapshot["assignments"].items()
                if row.get("admission_decision_sha256")}
    for cid in sorted({row["concept_id"] for row in admitted.values()}):
        concept = snapshot["concepts"][cid]
        key = "concept:" + cid
        nodes[key] = {"id": key, "kind": "operation_concept", "record": concept}
    for ident in sorted(admitted):
        row = admitted[ident]
        occurrence = row["occurrence"]
        key = "selection:" + row["occurrence_sha256"]
        nodes[key] = {"id": key, "kind": "protocol_selection", "method_id": occurrence["method_id"],
                      "workflow_sha256": occurrence["workflow_sha256"],
                      "source_packet_sha256": occurrence["source_packet_sha256"],
                      "operation_ids": [op["id"] for op in occurrence["operations"]]}
        edges.append({"source": key, "target": "concept:" + row["concept_id"],
                      "kind": "instance_of_concept", "basis": row["proposal"]["relation"],
                      "assignment_id": ident, "review_sha256": row["review_sha256"],
                      "review_contract": snapshot["decisions"][row["admission_decision_sha256"]][
                          "review"].get("format_version", "concept_admission_review/1.0")})
    return {"format_version": "operation_concept_overlay/1.0", "registry_revision": digest(snapshot),
            "nodes": list(nodes.values()), "edges": edges, "boundary": BOUNDARY}
