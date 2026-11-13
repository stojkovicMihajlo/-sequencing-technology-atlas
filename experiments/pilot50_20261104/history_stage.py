"""Trace citations, compare existing workflows, and assemble the bounded pilot.

Publication identity retrieves candidates; only the existing paired author/fresh
reviewer can assert a relationship between exact extracted implementations.
No graph is refined in place and no operation label establishes equivalence.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from genomics_discovery.illumina.extraction import read, sha, write
from genomics_discovery.illumina.graph import _load_card, build_graph
from genomics_discovery.illumina.relationships import load_reviewed_pair, RELATIONS
from genomics_discovery.illumina.sources import jats_text
from genomics_discovery.illumina.history_inputs import resolve_article,is_html_article,html_article_text
from genomics_discovery.illumina.citations import bibliography


def key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def _paper(source_id, source):
    """Recover only a registered original; missing/PDF seed bibliographies are held."""
    item = {"source_id": source_id, "pmid": str(source.get("pmid") or ""),
            "title": source.get("title"), "content_level": source.get("content_level"),
            "article": None, "article_sha256": None,
            "text_sha256": hashlib.sha256(source.get("text", "").encode()).hexdigest(), "format": None,
            "seed_eligible": False, "pair_eligible": False}
    if source.get("content_level") != "fulltext":
        item["hold_reason"] = "registered_fulltext_original_unavailable"
        return item
    try:
        original = resolve_article(source)
    except (OSError, ValueError) as exc:
        item.update(hold_reason="registered_original_missing_or_changed", source_error=str(exc))
        return item
    path = Path(original["path"])
    if not path.is_file() or sha(path) != original.get("sha256"):
        item["hold_reason"] = "registered_original_missing_or_changed"
        return item
    raw = path.read_bytes()
    item.update(article=str(path), article_sha256=original["sha256"])
    if raw.startswith(b"%PDF-"):
        item.update(format="PDF", pair_eligible=True,
                    hold_reason="PDF_citation_context_parser_not_implemented")
    elif is_html_article(raw):
        item.update(format='HTML',pair_eligible=True)
        try:
            text=html_article_text(source,raw)
            refs=bibliography(raw,text=text,pmid=source.get('pmid'))
            item.update(seed_eligible=True,bibliography_references=len(refs))
        except ValueError as exc:
            item.update(hold_reason='registered_HTML_citation_context_unavailable',source_error=str(exc))
    else:
        try:
            tree = ET.fromstring(raw)
        except ET.ParseError:
            item["hold_reason"] = "registered_original_not_parseable_article"
            return item
        if tree.tag.rsplit("}", 1)[-1] == "article":
            item.update(format="JATS", pair_eligible=True)
            text, _ = jats_text(raw)
            if text != source.get("text"):
                item["hold_reason"] = "registered_JATS_text_rendition_changed"
            else:
                item["seed_eligible"] = True
        else:
            item["hold_reason"] = "registered_original_not_article_XML"
    return item


def prepare_index(batch, primary_selection=None):
    """Index reviewed architecture papers; do not choose a method by paper ID."""
    batch = Path(batch).resolve()
    state = read(batch / "state.json")
    explicit = read(Path(primary_selection)) if primary_selection else {}
    if set(explicit) - set(state["cards"]):
        raise ValueError("Primary selection names a method outside this extraction batch")
    methods = {}
    for record_id, row in state["cards"].items():
        item = {"record_id": record_id, "batch": str(batch), "title": row.get("title", record_id),
                "status": "held", "papers": [], "selected_primary": None}
        methods[record_id] = item
        if row["status"] != "supported":
            item["hold_reason"] = "extraction_not_supported: " + row["status"]
            continue
        parent = Path(row["attempt_directory"])
        try:
            graph, registry = _load_card(row, read(parent / "card.json"))
        except Exception as exc:
            item["hold_reason"] = "source_reopening_failed: " + str(exc)
            continue
        workflow = graph["workflow"]
        item.update(extraction_sha256=row["artifact_pins"]["extraction.json"],
                    source_registry=str(parent / "source_registry.json"),
                    source_registry_sha256=row["artifact_pins"]["source_registry.json"],
                    method_names=workflow["method_names"], scope=workflow["scope"])
        seen = set()
        for match in workflow["paper_matches"]:
            if (match["assessment"] != "content_checked" or
                    match["relationship"] not in {"supports_architecture", "supports_variant"} or
                    not set(match["methods"]).intersection(workflow["method_names"])):
                continue
            source_id = match["source_id"]
            if source_id in seen:
                continue
            seen.add(source_id)
            paper = _paper(source_id, registry[source_id])
            paper.update(paper_match_id=match["id"], methods=match["methods"],
                         relationship=match["relationship"])
            item["papers"].append(paper)
        eligible = [p for p in item["papers"] if p["seed_eligible"]
                    and p["relationship"] == "supports_architecture"]
        # Duplicate registrations of exact bytes are one paper, not a new choice.
        unique = {}
        for paper in eligible:
            unique.setdefault(paper["article_sha256"], paper)
        eligible = list(unique.values())
        if record_id in explicit:
            selected_id = explicit[record_id]
            chosen = next((p for p in item["papers"] if p["source_id"] == selected_id
                           and p["seed_eligible"]), None)
            if chosen is None:
                # Source bundles may register the same paper twice: an original
                # card ID and a selected-primary ID. Byte/rendition identity can
                # resolve that alias; a matching PMID or title cannot.
                proposed = _paper(selected_id, registry[selected_id]) if selected_id in registry else None
                aliases = [p for p in item["papers"] if proposed and proposed["seed_eligible"]
                    and p["seed_eligible"] and p["article_sha256"] == proposed["article_sha256"]
                    and registry[p["source_id"]].get("text") == registry[selected_id].get("text")]
                if aliases:
                    chosen = {**proposed, "paper_match_id": aliases[0]["paper_match_id"],
                              "methods": aliases[0]["methods"], "relationship": aliases[0]["relationship"],
                              "reviewed_source_aliases": [p["source_id"] for p in aliases],
                              "selection_basis": "Exact original bytes and registered rendition match a reviewed paper assignment"}
                else:
                    reviewed=next((p for p in item['papers'] if p['source_id']==selected_id),None)
                    item["hold_reason"] = (reviewed.get('hold_reason') if reviewed else None) or "explicit_primary_not_supported_by_reviewed_paper_assignment"
        elif len(eligible) == 1:
            chosen = eligible[0]
        else:
            chosen = None
        if chosen:
            item.update(status="ready", selected_primary=chosen)
        else:
            item.setdefault("hold_reason", "multiple_architecture_primaries_require_selection" if eligible
                            else "no_registered_JATS_architecture_primary")
    return {"format_version": "pilot_history_methods/1", "batch": str(batch),
            "batch_state_sha256": sha(batch / "state.json"), "methods": methods,
            "boundary": "Paper identities nominate comparisons; no method merge or historical edge is inferred."}


def pair_candidates(index, citation_runs):
    """Select citation-nominated in-cohort pairs; pair review decides the science."""
    by_pmid, pairs, held = {}, {}, []
    for method in index["methods"].values():
        for paper in method["papers"]:
            if paper["pmid"] and paper["pair_eligible"]:
                by_pmid.setdefault(paper["pmid"], []).append((method, paper))
    for seed_id, run in citation_runs.items():
        root = Path(run)
        if read(root / "state.json").get("status") != "completed":
            continue
        analysis, sources = read(root / "analysis.json"), read(root / "inputs/sources.json")
        seed = index["methods"][seed_id]
        for claim in analysis["claims"]:
            if claim["relation"] in {"background", "no_established_relation"}:
                continue
            if claim.get("claim_scope") != "seed_workflow":
                held.append({"seed": seed_id, "claim_id": claim["id"], "run": str(root),
                             "reason": "citation_contribution_outside_extracted_workflow"})
                continue
            pmid = str(sources.get(claim["reference_id"], {}).get("pmid") or "")
            candidates = [x for x in by_pmid.get(pmid, []) if x[0]["record_id"] != seed_id]
            declared = set(claim["catalogue_candidates"])
            if declared:
                candidates = [x for x in candidates if x[0]["record_id"] in declared]
            if not candidates:
                held.append({"seed": seed_id, "claim_id": claim["id"], "run": str(root),
                             "reason": "no_extracted_implementation_with_cited_primary",
                             "reference_id": claim["reference_id"], "pmid": pmid,
                             "catalogue_candidates": claim["catalogue_candidates"]})
            for earlier, paper in candidates:
                pair_key = key(sorted([seed_id, earlier["record_id"]]))
                plan = {"left": {"batch": earlier["batch"], "record_id": earlier["record_id"],
                                 "primary_paper": paper["source_id"]},
                        "right": {"batch": seed["batch"], "record_id": seed_id,
                                  "primary_paper": seed["selected_primary"]["source_id"]}}
                basis = {"citation_run": str(root), "claim_id": claim["id"],
                         "reference_id": claim["reference_id"], "cited_pmid": pmid,
                         "basis": "cited publication + in-cohort reviewed paper match; method/version relation still unverified"}
                paper_bindings = {method_id: {field: source[field] for field in
                    ("article_sha256", "text_sha256")} for method_id, source in
                    ((earlier["record_id"], paper), (seed_id, seed["selected_primary"]))}
                if pair_key not in pairs:
                    pairs[pair_key] = {"pair_id": pair_key, "plan": plan, "paper_bindings": paper_bindings,
                                       "basis": [], "status": "candidate"}
                existing = pairs[pair_key]
                # Registration aliases do not create a version conflict. Both
                # original bytes and the text shown to the agents must agree;
                # title, PMID and semantic similarity cannot establish this.
                if existing["paper_bindings"] != paper_bindings:
                    existing["status"] = "held_primary_version_conflict"
                    existing.setdefault("alternative_plans", []).append(
                        {"plan": plan, "paper_bindings": paper_bindings})
                existing["basis"].append(basis)
    return {"pairs": list(pairs.values()), "unbound_research_leads": held}


def assemble(batch, output, pair_runs, citation_runs, *, base_graph=None):
    """Add only freshly pair-reviewed links to actual workflow endpoints."""
    batch, output = Path(batch), Path(output)
    graph = copy.deepcopy(base_graph if base_graph is not None else build_graph(batch, batch / "inventory.json"))
    graph["format_version"] = "illumina_evolution_pilot/1"
    graph["relationship_overlays"] = []
    graph["citation_research"] = {k: str(v) for k, v in citation_runs.items()}
    graph["refinement_requests"], graph["history_publication_holds"] = [], []
    manifest = {"format_version": "illumina_history_inputs/1", "overlays": []}
    for pair_id, root in pair_runs.items():
        root = Path(root)
        state = read(root / "state.json")
        if state.get("status") != "completed":
            graph["history_publication_holds"].append({"pair_id": pair_id, "reason": "pair_not_completed"})
            continue
        path, pin = root / "graph.json", sha(root / "graph.json")
        overlay = load_reviewed_pair(path, pin)
        for side, binding in overlay["bindings"].items():
            card = graph["cards"].get(binding["record_id"], {})
            if card.get("admission") != "supported" or card["artifact_pins"]["extraction.json"] != binding["extraction_sha256"]:
                raise ValueError("Pair endpoint does not bind the current supported extraction")
        needs_refinement = [x for x in overlay["diagram_adequacy"] if x["verdict"] == "needs_finer_extraction"]
        for request in needs_refinement:
            binding = next(x for x in overlay["bindings"].values() if x["record_id"] == request["record_id"])
            graph["refinement_requests"].append({"record_id": request["record_id"],
                "before_extraction_sha256": binding["extraction_sha256"], "explanation": request["explanation"],
                "pair_id": pair_id, "pair_graph": str(path), "pair_graph_sha256": pin,
                "status": "needs_source_grounded_refinement_and_fresh_review",
                "boundary": "No State/Operation expansion was applied; a changed extraction requires a new pair review."})
        if needs_refinement:
            graph["history_publication_holds"].append({"pair_id": pair_id,
                "reason": "pair_scope_adequacy_needs_refinement", "overlay": str(path), "sha256": pin})
            continue
        manifest["overlays"].append({"path": str(path.resolve()), "sha256": pin})
        graph["relationship_overlays"].append({"path": str(path), "sha256": pin})
        for item in overlay["historical_links"] + overlay["correspondences"]:
            claim = item["claim"]
            sides = overlay["bindings"]
            left, right = sides["left"]["record_id"], sides["right"]["record_id"]
            if claim["direction"] == "right_to_left":
                left, right = right, left
            graph["edges"].append({"id": "relationship:" + pin + ":" + claim["id"],
                "source": graph["cards"][left]["workflow_id"], "target": graph["cards"][right]["workflow_id"],
                "kind": claim["relation"], "relationship_layer": RELATIONS[claim["relation"]]["layer"],
                "directed": claim["direction"] != "undirected", "admission": "model_reviewed_pilot",
                "claim": copy.deepcopy(claim), "review": copy.deepcopy(item["review"]),
                "pair_graph": str(path), "pair_graph_sha256": pin})
    graph["boundary"] = ("Illumina-derived workflows plus separately reviewed, exact-version relationship overlays. "
        "Citation research remains candidate leads; paper identity alone creates no edge. "
        "Undirected correspondences are not ancestry, and shared components are not guaranteed interchangeable.")
    write(output / "graph.json", graph)
    write(output / "history_inputs.json", manifest)
    write(output / "refinement_requests.json", graph["refinement_requests"])
    return {"graph": str(output / "graph.json"), "workflows": sum(c.get("admission") == "supported" for c in graph["cards"].values()),
            "pair_overlays": len(graph["relationship_overlays"]),
            "historical_edges": sum(e.get("relationship_layer") == "history" for e in graph["edges"]),
            "refinement_requests": len(graph["refinement_requests"]),
            "publication_holds": len(graph["history_publication_holds"])}


async def execute(batch, output, runtime, *, workers=4, primary_selection=None, source_cache=None):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native history stage requires Sherlock Slurm")
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError("Use one to eight bounded concurrent native workers")
    batch, output, runtime = Path(batch).resolve(), Path(output).resolve(), Path(runtime).resolve()
    output.mkdir(parents=True, exist_ok=False)
    index = prepare_index(batch, primary_selection)
    write(output / "methods.json", index)
    state = {"format_version": "pilot_history_stage/1", "status": "citation_research", "job_id": os.environ["SLURM_JOB_ID"],
             "batch": str(batch), "batch_state_sha256": index["batch_state_sha256"],
             "workers": workers, "citation_runs": {}, "pair_runs": {}}
    semaphore = asyncio.Semaphore(workers)

    async def launch(kind, ident, command, destination):
        async with semaphore:
            row = {"status": "running", "path": str(destination), "command": command}
            state[kind][ident] = row; write(output / "state.json", state)
            log = output / "logs" / (kind + "_" + ident + ".log")
            log.parent.mkdir(exist_ok=True)
            try:
                with log.open("wb") as handle:
                    process = await asyncio.create_subprocess_exec(*command, stdout=handle, stderr=asyncio.subprocess.STDOUT)
                    code = await process.wait()
                result_path = destination / "state.json"
                status = read(result_path).get("status") if result_path.exists() else "no_stage_state"
                row.update(status="completed" if code == 0 and status == "completed" else "failed",
                           exit_code=code, native_status=status, log=str(log))
            except Exception as exc:
                row.update(status="failed", error=type(exc).__name__ + ": " + str(exc), log=str(log))
            finally:
                write(output / "state.json", state)

    citation_tasks = []
    for record_id, method in index["methods"].items():
        if method["status"] != "ready":
            continue
        paper, destination = method["selected_primary"], output / "citations" / record_id
        command = [sys.executable, str(runtime / "experiments/citation_lineage_pilot/run.py"), "run",
            "--batch", str(batch), "--inventory", str(batch / "inventory.json"), "--record", record_id,
            "--paper", paper["source_id"], "--article", paper["article"], "--output", str(destination)]
        if source_cache:
            command += ["--source-cache", str(source_cache)]
        citation_tasks.append(launch("citation_runs", record_id, command, destination))
    await asyncio.gather(*citation_tasks)
    citation_runs = {i: r["path"] for i, r in state["citation_runs"].items() if r["status"] == "completed"}
    candidates = pair_candidates(index, citation_runs); write(output / "pair_candidates.json", candidates)
    state["status"] = "paired_workflow_review"; write(output / "state.json", state)
    pair_tasks = []
    for candidate in candidates["pairs"]:
        if candidate["status"] != "candidate":
            continue
        ident = candidate["pair_id"]
        plan, destination = output / "plans" / (ident + ".json"), output / "pairs" / ident
        write(plan, candidate["plan"])
        command = [sys.executable, str(runtime / "experiments/citation_lineage_pilot/pair_run.py"),
                   "run", "--plan", str(plan), "--output", str(destination)]
        pair_tasks.append(launch("pair_runs", ident, command, destination))
    await asyncio.gather(*pair_tasks)
    if sha(batch / "state.json") != index["batch_state_sha256"]:
        raise ValueError("Extraction batch changed during history work")
    pair_runs = {i: r["path"] for i, r in state["pair_runs"].items() if r["status"] == "completed"}
    result = assemble(batch, output, pair_runs, citation_runs)
    result.update(citation_runs=len(citation_runs), citation_holds=sum(m["status"] != "ready" for m in index["methods"].values()),
                  failed_citations=sum(r["status"] != "completed" for r in state["citation_runs"].values()),
                  failed_pairs=sum(r["status"] != "completed" for r in state["pair_runs"].values()),
                  held_pairs=sum(c["status"] != "candidate" for c in candidates["pairs"]))
    state.update(status="completed", result=result); write(output / "state.json", state)
    write(output / "RESULT.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("batch", "output", "runtime"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--primary-selection", type=Path)
    parser.add_argument("--source-cache", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(execute(args.batch, args.output, args.runtime, workers=args.workers,
        primary_selection=args.primary_selection, source_cache=args.source_cache)), indent=2))
