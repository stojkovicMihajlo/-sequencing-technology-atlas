"""Admit accepted batch workflows to a graph through the existing review gate.

Job IDs are explicit inventory-to-atlas bindings; no fuzzy method identity or
new cross-method relationship is inferred here. Historical claims retain their
original review scope. Optional reviewed matching indexes supply comparisons.

Later batches may supply ``previous`` to grow the accepted graph from its pinned
registry, bindings and reviewed indexes. Replacing a method's current extraction
requires naming that method in ``replace_methods``. The older publication stays
immutable; inherited comparisons keep their original reviewed workflow versions.
Prior canonical-atlas bytes remain pinned; a legitimate updated atlas should use
a new source path. New batches grow accepted coverage without assigning novel
scientific edges, and inherited indexes are never silently rebound to a new run.
"""

import argparse
import json
from pathlib import Path

from .extraction_batch.controller import _load, _recover
from .extraction_batch.storage import check_pins, controller_lock, sha, write
from .publication_sources import batch_source_pins, merge_pins, publication_source_pins, unique_exclusions
from .technology_graph.assembly import build_graph
from .technology_graph.comparison_bindings import receipt_paths, cli_receipts
from .workflow_extraction.models import digest
from .workflow_extraction.sources import load_packet

PUBLICATION = "batch_graph_publication/1.0"
ARTIFACTS = {"extractions.json", "bindings.json", "indexes.json", "graph.json"}


def _previous(directory: Path, *, recover_legacy_sources=False, execution_metadata=None,
              include_source_metadata=False):
    """Load only reviewed inputs; never import old integrated-graph assertions."""
    receipt_path = directory / "publication.json"
    receipt = json.loads(receipt_path.read_bytes())
    if receipt.get("format_version") != PUBLICATION or set(receipt.get("files", {})) != ARTIFACTS:
        raise ValueError("Prior publication lacks the pinned registry/bindings/index manifest; rebuild it with this publisher")
    pins = {str(directory / name): expected for name, expected in receipt["files"].items()}
    inputs = receipt.get("input_files")
    if not isinstance(inputs, dict) or not inputs:
        raise ValueError("Prior publication lacks input hash pins")
    if any(not isinstance(path, str) or not Path(path).is_absolute()
           or not isinstance(value, str) or len(value) != 64 for path, value in inputs.items()):
        raise ValueError("Invalid prior publication input pins")
    pins.update(inputs)
    # The receipt itself becomes a pinned dependency of its successor.
    pins[str(receipt_path)] = sha(receipt_path)
    check_pins(pins)
    graph = json.loads((directory / "graph.json").read_bytes())
    if (receipt.get("graph_sha256") != sha(directory / "graph.json")
            or receipt.get("revision_id") != graph.get("revision_id")
            or digest({k:v for k,v in graph.items() if k != "revision_id"}) != graph.get("revision_id")):
        raise ValueError("Prior publication graph/revision pins disagree")
    if any(inputs.get(path) != value for path, value in graph.get("input_files", {}).items()):
        raise ValueError("Prior publication omitted or changed a graph input pin")
    for name in ("extractions.json", "bindings.json"):
        if graph.get("input_files", {}).get(str(directory / name)) != receipt["files"][name]:
            raise ValueError("Prior registry/bindings are not the graph's pinned inputs")
    registry = json.loads((directory / "extractions.json").read_bytes())
    bindings = json.loads((directory / "bindings.json").read_bytes())
    indexes = json.loads((directory / "indexes.json").read_bytes())
    if not isinstance(registry, dict) or not registry or not isinstance(bindings, dict):
        raise ValueError("Invalid prior extraction registry/bindings")
    if not isinstance(indexes, list):
        raise ValueError("Invalid prior reviewed-index manifest")
    indexed, comparison_receipts = [], {}
    for entry in indexes:
        if isinstance(entry, str):
            index, selection_receipt = (directory / entry).resolve(), None
        elif isinstance(entry, dict) and set(entry) == {"index", "selection_receipt"} and all(isinstance(v,str) for v in entry.values()):
            index = (directory / entry["index"]).resolve()
            selection_receipt = (directory / entry["selection_receipt"]).resolve()
        else:
            raise ValueError("Invalid prior reviewed-index/selection-receipt record")
        if index in indexed:
            raise ValueError("Duplicate prior reviewed-index record")
        indexed.append(index)
        if selection_receipt is not None:
            if str(selection_receipt) not in graph.get("input_files", {}):
                raise ValueError("Prior comparison selection receipt is not a graph-pinned input")
            comparison_receipts[index] = selection_receipt
    if {str(index):str(receipt) for index,receipt in comparison_receipts.items()} != graph.get("comparison_identity_receipts", {}):
        raise ValueError("Prior comparison receipt associations differ from the pinned graph")
    registry = {key:str((directory / path).resolve()) for key,path in registry.items()}
    indexes = indexed
    if any(str(index) not in graph.get("input_files", {}) for index in indexes):
        raise ValueError("Prior index manifest contains an unpinned comparison index")
    if set(registry) != set(receipt.get("accepted_methods", [])) or not set(registry) <= bindings.keys():
        raise ValueError("Prior accepted-method registry/bindings disagree")
    exclusions = []
    sources = publication_source_pins(directory, receipt, exclusions=exclusions,
                                     recover_legacy_sources=recover_legacy_sources,
                                     execution_metadata=execution_metadata)
    if "source_files" in receipt and any(
            graph["input_files"].get(p) != h for p,h in receipt["source_files"].items()):
        raise ValueError("Prior graph omitted a committed source receipt pin")
    pins = merge_pins(pins, sources)
    result = registry, bindings, indexes, pins, receipt, comparison_receipts
    return (*result, sources, exclusions) if include_source_metadata else result


def publish(run: Path, atlas_path: Path, output: Path, *, indexes: list[Path] | None = None,
            previous: Path | None = None, replace_methods: set[str] | None = None,
            comparison_receipts: dict[Path, Path] | None = None,
            recover_legacy_sources: bool = False, execution_metadata: list[Path] | None = None) -> dict:
    run, atlas_path, output = Path(run).resolve(), Path(atlas_path).resolve(), Path(output).resolve()
    with controller_lock(run):
        return _publish(run, atlas_path, output, indexes=indexes, previous=previous,
                        replace_methods=replace_methods, comparison_receipts=comparison_receipts,
                        recover_legacy_sources=recover_legacy_sources, execution_metadata=execution_metadata)


def _publish(run: Path, atlas_path: Path, output: Path, *, indexes=None,
             previous=None, replace_methods=None, comparison_receipts=None,
             recover_legacy_sources=False, execution_metadata=None) -> dict:
    if output.exists():
        raise FileExistsError("Publication output must be a fresh immutable directory")
    plan, config, current = _load(run)
    if current["status"] in {"running", "planned", "blocked_native_sessions"}:
        raise ValueError("Publish only a stopped batch with completed, reviewed results")
    jobs = {row["id"]: row for row in plan["jobs"]}
    # accepted_parents is a controller export cache, refreshed only after all
    # jobs settle. A cancelled run can have newer completed supported rows.
    # Reopen every such row through the same recovery gate without updating it.
    parents = {}
    for mid, row in current["jobs"].items():
        if row["status"] != "supported":
            continue
        job = jobs[mid]
        check_pins(job["pins"])
        packet = load_packet(Path(job["source_packet"]))
        _, pair, _, _ = _recover(row, packet, config)
        if pair is None or pair["outcome"] != "supported":
            raise ValueError(f"Supported method lacks a verified supported workflow/review pair: {mid}")
        parents[mid] = pair
    if not parents:
        raise ValueError("No accepted extraction parents to publish")
    atlas = json.loads(atlas_path.read_bytes())
    # Use canonical atlas source, not an already-rendered combined graph.
    if (not isinstance(atlas.get("methods"), list) or not isinstance(atlas.get("claims"), list)
            or not isinstance(atlas.get("revision_id"), str) or not atlas["revision_id"].strip()):
        raise ValueError("Use the canonical atlas with methods, claims and revision_id")
    method_ids = {row["id"] for row in atlas["methods"]}
    registry, bindings, inherited_indexes, prior_pins, prior_receipt = {}, {}, [], {}, None
    inherited_receipts = {}
    metadata = {str(Path(p).resolve()):sha(Path(p)) for p in execution_metadata or []}
    source_exclusions = []
    inherited_sources = {}
    if previous is not None:
        previous = Path(previous).resolve()
        registry, bindings, inherited_indexes, prior_pins, prior_receipt, inherited_receipts, inherited_sources, inherited_exclusions = _previous(
            previous, recover_legacy_sources=recover_legacy_sources, execution_metadata=metadata,
            include_source_metadata=True)
        metadata = merge_pins(metadata, prior_receipt.get("source_execution_metadata", {}))
        source_exclusions.extend(inherited_exclusions)
    source_pins = merge_pins(inherited_sources, batch_source_pins(
        run, sha(run / "plan.json"), sorted(parents),
        execution_metadata=metadata, exclusions=source_exclusions))
    requested_receipts = receipt_paths(comparison_receipts)
    retained = set(registry) - parents.keys()
    replace_methods = set(replace_methods or ())
    if not replace_methods <= parents.keys() & registry.keys():
        raise ValueError("replace_methods must name methods present in both previous and current batches")
    replaced = []
    for mid, parent in parents.items():
        runtime_run = Path(parent["runtime_run"])
        binding = {"atlas_method_id": mid, "workflow_method_id": jobs[mid]["method_id"],
                   "basis": "Explicit batch inventory job ID; extraction packet scope retained independently"}
        if mid not in method_ids:
            title = jobs[mid].get("title")
            if not title:
                raise ValueError(f"New method {mid} requires an explicit inventory title")
            binding["external_method"] = {"id": mid, "name": title}
        changed = mid in registry and (registry[mid] != str(runtime_run) or bindings[mid] != binding)
        if changed and mid not in replace_methods:
            raise ValueError(f"Method {mid} already has another accepted version; explicitly name it in replace_methods")
        if changed:
            replaced.append(mid)
        registry[mid], bindings[mid] = str(runtime_run), binding
    reviewed_indexes = list(dict.fromkeys([*inherited_indexes, *(Path(p).resolve() for p in indexes or [])]))
    if set(requested_receipts) - set(reviewed_indexes):
        raise ValueError("Comparison selection receipt needs an explicitly supplied matching index")
    for index, receipt in requested_receipts.items():
        if index in inherited_indexes and inherited_receipts.get(index) != receipt:
            raise ValueError("Cannot rebind a previously published comparison selection receipt")
    scoped_receipts = {**inherited_receipts, **requested_receipts}
    output.mkdir(parents=True, exist_ok=False)
    write(output / "extractions.json", registry)
    write(output / "bindings.json", bindings)
    write(output / "indexes.json", [({"index":str(path), "selection_receipt":str(scoped_receipts[path])}
                                      if path in scoped_receipts else str(path)) for path in reviewed_indexes])
    # Every inherited extraction and comparison is reopened by the existing gate.
    # No old graph node/edge is copied or a reviewed comparison rebased.
    graph = build_graph(atlas_path, output / "bindings.json", reviewed_indexes,
                        extractions_path=output / "extractions.json", comparison_receipts=scoped_receipts)
    graph["input_files"] = merge_pins(graph["input_files"], source_pins)
    graph["revision_id"] = digest({k:v for k,v in graph.items() if k != "revision_id"})
    check_pins(prior_pins)
    input_pins = {**prior_pins, **graph["input_files"]}
    if any(path in prior_pins and prior_pins[path] != value for path,value in graph["input_files"].items()):
        raise ValueError("Publication replaced a pinned prior input in place")
    check_pins(input_pins)
    # No graph is exposed when a scientific or pin validation above fails. The
    # receipt is the commit marker, written last after the graph's atomic write.
    write(output / "graph.json", graph)
    write(output / "publication.json", {
        "format_version": PUBLICATION,
        "batch_run": str(run), "batch_plan_sha256": sha(run / "plan.json"),
        "batch_state_sha256": sha(run / "state.json"),
        "graph_sha256": sha(output / "graph.json"), "revision_id": graph["revision_id"],
        "accepted_methods": sorted(registry), "new_batch_methods": sorted(parents),
        "retained_methods": sorted(retained), "replaced_methods": sorted(replaced),
        "replacement_policy": "Latest explicitly selected completed run per method in the registry; earlier immutable publications and reviewed index versions remain unchanged",
        "previous_publication": ({"directory":str(previous), "revision_id":prior_receipt["revision_id"],
                                  "publication_sha256":prior_pins[str(previous / "publication.json")]} if prior_receipt else None),
        "files": {name:sha(output / name) for name in sorted(ARTIFACTS)},
        "input_files": input_pins, "statistics": graph["statistics"],
        "source_files": source_pins,
        "source_exclusions": unique_exclusions(source_exclusions),
        "source_execution_metadata": {item["owner"]:item["owner_sha256"] for item in source_exclusions},
        "boundary": "Source-reviewed workflow admission; no inferred historical ancestry, "
                    "interchangeability, or automatic scientific acceptance of similar modules."})
    return graph


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--atlas", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--index", type=Path, action="append", default=[])
    parser.add_argument("--comparison-receipt", nargs=2, action="append", default=[],
                        metavar=("INDEX", "SELECTION_RECEIPT"),
                        help="Prepared exact identity receipt scoped to one reviewed matching index")
    parser.add_argument("--previous", type=Path, help="Prior immutable publication whose accepted inputs are retained")
    parser.add_argument("--recover-legacy-sources", action="store_true",
                        help="Explicitly verify legacy publication source receipts from recorded plan/manifest hashes")
    parser.add_argument("--execution-metadata", action="append", type=Path, default=[],
                        help="Frozen harness-release metadata proving exact legacy execution-source exclusions")
    parser.add_argument("--replace-method", action="append", default=[],
                        help="Explicitly select a new completed extraction version for this method ID")
    args = parser.parse_args()
    result = publish(args.run, args.atlas, args.output, indexes=args.index,
                     previous=args.previous, replace_methods=set(args.replace_method),
                     comparison_receipts=cli_receipts(args.comparison_receipt),
                     recover_legacy_sources=args.recover_legacy_sources, execution_metadata=args.execution_metadata)
    print(json.dumps({"revision_id": result["revision_id"], "statistics": result["statistics"],
                      "graph": str(args.output / "graph.json")}))


if __name__ == "__main__":
    main()
