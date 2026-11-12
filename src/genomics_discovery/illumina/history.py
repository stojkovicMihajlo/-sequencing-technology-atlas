"""Query documented contributions across explicitly pinned relationship overlays.

Two overlays share a workflow only when both record ID AND extraction byte hash
match. This is exact artifact reuse, not semantic method/version resolution.
External cited contributions remain scoped to their original evidence overlay.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from genomics_discovery.illumina.relationships import (
    HISTORICAL_RELATIONS, RELATIONS, load_reviewed_pair, relationship_neighbors,
    contribution_layer, is_assay_history, ASSAY_HISTORY_POLICY,
)


def _read(path, expected):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("History input changed: " + str(path))
    return json.loads(raw)


def _check_review(claim, review):
    if review["claim_id"] != claim["id"] or review["decision"] != "accept":
        raise ValueError("Historical context requires the matching accepted review")
    issues = review["unresolved_assessments"]
    if (sorted(x["issue_index"] for x in issues) != list(range(len(claim["unresolved"]))) or
            any(x["blocks_relation"] for x in issues)):
        raise ValueError("Unreviewed or blocking relationship qualification")


def _check_current_citation(item, *, scoped_history=False):
    """Keep v3 scope/review declarations intact when reading a pinned export.

    This does not reread the bibliography or establish quotation entailment;
    those checks belong to the source-backed author/reviewer/export stages.
    """
    claim, review = item["claim"], item["review"]
    scope = claim.get("claim_scope")
    if not claim.get("citation_context", "").strip():
        raise ValueError("Current citation relation requires its located citation context")
    if scope not in {"seed_workflow", "supporting_experiment", "unlocalized"}:
        raise ValueError("Unknown current citation scope")
    if bool(claim["seed_operation_ids"]) != (scope == "seed_workflow"):
        raise ValueError("Citation scope disagrees with seed Operation anchors")
    expected_layer = contribution_layer(claim) if scoped_history else RELATIONS[claim['relation']]['layer']
    if expected_layer == 'history' and scope != "seed_workflow":
        raise ValueError("Historical contribution must belong to the extracted seed workflow")
    cited = {e["source_id"] for e in review.get("evidence", []) if e.get("quote", "").strip()}
    if not {"seed", claim["reference_id"]} <= cited:
        raise ValueError("Current citation review needs seed and exact predecessor evidence")
    expected_flag = expected_layer == 'history' if scoped_history else claim["relation"] == "method_development"
    if (item.get("relationship_layer") != expected_layer or
            item.get("historical_development_edge") != expected_flag):
        raise ValueError("Citation relationship layer disagrees with its declared type")


def _source_registry_context(entry, manifest_path, graph):
    """Return pinned, referenced source metadata, never the registered paper text."""
    specification = entry.get("source_registry")
    if specification is None:
        return {"status": "not_supplied", "path": None, "sha256": None, "sources": {},
                "boundary": "No separately pinned source registry was supplied for this overlay."}
    path = (manifest_path.parent / specification["path"]).resolve()
    registry = _read(path, specification["sha256"])
    referenced = set()

    def collect(value):
        if isinstance(value, dict):
            if "source_id" in value and "quote" in value:
                referenced.add(value["source_id"])
            for child in value.values(): collect(child)
        elif isinstance(value, list):
            for child in value: collect(child)

    for section in ("links", "historical_links", "correspondences", "withheld"):
        for item in graph.get(section, []):
            # Pair claims/reviews use left:/right: IDs from the pair registry.
            # Embedded operation groups retain extraction-local IDs and their own
            # provenance. They are not fields returned by this history index and
            # must not be resolved against the pair's differently scoped registry.
            if graph["format_version"] == "illumina_method_pair/1":
                collect(item.get("claim", {}))
                collect(item.get("review", {}))
            else:
                collect(item)
    collect(graph.get('coverage_review', {}))
    missing = referenced - registry.keys()
    if missing:
        raise ValueError("Evidence source IDs absent from supplied registry: " + ", ".join(sorted(missing)))
    metadata = {}
    document_fields = {"path", "sha256", "media_type", "format", "status"}
    for sid in sorted(referenced):
        source = registry[sid]
        record = {k: copy.deepcopy(source[k]) for k in ("title", "content_level", "pmid", "url") if k in source}
        documents = {}
        for name, value in (("source_artifact", source),
                            ("fulltext_acquisition", source.get("fulltext_acquisition", {})),
                            ("provenance.original", source.get("provenance", {}).get("original", {})),
                            ("metadata_receipt", source.get("metadata_receipt", {}))):
            if value.get("path") or value.get("sha256"):
                documents[name] = {k: copy.deepcopy(v) for k, v in value.items() if k in document_fields}
        if documents:
            record["document_artifacts"] = documents
        metadata[sid] = record
    return {"status": "pinned_metadata", "path": str(path), "sha256": specification["sha256"],
            "sources": metadata,
            "boundary": "Metadata for cited evidence sources only; no paper text. Registry bytes and source IDs "
                        "are checked, not scientific entailment. Document paths/hashes are preserved provenance, "
                        "not rebased or freshly verified file availability."}


def build_history_index(manifest_path):
    manifest = json.loads(manifest_path.read_text())
    if manifest["format_version"] != "illumina_history_inputs/1":
        raise ValueError("Unknown history manifest")
    nodes, edges, loaded, withheld, coverage_reports = {}, [], set(), [], []

    def workflow(record_id, extraction_sha, title):
        if len(extraction_sha) != 64:
            raise ValueError("Workflow binding requires its recorded extraction hash")
        key = "workflow:" + record_id + ":" + extraction_sha
        nodes.setdefault(key, {"id": key, "name": title, "record_id": record_id,
                               "extraction_sha256": extraction_sha, "kind": "bound_workflow"})
        return key

    for entry in manifest["overlays"]:
        path = (manifest_path.parent / entry["path"]).resolve()
        pin = entry["sha256"]
        if pin in loaded:
            raise ValueError("Duplicate relationship overlay")
        loaded.add(pin)
        graph = _read(path, pin)
        source = {"path": str(path), "sha256": pin, "format_version": graph["format_version"],
                  "source_registry": _source_registry_context(entry, manifest_path, graph)}
        if graph['format_version'] == 'illumina_citation_binding/1':
            from .history_bindings import load_reviewed_binding
            graph = load_reviewed_binding(path, pin)
            sides = {side: workflow(b['record_id'], b['extraction_sha256'], b['title'])
                     for side,b in graph['bindings'].items()}
            for item in graph['links']:
                claim = item['claim']
                edges.append({'id': pin + ':' + claim['id'], 'source': sides['left'], 'target': sides['right'],
                    'relation': item['kind'], 'claim': copy.deepcopy(claim), 'review': copy.deepcopy(item['review']),
                    'mapping_review': copy.deepcopy(item['mapping_review']), 'mapping': copy.deepcopy(item['mapping']),
                    'source_citation': copy.deepcopy(graph['source_citation']), 'artifact': source,
                    'claim_id': claim['id'], 'status': item['status']})
            for finding in graph['withheld']:
                withheld.append({'target': sides['right'], 'artifact': copy.deepcopy(source),
                    'finding': copy.deepcopy(finding), 'status': 'withheld_not_a_graph_edge'})
        elif graph["format_version"] == "illumina_method_pair/1":
            # Reuse the pair reader's direction, anchor, evidence and review checks.
            graph = load_reviewed_pair(path, pin)
            sides = {s: workflow(b["record_id"], b["extraction_sha256"], b["title"])
                     for s, b in graph["bindings"].items()}
            rows = relationship_neighbors(graph, graph["bindings"]["left"]["record_id"], layer="all")
            items = {x["claim"]["id"]: x for x in graph["historical_links"] + graph["correspondences"]}
            for row in rows["relationships"]:
                item = items[row["claim_id"]]; c = item["claim"]
                a, b = (sides["right"], sides["left"]) if c["direction"] == "right_to_left" else (sides["left"], sides["right"])
                edges.append({"id": pin + ":" + c["id"], "source": a, "target": b,
                              "relation": c["relation"], "claim": copy.deepcopy(c),
                              "review": copy.deepcopy(item["review"]), "artifact": source,
                              "claim_id": c["id"], "status": item["status"]})
        elif graph["format_version"] in {"citation_lineage_pilot/2", "citation_lineage_pilot/3", "citation_lineage_pilot/4"}:
            original_nodes = {n["id"]: n for n in graph["nodes"]}
            if len(original_nodes) != len(graph["nodes"]):
                raise ValueError("Duplicate citation node")
            seed = original_nodes["seed"]; binding = seed["bindings"]
            pin_extraction = binding["parent_artifact_pins"]["extraction.json"]
            extraction = _read((manifest_path.parent / entry["seed_extraction"]).resolve(), pin_extraction)
            if extraction["record_id"] != binding["record_id"]:
                raise ValueError("Seed extraction identity mismatch")
            op_ids = {o["id"] for o in extraction["operations"]}
            target = workflow(binding["record_id"], pin_extraction, seed["name"])
            if 'coverage_review' in graph:
                from .history_coverage import check_published_coverage
                specification = entry.get('source_registry')
                if specification is None:
                    raise ValueError('Historical coverage requires its pinned source registry')
                registry = _read((manifest_path.parent / specification['path']).resolve(), specification['sha256'])
                check_published_coverage(graph['coverage_review'], registry, graph['links'], graph.get('withheld', []))
                coverage_reports.append({'target': target, 'artifact': copy.deepcopy(source),
                                         'coverage': copy.deepcopy(graph['coverage_review'])})
            for finding in graph.get("withheld", []):
                withheld.append({"target": target, "artifact": copy.deepcopy(source),
                                 "finding": copy.deepcopy(finding),
                                 "status": "withheld_not_a_graph_edge"})
            for item in graph["links"]:
                c, review, kind = item["claim"], item["review"], item["kind"]
                _check_review(c, review)
                if (kind not in RELATIONS or RELATIONS[kind]["layer"] == "excluded" or c["relation"] != kind
                        or item["target"] != "seed" or item["source"] == "seed"
                        or item["status"] != "model_reviewed_pilot_overlay"):
                    raise ValueError("Invalid published citation relation")
                if graph["format_version"] in {"citation_lineage_pilot/3", "citation_lineage_pilot/4"}:
                    policy = graph.get('classification_policy')
                    if policy not in {None, ASSAY_HISTORY_POLICY}:
                        raise ValueError('Unknown citation contribution classification policy')
                    _check_current_citation(item, scoped_history=policy == ASSAY_HISTORY_POLICY)
                if graph['format_version'] == 'citation_lineage_pilot/4':
                    from .change_records import ExperimentalChange, validate_changes, workflow_records
                    specification = entry.get('source_registry')
                    if specification is None:
                        raise ValueError('Explicit change overlay requires its pinned primary-source registry')
                    registry = _read((manifest_path.parent / specification['path']).resolve(), specification['sha256'])
                    changes = [ExperimentalChange.model_validate(row) for row in c.get('change_records', [])]
                    if not changes:
                        raise ValueError('Explicit change overlay omitted its change records')
                    validate_changes(changes, sources=registry, later_records=workflow_records(extraction),
                        later_source_ids={sid for sid, value in registry.items()
                                          if sid == 'seed' or value.get('role') == 'seed_supplement'},
                        earlier_source_ids={c['reference_id']}, require_later_anchor=c['claim_scope'] == 'seed_workflow')
                    checks = review.get('change_checks', [])
                    if (len(checks) != len(changes) or {r['change_id'] for r in checks} != {r.id for r in changes}
                            or any(r['decision'] != 'accept' for r in checks)):
                        raise ValueError('Explicit change overlay lacks complete accepted change review')
                    if kind == 'documented_reuse' and any(not change.evidence.inheritance for change in changes):
                        raise ValueError('Explicit reuse omitted its attribution evidence')
                if not c["seed_evidence"] or not c["predecessor_evidence"]:
                    raise ValueError("Missing two-sided citation support")
                allowed_seed = lambda sid: sid == 'seed' or (graph['format_version'] == 'citation_lineage_pilot/4' and sid.startswith('seed_supplement:'))
                if (any(not allowed_seed(e["source_id"]) for e in c["seed_evidence"]) or
                        any(e["source_id"] != c["reference_id"] for e in c["predecessor_evidence"])):
                    raise ValueError("Wrong citation-evidence side")
                if set(c["seed_operation_ids"]) - op_ids:
                    raise ValueError("Unknown seed Operation anchor")
                contributor = original_nodes[item["source"]]
                if contributor["reference_id"] != c["reference_id"]:
                    raise ValueError("Contributor/reference mismatch")
                origin = "external:" + pin + ":" + contributor["id"]
                nodes[origin] = {"id": origin, "name": contributor["name"], "kind": contributor["kind"],
                                 "reference_id": contributor["reference_id"],
                                 "identity_status": contributor["identity_status"],
                                 "catalogue_candidates": contributor["catalogue_candidates"]}
                edges.append({"id": pin + ":" + c["id"], "source": origin, "target": target,
                              "relation": kind, "claim": copy.deepcopy(c), "review": copy.deepcopy(review),
                              "artifact": source, "claim_id": c["id"], "status": item["status"]})
        else:
            raise ValueError("Unsupported relationship overlay format")
    # Replace only the exact paper-only assertion that a fresh workflow mapping
    # resolves. Similar names or different versions never merge contributions.
    bound = [(edge['source_citation']['overlay_sha256'], edge['source_citation']['claim_id'])
             for edge in edges if 'source_citation' in edge]
    if len(bound) != len(set(bound)):
        raise ValueError('Conflicting workflow mappings of one citation contribution require review')
    superseded = set(bound)
    edges = [edge for edge in edges if 'source_citation' in edge or
             (edge['artifact']['sha256'], edge['claim_id']) not in superseded]
    used = {edge[endpoint] for edge in edges for endpoint in ('source', 'target')}
    nodes = {key:value for key,value in nodes.items() if value['kind'] == 'bound_workflow' or key in used}
    if len({e["id"] for e in edges}) != len(edges):
        raise ValueError("Duplicate relationship claim")
    # Classify the query view without rewriting the original pinned artifacts.
    # Older citation exports could call software/resource reuse historical.
    for edge in edges:
        edge['relationship_layer'] = contribution_layer(edge['claim'], relation=edge['relation'])
        edge['historical_development_edge'] = is_assay_history(edge)
    identity_issues = []
    if manifest.get('method_registry'):
        from .history_method_identity import bind_reviewed_methods
        spec = manifest['method_registry']
        registry = _read((manifest_path.parent / spec['path']).resolve(), spec['sha256'])
        identity_issues = bind_reviewed_methods(nodes, edges, registry)
        used = {e[k] for e in edges for k in ('source','target')}
        nodes = {k:v for k,v in nodes.items() if v.get('kind') == 'bound_workflow' or k in used}
    # Detect contradictions rather than silently looping or choosing an arbitrary parent.
    pending = {n: set() for n in nodes}
    for e in edges:
        if is_assay_history(e):
            pending[e["target"]].add(e["source"])
    while pending:
        ready = {n for n, predecessors in pending.items() if not predecessors}
        if not ready:
            raise ValueError("Historical contribution cycle needs review")
        pending = {n: predecessors - ready for n, predecessors in pending.items() if n not in ready}
    return {"nodes": nodes, "edges": edges, "withheld": withheld, "coverage_reports": coverage_reports, "overlay_sha256": sorted(loaded), "method_identity_issues": identity_issues,
            "boundary": "Explicitly loaded, previously model-reviewed pilot overlays; no new scientific validation, "
                        "date/priority verification, or exact earlier-Operation mapping. Canonical endpoints require explicit reviewed identity and pinned method metadata."}


def technology_history(index, record_id, *, extraction_sha256=None, max_depth=2):
    if not 1 <= max_depth <= 10:
        raise ValueError("Choose an explicit traversal depth between 1 and 10")
    candidates = [n for n in index["nodes"].values() if n.get("record_id") == record_id and
                  (extraction_sha256 is None or n.get("extraction_sha256") == extraction_sha256)]
    if len(candidates) != 1:
        raise ValueError("Expected one exact workflow; unknown record or multiple versions require an extraction hash")
    target = candidates[0]; visited = {target["id"]}; frontier = [(target["id"], 0)]
    selected, stops = {}, []
    while frontier:
        node, depth = frontier.pop(0)
        incoming = [e for e in index["edges"] if e["target"] == node and is_assay_history(e)]
        if depth == max_depth or not incoming:
            stops.append({"node": copy.deepcopy(index["nodes"][node]),
                          "reason": "depth_limit" if depth == max_depth and incoming else "no_further_history_in_loaded_evidence",
                          "continuation_status": (
                              "unbound_external_contribution" if index["nodes"][node].get("kind") != "bound_workflow"
                              else "depth_limit" if depth == max_depth and incoming
                              else "no_admitted_incoming_claims_in_loaded_evidence")})
            continue
        for edge in incoming:
            selected[edge["id"]] = copy.deepcopy(edge)
            if edge["source"] not in visited:
                visited.add(edge["source"]); frontier.append((edge["source"], depth + 1))
    other_direct = [copy.deepcopy(e) for e in index["edges"]
                    if target["id"] in {e["source"], e["target"]} and not is_assay_history(e)]
    related_ids = {n for e in other_direct for n in (e["source"], e["target"])} - visited
    return {
        "technology": copy.deepcopy(target), "max_depth": max_depth,
        "nodes": [copy.deepcopy(index["nodes"][n]) for n in sorted(visited)],
        "historical_contributions": list(selected.values()),
        "other_direct_relationships": other_direct,
        "other_relationship_nodes": [copy.deepcopy(index["nodes"][n]) for n in sorted(related_ids)],
        "direct_withheld_claims": [copy.deepcopy(item) for item in index.get("withheld", [])
                                   if item["target"] == target["id"]],
        "coverage_reports": [copy.deepcopy(item) for item in index.get('coverage_reports', [])
                             if item['target'] == target['id']],
        "frontier": stops, "overlay_sha256": index["overlay_sha256"],
        "boundary": index["boundary"] + " Paths join distinct scoped claims; they do not establish transitive "
                    "inheritance of a component or whole assay. A traversal endpoint is not proof of first invention. "
                    "An external contribution has no verified workflow binding here; withheld findings are research leads, not edges.",
    }


def select_history_claim(result, claim_id, *, overlay_sha256=None):
    """Select an exact local claim within the already bounded history result.

    Reused local IDs require the original overlay hash. Withheld findings remain
    research leads and cannot supply an asserted predecessor endpoint.
    """
    if result.get("view") == "summary":
        raise ValueError("Select a claim before creating the summary view")
    matches = []
    for section in ("historical_contributions", "other_direct_relationships", "direct_withheld_claims"):
        for item in result.get(section, []):
            local_id = item["finding"]["claim"]["id"] if section == "direct_withheld_claims" else item["claim_id"]
            if local_id == claim_id and (overlay_sha256 is None or item["artifact"]["sha256"] == overlay_sha256):
                matches.append((section, item))
    if not matches:
        raise ValueError("No exact claim in the loaded history selection")
    if len(matches) != 1:
        raise ValueError("Ambiguous local claim ID; supply its exact overlay hash")
    section, item = matches[0]
    selected = copy.deepcopy(result)
    for key in ("historical_contributions", "other_direct_relationships", "direct_withheld_claims"):
        selected[key] = [copy.deepcopy(item)] if key == section else []
    endpoints = {item["target"]}
    if section != "direct_withheld_claims":
        endpoints.add(item["source"])
    available = {n["id"]: n for n in result["nodes"] + result.get("other_relationship_nodes", [])}
    if endpoints - available.keys():
        raise ValueError("Selected claim has an endpoint absent from its loaded context")
    selected["nodes"] = [copy.deepcopy(available[n]) for n in sorted(endpoints)]
    selected["other_relationship_nodes"] = []
    selected["frontier"] = [copy.deepcopy(stop) for stop in result["frontier"] if stop["node"]["id"] in endpoints]
    selected["selection"] = {"claim_id": claim_id, "overlay_sha256": item["artifact"]["sha256"],
                             "section": section, "scope": "One exact recorded finding, not the full method history."}
    return selected


def history_summary(result):
    """Lossless read view of selected claims; index repeated quotes/artifact paths.

    'Summary' describes the packaging, not an LLM rewrite. Every selected claim,
    review, qualification and traversal limit remains available in this object.
    Evidence IDs are local to this returned packet and identify exact stored
    text within its source overlay, not semantic equivalence between passages.
    """
    summary = copy.deepcopy(result)
    evidence, artifacts, evidence_lookup, artifact_lookup = {}, {}, {}, {}

    def reference_quotes(value, artifact_id):
        if isinstance(value, dict):
            if "source_id" in value and "quote" in value:
                record = {"artifact_id": artifact_id, "original": copy.deepcopy(value)}
                key = json.dumps(record, sort_keys=True, ensure_ascii=False)
                if key not in evidence_lookup:
                    evidence_lookup[key] = "e" + str(len(evidence_lookup) + 1)
                    evidence[evidence_lookup[key]] = record
                ident = evidence_lookup[key]
                return {"evidence_id": ident}
            return {k: reference_quotes(v, artifact_id) for k, v in value.items()}
        if isinstance(value, list):
            return [reference_quotes(v, artifact_id) for v in value]
        return value

    for section in ("historical_contributions", "other_direct_relationships", "direct_withheld_claims"):
        for edge in summary.get(section, []):
            artifact = edge.pop("artifact")
            pin = artifact["sha256"]
            if pin not in artifact_lookup:
                artifact_lookup[pin] = "a" + str(len(artifact_lookup) + 1)
                artifacts[artifact_lookup[pin]] = artifact
            artifact_id = artifact_lookup[pin]
            edge["artifact_id"] = artifact_id
            for field in ("claim", "review", "finding"):
                if field in edge:
                    edge[field] = reference_quotes(edge[field], artifact_id)
    summary.update(format_version="illumina_history_context/1", view="summary",
                   evidence=evidence, artifacts=artifacts,
                   reading_note="Descriptions and review findings are unchanged. Resolve each evidence_id "
                                "in evidence, then its artifact_id in artifacts. IDs are local to this packet. This is a deterministic "
                                "reading view, not a new review or an executable protocol.")
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--record-id", required=True)
    p.add_argument("--extraction-sha256")
    p.add_argument("--max-depth", type=int, default=2)
    p.add_argument("--view", choices=["summary", "full"], default="full",
                   help="summary indexes repeated evidence without rewriting or truncating scientific text")
    p.add_argument("--claim-id", help="Return one exact local claim ID from this history selection")
    p.add_argument("--overlay-sha256", help="Disambiguate a claim ID reused across loaded overlays")
    args = p.parse_args()
    if args.overlay_sha256 and not args.claim_id:
        p.error("--overlay-sha256 requires --claim-id")
    result = technology_history(build_history_index(args.manifest), args.record_id,
        extraction_sha256=args.extraction_sha256, max_depth=args.max_depth)
    if args.claim_id:
        result = select_history_claim(result, args.claim_id, overlay_sha256=args.overlay_sha256)
    if args.view == "summary":
        result = history_summary(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
