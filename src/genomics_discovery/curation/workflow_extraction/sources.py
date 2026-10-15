"""Prepare and read bounded, reproducible passages from registered source files.

Passage matching establishes provenance, not scientific entailment. JATS identity
checks reuse the existing source preflight; plain text identity remains supplied
metadata. Source acquisition remains the existing curation workflow's job.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from ..document_manifest import MAX_BYTES, inspect_jats
from .jats_text import RENDITION_VERSION, SUPPORTED_RENDITIONS, render_block

FORMAT = "workflow_sources/1.1"
LEGACY_FORMAT = "workflow_sources/1.0"
PASSAGE_CHARS = 4000
ROLES = {"primary_paper", "supplement", "manufacturer_protocol", "cited_protocol", "other"}
MAX_READ_BATCH = 8
_METHOD_HEADING = re.compile(
    r"\b(?:methods?|materials|experimental (?:procedures?|section)|protocols?)\b", re.I)


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _xml_text(element) -> str:
    """Preserve table cell boundaries; this is a rendition, not original layout."""
    if _tag(element) == "table-wrap":
        rows = []
        for child in element:
            if _tag(child) in {"label", "caption", "table-wrap-foot"}:
                rows.append(" ".join("".join(child.itertext()).split()))
        for row in element.iter():
            if _tag(row) == "tr":
                cells = [" ".join("".join(cell.itertext()).split()) for cell in row
                         if _tag(cell) in {"td", "th"}]
                if cells:
                    rows.append(" | ".join(cells))
        return " ; ".join(rows) or " ".join("".join(element.itertext()).split())
    if _tag(element) in {"fig", "caption"}:
        return " ; ".join(" ".join("".join(child.itertext()).split()) for child in element
                          if _tag(child) in {"label", "title", "caption", "p"})
    return " ".join("".join(element.itertext()).split())


def _passages(raw: bytes, suffix: str) -> tuple[list[dict], dict]:
    """Make a deterministic text rendition, preserving structural source locators."""
    if len(raw) > MAX_BYTES:
        raise ValueError("Source exceeds the existing source-preflight size limit")
    if raw.startswith((b"%PDF-", b"PK\x03\x04", b"\x89PNG", b"\xff\xd8")):
        raise ValueError("Register a text rendition and inspect its binary original separately")
    blocks, identity = [], {"status": "supplied_metadata_not_publication_verified"}
    if suffix == ".xml":
        identity = inspect_jats(raw)["identity"]
        root = ET.fromstring(raw)
        parents = {child: parent for parent in root.iter() for child in parent}
        counters = {}
        for element in root.iter():
            if _tag(element) not in {"p", "table-wrap", "ref", "fig", "caption"}:
                continue
            ancestors, parent = [], parents.get(element)
            while parent is not None:
                ancestors.append(parent)
                parent = parents.get(parent)
            # Structured blocks retain their context without duplicating paragraphs.
            if any(_tag(a) in {"table-wrap", "ref", "fig", "caption"} for a in ancestors):
                continue
            owner = next((a for a in ancestors if _tag(a) in {"sec", "abstract"}), None)
            section = owner.get("id", _tag(owner)) if owner is not None else "article"
            title = next((" ".join("".join(c.itertext()).split()) for c in owner
                          if _tag(c) == "title"), "") if owner is not None else ""
            key = (section, _tag(element))
            counters[key] = counters.get(key, 0) + 1
            locator = f"{section}/{_tag(element)}{counters[key]}"
            if element.get("id"):
                locator += f" [{element.get('id')}]"
            if title:
                locator += f" — {title}"
            text = _xml_text(element)
            if text:
                blocks.append((locator, text))
    elif suffix == ".txt":
        text = raw.decode("utf-8-sig")
        for number, block in enumerate(re.split(r"\n\s*\n", text), 1):
            normalized = " ".join(block.split())
            if normalized:
                blocks.append((f"text block {number}", normalized))
    else:
        raise ValueError("This small reader supports JATS .xml and UTF-8 .txt sources")
    passages = []
    for locator, text in blocks:
        # A long source block is split without removing any characters.
        for start in range(0, len(text), PASSAGE_CHARS):
            passages.append({"passage_id": f"p{len(passages) + 1:05d}",
                             "locator": f"{locator}; normalized chars {start}:"
                                        f"{min(start + PASSAGE_CHARS, len(text))}",
                             "text": text[start:start + PASSAGE_CHARS]})
    if not passages:
        raise ValueError("Source has no readable passages")
    return passages, identity


def _structured_passages(
    raw: bytes, suffix: str, rendition_version: str,
) -> tuple[list[dict], dict]:
    """Versioned rendition with explicit scripts and retained display formula sources."""
    if suffix != ".xml":
        passages, identity = _passages(raw, suffix)
        return [{**p, "rendition_notes": []} for p in passages], identity
    if len(raw) > MAX_BYTES:
        raise ValueError("Source exceeds the existing source-preflight size limit")
    if raw.startswith((b"%PDF-", b"PK\x03\x04", b"\x89PNG", b"\xff\xd8")):
        raise ValueError("Register a text rendition and inspect its binary original separately")
    identity = inspect_jats(raw)["identity"]
    root = ET.fromstring(raw)
    parents = {child: parent for parent in root.iter() for child in parent}
    kinds = {"p", "table-wrap", "ref", "fig", "caption", "disp-formula"}
    enclosing = {"table-wrap", "ref", "fig", "caption", "disp-formula"}
    counters, passages = {}, []
    for element in root.iter():
        if _tag(element) not in kinds:
            continue
        ancestors, parent = [], parents.get(element)
        while parent is not None:
            ancestors.append(parent)
            parent = parents.get(parent)
        if any(_tag(a) in enclosing for a in ancestors):
            continue
        owner = next((a for a in ancestors if _tag(a) in {"sec", "abstract"}), None)
        section = owner.get("id", _tag(owner)) if owner is not None else "article"
        title = next((" ".join("".join(c.itertext()).split()) for c in owner
                      if _tag(c) == "title"), "") if owner is not None else ""
        key = (section, _tag(element))
        counters[key] = counters.get(key, 0) + 1
        locator = f"{section}/{_tag(element)}{counters[key]}"
        if element.get("id"):
            locator += f" [{element.get('id')}]"
        if title:
            locator += f" — {title}"
        text, notes = render_block(element, rendition_version)
        for start in range(0, len(text), PASSAGE_CHARS):
            passages.append({"passage_id": f"p{len(passages) + 1:05d}",
                             "locator": f"{locator}; normalized chars {start}:"
                                        f"{min(start + PASSAGE_CHARS, len(text))}",
                             "text": text[start:start + PASSAGE_CHARS], "rendition_notes": notes})
    if not passages:
        raise ValueError("Source has no readable passages")
    return passages, identity


def _versioned_passages(raw: bytes, suffix: str, format_version: str, rendition_version=None):
    return (_passages(raw, suffix) if format_version == LEGACY_FORMAT
            else _structured_passages(raw, suffix, rendition_version))


def build_packet(*, method_id: str, title: str, scope: str, variant: str,
                 sources: list[dict], format_version: str = FORMAT,
                 rendition_version: str | None = None) -> dict:
    """Register actual source bytes; never load a previous extraction as evidence."""
    if format_version not in {FORMAT, LEGACY_FORMAT}:
        raise ValueError("Unsupported workflow source packet version")
    if format_version == FORMAT:
        rendition_version = RENDITION_VERSION if rendition_version is None else rendition_version
        if not isinstance(rendition_version, str) or rendition_version not in SUPPORTED_RENDITIONS:
            raise ValueError("Unsupported structured source rendition version")
    elif rendition_version is not None:
        raise ValueError("Legacy1.0 packets do not declare a structured rendition")
    packet = {"format_version": format_version, "method_id": method_id, "title": title,
              "scope": scope, "variant": variant, "sources": []}
    if format_version == FORMAT:
        packet["rendition_version"] = rendition_version
    for source in sources:
        path = Path(source["path"]).expanduser().resolve(strict=True)
        raw = path.read_bytes()
        passages, identity = _versioned_passages(
            raw, path.suffix.lower(), format_version, rendition_version)
        packet["sources"].append({
            **{key: source[key] for key in ("source_id", "title", "url", "role")},
            "original_path": str(path), "original_sha256": hashlib.sha256(raw).hexdigest(),
            "document_identity": identity, "passages": passages,
        })
    validate_packet(packet)
    return packet


def validate_packet(packet: dict) -> None:
    if not isinstance(packet, dict):
        raise ValueError("Invalid workflow source packet")
    format_version = packet.get("format_version")
    rendition_version = packet.get("rendition_version")
    expected = {"format_version", "method_id", "title", "scope", "variant", "sources"}
    if format_version == FORMAT:
        expected.add("rendition_version")
        if not isinstance(rendition_version, str) or rendition_version not in SUPPORTED_RENDITIONS:
            raise ValueError("Unsupported structured source rendition version")
    if format_version not in {FORMAT, LEGACY_FORMAT} or set(packet) != expected:
        raise ValueError("Invalid workflow source packet")
    for key in ("method_id", "title", "scope", "variant"):
        if not isinstance(packet[key], str) or not packet[key].strip():
            raise ValueError(f"Source packet needs a nonempty {key}")
    if not isinstance(packet["sources"], list) or not packet["sources"]:
        raise ValueError("Source packet needs registered documents")
    seen = set()
    for source in packet["sources"]:
        fields = {"source_id", "title", "url", "role", "original_path", "original_sha256",
                  "document_identity", "passages"}
        if not isinstance(source, dict) or set(source) != fields:
            raise ValueError("Invalid registered source fields")
        sid = source["source_id"]
        if not isinstance(sid, str) or not sid.strip() or sid in seen:
            raise ValueError("Registered source IDs must be nonempty and unique")
        seen.add(sid)
        if source["role"] not in ROLES:
            raise ValueError("Invalid source role")
        if not all(isinstance(source[k], str) and source[k].strip()
                   for k in ("title", "url", "original_path")):
            raise ValueError("Source title, URL and original path are required")
        path = Path(source["original_path"])
        if not path.is_absolute():
            raise ValueError("Registered originals require absolute paths")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["original_sha256"]:
            raise ValueError(f"Source changed: {sid}")
        passages, identity = _versioned_passages(
            raw, path.suffix.lower(), format_version, rendition_version)
        if passages != source["passages"] or identity != source["document_identity"]:
            raise ValueError(f"Stored rendition/identity differs from original: {sid}")


def load_packet(path: Path) -> dict:
    packet = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_packet(packet)
    return packet


def _section_titles(source: dict) -> dict[str, set[tuple[str, ...]]]:
    """Recover document section ancestry without changing registered passages."""
    path = Path(source["original_path"])
    if path.suffix.lower() != ".xml":
        return {}
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != source["original_sha256"]:
        raise ValueError(f"Source changed: {source['source_id']}")
    sections = {}

    def visit(element, titles):
        if _tag(element) in {"sec", "abstract"}:
            title = next((" ".join("".join(child.itertext()).split()) for child in element
                          if _tag(child) == "title"), _tag(element))
            titles = (*titles, title)
            sections.setdefault(element.get("id", _tag(element)), set()).add(titles)
        for child in element:
            visit(child, titles)

    visit(ET.fromstring(raw), ())
    return sections


def catalog(packet: dict, focus: str = "all") -> dict:
    """Compact structural reading hints, never a certificate of methods coverage."""
    if focus not in {"all", "methods"}:
        raise ValueError("Catalogue focus must be all or methods")
    sources = []
    for source in packet["sources"]:
        titles = _section_titles(source)
        sections, hints = {}, {}
        for passage in source["passages"]:
            section = passage["locator"].split("/", 1)[0] if titles else "document"
            context = titles.get(section, set())
            ancestry = next(iter(context)) if len(context) == 1 else ()
            label = section + (" — " + " > ".join(ancestry) if ancestry else "")
            hints[label] = (any(_METHOD_HEADING.search(title) for title in ancestry)
                            if ancestry else None)
            sections.setdefault(label, []).append(passage["passage_id"])
        role_hint = source["role"] in {"supplement", "manufacturer_protocol", "cited_protocol"}
        structural_hint = any(hints.values())
        if focus == "methods" and structural_hint and not role_hint:
            sections = {label: ids for label, ids in sections.items() if hints[label] is not False}
        listed = sum(len(ids) for ids in sections.values())
        sources.append({**{key: source[key] for key in (
            "source_id", "title", "url", "role", "original_path", "original_sha256",
            "document_identity")}, "passage_count": len(source["passages"]),
            "listed_passage_count": listed, "sections": sections,
            "method_sections": [label for label in sections if hints[label]],
            "focus_basis": ("registered supplement/protocol role" if role_hint else
                            "structural section headings" if structural_hint else
                            "no unambiguous methods heading; all groups retained")})
    return {"method_id": packet["method_id"], "scope": packet["scope"],
            "variant": packet["variant"], "focus": focus, "sources": sources,
            "boundary": "Structural navigation hints; all passages remain registered. "
                        "Use the all catalogue/search for other context; "
                        "no coverage or absence proof."}


def read_passage(packet: dict, source_id: str, passage_id: str) -> dict:
    for source in packet["sources"]:
        if source["source_id"] == source_id:
            for passage in source["passages"]:
                if passage["passage_id"] == passage_id:
                    return {"source_id": source_id, "original_sha256": source["original_sha256"],
                            "role": source["role"], **passage}
    raise ValueError("Unknown registered source/passage")


def read_batch(packet: dict, references: list[dict]) -> dict:
    """Read a bounded selection in requested order with unchanged provenance/text."""
    if not isinstance(references, list) or not 1 <= len(references) <= MAX_READ_BATCH:
        raise ValueError(f"Read batch requires 1 to {MAX_READ_BATCH} passage references")
    seen, passages = set(), []
    for ref in references:
        if (not isinstance(ref, dict) or set(ref) != {"source_id", "passage_id"}
                or not all(isinstance(value, str) and value for value in ref.values())):
            raise ValueError("Read batch needs exact source_id/passage_id reference objects")
        key = (ref["source_id"], ref["passage_id"])
        if key in seen:
            raise ValueError("Read batch contains a duplicate passage reference")
        seen.add(key)
        passages.append(read_passage(packet, *key))
    return {"passages": passages,
            "boundary": "Ordered original passages; this selection does not establish coverage."}


def search(packet: dict, query: str, limit: int = 6, *, source_id: str | None = None) -> dict:
    if not query.strip() or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Search requires a nonempty query and limit between 1 and 20")
    terms = set(re.findall(r"\w+", query.lower()))
    if not terms:
        raise ValueError("Search needs nonempty lexical terms")
    if source_id is not None and source_id not in {s["source_id"] for s in packet["sources"]}:
        raise ValueError("Unknown registered search source")
    sources = [s for s in packet["sources"] if source_id is None or s["source_id"] == source_id]
    hits = []
    for source in sources:
        for passage in source["passages"]:
            text = (passage["locator"] + " " + passage["text"]).lower()
            score = sum(text.count(term) for term in terms)
            if score:
                hits.append({"source_id": source["source_id"], "passage_id": passage["passage_id"],
                             "locator": passage["locator"], "score": score,
                             "preview": passage["text"][:280]})
    hits.sort(key=lambda hit: (-hit["score"], hit["source_id"], hit["passage_id"]))
    return {"query": query, "hits": hits[:limit], "total": len(hits), "limit": limit,
            "truncated": len(hits) > limit, "source_scope": [s["source_id"] for s in sources],
            "boundary": "Lexical passage discovery; no search hit proves entailment or absence."}
