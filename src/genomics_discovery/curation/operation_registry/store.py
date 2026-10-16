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
            "registry_revision": digest(snapshot), "concepts": snapshot["concepts"]}


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
                         "matched_terms": sorted(matched), "score": len(matched) / len(terms)})
    hits.sort(key=lambda h: (-int(h["exact_label_hit"]), -h["score"], h["concept_id"]))
    return {"registry_revision": revision(snapshot), "query": query, "hits": hits[:limit],
            "total": len(hits), "truncated": len(hits) > limit,
            "boundary": "Candidate lookup only. An empty result does not establish a new concept."}


def check_proposal(snapshot, proposal, occurrences, packets, *, allow_stale_reuse=False) -> list[dict]:
    if (proposal.registry_revision != revision(snapshot)
        and not (allow_stale_reuse and proposal.decision == "reuse")):
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS registry (id INTEGER PRIMARY KEY CHECK(id=1), "
                       "payload TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO registry VALUES (1, ?)",
                       (json.dumps(empty_snapshot()),))
        return self.snapshot()

    def snapshot(self) -> dict:
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
            value = json.loads(db.execute("SELECT payload FROM registry WHERE id=1").fetchone()[0])
        validate_snapshot(value)
        return value

    def apply(self, proposal: Assignment, review: MembershipReview, occurrences: dict,
              packets: dict, *, provenance: dict | None = None) -> dict:
        """Caller must first rebuild occurrences from sources using load_occurrences.

        Validated reviewer JSON is not proof of reviewer independence. The live driver
        records and verifies distinct native threads; manual callers remain explicit.
        """
        checked = review_membership(proposal, review, occurrences, packets)
        if checked["status"] != "supported_membership_candidate":
            raise ValueError(f"Membership was not supported: {checked['status']}")
        # Do not create an empty database as a side effect of a mistaken path.
        with sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            snapshot = json.loads(db.execute("SELECT payload FROM registry WHERE id=1").fetchone()[0])
            validate_snapshot(snapshot)
            commit_base_revision = digest(snapshot)
            # A reviewed use of an unchanged concept survives unrelated concurrent additions.
            # New concepts still require reconciliation against the current vocabulary.
            lookups = check_proposal(snapshot, proposal, occurrences, packets, allow_stale_reuse=True)
            member = proposal.members[0]
            if any(row["concept_id"] == proposal.definition.id and
                   row["occurrence_sha256"] == member.occurrence_sha256
                   for row in snapshot["assignments"].values()):
                raise ValueError("This exact occurrence already has this concept")
            snapshot["concepts"][proposal.definition.id] = proposal.concept()
            key = digest(proposal)
            snapshot["assignments"][key] = {
                "concept_id": proposal.definition.id,
                "occurrence_sha256": member.occurrence_sha256,
                "occurrence": occurrences[member.occurrence_id].model_dump(mode="json"),
                "proposal": proposal.model_dump(mode="json"),
                "review": review.model_dump(mode="json"), "review_sha256": digest(review),
                "commit_base_revision": commit_base_revision,
                "lookup_results": lookups, "review_execution": provenance or {
                    "kind": "supplied_review_artifact", "independent_execution_verified": False},
            }
            db.execute("UPDATE registry SET payload=? WHERE id=1", (json.dumps(snapshot),))
        return {"assignment_id": key, "concept_id": proposal.definition.id,
                "registry_revision": digest(snapshot), "boundary": BOUNDARY}


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
                     "review_sha256": row["review_sha256"]})
    end = offset + limit
    return {"concept": concept, "uses": rows[offset:end], "total": len(rows),
            "next_offset": end if end < len(rows) else None, "boundary": BOUNDARY}


def graph_overlay(snapshot: dict) -> dict:
    """A separate overlay; the exact source selections remain intact."""
    validate_snapshot(snapshot)
    nodes, edges = {}, []
    for cid, concept in snapshot["concepts"].items():
        key = "concept:" + cid
        nodes[key] = {"id": key, "kind": "operation_concept", "record": concept}
    for ident, row in snapshot["assignments"].items():
        occurrence = row["occurrence"]
        key = "selection:" + row["occurrence_sha256"]
        nodes[key] = {"id": key, "kind": "protocol_selection", "method_id": occurrence["method_id"],
                      "workflow_sha256": occurrence["workflow_sha256"],
                      "source_packet_sha256": occurrence["source_packet_sha256"],
                      "operation_ids": [op["id"] for op in occurrence["operations"]]}
        edges.append({"source": key, "target": "concept:" + row["concept_id"],
                      "kind": "instance_of_concept", "basis": row["proposal"]["relation"],
                      "assignment_id": ident, "review_sha256": row["review_sha256"]})
    return {"format_version": "operation_concept_overlay/1.0", "registry_revision": digest(snapshot),
            "nodes": list(nodes.values()), "edges": edges, "boundary": BOUNDARY}
