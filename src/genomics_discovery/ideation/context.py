"""Bounded, audited access to a pinned atlas and captured literature passages.

Atlas annotations and imported text remain evidence to inspect, never proof that
a proposed transfer works. Lexical retrieval is a shortlist, not equivalence.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from genomics_discovery.illumina.graph import get_record, operation_group, workflow
from genomics_discovery.illumina.history import build_history_index, technology_history
from genomics_discovery.illumina.relationships import HISTORICAL_RELATIONS


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _read_pinned(path, expected):
    raw = Path(path).read_bytes()
    if _sha(raw) != expected:
        raise ValueError("Pinned input changed: " + str(path))
    return json.loads(raw)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in {"evidence", "source_label", "source_registry", "provenance"}:
                yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _compact(value):
    """Index repeated evidence exactly; omit only registered full-document bodies."""
    evidence = {}

    def visit(item, registry=False):
        if isinstance(item, list):
            return [visit(x, registry) for x in item]
        if not isinstance(item, dict):
            return item
        if "source_id" in item and ("quote" in item or "bbox" in item):
            key = _sha(_json(item).encode())
            evidence[key] = copy.deepcopy(item)
            return {"evidence_id": key}
        return {k: visit(v, registry or k in {"provenance", "source_registry"})
                for k, v in item.items()
                if not (registry and k in {"text", "full_text", "body", "content"})}

    result = visit(value)
    return {"data": result, "evidence_index": evidence,
            "reading_note": "Resolve evidence_id in evidence_index. Scientific records and caveats are unchanged; full document bodies are not included."}


def _usage(action, arguments, value):
    """Delivery metadata only; never a claim that the model understood a result."""
    metadata = {"kind": "other", "record_ids": [], "node_ids": [], "historical_claim_ids": [],
                "result_state": value.get("status", "available")}
    if action in {"workflow", "node", "group"}:
        binding = value["binding"]
        metadata.update(kind="workflow_content", record_ids=[binding["record_id"]], binding=binding)
        if action == "node":
            metadata["node_ids"] = [arguments["node_id"]]
        elif action == "group":
            metadata["node_ids"] = arguments["operation_ids"]
    elif action in {"history", "claim"}:
        metadata["kind"] = "historical_claim_content"
        edges = value.get("historical_contributions", []) if action == "history" else [value]
        metadata["historical_claim_ids"] = [edge["id"] for edge in edges if edge.get("relation") in HISTORICAL_RELATIONS]
        if action == "history":
            metadata["record_ids"] = [arguments["record_id"]]
    elif action in {"search", "list", "history-list", "change-context"}:
        metadata["kind"] = "navigation"
        if action == "search":
            metadata["node_ids"] = [row["node_id"] for row in value.get("matches", [])]
            metadata["record_ids"] = sorted({row["record_id"] for row in value.get("matches", [])})
        elif action == "list":
            metadata["record_ids"] = [row["record_id"] for row in value["methods"]]
        elif action == "history-list":
            metadata["navigated_claim_ids"] = [row["claim_id"] for row in value.get("relationships", [])]
        else:
            metadata["navigated_claim_ids"] = [value["relationship"]["id"]]
    elif action in {"source-read", "atlas-source"}:
        metadata["kind"] = "source_excerpt" if "text" in value else "source_metadata"
        metadata["returned_text_characters"] = len(value.get("text", ""))
        metadata.update({key: value[key] for key in ("source_id", "content_sha256", "source_registry_sha256", "start", "end") if key in value})
    elif action == "result-read":
        metadata.update(kind="result_window", result_id=value["result_id"],
                        start=value["start"], end=value["end"], total_characters=value["total_characters"])
    return metadata


def _focus_sources(value, record_id):
    """Keep scientific content exact; do not repeat a whole paper bibliography."""
    referenced = set()

    def collect(item):
        if isinstance(item, dict):
            if "source_id" in item:
                referenced.add(item["source_id"])
            for key, child in item.items():
                if key not in {"provenance", "source_registry"}:
                    collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)

    collect(value)
    registry = value.get("provenance", {})
    fields = {"title", "url", "pmid", "kind", "content_level", "path", "sha256",
              "identity_status", "association_status", "fulltext_status"}
    return {**value,
        "provenance": {key: {field: copy.deepcopy(item) for field, item in row.items() if field in fields}
                       for key, row in registry.items() if key in referenced},
        "source_metadata_access": {"view": "cited_source_metadata",
            "referenced_source_ids": sorted(referenced),
            "missing_from_this_registry": sorted(referenced - registry.keys()),
            "full_registry_source_count": len(registry),
            "read_commands": [["atlas-source", record_id], ["workflow", record_id]],
            "boundary": "Scientific records, quotations and qualifications are unchanged. Only cited-source metadata is shown; "
                        "full workflow provenance and its pinned registry remain available through the listed tools and full_source_registry_artifact."}}


class AtlasContext:
    def __init__(self, atlas_path, *, expected_sha256, history_manifest=None,
                 history_sha256=None, sources_path=None, audit_log=None,
                 max_result_chars=24000):
        self.path, self.sha256 = Path(atlas_path).resolve(), expected_sha256
        self.graph = _read_pinned(self.path, expected_sha256)
        if not isinstance(self.graph.get("cards"), dict) or not isinstance(self.graph.get("nodes"), list):
            raise ValueError("Expected an Illumina atlas with cards and nodes")
        self.cards = {k: v for k, v in self.graph["cards"].items()
                      if v.get("status") == "supported" and v.get("admission") == "supported"}
        self.nodes = {n["id"]: n for n in self.graph["nodes"] if n.get("record_id") in self.cards
                      and n.get("admission") == "supported"}
        if len({n["id"] for n in self.graph["nodes"]}) != len(self.graph["nodes"]):
            raise ValueError("Duplicate atlas node IDs")
        self.sources_path = Path(sources_path).resolve() if sources_path else None
        self.audit_log = Path(audit_log).resolve() if audit_log else None
        self.max_result_chars = max_result_chars
        if not 1000 <= max_result_chars <= 100000:
            raise ValueError("Result budget must be 1000–100000 characters")
        self.history_index = None
        self.history_manifest = Path(history_manifest) if history_manifest else None
        self.history_sha256 = history_sha256
        if history_manifest:
            if not history_sha256:
                raise ValueError("A history manifest requires its expected hash")
            _read_pinned(history_manifest, history_sha256)

    def _history(self):
        if self.history_index is None and self.history_manifest:
            _read_pinned(self.history_manifest, self.history_sha256)
            self.history_index = build_history_index(self.history_manifest)
        return self.history_index

    def validate_history_inputs(self):
        """Exercise all pinned overlays and registries before native execution."""
        index = self._history()
        return {"status": "not_loaded" if index is None else "passed",
                "indexed_relationships": 0 if index is None else len(index["edges"]),
                "boundary": "Input readability/integrity only; not fresh scientific review."}

    def _card(self, record_id):
        if record_id not in self.cards:
            raise ValueError("Unknown or unsupported workflow: " + record_id)
        return self.cards[record_id]

    def _pin(self, record_id):
        card = self._card(record_id)
        return {"record_id": record_id, "atlas_sha256": self.sha256,
                "extraction_sha256": card.get("artifact_pins", {}).get("extraction.json"),
                "candidate_sha256": card.get("candidate_sha256"),
                "review_sha256": card.get("review_sha256")}

    def _result(self, action, arguments, value):
        result = {"atlas_sha256": self.sha256, **_compact(value)}
        raw = _json(result).encode()
        oversized = len(raw.decode()) > self.max_result_chars
        root = self.audit_log.parent if self.audit_log else self.sources_path.parent if self.sources_path else None
        if oversized:
            if root is None:
                raise ValueError("Oversized context requires --audit-log or --sources for bounded continuation")
            path = root / "tool_results" / (_sha(raw) + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_bytes() != raw:
                raise ValueError("Stored tool result changed")
            path.write_bytes(raw)
        if self.audit_log:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log.open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                handle.write(_json({"at": datetime.now(timezone.utc).isoformat(), "action": action,
                    "arguments": arguments, "atlas_sha256": self.sha256,
                    "status": "complete", "result_sha256": _sha(raw), "characters": len(raw.decode()),
                    "delivery": "stored_pointer" if oversized else "inline",
                    "usage": _usage(action, arguments, value)}) + "\n")
        if not oversized:
            return result
        return {"status": "result_too_large", "result_id": _sha(raw), "characters": len(raw.decode()),
                "next_action": "result-read <result_id> --start 0 --limit 6000; continue from next_start. Prefer node/group for a smaller scientific selection.",
                "atlas_sha256": self.sha256}

    def list_methods(self, *, limit=20, offset=0):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid page")
        ids = sorted(self.cards)
        items = [{**self._pin(k), "title": self.cards[k]["title"],
                  "scope": self.cards[k].get("scope"), "operation_ids": self.cards[k]["operation_ids"]}
                 for k in ids[offset:offset + limit]]
        return self._result("list", {"offset": offset, "limit": limit},
            {"methods": items, "total": len(ids), "next_offset": offset + limit if offset + limit < len(ids) else None})

    def search(self, query, *, limit=10):
        tokens = set(re.findall(r"\w+", query.casefold()))
        if not tokens or not 1 <= limit <= 100:
            raise ValueError("Supply search terms and limit 1–100")
        rows = []
        for key, node in self.nodes.items():
            if node["node_type"] not in {"operation", "state"}:
                continue
            text = " ".join(_strings(node.get("original", {}))) + " " + self.cards[node["record_id"]]["title"]
            matched = sorted(tokens & set(re.findall(r"\w+", text.casefold())))
            if matched:
                rows.append({**self._pin(node["record_id"]), "node_id": key,
                    "node_type": node["node_type"], "label": node["original"]["label"],
                    "matched_terms": matched, "score": len(matched) / len(tokens)})
        rows.sort(key=lambda r: (-r["score"], r["node_id"]))
        return self._result("search", {"query": query, "limit": limit},
            {"matches": rows[:limit], "total_matches": len(rows),
             "boundary": "Lexical shortlist including typed properties, roles and effects; not biochemical similarity, compatibility, or an exhaustive novelty search."})

    def workflow(self, record_id):
        self._card(record_id)
        return self._result("workflow", {"record_id": record_id},
                            {"binding": self._pin(record_id), **workflow(self.graph, record_id)})

    def node(self, node_id):
        node = self.nodes.get(node_id)
        if not node or node["node_type"] not in {"operation", "state"}:
            raise ValueError("Choose an exact supported State or Operation ID")
        return self._result("node", {"node_id": node_id}, {"binding": self._pin(node["record_id"]),
            **_focus_sources(get_record(self.graph, node["node_type"], node_id), node["record_id"])})

    def group(self, record_id, operation_ids):
        self._card(record_id)
        return self._result("group", {"record_id": record_id, "operation_ids": operation_ids},
            {"binding": self._pin(record_id), **_focus_sources(operation_group(self.graph, record_id, operation_ids), record_id)})

    def history(self, record_id, *, depth=2):
        binding = self._pin(record_id)
        if self._history() is None:
            return self._result("history", {"record_id": record_id},
                {"status": "not_loaded", "meaning": "History is unavailable here; this does not mean no predecessor exists."})
        if not binding["extraction_sha256"]:
            raise ValueError("Workflow lacks its raw extraction hash for a history join")
        result = technology_history(self.history_index, record_id,
            extraction_sha256=binding["extraction_sha256"], max_depth=depth)
        return self._result("history", {"record_id": record_id, "depth": depth}, result)

    def claim(self, claim_id):
        matches = [e for e in (self._history() or {}).get("edges", []) if e["id"] == claim_id]
        if len(matches) != 1:
            raise ValueError("Choose an exact globally qualified history edge ID")
        return self._result("claim", {"claim_id": claim_id}, matches[0])

    def history_list(self, record_id, *, depth=2, offset=0, limit=10):
        """Page exact historical IDs before loading potentially large claims."""
        if offset < 0 or not 1 <= limit <= 20:
            raise ValueError("Use nonnegative offset and limit 1–20")
        binding = self._pin(record_id)
        arguments = {"record_id": record_id, "depth": depth, "offset": offset, "limit": limit}
        index = self._history()
        if index is None:
            return self._result("history-list", arguments, {"status": "not_loaded",
                "boundary": "History is unavailable here; no claim about absence of predecessors."})
        if not binding["extraction_sha256"]:
            raise ValueError("Workflow lacks its raw extraction hash for a history join")
        history = technology_history(index, record_id, extraction_sha256=binding["extraction_sha256"], max_depth=depth)
        edges = sorted(history["historical_contributions"], key=lambda edge: edge["id"])
        rows = [{"claim_id": edge["id"], "relation": edge["relation"],
                 "before": {key: index["nodes"][edge["source"]][key] for key in ("id", "name", "kind")},
                 "after": {key: index["nodes"][edge["target"]][key] for key in ("id", "name", "kind")},
                 "read_commands": [["claim", edge["id"]], ["change-context", edge["id"]]]}
                for edge in edges[offset:offset + limit]]
        return self._result("history-list", arguments, {"view": "navigation", "binding": binding,
            "relationships": rows, "total": len(edges),
            "next_offset": offset + limit if offset + limit < len(edges) else None,
            "other_direct_relationship_count": len(history["other_direct_relationships"]),
            "withheld_count": len(history["direct_withheld_claims"]),
            "read_history_command": ["history", record_id, "--depth", str(depth)],
            "boundary": "Navigation only: statements, review, evidence and limits require claim; exact workflow boundaries require change-context. "
                        "Only loaded reviewed historical relations within requested depth are listed. Empty results do not establish no predecessors or novelty."})

    def change_context(self, claim_id):
        """Join one reviewed historical change to exact available workflow versions.

        This is a navigation packet, not a summary of scientific support. Exact
        claim/group commands retain access to qualifications and source evidence.
        """
        index = self._history() or {}
        matches = [edge for edge in index.get("edges", []) if edge["id"] == claim_id]
        if len(matches) != 1:
            raise ValueError("Choose an exact globally qualified history edge ID")
        edge = matches[0]
        if edge["relation"] not in HISTORICAL_RELATIONS:
            raise ValueError("Before/after context requires a historical relation, not a similarity")
        claim = edge["claim"]
        is_pair = edge["artifact"]["format_version"] == "illumina_method_pair/1"
        if is_pair:
            direction = claim["direction"]
            if direction not in {"left_to_right", "right_to_left"}:
                raise ValueError("Historical pair lacks an explicit direction")
            before, after = (("left", "right") if direction == "left_to_right" else ("right", "left"))
            selections = {"before": claim[before + "_operation_ids"], "after": claim[after + "_operation_ids"]}
        else:
            selections = {"before": [], "after": claim["seed_operation_ids"]}

        def endpoint(side, node_id):
            node = index["nodes"][node_id]
            if node["kind"] != "bound_workflow":
                return {"status": "external_contribution", "historical_endpoint": node,
                        "boundary": "A cited contribution, not an extracted workflow. Catalogue candidates do not establish an exact join."}
            record_id = node["record_id"]
            card = self.cards.get(record_id)
            if card is None or card.get("artifact_pins", {}).get("extraction.json") != node["extraction_sha256"]:
                return {"status": "workflow_version_unavailable", "historical_endpoint": node,
                        "boundary": "This exact extraction version is absent from the loaded atlas; no substitute version was joined."}
            operations = selections[side]
            if not is_pair:
                lookup = {n["original"]["id"]: n["id"] for n in self.nodes.values()
                          if n["record_id"] == record_id and n["node_type"] == "operation"}
                if set(operations) - lookup.keys():
                    raise ValueError("Historical claim references absent local Operations")
                operations = [lookup[ident] for ident in operations]
            binding = self._pin(record_id)
            try:
                group = operation_group(self.graph, record_id, operations)
            except ValueError as exc:
                if str(exc) != "Operations must form one connected source group":
                    raise
                return {"status": "separate_operation_groups", "binding": binding,
                        "operation_ids": operations, "read_commands": [["node", ident] for ident in operations],
                        "boundary": "Affected Operations are disconnected in the recorded workflow; this claim does not imply a connecting route."}
            return {"status": "exact_workflow_join", "binding": binding,
                    "operation_ids": operations, "selection_sha256": group["selection_sha256"],
                    "input_state_ids": group["input_state_ids"], "output_state_ids": group["output_state_ids"],
                    "internal_state_ids": group["internal_state_ids"],
                    "boundary_flows": [{k: flow[k] for k in ("id", "source", "target", "kind")} for flow in group["boundary_flows"]],
                    "unknowns": group["unknowns"], "routing_ambiguities": group["routing_ambiguities"],
                    "read_commands": [["group", record_id, *operations]],
                    "boundary": "Read the exact group for material properties, operation descriptions and evidence before proposing a transfer."}

        return self._result("change-context", {"claim_id": claim_id},
            {"format_version": "ideation_change_context/1", "view": "navigation",
             "relationship": {key: edge[key] for key in ("id", "source", "target", "relation")},
             "read_commands": [["claim", claim_id]],
             "before": endpoint("before", edge["source"]), "after": endpoint("after", edge["target"]),
             "boundary": "Navigation over one reviewed historical change. Read claim for the full statement, review, evidence and qualifications; read group for material/operation details. These joins do not establish compatibility with a new assay."})

    def atlas_source(self, record_id, *, source_id=None, start=0, limit=6000, query=None):
        """Reopen the extraction's exact source registry; never return whole papers."""
        card = self._card(record_id)
        pin = card.get('artifact_pins', {}).get('source_registry.json')
        if not pin or not card.get('attempt_directory'):
            raise ValueError('Workflow has no pinned source registry available')
        path = Path(card['attempt_directory'])/'source_registry.json'
        registry = _read_pinned(path, pin)
        binding = {'record_id': record_id, 'source_registry_sha256': pin, 'source_registry_path': str(path)}
        fields = {'title', 'url', 'pmid', 'kind', 'content_level', 'sha256', 'path'}
        if source_id is None:
            result = {'sources': [{"source_id": sid, **{k: v for k, v in row.items() if k in fields},
                                   'characters': len(row.get('text', ''))} for sid, row in sorted(registry.items())]}
        else:
            if source_id not in registry or start < 0 or not 1 <= limit <= 12000:
                raise ValueError('Unknown source or invalid bounded read')
            result = {**{k: v for k, v in registry[source_id].items() if k in fields},
                      'source_id': source_id,
                      **self._window(registry[source_id].get('text', ''), start, limit, query),
                      'retrieval_basis': 'literal text match starting at requested offset; inspect passage for scientific support'}
        return self._result('atlas-source', {'record_id': record_id, 'source_id': source_id,
            'start': start, 'limit': limit, 'query': query}, {**binding, **result,
            'boundary': 'Exact registered rendition and provenance; quotation availability does not establish claim entailment or support a proposed transfer.'})

    def validate_atlas_ref(self, record_id, node_id, extraction_sha256, quote=None):
        binding = self._pin(record_id)
        if not extraction_sha256 or extraction_sha256 != binding["extraction_sha256"]:
            raise ValueError("Atlas reference has the wrong extraction hash")
        node = self.nodes.get(node_id)
        if not node or node["record_id"] != record_id or node["node_type"] not in {"state", "operation"}:
            raise ValueError("Atlas reference has an unknown, unsupported or wrong-method node")
        if quote is not None and (not quote.strip() or not any(quote in s for s in _strings(node["original"]))):
            raise ValueError("Atlas quote does not occur in the selected scientific record")
        return copy.deepcopy(node)

    def _sources(self):
        if self.sources_path is None:
            raise ValueError("Literature access requires --sources")
        if not self.sources_path.exists():
            return {"format_version": "ideation_sources/1", "sources": {}}
        value = json.loads(self.sources_path.read_text())
        if value.get("format_version") != "ideation_sources/1":
            raise ValueError("Unknown literature registry format")
        return value

    def add_source(self, path, *, url, title, content_level):
        if content_level not in {"excerpt", "abstract", "fulltext"} or urlparse(url).scheme not in {"http", "https"}:
            raise ValueError("Supply a source URL and excerpt/abstract/fulltext content level")
        raw = Path(path).read_bytes()
        text = raw.decode("utf-8")
        if not title.strip() or not text.strip() or text.strip() == title.strip():
            raise ValueError("A title or empty text is not scientific evidence")
        self._sources()
        root = self.sources_path.parent
        root.mkdir(parents=True, exist_ok=True)
        ident = "literature:" + _sha(url.encode() + b"\0" + raw)
        destination = root / "evidence" / (_sha(raw) + ".txt")
        destination.parent.mkdir(exist_ok=True)
        if destination.exists() and destination.read_bytes() != raw:
            raise ValueError("Captured source changed")
        destination.write_bytes(raw)
        entry = {"source_id": ident, "url": url, "title": title, "content_level": content_level,
            "content_sha256": _sha(raw), "path": str(destination), "characters": len(text),
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "provenance_status": "agent_imported_text; retrieval origin and entailment require review"}
        with self.sources_path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            registry = self._sources()
            existing = registry["sources"].get(ident)
            if existing:
                if existing["content_level"] != content_level or existing["title"] != title:
                    # Stage inputs remain immutable. Different descriptions of
                    # identical bytes are declarations, not new evidence or an
                    # automatic upgrade from an excerpt to full text.
                    declarations_path = root / "capture_declarations.json"
                    declarations = json.loads(declarations_path.read_text()) if declarations_path.exists() else {}
                    captures = declarations.setdefault(ident, [])
                    fields = ("title", "content_level", "captured_at", "provenance_status")
                    if not captures:
                        captures.append({key: existing[key] for key in fields})
                    if not any(row["title"] == title and row["content_level"] == content_level for row in captures):
                        captures.append({key: entry[key] for key in fields})
                    temporary = declarations_path.with_suffix(f".{os.getpid()}.tmp")
                    temporary.write_text(_json(declarations)); temporary.replace(declarations_path)
                entry = existing
            registry["sources"][ident] = entry
            temporary = self.sources_path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(_json(registry))
            temporary.replace(self.sources_path)
        return self._result("source-add", {"url": url, "content_level": content_level}, self._capture_metadata(entry))

    def _capture_metadata(self, entry):
        path = self.sources_path.parent / "capture_declarations.json"
        declarations = json.loads(path.read_text()).get(entry["source_id"], []) if path.exists() else []
        if not declarations:
            return entry
        return {**entry, "capture_declarations": declarations,
                "metadata_status": "differing_capture_declarations; original registration retained; alternatives are not verified bibliographic facts or an upgrade in evidence level"}

    def _source_text(self, source_id):
        entry = self._sources()["sources"].get(source_id)
        if not entry:
            raise ValueError("Unknown captured literature source")
        raw = Path(entry["path"]).read_bytes()
        if _sha(raw) != entry["content_sha256"]:
            raise ValueError("Captured literature bytes changed")
        return entry, raw.decode("utf-8")

    def fetch_source(self, url, *, title):
        """Fetch publication content with host custody; never certify its meaning."""
        from .evidence import fetch_source
        before = self._sources()
        fetched = fetch_source(url, self.sources_path.parent/'acquisitions')
        raw = Path(fetched['text_path']).read_bytes()
        ident = 'literature:' + _sha(url.encode() + b'\0' + raw)
        # Preserve an inherited content-level declaration; receipt separately says
        # what the host actually fetched, without promoting a page to full text.
        level = before['sources'].get(ident, {}).get('content_level', fetched['content_level'])
        self.add_source(fetched['text_path'], url=url, title=title, content_level=level)
        receipts_path = self.sources_path.parent/'origin_receipts.json'
        with self.sources_path.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            receipts = json.loads(receipts_path.read_text()) if receipts_path.exists() else {}
            captures = receipts.setdefault(ident, [])
            if fetched['receipt'] not in captures:
                captures.append(fetched['receipt'])
            temporary = receipts_path.with_suffix(f'.{os.getpid()}.tmp')
            temporary.write_text(_json(receipts)); temporary.replace(receipts_path)
            registry = self._sources()
            if ident not in before['sources']:
                registry['sources'][ident]['provenance_status'] = 'host_fetched_text; bibliographic identity and entailment require review'
                registry['sources'][ident]['origin_receipts'] = [fetched['receipt']]
                temporary = self.sources_path.with_suffix(f'.{os.getpid()}.tmp')
                temporary.write_text(_json(registry)); temporary.replace(self.sources_path)
            entry = registry['sources'][ident]
        return self._result('source-fetch', {'url': url, 'title': title},
                            {**self._capture_metadata(entry), 'origin_receipt': fetched['receipt']})

    def read_source(self, source_id, *, start=0, limit=6000, query=None):
        entry, text = self._source_text(source_id)
        return self._result("source-read", {"source_id": source_id, "start": start, "limit": limit, "query": query},
                            {**self._capture_metadata(entry), **self._window(text, start, limit, query),
                             "boundary": "Bounded literal access to this exact capture. No match does not establish absence of evidence in the paper or wider literature; captured text may be incomplete."})

    @staticmethod
    def _window(text, start, limit, query=None):
        if start < 0 or not 1 <= limit <= 12000:
            raise ValueError("Use nonnegative start and limit 1–12000")
        if query is not None:
            position = text.casefold().find(query.casefold(), start)
            if not query.strip() or position < 0:
                return {"status": "no_match", "text": "", "next_start": None}
            start = max(start, position - min(200, limit // 4))
        end = min(len(text), start + limit)
        return {"text": text[start:end], "start": start, "end": end,
                "next_start": end if end < len(text) else None, "total_characters": len(text)}

    def validate_literature_ref(self, source_id, content_sha256, quote):
        entry, text = self._source_text(source_id)
        if content_sha256 != entry["content_sha256"] or not quote.strip() or quote not in text:
            raise ValueError("Literature reference hash/quote does not match captured text")
        return copy.deepcopy(entry)

    def read_result(self, result_id, *, start=0, limit=6000):
        if not re.fullmatch(r"[a-f0-9]{64}", result_id):
            raise ValueError("Invalid result ID")
        root = self.audit_log.parent if self.audit_log else self.sources_path.parent if self.sources_path else None
        if root is None:
            raise ValueError("Result continuation requires --audit-log or --sources")
        raw = (root / "tool_results" / (result_id + ".json")).read_bytes()
        if _sha(raw) != result_id:
            raise ValueError("Stored tool result changed")
        return self._result("result-read", {"result_id": result_id, "start": start, "limit": limit},
                            {"result_id": result_id, **self._window(raw.decode(), start, limit)})


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--atlas", required=True, type=Path)
    p.add_argument("--atlas-sha256", required=True)
    p.add_argument("--history-manifest", type=Path)
    p.add_argument("--history-sha256")
    p.add_argument("--sources", type=Path)
    p.add_argument("--audit-log", type=Path)
    s = p.add_subparsers(dest="command", required=True)
    listing = s.add_parser("list"); listing.add_argument("--offset", type=int, default=0); listing.add_argument("--limit", type=int, default=20)
    search = s.add_parser("search"); search.add_argument("query"); search.add_argument("--limit", type=int, default=10)
    for command, field in (("workflow", "record_id"), ("node", "node_id"), ("claim", "claim_id"), ("change-context", "claim_id")):
        s.add_parser(command).add_argument(field)
    group = s.add_parser("group"); group.add_argument("record_id"); group.add_argument("operation_ids", nargs="+")
    history = s.add_parser("history"); history.add_argument("record_id"); history.add_argument("--depth", type=int, default=2)
    listing = s.add_parser("history-list"); listing.add_argument("record_id"); listing.add_argument("--depth", type=int, default=2)
    listing.add_argument("--offset", type=int, default=0); listing.add_argument("--limit", type=int, default=10)
    source = s.add_parser("atlas-source"); source.add_argument("record_id"); source.add_argument("--source-id")
    source.add_argument("--query"); source.add_argument("--start", type=int, default=0); source.add_argument("--limit", type=int, default=6000)
    add = s.add_parser("source-add"); add.add_argument("path", type=Path)
    for name in ("url", "title", "content-level"): add.add_argument("--" + name, required=True)
    fetch = s.add_parser('source-fetch'); fetch.add_argument('url'); fetch.add_argument('--title', required=True)
    for command, field in (("source-read", "source_id"), ("result-read", "result_id")):
        read = s.add_parser(command); read.add_argument(field)
        read.add_argument("--start", type=int, default=0); read.add_argument("--limit", type=int, default=6000)
        if command == "source-read": read.add_argument("--query")
    args = vars(p.parse_args(argv)); command = args.pop("command")
    context = AtlasContext(args.pop("atlas"), expected_sha256=args.pop("atlas_sha256"),
        history_manifest=args.pop("history_manifest"), history_sha256=args.pop("history_sha256"),
        sources_path=args.pop("sources"), audit_log=args.pop("audit_log"))
    action = {"list": "list_methods", "history-list": "history_list", "change-context": "change_context", "atlas-source": "atlas_source", "source-add": "add_source", "source-fetch": "fetch_source", "source-read": "read_source", "result-read": "read_result"}.get(command, command)
    try:
        result = getattr(context, action)(**args)
    except (ValueError, KeyError, OSError, StopIteration, UnicodeError) as exc:
        result = {"status": "tool_error", "action": command, "error": str(exc)}
        if context.audit_log:
            context.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with context.audit_log.open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                handle.write(_json({"at": datetime.now(timezone.utc).isoformat(), "action": command,
                    "arguments": {k: str(v) if isinstance(v, Path) else v for k, v in args.items()},
                    "atlas_sha256": context.sha256, **result}) + "\n")
        print(_json(result))
        raise SystemExit(2)
    print(_json(result))


if __name__ == "__main__":
    main()
