"""Per-reviewed-index reuse of prepared, exact atlas identity bindings.

Aliases such as left/right are local to one matching run. This module never
infers identity from titles or changes a matching artifact or global binding.
"""
import hashlib
import json
from pathlib import Path
import re

from ..workflow_extraction.models import digest
from ..workflow_matching.models import Selection
from ..workflow_matching.tools import load_context


def receipt_paths(values):
    result = {}
    for index, receipt in (values or {}).items():
        index, receipt = Path(index).resolve(), Path(receipt).resolve()
        if index in result and result[index] != receipt:
            raise ValueError("One comparison index cannot have conflicting selection receipts")
        result[index] = receipt
    return result


def cli_receipts(pairs):
    result = {}
    for index, receipt in pairs:
        index, receipt = Path(index).resolve(), Path(receipt).resolve()
        if index in result and result[index] != receipt:
            raise ValueError("One comparison index cannot have conflicting selection receipts")
        result[index] = receipt
    return result


def resolve_bindings(receipt_path, loaded):
    """Return aliases only after graph, seed and reviewed-parent identity replay."""
    # Local import avoids the queries -> assembly -> binding-reader import cycle.
    from .queries import load_graph
    receipt_path = Path(receipt_path).resolve()
    raw = receipt_path.read_bytes(); receipt = json.loads(raw)
    pins = {str(receipt_path): hashlib.sha256(raw).hexdigest()}

    def pin(path, expected):
        path = Path(path).resolve()
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid comparison preparation hash pin")
        if (str(path) in pins and pins[str(path)] != expected
                or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError("Prepared comparison input changed or has conflicting pins")
        pins[str(path)] = expected

    if receipt.get("format_version") != "graph_comparison_job/1.0":
        raise ValueError("Expected an exact prepare-comparison selection receipt")
    graph_path = Path(receipt["graph"]).resolve();pin(graph_path, receipt["graph_sha256"])
    graph = load_graph(graph_path)
    if graph["revision_id"] != receipt["graph_revision_id"]:
        raise ValueError("Prepared comparison source graph revision differs")
    for path, expected in graph["input_files"].items():pin(path, expected)
    if not isinstance(receipt.get("input_files"), dict) or not receipt["input_files"]:
        raise ValueError("Prepared comparison needs original input pins")
    for path, expected in receipt["input_files"].items():pin(path, expected)
    parents_path, manifest_path = receipt_path.parent / "parents.json", receipt_path.parent / "manifest.json"
    prepared = load_context(parents_path, manifest_path)
    cases_path = receipt_path.parent / "cases.json"
    if not {str(p.resolve()) for p in (*prepared.files, cases_path)} <= pins.keys():
        raise ValueError("Selection receipt omitted a prepared parent/seed input pin")
    context, _, _, _, _, cases = loaded
    if (set(context.parents) != {"left", "right"} or set(prepared.parents) != set(context.parents)
            or set(receipt.get("identity_bindings", {})) != set(context.parents)
            or set(receipt.get("selections", {})) != set(context.parents)):
        raise ValueError("Prepared comparison aliases differ from reviewed parents")
    if (json.loads(cases_path.read_bytes()) != [receipt["case"]]
            or [case.model_dump(mode="json") for case in cases] != [receipt["case"]]):
        raise ValueError("Reviewed comparison case differs from prepared request")
    bindings = {}
    for alias, row in context.parents.items():
        selection = receipt["selections"][alias]
        source = graph["workflows"].get(selection["workflow_id"])
        if source is None:
            raise ValueError("Prepared comparison names an unknown source workflow")
        binding = receipt["identity_bindings"][alias]
        if (binding != source["identity_binding"]
                or binding.get("atlas_method_id") != source["atlas_method_id"]):
            raise ValueError("Prepared atlas identity binding was changed or rebound")
        expected = (source["workflow_sha256"], source["packet_sha256"], source["review_sha256"])
        for parent in (prepared.parents[alias], row):
            actual = tuple(digest(parent[key]) if parent[key] is not None else None
                           for key in ("workflow", "packet", "review"))
            if actual != expected:
                raise ValueError("Prepared identity cannot bind a different reviewed parent workflow/packet/review")
        seed = prepared.cut(Selection(parent_id=alias, operation_ids=selection["operation_ids"]))
        if digest(seed) != selection["cut_sha256"] or seed.id not in prepared.occurrences:
            raise ValueError("Prepared comparison seed differs from selected frozen workflow")
        bindings[alias] = binding
    if receipt["selections"]["left"]["workflow_id"] == receipt["selections"]["right"]["workflow_id"]:
        raise ValueError("Cross-protocol comparison needs distinct frozen workflows")
    return bindings, pins
