"""Preserve explicitly declared acquisition lineage without executing conversions.

This adapter handles the existing acquisition receipt, including compilations
and identity copies. Hash checks establish custody of the declared process's
inputs and outputs, not that its description is true or that it was replayed.
"""

import os
from pathlib import Path
import re


FORMAT = "primary_source_acquisition/1.0"
BOUNDARY = ("Declared conversion/copy/compilation lineage; all listed bytes are hash-checked. "
            "Commands are not executed. This is not conversion replay, publication identity, "
            "visual equivalence or scientific completeness.")


def _file_map(row, *, outputs=False):
    """Normalize the two historical output spellings without silently choosing."""
    path_keys = ("output", "outputs") if outputs else ("inputs",)
    hash_keys = ("output_hashes", "outputs_hashes") if outputs else ("inputs_hashes",)
    present_paths = [key for key in path_keys if key in row]
    present_hashes = [key for key in hash_keys if key in row]
    if len(present_paths) != 1 or len(present_hashes) != 1:
        raise ValueError("Derivation needs one explicit path list and hash map")
    paths = row[present_paths[0]]
    paths = [paths] if present_paths[0] == "output" else paths
    hashes = row[present_hashes[0]]
    if (not isinstance(paths, list) or not paths
            or any(not isinstance(path, str) or not path.strip() for path in paths)
            or len(set(paths)) != len(paths) or not isinstance(hashes, dict)
            or set(paths) != set(hashes)):
        raise ValueError("Derivation path and hash-map sets must match exactly")
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
           for value in hashes.values()):
        raise ValueError("Derivation has an invalid SHA256 pin")
    return {path: hashes[path] for path in paths}


def preserve_derivations(declarations, selected_sources, *, checked, pin, read_json):
    """Return selected process records and covered packet sources.

    The archiver owns safe path checks, pin conflicts and immutable-byte custody.
    Unrelated row inputs are never opened; only exact selected output paths and
    their explicitly declared upstream dependencies select process records.
    """
    records, output_index = {}, {}

    def path_key(value, base):
        if not isinstance(value, str) or not value.strip() or ".." in Path(value).parts:
            raise ValueError("Derivation paths must be nonempty and not traverse parents")
        return Path(os.path.abspath(base / value))

    for ledger_value, ledger_sha in declarations:
        ledger = checked(ledger_value)
        pin(ledger, ledger_sha, "declared_derivation_ledger")
        value = read_json(ledger)
        if (not isinstance(value, dict) or value.get("format_version") != FORMAT
                or not isinstance(value.get("derivations"), list)):
            raise ValueError("Unsupported acquisition derivation ledger")
        for index, row in enumerate(value["derivations"]):
            if not isinstance(row, dict) or not isinstance(row.get("operation"), str) or not row["operation"].strip():
                raise ValueError("Derivation needs an explicit process description")
            input_map, output_map = _file_map(row), _file_map(row, outputs=True)
            inputs = {path_key(path, ledger.parent): expected for path, expected in input_map.items()}
            outputs = {path_key(path, ledger.parent): expected for path, expected in output_map.items()}
            if len(inputs) != len(input_map) or len(outputs) != len(output_map):
                raise ValueError("Derivation contains aliased file paths")
            key = (ledger, index)
            record = {"ledger": ledger, "ledger_sha256": ledger_sha, "derivation_index": index,
                      "operation": row["operation"], "inputs": inputs, "outputs": outputs,
                      "script": value.get("reproducible_derivation_script")}
            if key in records and records[key] != record:
                raise ValueError("Conflicting derivation record")
            records[key] = record
            for path in outputs:
                if path in output_index and output_index[path] != key:
                    raise ValueError("Ambiguous derivations for the same output path")
                output_index[path] = key

    preserved, visited, active = [], set(), set()

    def visit(key):
        if key in active:
            raise ValueError("Cyclic source derivation lineage")
        if key in visited:
            return
        row = records[key]
        if set(row["inputs"]) & set(row["outputs"]):
            raise ValueError("A derivation cannot use its own output as an input")
        script = row["script"]
        if not isinstance(script, dict) or not isinstance(script.get("path"), str):
            raise ValueError("Selected derivation needs its pinned derivation script")
        script_path = path_key(script["path"], row["ledger"].parent)
        if script_path in row["outputs"]:
            raise ValueError("A derivation cannot produce its own derivation script")
        active.add(key)
        for role in ("inputs", "outputs"):
            for path, expected in row[role].items():
                pin(checked(path), expected, f"derivation_{role}:{row['ledger']}:{row['derivation_index']}")
        pin(checked(script_path), script.get("sha256"), f"derivation_script:{row['ledger']}")
        for path, expected in [*row["inputs"].items(), (script_path, script.get("sha256"))]:
            if path in output_index:
                upstream = output_index[path]
                if records[upstream]["outputs"][path] != expected:
                    raise ValueError("Upstream derivation output pin disagrees with its consumer")
                visit(upstream)
        active.remove(key)
        visited.add(key)
        preserved.append({"ledger": str(row["ledger"]), "ledger_sha256": row["ledger_sha256"],
            "derivation_index": row["derivation_index"], "operation": row["operation"],
            **{role: [{"path": str(path), "sha256": expected} for path, expected in row[role].items()]
               for role in ("inputs", "outputs")},
            "derivation_script": {"path": str(script_path), "sha256": script["sha256"]},
            "selected_packet_sources": [str(path) for path in row["outputs"] if path in selected_sources],
            "boundary": BOUNDARY})

    covered = set()
    for path, expected in selected_sources.items():
        if path not in output_index:
            continue
        key = output_index[path]
        if records[key]["outputs"][path] != expected:
            raise ValueError("Derivation output pin disagrees with the selected packet source")
        visit(key)
        covered.add(path)
    return preserved, covered
