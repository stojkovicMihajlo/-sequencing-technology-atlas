"""Deterministic atlas-access receipts; delivery is not understanding or causation.

Read exact host tool logs. Legacy logs record completion before returning a large
result pointer, and cannot establish delivery or a literal search match. Never
retroactively promote those rows to evidence successfully read.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _ranges(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _atlas_references(value, path=""):
    if isinstance(value, dict):
        if {"record_id", "node_id", "extraction_sha256"} <= value.keys():
            yield {"path": path, **{k: value[k] for k in ("record_id", "node_id", "extraction_sha256")}}
        for key, child in value.items():
            yield from _atlas_references(child, path + "/" + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _atlas_references(child, path + "/" + str(index))


def usage_from_logs(logs, *, candidates=None):
    """Use {log-name: exact JSONL text}, optionally {candidate-name: JSON object}."""
    reports, delivered_nodes = [], set()
    for name, text in sorted(logs.items()):
        events = []
        for line, raw in enumerate(text.splitlines(), 1):
            if not raw.strip():
                continue
            row = json.loads(raw)
            if not isinstance(row, dict) or "action" not in row or "status" not in row:
                raise ValueError(f"Malformed tool audit row: {name}:{line}")
            events.append({**row, "line": line})
        # Coverage is per log/session. One expert reading a result does not mean
        # a different expert received it, even when hashes happen to match.
        origins = {row["result_sha256"]: row for row in events
                   if row.get("result_sha256") and row["action"] != "result-read"}
        coverage = {}
        for row in events:
            if row["action"] != "result-read" or row["status"] != "complete":
                continue
            args, meta = row.get("arguments", {}), row.get("usage", {})
            ident = args["result_id"]
            item = coverage.setdefault(ident, {"result_id": ident, "delivered_ranges": [], "legacy_requested_ranges": []})
            if row.get("delivery") == "inline" and meta.get("kind") == "result_window":
                item["delivered_ranges"].append([meta["start"], meta["end"]])
            elif "delivery" not in row:
                item["legacy_requested_ranges"].append([args["start"], args["start"] + args["limit"]])
        for ident, item in coverage.items():
            origin = origins.get(ident, {})
            total = origin.get("characters")
            item["origin_action"] = origin.get("action")
            item["origin_arguments"] = origin.get("arguments", {})
            item["total_characters"] = total
            for key in ("delivered_ranges", "legacy_requested_ranges"):
                item[key] = _ranges(item[key])
            item["complete_content_delivered"] = bool(total is not None and item["delivered_ranges"] == [[0, total]])
            item["delivered_characters"] = sum(end - start for start, end in item["delivered_ranges"])

        content, navigation, errors, legacy, unavailable = [], [], [], [], []
        for row in events:
            base = {k: row[k] for k in ("line", "action", "arguments", "result_sha256") if k in row}
            if row["status"] != "complete":
                errors.append({**base, "status": row["status"], "error": row.get("error")})
                continue
            if "delivery" not in row:
                legacy.append({**base, "characters": row.get("characters"), "delivery": "unknown_legacy_log"})
                continue
            meta = row.get("usage", {})
            if meta.get("result_state") not in {None, "available"}:
                unavailable.append({**base, "result_state": meta["result_state"],
                                    "returned_text_characters": meta.get("returned_text_characters")})
            delivered = row["delivery"] == "inline" or coverage.get(row.get("result_sha256"), {}).get("complete_content_delivered", False)
            if not delivered:
                continue
            item = {**base, "delivery": "inline" if row["delivery"] == "inline" else "complete_pagination", **meta}
            if meta.get("kind") == "navigation":
                navigation.append(item)
            elif meta.get("kind") in {"workflow_content", "historical_claim_content", "source_excerpt"}:
                if meta.get("result_state") not in {None, "available"}:
                    continue
                content.append(item)
                binding = meta.get("binding", {})
                delivered_nodes.update((binding.get("record_id"), ident, binding.get("extraction_sha256"))
                                       for ident in meta.get("node_ids", []))
        reports.append({"log": name, "sha256": _sha(text.encode()), "event_count": len(events),
            "action_counts": dict(sorted(Counter(row["action"] for row in events).items())),
            "errors": errors, "navigation_delivered": navigation, "content_delivered": content,
            "unavailable_or_no_match": unavailable, "legacy_delivery_unknown": legacy,
            "pagination": [coverage[key] for key in sorted(coverage)],
            "historical_claim_ids_delivered": sorted({ident for item in content for ident in item.get("historical_claim_ids", [])})})
    citations = []
    for name, candidate in sorted((candidates or {}).items()):
        refs = list(_atlas_references(candidate))
        for ref in refs:
            ref["exact_node_delivery_observed_any_session"] = (ref["record_id"], ref["node_id"], ref["extraction_sha256"]) in delivered_nodes
        citations.append({"candidate": name, "declared_atlas_references": refs,
                          "unique_node_ids": sorted({ref["node_id"] for ref in refs})})
    return {"format_version": "ideation_atlas_usage/1", "logs": reports, "candidate_citations": citations,
        "boundary": "Host tool-log delivery only, not proof of reading, comprehension, scientific support or causal benefit. "
                    "Navigation is not claim evidence. Full pagination is character coverage, not understanding. "
                    "Candidate references are declarations, not validated entailment. A matching delivery may belong to another team session; "
                    "a missing exact-node match can reflect workflow access, other channels or incomplete logs. "
                    "Legacy completion records cannot certify returned content, no-match status or delivery."}


def build_usage_receipt(run_dir, candidate_paths=()):
    """Read a run's attempts; never mutate it or infer success from file presence."""
    root = Path(run_dir)
    logs = {str(path.relative_to(root)): path.read_text() for path in sorted((root / "attempts").glob("*/tool_calls.jsonl"))}
    paths = [Path(path) for path in candidate_paths]
    candidates = {str(path): json.loads(path.read_text()) for path in paths}
    receipt = usage_from_logs(logs, candidates=candidates)
    receipt["candidate_artifacts"] = [{"path": str(path), "sha256": _sha(path.read_bytes())} for path in paths]
    receipt["log_scope"] = "Only existing attempts/*/tool_calls.jsonl in this run; absent logs are not zero usage."
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--candidate", action="append", default=[], type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(build_usage_receipt(args.run, args.candidate), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
