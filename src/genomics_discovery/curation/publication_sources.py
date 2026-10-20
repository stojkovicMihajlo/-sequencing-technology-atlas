"""Recover only receipt-declared source inputs from immutable batch plans.

Execution implementation pins are intentionally not copied: source provenance
must survive later runtime upgrades. Legacy publications require explicit recovery
from their recorded plan hash; absent or changed records are never reconstructed.
"""

import json
from pathlib import Path
import re

from .extraction_batch.storage import check_pins, sha


def merge_pins(*groups):
    merged = {}
    for group in groups:
        for path, expected in group.items():
            if (not isinstance(path, str) or not Path(path).is_absolute()
                    or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)):
                raise ValueError("Invalid publication source path/SHA256")
            if path in merged and merged[path] != expected:
                raise ValueError("Conflicting publication source pin: " + path)
            merged[path] = expected
    return merged


def execution_ownership(metadata):
    """Only exact source entries in an explicitly supplied frozen harness release."""
    owned = {}
    for filename, expected in merge_pins(metadata or {}).items():
        check_pins({filename: expected})
        record = json.loads(Path(filename).read_bytes())
        if (record.get("format_version") != "isolated_harness_release/1.0"
                or record.get("status") != "frozen_for_live_validation"
                or not isinstance(record.get("base_files"), dict)
                or not Path(record.get("source_repository", "")).is_absolute()):
            raise ValueError("Execution exclusion requires exact frozen harness-release ownership")
        root = Path(record["source_repository"]).resolve()
        for relative, value in record["base_files"].items():
            path = Path(relative)
            if (path.is_absolute() or ".." in path.parts
                    or path.parts[:2] != ("src", "genomics_discovery") or path.suffix != ".py"):
                continue  # Examples, documentation and paper records never qualify.
            source = str(root / path)
            merge_pins({source: value})
            proof = {"path": source, "sha256": value, "owner": filename,
                     "owner_sha256": expected, "owner_pointer": "/base_files/" + relative.replace("~", "~0").replace("/", "~1"),
                     "rationale": "Exact execution-source entry in the frozen harness release; retained receipt bytes record its historical hash, while current runtime bytes are not a scientific source dependency."}
            if source in owned and owned[source]["sha256"] != value:
                raise ValueError("Ambiguous frozen execution ownership: " + source)
            owned[source] = proof
    return owned


def batch_source_pins(run, expected_plan_sha256, methods, *, execution_metadata=None, exclusions=None):
    """Verify the plan/manifest/receipt association, without loading runtime code pins."""
    if not Path(run).is_absolute():
        raise ValueError("Source recovery requires the exact absolute batch path")
    plan_path = Path(run).resolve() / "plan.json"
    if sha(plan_path) != expected_plan_sha256:
        raise ValueError("Publication batch plan differs from its recorded hash")
    plan = json.loads(plan_path.read_bytes())
    if plan.get("format_version") != "extraction_batch_plan/1.0":
        raise ValueError("Publication needs a versioned source batch plan")
    global_pins = merge_pins(plan["pins"])
    if not Path(plan["manifest"]).is_absolute():
        raise ValueError("Source recovery requires the exact absolute manifest path")
    manifest_path = Path(plan["manifest"]).resolve()

    def bound(path, recorded):
        if not Path(path).is_absolute():
            raise ValueError("Source recovery requires exact absolute input paths")
        path = str(Path(path).resolve())
        if path not in recorded:
            raise ValueError("Source receipt/input is absent from the frozen batch pins: " + path)
        return {path: recorded[path]}

    pins = merge_pins({str(plan_path): expected_plan_sha256}, bound(manifest_path, global_pins))
    check_pins(pins)
    manifest = json.loads(manifest_path.read_bytes())
    receipt_name = manifest.get("source_receipts")
    owned = execution_ownership(execution_metadata)
    if receipt_name is not None:
        if not isinstance(receipt_name, str) or not receipt_name:
            raise ValueError("Source receipt needs an explicit path in the bound manifest")
        receipt = (manifest_path.parent / receipt_name).resolve()
        pins = merge_pins(pins, bound(receipt, global_pins))
        check_pins(pins)
        files = json.loads(receipt.read_bytes()).get("files")
        if not isinstance(files, dict):
            raise ValueError("Source receipt requires a files mapping")
        declared = {}
        for filename, expected in files.items():
            if not isinstance(filename, str) or not filename:
                raise ValueError("Invalid source receipt filename")
            path = str((receipt.parent / filename).resolve())
            merge_pins({path: expected})
            if global_pins.get(path) != expected:
                raise ValueError("Source receipt differs from the frozen batch global pin: " + path)
            if path in owned:
                proof = owned[path]
                if proof["sha256"] != expected:
                    raise ValueError("Execution ownership hash differs from frozen receipt: " + path)
                if exclusions is not None:
                    exclusions.append(proof)
                pins = merge_pins(pins, {proof["owner"]: proof["owner_sha256"]})
            else:
                declared = merge_pins(declared, {path: expected})
        pins = merge_pins(pins, declared)
    if (not isinstance(methods, list) or not methods
            or any(not isinstance(m, str) or not m for m in methods)
            or len(set(methods)) != len(methods)):
        raise ValueError("Publication needs its exact nonempty batch method list")
    jobs = {job["id"]: job for job in plan["jobs"]}
    if len(jobs) != len(plan["jobs"]) or not set(methods) <= jobs.keys():
        raise ValueError("Publication methods differ from their bound batch plan")
    for method in methods:
        job = jobs[method]
        job_pins = merge_pins(job["pins"])
        for key in ("packet", "source_packet"):
            pins = merge_pins(pins, bound(job[key], job_pins))
        check_pins(pins)
        packet = json.loads(Path(job["source_packet"]).read_bytes())
        for source in packet["sources"]:
            pins = merge_pins(pins, bound(source["original_path"], job_pins))
    check_pins(pins)
    return pins


def publication_source_pins(directory, receipt, *, recover_legacy_sources=False,
                            execution_metadata=None, exclusions=None):
    """Bounded, cycle-safe source recovery across exact prior publication receipts."""
    if "source_files" not in receipt and not recover_legacy_sources:
        raise ValueError("Legacy publication lacks source pins; explicitly enable verified source receipt recovery")
    chain, seen = [], set()
    metadata = merge_pins(execution_metadata or {})
    for _ in range(128):
        directory = Path(directory).resolve()
        if directory in seen:
            raise ValueError("Publication source recovery contains a cycle")
        seen.add(directory)
        chain.append((directory, receipt))
        metadata = merge_pins(metadata, receipt.get("source_execution_metadata", {}))
        previous = receipt.get("previous_publication")
        if previous is None:
            break
        if not Path(previous["directory"]).is_absolute():
            raise ValueError("Source recovery requires exact absolute prior-publication paths")
        directory = Path(previous["directory"]).resolve()
        previous_path = directory / "publication.json"
        if sha(previous_path) != previous["publication_sha256"]:
            raise ValueError("Prior source-recovery publication receipt changed")
        receipt = json.loads(previous_path.read_bytes())
        if receipt.get("format_version") != "batch_graph_publication/1.0":
            raise ValueError("Unrecognized prior source-recovery publication")
    else:
        raise ValueError("Publication source recovery exceeds 128 receipts")
    recovered = {}
    omitted = []
    for directory, receipt in reversed(chain):
        recovered = merge_pins(recovered, batch_source_pins(
            receipt["batch_run"], receipt["batch_plan_sha256"], receipt["new_batch_methods"],
            execution_metadata=metadata, exclusions=omitted))
        if "source_files" in receipt:
            if merge_pins(receipt["source_files"]) != recovered:
                raise ValueError("Publication source pins omit or change the verified receipt chain")
            if any(receipt.get("input_files", {}).get(p) != h for p, h in recovered.items()):
                raise ValueError("Publication source chain is absent from input pins")
            if unique_exclusions(receipt.get("source_exclusions", [])) != unique_exclusions(omitted):
                raise ValueError("Publication execution exclusions differ from verified ownership")
        recovered = merge_pins(recovered, {str(directory / "publication.json"): sha(directory / "publication.json")})
    check_pins(recovered)
    if exclusions is not None:
        exclusions.extend(unique_exclusions(omitted))
    return recovered


def unique_exclusions(items):
    return [json.loads(item) for item in sorted({json.dumps(item, sort_keys=True) for item in items})]
