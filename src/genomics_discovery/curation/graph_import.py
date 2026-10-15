"""Project current reviewed curation into a separate, queryable graph candidate.

The accepted export is regenerated and checked against the locked run. The live
atlas is never changed; destination approval remains a separate operation.
"""

from __future__ import annotations

import copy
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

from genomics_discovery.knowledge.atlas_graph import TechnologyKnowledgeGraph

from .evolution import evolution_export_status
from .harness import (
    ACCEPTED,
    check_frozen,
    export_accepted,
    read,
    reviewed_source_registrations,
    run_lock,
    sha,
    validate_job_review,
    write,
)
from .validation import canonical_hash


def _component_projection():
    # Pure packaged mapping; never scan old curation folders or infer new claims.
    from . import graph_projection

    return graph_projection


def _check_bundle(run, state, config, bundle):
    """Bind the regenerated export to current accepted jobs and their reviews."""
    from .followup import pending_followup_reviews

    check_frozen(run, state, config)
    jobs = {mid: job for mid, job in state["jobs"].items() if job["status"] == "accepted"}
    if not jobs:
        raise ValueError("No currently accepted profiles are available for graph import")
    profiles = bundle.get("profiles", [])
    if len(profiles) != len(jobs):
        raise ValueError("Accepted export does not match the current accepted jobs")
    expected_sources, expected_provenance, expected_source_provenance = {}, [], []
    documents = {}
    for profile in profiles:
        assigned = profile.get("method_ids", [])
        if len(assigned) != 1 or assigned[0] not in jobs or assigned[0] in documents:
            raise ValueError("Export contains a nonaccepted or duplicate method assignment")
        mid, job = assigned[0], jobs[assigned[0]]
        extraction, review = Path(job["extraction"]), Path(job["review"])
        from .accepted_transport import reviewer_label, validate_accepted_transports
        transport_rows = validate_accepted_transports(job, config)
        holds = pending_followup_reviews(run, mid, extraction)
        errors = validate_job_review(run, config, job, review, extraction, mid)
        artifact, verdict = read(extraction), read(review)
        if holds or errors or verdict.get("outcome") not in ACCEPTED:
            raise ValueError(f"Invalid current acceptance for {mid}: {holds or errors}")
        raw_profile = artifact["profiles"][0]
        if {k: v for k, v in profile.items() if k != "verification"} != raw_profile:
            raise ValueError(f"Export differs from the reviewed profile: {mid}")
        verification = profile.get("verification", {})
        if any(verification.get(key) != value for key, value in {
            "outcome": verdict["outcome"], "reviewer": reviewer_label(transport_rows),
            "report_path": str(review),
            "report_sha256": sha(review), "verified_profile_sha256": canonical_hash(raw_profile),
            "scientific_scope": verdict.get("scientific_scope", {}),
            "remaining_gaps": verdict["remaining_gaps"],
        }.items()):
            raise ValueError(f"Export has stale review provenance: {mid}")
        coverage_keys = {
            "review_scope", "review_scope_sha256", "source_checklist_path",
            "source_checklist_sha256", "source_to_record_check", "source_checklist_policy",
            "source_audit",
        }
        if config.get("review_scope_sha256") is not None:
            expected_coverage = {
                "review_scope": read(run / "review_scope.json")["methods"][mid],
                "review_scope_sha256": config["review_scope_sha256"],
            }
            if config.get("source_checklist_policy", "independent") == "reviewer_source_audit":
                expected_coverage.update(source_checklist_policy="reviewer_source_audit",
                                         source_audit=verdict["coverage_check"]["source_audit"])
                checklist_keys = {"source_checklist_path", "source_checklist_sha256",
                                  "source_to_record_check"}
                if checklist_keys & verification.keys():
                    raise ValueError(
                        f"Reviewer source audit cannot claim a source checklist: {mid}")
            else:
                expected_coverage.update(
                    source_checklist_path=job["source_checklist"],
                    source_checklist_sha256=job["source_checklist_sha256"],
                    source_to_record_check=verdict["source_to_record_check"])
                if "source_audit" in verification or "source_checklist_policy" in verification:
                    raise ValueError(
                        f"Independent checklist export cannot claim lean provenance: {mid}")
            if any(verification.get(key) != value for key, value in expected_coverage.items()):
                raise ValueError(f"Export has stale source-to-record review provenance: {mid}")
        elif coverage_keys & verification.keys():
            raise ValueError(f"Legacy export cannot claim an independent source checklist: {mid}")
        if config.get("connection_review_policy"):
            checks = verdict["topology_check"]["connection_checks"]
            expected_connections = {
                "policy": config["connection_review_policy"],
                "checked_connections": len(checks), "checks_sha256": canonical_hash(checks),
            }
            if verification.get("connection_review") != expected_connections:
                raise ValueError(f"Export has stale connection review provenance: {mid}")
        elif "connection_review" in verification:
            raise ValueError(f"Legacy export cannot claim per-connection review: {mid}")
        if config.get("scientific_detail_policy"):
            assertion_checks = verdict["assertion_checks"]
            handoff_checks = verdict["topology_check"]["module_handoff_checks"]
            expected_detail = {
                "policy": config["scientific_detail_policy"],
                "assertion_groups": len(assertion_checks),
                "assertion_checks_sha256": canonical_hash(assertion_checks),
                "module_handoff_scans": len(handoff_checks),
                "module_handoff_checks_sha256": canonical_hash(handoff_checks),
            }
            if verification.get("scientific_detail_review") != expected_detail:
                raise ValueError(f"Export has stale scientific detail review provenance: {mid}")
        elif "scientific_detail_review" in verification:
            raise ValueError(f"Legacy export cannot claim scientific detail review: {mid}")
        provenance = {"method_id": mid, "extraction": str(extraction),
                      "extraction_sha256": sha(extraction), "review": str(review),
                      "review_sha256": sha(review)}
        expected_provenance.append(provenance)
        for artifact_role, source in reviewed_source_registrations(artifact, verdict):
            sid = source["source_id"]
            if sid in expected_sources and expected_sources[sid] != source:
                raise ValueError(f"Conflicting reviewed source definitions: {sid}")
            expected_sources[sid] = source
            source_artifact = extraction if artifact_role == "extractor" else review
            expected_source_provenance.append({
                "method_id": mid, "source_id": sid, "artifact_role": artifact_role,
                "artifact_path": str(source_artifact), "artifact_sha256": sha(source_artifact),
            })
        documents[mid] = (artifact, verdict, provenance)
    if bundle.get("sources") != list(expected_sources.values()):
        raise ValueError("Export source register differs from current reviewed artifacts")
    if bundle.get("promotion_provenance") != expected_provenance:
        raise ValueError("Export promotion provenance differs from current reviewed artifacts")
    if bundle.get("source_registration_provenance") != expected_source_provenance:
        raise ValueError("Export source registration provenance differs from "
                         "current reviewed artifacts")
    expected_evolution = {mid: artifact.get("evolution_claims", [])
                          for mid, (artifact, _, _) in documents.items()}
    records = bundle.get("evolution_records", [])
    if config.get("with_evolution") and (
        len(records) != len(documents)
        or {r["method_id"]: r["claims"] for r in records} != expected_evolution
    ):
        raise ValueError("Export evolution records differ from current reviewed artifacts")
    return documents


def _add_components(data, bundle, projection):
    methods = {m["id"]: m for m in data["methods"]}
    sources = data.setdefault("component_sources", {})
    for record in bundle["sources"]:
        sid, meta = record["source_id"], record["source_metadata"]
        if sid in sources and sources[sid] != meta:
            raise ValueError(f"Destination component source collision: {sid}")
        sources[sid] = copy.deepcopy(meta)
    profiles = data.setdefault("component_profiles", [])
    profile_ids = {p["profile_id"] for p in profiles}
    chains = {c["method_id"]: c for c in data.get("component_chains", [])}
    for method in methods.values():
        chains.setdefault(method["id"], projection._summary(method))
    for raw in bundle["profiles"]:
        pid, mid = raw["profile_id"], raw["method_ids"][0]
        if mid not in methods:
            raise ValueError(f"Reviewed profile method is absent from destination: {mid}")
        if pid in profile_ids:
            raise ValueError(f"Destination component profile collision: {pid}")
        profile = projection._profile(raw, sources, [])
        # Preserve typed edge details (including edge-level gaps/source refs)
        # that the standard display projection does not otherwise expose.
        edges = [edge for field in ("edges", "connections", "control_edges")
                 for edge in raw.get("topology", {}).get(field, [])]
        for connection, edge in zip(profile["connections"], edges, strict=True):
            connection["source_edge"] = copy.deepcopy(edge)
        profiles.append(profile)
        profile_ids.add(pid)
        previous = chains[mid]
        overview = previous.get("baseline_summary_chain", previous)
        available = list(dict.fromkeys([pid, *previous.get("available_representations", []),
                                       overview["profile_id"]]))
        chains[mid] = copy.deepcopy(profile) | {
            "id": "chain:" + mid, "method_id": mid, "method_name": methods[mid]["name"],
            "available_representations": available, "baseline_summary_chain": overview,
        }
    data.update(component_schema_version="1.0", component_chains=list(chains.values()))
    accepted_ids = {p["method_ids"][0] for p in bundle["profiles"]}
    data["component_review_queue"] = [r for r in data.get("component_review_queue", [])
                                     if r["method_id"] not in accepted_ids]
    data.setdefault("component_statistics", {}).update({
        "method_chains": len(chains), "source_scoped_profiles": len(profiles),
        "components": sum(len(c["components"]) for c in chains.values()),
        "source_scoped_method_chains": sum(
            c["representation"] == "source_scoped_component_profile" for c in chains.values()),
        "overview_chains": sum(
            c["representation"] == "overview_component_chain" for c in chains.values()),
        "independently_reviewed_profiles": sum(bool(p.get("verification")) for p in profiles),
        "source_scoped_profile_components": sum(len(p["components"]) for p in profiles),
    })
    errors = projection.validate_component_layer(data, set(methods))
    if errors:
        raise ValueError("Invalid candidate component layer: " + "; ".join(errors))


def _add_evolution(data, documents, config, run):
    methods = {m["id"] for m in data["methods"]}
    claims, evidence, papers = (data.setdefault(field, [])
                               for field in ("claims", "evidence", "papers"))
    claim_ids, evidence_ids = {c["id"] for c in claims}, {e["id"] for e in evidence}
    paper_ids = {p["paper_id"] for p in papers}
    development = [(c["source"], c["target"]) for c in claims
                   if c.get("relationship_kind") == "method_development"
                   and c.get("status") == "supported"]
    for mid, (artifact, review, provenance) in documents.items():
        disposition = evolution_export_status(
            review, artifact, mid, Path(provenance["extraction"]).parent,
            (Path(config["atlas"]), run), known_method_ids=methods,
            require_evaluated=bool(config.get("with_evolution")),
            existing_claim_ids=claim_ids, existing_development_edges=development)
        if not disposition["valid"]:
            raise ValueError("Destination evolution validation: " + "; ".join(
                disposition["errors"]))
        sources = {s["source_id"]: s for s in artifact["sources"]}
        checks = {c["claim_id"]: c for c in review.get("evolution_checks", [])}
        for raw in artifact.get("evolution_claims", []):
            cid = raw["claim_id"]
            mapped_papers, mapped_evidence = [], []
            for anchor in raw["evidence"]:
                eid, sid = anchor["evidence_id"], anchor["source_id"]
                if eid in evidence_ids:
                    raise ValueError(f"Destination evolution evidence collision: {eid}")
                source = sources[sid]
                paper_id = "curation-source:" + canonical_hash(source)[:24]
                if paper_id not in paper_ids:
                    meta = source["source_metadata"]
                    papers.append({"paper_id": paper_id, "source_id": sid,
                                   "title": meta.get("title"), "url": meta.get("primary_url"),
                                   "doi": meta.get("doi"),
                                   "publication_year": meta.get("publication_year"),
                                   "source_metadata": copy.deepcopy(meta)})
                    paper_ids.add(paper_id)
                evidence.append(copy.deepcopy(anchor) | {
                    "id": eid, "claim_id": cid, "paper_id": paper_id,
                    "source_url": source["source_metadata"].get("primary_url"),
                    "promotion_provenance": copy.deepcopy(provenance),
                })
                evidence_ids.add(eid)
                mapped_papers.append(paper_id)
                mapped_evidence.append(eid)
            status = disposition["claim_statuses"][cid]
            claim = copy.deepcopy(raw) | {
                "id": cid, "source": raw["source_method_id"],
                "target": raw["target_method_id"], "status": status,
                "extraction_status": raw["status"],
                "relation_layer": {"method_development": "historical_change",
                                   "component_reuse": "component_transfer",
                                   "comparison": "comparison", "analogy": "analogy"}[
                                       raw["relationship_kind"]],
                "assertion_kind": "binary_relation" if status == "supported"
                                  else "candidate_relation",
                "paper_ids": list(dict.fromkeys(mapped_papers)),
                "evidence_ids": mapped_evidence, "joint_change_event_ids": [],
                "evidence_location": [e["locator"] for e in raw["evidence"]],
                "profile_id": artifact["profiles"][0]["profile_id"],
                "verification": {"report_path": provenance["review"],
                                 "report_sha256": provenance["review_sha256"],
                                 "evolution_check": copy.deepcopy(checks[cid])},
                "promotion_provenance": copy.deepcopy(provenance),
            }
            claims.append(claim)
            claim_ids.add(cid)
            if status == "supported" and raw["relationship_kind"] == "method_development":
                development.append((claim["source"], claim["target"]))


def export_graph_candidate(run: Path, output: Path | None = None, *,
                           base_graph: Path | None = None) -> dict:
    """Regenerate accepted export, then write a separate graph and diagnostics.

    Failed, pending, reopened and research-needed jobs are excluded. No worker
    processes or native model calls are launched by this operation.
    """
    run = Path(run).resolve()
    return _export_graph_runs([run], output, base_graph=base_graph, collection=False)


def export_graph_collection(runs: list[Path], output: Path, *,
                            base_graph: Path | None = None) -> dict:
    """Merge current accepted contributors, without manufacturing aggregate review.

    Each real run is independently revalidated. Multiple implementations of the
    same method across runs require an explicit selection policy and are refused.
    An earlier candidate cannot substitute for validating its contributors.
    """
    if not isinstance(runs, (list, tuple)) or not runs or output is None:
        raise ValueError("Graph collection requires contributing runs and a separate output")
    resolved = [Path(run).resolve() for run in runs]
    if len(resolved) != len(set(resolved)):
        raise ValueError("Duplicate graph collection contributor")
    return _export_graph_runs(sorted(resolved), output, base_graph=base_graph, collection=True)


def _export_graph_runs(runs, output, *, base_graph, collection):
    run = runs[0]
    configs = [read(path / "config.json") for path in runs]
    atlas = Path(configs[0]["atlas"]).resolve()
    if any(Path(config["atlas"]).resolve() != atlas for config in configs):
        raise ValueError("Graph collection contributors must use the same atlas")
    base_graph = Path(base_graph or atlas / "output/knowledge_graph.json").resolve()
    output = Path(output or run / "graph_candidate/knowledge_graph.json").resolve()
    diagnostics_path = (output.parent / "diagnostics.json" if output.name == "knowledge_graph.json"
                        else output.with_suffix(".diagnostics.json")).resolve()
    live_roots = [(atlas / folder).resolve()
                  for folder in ("output", "reviews", "papers", "scripts")]
    for path in (output, diagnostics_path):
        if (path == base_graph or any(path.is_relative_to(root) for root in live_roots)
                or any(path.is_relative_to(source_run)
                       and not path.is_relative_to(source_run / "graph_candidate")
                       for source_run in runs)):
            raise ValueError("Graph candidate must use a separate output, "
                             "outside live atlas/run artifacts")
    ownership = ({"mode": "accepted_run_collection", "runs": [str(path) for path in runs]}
                 if collection else {"run": str(run)})
    destination_pins = {path: sha(path) if path.exists() else None
                        for path in (output, diagnostics_path)}
    # Repeated exports replace only this operation's same-contributor artifacts.
    for path in (output, diagnostics_path):
        existing = read(path).get("candidate_import", {}) if path.exists() else ownership
        if any(existing.get(key) != value for key, value in ownership.items()):
            raise ValueError(f"Candidate output would overwrite an unrelated artifact: {path}")
    diagnostics = {"candidate_import": ownership, "output": str(output),
                   "base_graph": str(base_graph), "written": False}
    try:
        exports = [export_accepted(path) for path in runs]
        with ExitStack() as locks:
            for source_run in sorted(runs):
                locks.enter_context(run_lock(source_run))
            pins = {str(base_graph): sha(base_graph)}
            entries, contributors, accepted_ids = [], [], set()
            for source_run, exported in zip(runs, exports, strict=True):
                state, config = read(source_run / "state.json"), read(source_run / "config.json")
                bundle_path = Path(exported["output"])
                bundle = read(bundle_path)
                documents = _check_bundle(source_run, state, config, bundle)
                if collection and accepted_ids & documents.keys():
                    raise ValueError("Ambiguous overlapping method contributions "
                                     "in graph collection")
                accepted_ids.update(documents)
                pins.update({str(path): sha(path) for path in
                             (source_run / "state.json", source_run / "config.json", bundle_path)})
                contributors.append({
                    "run": str(source_run), "state_sha256": pins[str(source_run / "state.json")],
                    "config_sha256": pins[str(source_run / "config.json")],
                    "accepted_export": str(bundle_path),
                    "accepted_export_sha256": pins[str(bundle_path)],
                    "accepted_method_ids": sorted(documents),
                    "profile_ids": [profile["profile_id"] for profile in bundle["profiles"]],
                })
                entries.append((source_run, state, config, bundle, documents))
            base = TechnologyKnowledgeGraph.from_path(base_graph)
            data = base.export_snapshot()["data"]
            projection = _component_projection()
            for source_run, _, config, bundle, documents in entries:
                _add_components(data, bundle, projection)
                _add_evolution(data, documents, config, source_run)
            if any(key in data for key in (
                "candidate_import", "reviewed_curation", "reviewed_curation_history",
                "reviewed_curation_collection",
            )):
                raise ValueError("Candidate-base chaining is unsupported; "
                                 "revalidate every contributing run in a graph collection")
            if collection:
                data["reviewed_curation_collection"] = [
                    {"run": str(path), "bundle": copy.deepcopy(bundle)}
                    for path, _, _, bundle, _ in entries]
            else:
                data["reviewed_curation"] = copy.deepcopy(entries[0][3])
            data["candidate_import"] = ownership | {
                "base_graph": str(base_graph),
                "base_graph_sha256": pins[str(base_graph)],
                "base_snapshot_sha256": base.snapshot_sha256,
                **({"accepted_export": contributors[0]["accepted_export"],
                    "accepted_export_sha256": contributors[0]["accepted_export_sha256"]}
                   if not collection else {}),
                "requires_separate_graph_import_review": True,
                "scope": "Source-scoped reviewed candidate snapshot; no live atlas promotion.",
            }
            # Keep historical single-run snapshots byte-compatible with their
            # deterministic replay contracts. Collection-only provenance is new.
            scope_labels = {
                "baseline_validation_scope": "structure_only_not_revalidated_current_science",
                "reviewed_scope_semantics": "Each profile retains its declared reviewed scope; "
                                            "acceptance is not whole-method completeness.",
            }
            if collection:
                data["candidate_import"].update(contributors=contributors, **scope_labels)
            data["revision_id"] = "candidate:" + canonical_hash(data)
            graph = TechnologyKnowledgeGraph(data)
            validation = graph.validate()
            if not validation["valid"]:
                raise ValueError("Invalid candidate graph: " + "; ".join(validation["errors"]))
            for source_run, state, config, bundle, _ in entries:
                _check_bundle(source_run, state, config, bundle)
            if any(sha(Path(path)) != digest for path, digest in pins.items()):
                raise ValueError("Run, accepted export or destination changed during graph import")
            if any((sha(path) if path.exists() else None) != digest
                   for path, digest in destination_pins.items()):
                raise ValueError("Candidate destination changed during graph import")
            diagnostics.update({
                "candidate_import": copy.deepcopy(data["candidate_import"]),
                "contributors": contributors, **scope_labels,
                "profiles": sum(len(bundle["profiles"]) for _, _, _, bundle, _ in entries),
                "modules": sum(exported["modules"] for exported in exports),
                "imported_evolution_claims": sum(len(a.get("evolution_claims", []))
                    for _, _, _, _, documents in entries for a, _, _ in documents.values()),
                "excluded_jobs": [{"method_id": mid, "status": job["status"],
                                   **({"run": str(path)} if collection else {})}
                                  for path, state, _, _, _ in entries
                                  for mid, job in state["jobs"].items()
                                  if job["status"] != "accepted"],
                "job_status_counts": dict(Counter(j["status"] for _, state, _, _, _ in entries
                                                   for j in state["jobs"].values())),
                "graph_validation": validation, "written": True,
            })
            write(output, data)
            if (sha(diagnostics_path) if diagnostics_path.exists() else None) != (
                destination_pins[diagnostics_path]
            ):
                raise ValueError("Candidate diagnostics changed during graph publication")
            write(diagnostics_path, diagnostics)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        diagnostics.update(errors=[str(exc)], existing_snapshot_retained=output.exists())
        if (sha(diagnostics_path) if diagnostics_path.exists() else None) == (
            destination_pins[diagnostics_path]
        ):
            write(diagnostics_path, diagnostics)
        raise
    return {"output": str(output), "diagnostics": str(diagnostics_path),
            "profiles": diagnostics["profiles"], "modules": diagnostics["modules"],
            "imported_evolution_claims": diagnostics["imported_evolution_claims"],
            "contributors": contributors, "accepted_method_ids": sorted(accepted_ids),
            "snapshot_sha256": graph.snapshot_sha256,
            "requires_separate_graph_import_review": True}
