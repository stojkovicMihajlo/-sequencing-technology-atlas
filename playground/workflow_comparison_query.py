"""Read complete contextual units and direct comparisons; lexical hits create no edges."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from genomics_discovery.curation.workflow_extraction.models import Workflow, digest
from genomics_discovery.curation.workflow_extraction.projection import query_workflow
from genomics_discovery.curation.workflow_extraction.transformations import (
    MembershipCandidate,
    MembershipReview,
    TransformationDefinition,
    TransformationOccurrence,
    load_occurrences,
    validate_membership_review,
)

BOUNDARY = ("Recorded direct comparison candidates, not interchangeability, compatibility, "
            "ancestry or transitive equivalence. Graph syntax and digest checks are not source "
            "entailment; inspect cited source passages and contextual interfaces before reuse.")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_graph(graph, manifest=None):
    """Check export consistency; optional manifest also verifies bound cuts and source quotes."""
    _require(graph.get("format_version") == "transformation_comparison_graph/1.0", "Wrong graph")
    nodes = {node["id"]: node for node in graph["nodes"]}
    _require(len(nodes) == len(graph["nodes"]), "Duplicate node ID")
    _require(all(node["kind"] in {"workflow", "transformation_occurrence",
                                 "transformation_definition"} for node in nodes.values()),
             "Unknown node kind")
    occurrences = {ident: TransformationOccurrence.model_validate(node["record"])
                   for ident, node in nodes.items() if node["kind"] == "transformation_occurrence"}
    verified, packets = load_occurrences(manifest) if manifest is not None else ({}, {})
    if manifest is not None:
        _require(set(verified) == set(occurrences) and all(
            digest(verified[key]) == digest(value) for key, value in occurrences.items()),
            "Graph occurrence differs from its bound parent/source/review")
    bindings, expected_edges = {}, set()
    for ident, occurrence in occurrences.items():
        selection = digest({"operations": sorted(op.id for op in occurrence.operations)})
        _require(ident == occurrence.id == (
            f"{occurrence.method_id}:{occurrence.workflow_sha256}:{selection}"),
            "Occurrence ID differs from record/selection")
        _require((occurrence.workflow_review_status == "not_reviewed")
                 == (occurrence.workflow_review_sha256 is None), "Missing parent review binding")
        parent = f"workflow:{occurrence.workflow_sha256}"
        _require(parent in nodes and nodes[parent]["kind"] == "workflow"
                 and nodes[parent]["method_id"] == occurrence.method_id, "Wrong occurrence parent")
        bindings.setdefault(parent, set()).add(
            (occurrence.workflow_review_sha256, occurrence.workflow_review_status))
        expected_edges.add((parent, ident, "contains_unit", None))
        states = {state.id: state for state in (*occurrence.inputs, *occurrence.outputs,
                                               *occurrence.internal_states)}
        operations = {operation.id for operation in occurrence.operations}
        _require(len(operations) == len(occurrence.operations)
                 and len({flow.id for flow in occurrence.flows}) == len(occurrence.flows)
                 and not operations & states.keys() and all(state == states[state.id] for state in
                     (*occurrence.inputs, *occurrence.outputs, *occurrence.internal_states)),
                 "Duplicate or conflicting retained object ID")
        operations |= {ref.operation_id for ref in occurrence.external_operations}
        _require(all(ref.workflow_sha256 == occurrence.workflow_sha256
                     for ref in occurrence.external_operations), "Wrong external operation parent")
        for flow in occurrence.flows:
            if flow.kind in {"precedence", "control"}:
                valid = flow.source in operations and flow.target in operations
            else:
                sid, oid = ((flow.source, flow.target) if flow.kind.endswith("input")
                            else (flow.target, flow.source))
                valid = (sid in states and oid in operations
                         and flow.kind.startswith(states[sid].kind))
            _require(valid, "Bad retained flow endpoint/type")
    for ident, node in nodes.items():
        if node["kind"] == "workflow":
            actual = [(item["review_sha256"], item["status"]) for item in node["review_bindings"]]
            _require(ident in bindings and len(actual) == len(set(actual))
                     and set(actual) == bindings[ident], "Parent review context laundering")
            statuses = {status for _, status in actual}
            summary = next(iter(statuses)) if len(statuses) == 1 else "multiple_review_contexts"
            _require(node["review_status"] == summary, "Parent review summary laundering")
    proofs, definitions = {}, set()
    candidates = set()
    for record in graph["comparisons"]:
        cid, rid = record["candidate_sha256"], record["review_sha256"]
        _require(all(re.fullmatch(r"[0-9a-f]{64}", value) for value in (cid, rid))
                 and cid not in candidates, "Bad or ambiguous comparison hash")
        candidates.add(cid)
        _require(record["status"] in {"supported_membership_candidate", "needs_revision",
                                      "research_needed"}, "Unknown comparison status")
        _require(record["relation"] in {"shared_purpose", "shared_mechanism"}
                 and all(record[key] == "not_assessed" for key in
                         ("compatibility", "ancestry", "production_acceptance"))
                 and record["transitive_inference"] is False, "Comparison claim laundering")
        definition = TransformationDefinition.model_validate(record["definition"])
        _require(record["relation"] != "shared_mechanism" or definition.physical_mechanism,
                 "Shared mechanism without a mechanism")
        members = [member["occurrence_id"] for member in record["members"]]
        _require(len(members) >= 2 and len(members) == len(set(members)) and all(
            key in occurrences for key in members), "Bad comparison members")
        _require(all(member["workflow_review_status"]
                     == occurrences[member["occurrence_id"]].workflow_review_status
                     for member in record["members"]), "Member parent status laundering")
        _require(("candidate" in record) == ("review" in record), "Incomplete embedded proof")
        provenance = "hash_only; retrieve exact candidate and review artifacts"
        if "candidate" in record:
            candidate = MembershipCandidate.model_validate(record["candidate"])
            review = MembershipReview.model_validate(record["review"])
            _require(digest(candidate) == cid and digest(review) == rid
                     and review.candidate_sha256 == cid and candidate.relation == record["relation"]
                     and candidate.definition == definition
                     and [member.occurrence_id for member in candidate.members] == members
                     and all(member.occurrence_sha256 == digest(occurrences[member.occurrence_id])
                             for member in candidate.members), "Embedded proof binding mismatch")
            verdicts = {check.verdict for check in review.checks}
            status = ("needs_revision" if "unsupported" in verdicts else "research_needed"
                      if "unresolved" in verdicts else "supported_membership_candidate")
            _require(len(review.checks) == len(members)
                     and {check.occurrence_id for check in review.checks} == set(members)
                     and record["status"] == status, "Embedded verdict/coverage laundering")
            provenance = "embedded_digest_bound; source entailment not established by syntax"
            if manifest is not None:
                _require(not validate_membership_review(review, candidate, verified, packets),
                         "Embedded proof fails source/provenance validation")
                provenance = "embedded_digest_bound_with_source_integrity; recorded review judgment"
        proofs[cid] = provenance
        if record["status"] == "supported_membership_candidate":
            ident = f"transformation:{cid}"
            definitions.add(ident)
            expected = {"id": ident, "kind": "transformation_definition", **record}
            _require(nodes.get(ident) == expected,
                     "Definition differs from its comparison proof")
            expected_edges.update((key, ident, record["relation"], rid) for key in members)
    _require({key for key, node in nodes.items() if node["kind"] == "transformation_definition"}
             == definitions, "Unreviewed transformation definition")
    edges = [(edge["source"], edge["target"], edge["kind"], edge.get("review_sha256"))
             for edge in graph["edges"]]
    _require(len(edges) == len(set(edges)) and set(edges) == expected_edges,
             "Unknown endpoint, edge type or direct membership proof")
    return nodes, occurrences, proofs


def query_graph(graph, occurrence_id=None, *, query=None, manifest=None, limit=10,
                parent_context=False, relation=None):
    _require(type(limit) is int and limit > 0, "Search limit must be a positive integer")
    _require(not parent_context or manifest is not None and query is None,
             "Parent context requires a manifest and full occurrence retrieval")
    _require(relation is None or relation in {"shared_purpose", "shared_mechanism"},
             "Unknown relation filter")
    nodes, occurrences, proofs = validate_graph(graph, manifest)
    header = {"boundary": BOUNDARY, "recorded_export_provenance": {
        key: graph[key] for key in ("original_export", "original_run_state", "source_manifest",
                                   "native_artifacts", "bundle_implementation",
                                   "proof_bundle_format", "review_backend", "review_artifacts")
        if key in graph}}

    def unit(ident):
        occurrence = occurrences[ident]
        hints = {"source_packet_sha256": occurrence.source_packet_sha256,
                 "read": "Reopen cited source_id/passage_id pairs in this exact source packet."}
        contexts = []
        if manifest is not None:
            hints["packet_path"] = manifest[ident]["packet"]
        if parent_context:
            workflow = Workflow.model_validate_json(Path(manifest[ident]["workflow"]).read_bytes())
            contexts = [query_workflow(workflow, op.id) for op in occurrence.operations]
        return {"occurrence": occurrence.model_dump(mode="json"),
                "parent": nodes[f"workflow:{occurrence.workflow_sha256}"],
                "parent_operation_contexts": contexts, "source_read_hint": hints}

    def index(ident):
        occurrence = occurrences[ident]
        return {"occurrence_id": ident, "method_id": occurrence.method_id,
                "title": occurrence.title, "variant": occurrence.variant,
                "scope": occurrence.scope,
                "operation_names": [operation.name for operation in occurrence.operations],
                "inputs": [{"id": state.id, "kind": state.kind, "description": state.description}
                           for state in occurrence.inputs],
                "outputs": [{"id": state.id, "kind": state.kind, "description": state.description}
                            for state in occurrence.outputs],
                "workflow_review_status": occurrence.workflow_review_status,
                "parent_review_status": nodes[
                    f"workflow:{occurrence.workflow_sha256}"]["review_status"],
                "full_get_hint": {"option": "--occurrence", "value": ident}}

    if query is not None:
        terms = re.findall(r"\w+", query.casefold())
        _require(bool(terms), "Search needs nonempty lexical terms")
        records = [r for r in graph["comparisons"]
                   if r["status"] == "supported_membership_candidate"
                   and (relation is None or r["relation"] == relation)]
        def text_matches(value):
            text = json.dumps(value, ensure_ascii=False).casefold()
            return all(term in text for term in terms)
        collections = sorted((r for r in records if text_matches(r["definition"])),
                             key=lambda r: r["candidate_sha256"])
        related = {m["occurrence_id"] for r in records for m in r["members"]}
        definition_hits = {m["occurrence_id"] for r in collections for m in r["members"]}

        def unit_text(unit):
            # Own scientific descriptions, not unrelated parent gaps, old opinions or quotes.
            return {"operations": [{"name": op.name, "action": op.action,
                                    "information_effect": op.information_effect}
                                   for op in unit.operations],
                    "states": [s.description for s in (*unit.inputs, *unit.outputs,
                                                       *unit.internal_states)],
                    "resources": [r.description for r in unit.requirements]}

        matches = []
        for key, occurrence in occurrences.items():
            if relation is not None and key not in related:
                continue
            own_text = unit_text(occurrence)
            if text_matches(own_text):
                tier, basis = 1, "unit_content"
            elif key in definition_hits:
                tier, basis = 2, "direct_reviewed_definition"
            elif text_matches({"identity": [occurrence.method_id, occurrence.title,
                                            occurrence.scope, occurrence.variant], **own_text}):
                # Preserve queries whose terms span unit and method descriptions as context hits.
                tier, basis = 3, "method_context_fallback"
            else:
                continue
            matches.append((tier, key, basis))
        matches.sort(key=lambda match: (match[0], match[1]))
        collection_index = [{"id": f"transformation:{r['candidate_sha256']}",
                             "relation": r["relation"], "name": r["definition"]["name"],
                             "purpose": r["definition"]["purpose"],
                             "physical_mechanism": r["definition"]["physical_mechanism"],
                             "members": [m["occurrence_id"] for m in r["members"]]}
                            for r in collections[:limit]]
        return {"view": "discovery_index", "retrieval_only": True, "query": query,
                "total": len(matches), "limit": limit, "truncated": len(matches) > limit,
                "hits": [{**index(key), "match_tier": tier, "match_basis": basis}
                         for tier, key, basis in matches[:limit]],
                "collections": collection_index, "collections_total": len(collections),
                "collections_truncated": len(collections) > limit, "relation_filter": relation,
                "index_boundary": "Lexical match tiers: unit operations, states and resources; "
                                  "direct reviewed definitions; then method identity and scope "
                                  "context. Within each tier, occurrence IDs determine order. "
                                  "These tiers do not establish semantic relevance. Retrieve the "
                                  "full unit for actions, resources, routing and evidence. "
                                  "This index is not a recipe.", **header}
    if occurrence_id not in occurrences:
        raise KeyError(f"Unknown occurrence: {occurrence_id}")
    peers = [{"peer": unit(member["occurrence_id"]), "comparison": record,
              "provenance": proofs[record["candidate_sha256"]]}
             for record in graph["comparisons"]
             if record["status"] == "supported_membership_candidate"
             and (relation is None or record["relation"] == relation)
             and any(member["occurrence_id"] == occurrence_id for member in record["members"])
             for member in record["members"] if member["occurrence_id"] != occurrence_id]
    return {**unit(occurrence_id), "direct_reviewed_peers": peers,
            "parent_context_included": parent_context, **header}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--occurrence")
    choice.add_argument("--search")
    parser.add_argument("--manifest", type=Path, help="Revalidate parent cuts and source proof")
    parser.add_argument("--limit", type=int, default=10, help="Maximum discovery index hits")
    parser.add_argument("--parent-context", action="store_true", help="Include parent neighborhood")
    parser.add_argument("--relation", choices=("shared_purpose", "shared_mechanism"),
                        help="Filter existing direct reviewed comparison links")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text()) if args.manifest else None
        if manifest is not None:
            for row in manifest.values():
                for key in ("occurrence", "packet", "workflow", "review"):
                    if row.get(key) is not None:
                        row[key] = str((args.manifest.resolve().parent / row[key]).resolve())
        result = query_graph(json.loads(args.graph.read_text()), args.occurrence,
                             query=args.search, manifest=manifest, limit=args.limit,
                             parent_context=args.parent_context, relation=args.relation)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"{type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
