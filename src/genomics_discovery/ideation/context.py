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
        if self.audit_log:
            self.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log.open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                handle.write(_json({"at": datetime.now(timezone.utc).isoformat(), "action": action,
                    "arguments": arguments, "atlas_sha256": self.sha256,
                    "status": "complete", "result_sha256": _sha(raw), "characters": len(raw.decode())}) + "\n")
        if len(raw.decode()) <= self.max_result_chars:
            return result
        root = self.audit_log.parent if self.audit_log else self.sources_path.parent if self.sources_path else None
        if root is None:
            raise ValueError("Oversized context requires --audit-log or --sources for bounded continuation")
        path = root / "tool_results" / (_sha(raw) + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != raw:
            raise ValueError("Stored tool result changed")
        path.write_bytes(raw)
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
            **get_record(self.graph, node["node_type"], node_id)})

    def group(self, record_id, operation_ids):
        self._card(record_id)
        return self._result("group", {"record_id": record_id, "operation_ids": operation_ids},
            {"binding": self._pin(record_id), **operation_group(self.graph, record_id, operation_ids)})

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

    def atlas_source(self, record_id, *, source_id=None, start=0, limit=6000, query=None):
        """Reopen the extraction's exact source registry; never return whole papers."""
        from genomics_discovery.illumina.extraction import source_excerpt
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
                      **source_excerpt(registry, source_id, start=start, limit=limit, query=query)}
            at = result['start']
            end = at + len(result['text']) if at is not None else None
            result['next_start'] = end if end is not None and end < result['total_characters'] else None
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
                    raise ValueError("Existing source metadata differs; do not silently relabel evidence")
                entry = existing
            registry["sources"][ident] = entry
            temporary = self.sources_path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(_json(registry))
            temporary.replace(self.sources_path)
        return self._result("source-add", {"url": url, "content_level": content_level}, entry)

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
                            {**entry, 'origin_receipt': fetched['receipt']})

    def read_source(self, source_id, *, start=0, limit=6000, query=None):
        entry, text = self._source_text(source_id)
        return self._result("source-read", {"source_id": source_id, "start": start, "limit": limit, "query": query},
                            {**entry, **self._window(text, start, limit, query)})

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
    for command, field in (("workflow", "record_id"), ("node", "node_id"), ("claim", "claim_id")):
        s.add_parser(command).add_argument(field)
    group = s.add_parser("group"); group.add_argument("record_id"); group.add_argument("operation_ids", nargs="+")
    history = s.add_parser("history"); history.add_argument("record_id"); history.add_argument("--depth", type=int, default=2)
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
    action = {"list": "list_methods", "atlas-source": "atlas_source", "source-add": "add_source", "source-fetch": "fetch_source", "source-read": "read_source", "result-read": "read_result"}.get(command, command)
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
