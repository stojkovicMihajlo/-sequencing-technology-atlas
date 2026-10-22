"""Host-only exact reconstruction through the existing bounded review accessor.

Run where registered originals exist. No validator is patched, no packet/path is
rewritten, and no scientific answer or finding ID is built into this audit.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import time
from pathlib import Path

from genomics_discovery.curation import workflow_coverage as access
from genomics_discovery.curation.workflow_extraction.models import ReadingReport, Workflow, digest
from genomics_discovery.curation.workflow_extraction.sources import load_packet


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(method_run: Path, packet_path: Path, progress: dict) -> dict:
    started = time.monotonic()
    progress.update(phase="loading_and_validating_inputs", archived_entries_checked=0,
                    bound_entries_completed=0, accessor_calls_started=0,
                    accessor_calls_completed=0, structured_nodes_completed=0)
    method_run, packet_path = method_run.resolve(), packet_path.resolve()
    state_path = method_run / "state.json"
    state_raw = state_path.read_bytes()
    state = json.loads(state_raw)
    row = next(item for item in state["attempts"]
               if item["role"] == "integrator" and item["status"] == "validated")
    directory = Path(row["directory"]).resolve()
    if not directory.is_relative_to(method_run):
        raise ValueError("Selected integrator is outside its declared method run")
    files = {name: directory / name for name in (
        "artifact.json", "coverage.json", "chemistry.json", "information.json", "finding_view.json")}
    files["packet"] = packet_path
    pins = {name: {"path": str(path), "sha256": sha(path)} for name, path in files.items()}
    if pins["artifact.json"]["sha256"] != row["artifact_sha256"]:
        raise ValueError("Candidate differs from the host completion checkpoint")
    if pins["coverage.json"]["sha256"] != row["coverage"]["sha256"]:
        raise ValueError("Coverage differs from the host completion checkpoint")
    for role in ("chemistry", "information"):
        if pins[role + ".json"]["sha256"] != row["inputs"][role]["sha256"]:
            raise ValueError(f"Reader {role} differs from the host input checkpoint")
    packet = load_packet(packet_path)  # Original/rendition checks remain intact.
    workflow = Workflow.model_validate_json(files["artifact.json"].read_bytes())
    coverage = access.IntegrationCoverage.model_validate_json(files["coverage.json"].read_bytes())
    readers = {role: ReadingReport.model_validate_json(files[role + ".json"].read_bytes())
               for role in ("chemistry", "information")}
    errors = access.validate_coverage(coverage, workflow, readers, packet)
    if errors:
        raise ValueError("Coverage failed original checks: " + "; ".join(errors))
    view = json.loads(files["finding_view.json"].read_bytes())
    if view["format_version"] != "coverage_finding_pages/1.0":
        raise ValueError("Unknown finding-view format")
    expected = {(entry.reader_role, entry.finding_id): {
        "reader_role": entry.reader_role, "finding_id": entry.finding_id,
        "original_finding": next(finding for finding in readers[entry.reader_role].findings
                                 if finding.id == entry.finding_id).model_dump(mode="json"),
        "proposed_dispositions": [part.model_dump(mode="json") for part in entry.dispositions],
    } for entry in coverage.entries}
    seen, references, inline = set(), [], 0
    page_chars = []
    for page_index, page in enumerate(view["pages"]):
        length = len(json.dumps(page, ensure_ascii=False))
        page_chars.append(length)
        if length > access.MAX_VIEW_CHARS:
            raise ValueError("Archived page exceeds the existing accessor budget")
        for key, value in {"source_packet_sha256": digest(packet),
                           "workflow_sha256": digest(workflow),
                           "coverage_sha256": digest(coverage),
                           "readers": [binding.model_dump(mode="json")
                                       for binding in coverage.readers]}.items():
            if page[key] != value:
                raise ValueError(f"Archived finding page has a stale {key}")
        for entry in page["entries"]:
            key = entry["reader_role"], entry["finding_id"]
            if key in seen or key not in expected:
                raise ValueError("Duplicate or unknown finding in archived pages")
            seen.add(key)
            progress["archived_entries_checked"] += 1
            if entry.get("content_mode") == "bound_reference":
                if digest(expected[key]) != entry["entry_sha256"]:
                    raise ValueError("Bound reference does not match the complete original entry")
                regenerated = access.coverage_review_view(
                    coverage, workflow, readers, packet, start=page["start"], limit=page["limit"])
                if regenerated != page:
                    raise ValueError("Bound-reference page differs from the existing view accessor")
                references.append((page_index, entry))
            else:
                if entry != expected[key]:
                    raise ValueError("Inline finding/proposals differ from the bound originals")
                inline += 1
    if seen != expected.keys():
        raise ValueError("Archived finding pages omit coverage entries")
    results = []
    progress["phase"] = "reconstructing_bound_entries"
    for page_index, locator in references:
        key = locator["reader_role"], locator["finding_id"]
        calls, max_chars, strings, node_counts = 0, 0, [], {}
        visited = set()

        def read(pointer, start):
            nonlocal calls, max_chars
            progress["accessor_calls_started"] += 1
            response = access.coverage_review_read(
                coverage, workflow, readers, packet, reader_role=key[0], finding_id=key[1],
                pointer=pointer, start=start, limit=access.MAX_VIEW_ENTRIES,
                chars=access.MAX_READ_CHARS)
            calls += 1
            progress["accessor_calls_completed"] += 1
            size = len(json.dumps(response, ensure_ascii=False))
            max_chars = max(max_chars, size)
            if size > access.MAX_VIEW_CHARS or response["entry_sha256"] != locator["entry_sha256"]:
                raise ValueError("Read exceeded its budget or changed entry binding")
            return response

        def reconstruct(pointer=""):
            if pointer in visited:
                raise ValueError("A structured node locator was visited twice")
            visited.add(pointer)
            response = read(pointer, 0)
            kind, original_hash = response["kind"], response["value_sha256"]
            node_counts[kind] = node_counts.get(kind, 0) + 1
            if kind == "scalar":
                value = response["value"]
            else:
                parts, windows, expected_start = [], 0, 0
                while True:
                    if (response["kind"] != kind or response["value_sha256"] != original_hash
                            or response["start"] != expected_start):
                        raise ValueError("Read continuation changed its field, type or offset")
                    windows += 1
                    parts.extend([response["text"]] if kind == "text" else response["children"])
                    next_start = response["next_start"]
                    if next_start is None:
                        break
                    if next_start <= expected_start:
                        raise ValueError("Read continuation did not advance")
                    expected_start = next_start
                    response = read(pointer, next_start)
                if kind == "text":
                    value = "".join(parts)
                    if len(value) != response["total_characters"]:
                        raise ValueError("Text reconstruction lost characters")
                    strings.append([pointer, len(value), hashlib.sha256(value.encode()).hexdigest(), windows])
                else:
                    if len(parts) != response["total_children"]:
                        raise ValueError("Container reconstruction lost children")
                    names = [child["pointer"] for child in parts]
                    if (len(names) != len(set(names))
                            or any(not name.startswith(pointer + "/") for name in names)):
                        raise ValueError("Repeated child locator")
                    children = [reconstruct(child["pointer"]) for child in parts]
                    if kind == "array":
                        if names != [pointer + "/" + str(index) for index in range(len(parts))]:
                            raise ValueError("Array child order changed")
                        value = children
                    else:
                        value = {name.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~"): child
                                 for name, child in zip(names, children, strict=True)}
            if digest({"value": value}) != original_hash:
                raise ValueError("Reconstructed descendant differs from its accessor hash")
            progress["structured_nodes_completed"] += 1
            return value

        rebuilt = reconstruct()
        if rebuilt != expected[key] or digest(rebuilt) != locator["entry_sha256"]:
            raise ValueError("Complete reconstruction differs from original finding/proposals")
        progress["bound_entries_completed"] += 1
        results.append({"reader_role": key[0], "finding_id": key[1], "archived_page_index": page_index,
                        "full_entry_sha256": digest(rebuilt), "exact_structured_equality": True,
                        "original_finding_sha256": digest(rebuilt["original_finding"]),
                        "proposed_dispositions_sha256": digest(rebuilt["proposed_dispositions"]),
                        "dispositions": len(rebuilt["proposed_dispositions"]),
                        "structured_nodes_reconstructed": node_counts, "accessor_calls": calls,
                        "unique_pointer_count": len(visited),
                        "maximum_response_characters": max_chars,
                        "string_columns": ["pointer", "characters", "sha256_utf8", "text_windows"],
                        "strings": strings})
    load_packet(packet_path)
    if any(sha(files[name]) != pin["sha256"] for name, pin in pins.items()):
        raise ValueError("Pinned audit input changed during reconstruction")
    implementation = Path(inspect.getsourcefile(access.coverage_review_read)).resolve()
    progress["phase"] = "complete"
    return {"format_version": "bounded_access_reconstruction_audit/1.0", "status": "passed",
            "scope": "Host-only exact accessor/content reconstruction; not scientific correctness, proof of native reviewer reads or a publication decision.",
            "state_snapshot_sha256": hashlib.sha256(state_raw).hexdigest(),
            "inputs": pins, "workflow_sha256": digest(workflow), "coverage_sha256": digest(coverage),
            "source_packet_sha256": digest(packet),
            "implementation": {"path": str(implementation), "sha256": sha(implementation)},
            "registered_original_and_coverage_validation": "passed_with_existing_unmodified_checks",
            "archived_pages": len(view["pages"]), "coverage_entries": len(expected),
            "inline_entries_compared_exactly": inline, "bound_reference_entries": len(references),
            "maximum_archived_page_characters": max(page_chars, default=0),
            "view_budget_characters": access.MAX_VIEW_CHARS,
            "accessor_calls": sum(row["accessor_calls"] for row in results),
            "elapsed_seconds": time.monotonic() - started, "progress": progress, "entries": results,
            "limits": ["No validator replacement, packet rewriting, model call or runtime change.",
                       "Exact readable content does not show that a model read or understood every field.",
                       "Complete current registered originals do not independently establish upstream binary-ancestor custody.",
                       "Method state is a selection snapshot; unrelated ongoing stages need not remain frozen."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method_run", type=Path)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--output", type=Path, required=True,
                        help="New host report outside the native method run")
    args = parser.parse_args()
    if args.output and args.output.resolve().is_relative_to(args.method_run.resolve()):
        parser.error("Host output must be outside the native method run")
    if args.output.exists():
        parser.error("Host output already exists; choose a new report path")
    progress, started = {}, time.monotonic()
    try:
        report = audit(args.method_run, args.packet, progress)
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        report = {"format_version": "bounded_access_reconstruction_audit/1.0", "status": "failed",
                  "error": f"{type(exc).__name__}: {exc}", "progress": progress,
                  "elapsed_seconds": time.monotonic() - started}
    content = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(content)
    print(json.dumps({"status": report["status"], "output": str(args.output),
                      "output_sha256": sha(args.output), "progress": progress,
                      "elapsed_seconds": report["elapsed_seconds"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
