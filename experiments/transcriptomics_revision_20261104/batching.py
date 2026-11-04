"""Bounded source-planning batches and cross-batch reconciliation inputs.

Connected name-candidate groups stay together for review; this is a scheduling
constraint, not identity inference. Reconciliation never silently merges methods
or authorizes extraction. A global identity review remains an explicit next gate.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from genomics_discovery.illumina.extraction import now, read, sha, write

try:
    from .planning import digest, search_key
except ImportError:
    from planning import digest, search_key


def components(census):
    adjacency = {r["record_id"]: set() for r in census["rows"]}
    if len(adjacency) != len(census["rows"]):
        raise ValueError("Duplicate census occurrence")
    for pair in census["candidate_identity_pairs"]:
        a, b = pair["left"], pair["right"]
        if a not in adjacency or b not in adjacency or a == b:
            raise ValueError("Invalid candidate identity pair")
        adjacency[a].add(b); adjacency[b].add(a)
    remaining, groups = set(adjacency), []
    while remaining:
        start = min(remaining); reached, pending = {start}, [start]
        while pending:
            additions = adjacency[pending.pop()] - reached
            reached |= additions; pending.extend(sorted(additions))
        remaining -= reached; groups.append(sorted(reached))
    return groups


def partition(census, selected_records, max_occurrences=12):
    """Twelve is a practical context cap, not an assay identity/granularity rule."""
    if type(max_occurrences) is not int or max_occurrences < 1:
        raise ValueError("Positive source-occurrence context cap required")
    available = {r["record_id"] for r in census["rows"]}
    seeds = set(selected_records)
    if not seeds or seeds - available:
        raise ValueError("Explicit existing source records are required")
    chosen = [g for g in components(census) if set(g) & seeds]
    # Stable order; first-fit packing never cuts a nominated identity component.
    chosen.sort(key=lambda g: (-len(g), g))
    groups = []
    for component in chosen:
        destination = next((b for b in groups if not b["oversized"] and
                            len(b["record_ids"]) + len(component) <= max_occurrences), None)
        if destination is None:
            destination = {"record_ids": [], "components": [], "oversized": len(component) > max_occurrences}
            groups.append(destination)
        destination["record_ids"].extend(component); destination["components"].append(component)
    selected = {x for g in chosen for x in g}
    for index, batch in enumerate(groups):
        batch.update(batch_id=f"batch_{index + 1:03d}",
                     status="held_oversized_component" if batch["oversized"] else "ready_for_identity_planning")
        batch["record_ids"].sort()
    return {"format_version": "transcriptomics_planning_batches/1",
            "parent_census_sha256": digest(census), "max_source_occurrences": max_occurrences,
            "requested_seed_records": sorted(seeds), "identity_neighbor_additions": sorted(selected - seeds),
            "selected_record_ids": sorted(selected), "pending_scope_record_ids": sorted(available - selected),
            "batches": groups,
            "boundary": "Explicit selected source-review scope, not a unique-method count or extraction authorization."}


def write_batches(census_path, output, selected_records, max_occurrences=12):
    census = read(census_path); manifest = partition(census, selected_records, max_occurrences)
    output.mkdir(parents=True, exist_ok=False)
    write(output / "parent_census.json", census)
    manifest["parent_census_file_sha256"] = sha(output / "parent_census.json")
    for batch in manifest["batches"]:
        ids = set(batch["record_ids"])
        subset = {**census, "status": "bounded_planning_input_not_full_transcriptomics_coverage",
                  "parent_census_sha256": digest(census),
                  "rows": [r for r in census["rows"] if r["record_id"] in ids],
                  "candidate_identity_pairs": [p for p in census["candidate_identity_pairs"] if p["left"] in ids]}
        subset["counts"] = {"source_occurrences": len(ids), "candidate_identity_pairs": len(subset["candidate_identity_pairs"])}
        path = output / (batch["batch_id"] + ".json"); write(path, subset)
        batch.update(census_file=path.name, census_file_sha256=sha(path), census_sha256=digest(subset))
    write(output / "manifest.json", manifest)
    return manifest


def load_batches(manifest_path):
    manifest = read(manifest_path)
    if manifest.get("format_version") != "transcriptomics_planning_batches/1":
        raise ValueError("Unknown planning batch manifest")
    parent_path = manifest_path.parent / "parent_census.json"
    if sha(parent_path) != manifest["parent_census_file_sha256"]:
        raise ValueError("Parent census bytes changed")
    parent = read(parent_path)
    if digest(parent) != manifest["parent_census_sha256"]:
        raise ValueError("Parent census disagrees with batch policy")
    expected = partition(parent, manifest["requested_seed_records"], manifest["max_source_occurrences"])
    for key in ("selected_record_ids", "pending_scope_record_ids", "identity_neighbor_additions"):
        if manifest[key] != expected[key]: raise ValueError("Selected source coverage changed")
    if len(manifest["batches"]) != len(expected["batches"]):
        raise ValueError("Batch coverage differs from source components")
    for actual, planned in zip(manifest["batches"], expected["batches"]):
        if any(actual.get(key) != value for key, value in planned.items()):
            raise ValueError("Batch cuts/repeats a source component or changes its context limit")
        path = manifest_path.parent / actual["census_file"]
        if Path(actual["census_file"]).name != actual["census_file"] or sha(path) != actual["census_file_sha256"]:
            raise ValueError("Unsafe or changed batch census")
        subset = read(path)
        if digest(subset) != actual["census_sha256"] or subset["parent_census_sha256"] != digest(parent):
            raise ValueError("Batch census pin disagrees with its parent")
        ids = set(actual["record_ids"])
        if subset["rows"] != [r for r in parent["rows"] if r["record_id"] in ids]:
            raise ValueError("Batch source rows differ from parent census")
        if subset["candidate_identity_pairs"] != [p for p in parent["candidate_identity_pairs"] if p["left"] in ids]:
            raise ValueError("Batch lost candidate identity questions")
    return manifest, parent


async def execute_batches(manifest_path, output, batch_ids, *, workers=4, executor=None):
    if not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("Native batch planning requires a Sherlock Slurm allocation")
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("Select one to four concurrent planning batches")
    manifest, _ = load_batches(manifest_path)
    selected = set(batch_ids); available = {b["batch_id"]: b for b in manifest["batches"]}
    if not selected or selected - set(available) or len(selected) != len(batch_ids):
        raise ValueError("Explicit, unique existing batch IDs are required")
    if any(available[b]["oversized"] for b in selected):
        raise ValueError("Oversized identity component needs an explicit context-limit revision; never cut it silently")
    if executor is None:
        try: from .native_planning import execute as executor
        except ImportError: from native_planning import execute as executor
    output.mkdir(parents=True, exist_ok=False)
    state = {"format_version": "transcriptomics_planning_batch_run/1", "manifest": str(manifest_path.resolve()),
             "manifest_sha256": sha(manifest_path), "job_id": os.environ["SLURM_JOB_ID"],
             "workers": workers, "started_at": now(),
             "batches": {b: {"status": "pending", "planning_run": str((output / b).resolve())} for b in sorted(selected)}}
    write(output / "state.json", state); semaphore = asyncio.Semaphore(workers)
    async def one(batch_id):
        async with semaphore:
            row = state["batches"][batch_id]; row.update(status="running", started_at=now())
            write(output / "state.json", state)
            try:
                if sha(manifest_path) != state["manifest_sha256"]: raise ValueError("Batch manifest changed")
                batch = available[batch_id]; source = manifest_path.parent / batch["census_file"]
                if sha(source) != batch["census_file_sha256"]: raise ValueError("Selected batch input changed")
                result = await executor(source.resolve(), Path(row["planning_run"]))
                row.update(status="reviewed" if result is not None else "held_after_review")
            except Exception as exc: row.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            finally:
                row["finished_at"] = now(); write(output / "state.json", state)
    await asyncio.gather(*(one(b) for b in sorted(selected)))
    state["finished_at"] = now(); write(output / "state.json", state)
    return state


def reconcile(manifest_path, run_state_path, *, plan_loader=None):
    """Reopen every reviewed native plan; emit global review INPUT, never jobs."""
    manifest, parent = load_batches(manifest_path); run = read(run_state_path)
    if run.get("format_version") != "transcriptomics_planning_batch_run/1" or run["manifest_sha256"] != sha(manifest_path):
        raise ValueError("Planning run is not bound to this manifest")
    available = {b["batch_id"]: b for b in manifest["batches"]}
    if set(run["batches"]) - set(available): raise ValueError("Unknown planned batch")
    if plan_loader is None:
        try: from .extract_plan import load_plan as plan_loader
        except ImportError: from extract_plan import load_plan as plan_loader
    units, pending, reviewed_sources, source_owner = [], [], set(), {}
    for batch_id, entry in sorted(run["batches"].items()):
        if entry["status"] != "reviewed":
            pending.append({"batch_id": batch_id, "status": entry["status"]}); continue
        directory = Path(entry["planning_run"])
        census = read(directory / "inputs/census.json")
        if digest(census) != available[batch_id]["census_sha256"]:
            raise ValueError("Planning run used a different batch source census")
        worklist, bundles = plan_loader(directory)
        if worklist["census_sha256"] != digest(census): raise ValueError("Reviewed worklist uses another source census")
        reviewed_sources.update(r["record_id"] for r in census["rows"])
        bundle_map = {b["unit_id"]: b for b in bundles}
        for unit in worklist["units"]:
            source_ids = {b["record_id"] for b in unit["bindings"]}
            if source_ids - set(available[batch_id]["record_ids"]): raise ValueError("Unit binds a source outside its batch")
            for rid in source_ids:
                if rid in source_owner and source_owner[rid] != batch_id:
                    raise ValueError("Cross-batch source overlap; duplicate extraction risk")
                source_owner[rid] = batch_id
            units.append({"global_unit_id": batch_id + ":" + unit["unit_id"], "batch_id": batch_id,
                          "planning_run": str(directory), "worklist_sha256": digest(worklist),
                          "unit": unit, "bundle": bundle_map[unit["unit_id"]]})
    questions = []
    row_by_id = {r["record_id"]: r for r in parent["rows"]}
    def identity_source_leads(item):
        pages, publications = set(), set()
        for binding in item["unit"]["bindings"]:
            if binding["role"] == "supporting_context": continue
            row = row_by_id[binding["record_id"]]
            sources = row.get("registered_sources", {})
            pages.update(s for s in sources if s.startswith("collection:"))
            for evidence in binding.get("evidence", []):
                source = sources.get(evidence["source_id"], {})
                if source.get("content_level") in {"fulltext", "abstract"} and source.get("pmid"):
                    publications.add(source["pmid"])
        return pages, publications
    for i, left in enumerate(units):
        for right in units[i + 1:]:
            if left["batch_id"] == right["batch_id"]: continue
            left_names = {search_key(left["unit"]["method_name"])}
            right_names = {search_key(right["unit"]["method_name"])}
            for item, names in ((left, left_names), (right, right_names)):
                for binding in item["unit"]["bindings"]:
                    if binding["role"] != "supporting_context":
                        names.update(search_key(n) for n in row_by_id[binding["record_id"]]["source_method_labels"])
            reasons = []
            if left_names & right_names: reasons.append("overlapping_reviewed_name_or_source_label; identity_unestablished")
            left_pages, left_pubs = identity_source_leads(left)
            right_pages, right_pubs = identity_source_leads(right)
            if left_pages & right_pages:
                reasons.append("shared_registered_collection_page; may_be_different_grouped_methods")
            if left_pubs & right_pubs:
                reasons.append("shared_identity_cited_publication; one_paper_may_describe_multiple_methods")
            if (left["unit"]["method_name"].casefold().strip(), left["unit"]["implementation_scope"].casefold().strip()) == (right["unit"]["method_name"].casefold().strip(), right["unit"]["implementation_scope"].casefold().strip()):
                reasons.append("same_recorded_name_and_scope; do_not_schedule_twice")
            if reasons:
                questions.append({"left": left["global_unit_id"], "right": right["global_unit_id"], "reasons": reasons,
                                  "status": "requires_cross_batch_identity_review"})
    return {"format_version": "transcriptomics_reconciliation_input/1", "status": "global_identity_review_required",
            "extraction_authorized": False, "batch_manifest_sha256": sha(manifest_path),
            "planning_run_state_sha256": sha(run_state_path), "reviewed_source_record_ids": sorted(reviewed_sources),
            "pending_source_record_ids": sorted(set(row_by_id) - reviewed_sources),
            "pending_batches": pending, "reviewed_units": units, "candidate_cross_batch_pairs": questions,
            "review_requirement": "Review all listed units for aliases missed by name retrieval. Resolve every nominated pair. Same scoped method requires a newly consolidated source plan, not two jobs. No detected collision does not establish unique identity.",
            "boundary": "Revalidated per-batch plans and bundles are retained. No cross-batch identity or canonical method count is inferred; no extraction is launched."}


def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare"); p.add_argument("census", type=Path); p.add_argument("output", type=Path)
    choose = p.add_mutually_exclusive_group(required=True)
    choose.add_argument("--rna-context", action="store_true"); choose.add_argument("--record-id", action="append"); choose.add_argument("--records-file", type=Path)
    p.add_argument("--max-occurrences", type=int, default=12)
    p = commands.add_parser("run"); p.add_argument("manifest", type=Path); p.add_argument("output", type=Path)
    p.add_argument("--batch-id", action="append", required=True); p.add_argument("--workers", type=int, default=4)
    p = commands.add_parser("reconcile"); p.add_argument("manifest", type=Path); p.add_argument("run_state", type=Path); p.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        census = read(args.census)
        ids = ([r["record_id"] for r in census["rows"] if r["nomination_reasons"]] if args.rna_context else
               read(args.records_file) if args.records_file else args.record_id)
        value = write_batches(args.census, args.output, ids, args.max_occurrences)
        print(json.dumps({"selected_sources": len(value["selected_record_ids"]), "pending_sources": len(value["pending_scope_record_ids"]), "planning_batches": len(value["batches"])}))
    elif args.command == "run": asyncio.run(execute_batches(args.manifest.resolve(), args.output.resolve(), args.batch_id, workers=args.workers))
    else: write(args.output, reconcile(args.manifest, args.run_state))


if __name__ == "__main__": main()
