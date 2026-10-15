"""Inspect JATS structure for source preparation, without extracting a protocol.

Methods-section citations are dependency candidates, not demonstrated inheritance.
Locators and raw supplement links are retained for a later source audit. XML does
not cover PDF-only papers, untagged procedures, or procedures inside supplements.
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import unquote
from xml.etree import ElementTree as ET

MAX_BYTES = 32_000_000
_METHOD_HEADING = re.compile(r"\b(methods?|protocols?|experimental procedures?)\b", re.I)


class NotArticleDocument(ValueError):
    """A metadata/experiment XML file is not an article to inspect."""


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _text(element):
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _child(element, name):
    return next((c for c in element if _tag(c) == name), None)


def _ids(elements, tag):
    result = {}
    for element in elements:
        if _tag(element) == tag:
            kind = element.get("pub-id-type", "unspecified").lower()
            value = _text(element)
        elif tag == "pub-id" and _tag(element) == "ext-link":
            kind = element.get("ext-link-type", "").lower()
            if kind not in {"pmid", "pmcid", "doi"}:
                continue
            value = next((v for k, v in element.attrib.items()
                          if k.rsplit("}", 1)[-1] == "href"), "")
            value = unquote(value)
            if kind == "pmid":
                match = re.fullmatch(
                    r"(?:https?://(?:pubmed\.ncbi\.nlm\.nih\.gov/|"
                    r"(?:www\.)?ncbi\.nlm\.nih\.gov/pubmed/))?(\d+)/?", value)
                if not match:
                    continue
                value = match[1]
            elif kind == "pmcid":
                match = re.fullmatch(
                    r"(?:https?://(?:pmc\.ncbi\.nlm\.nih\.gov/articles/|"
                    r"(?:www\.)?ncbi\.nlm\.nih\.gov/pmc/articles/))?(PMC\d+)/?", value)
                if not match:
                    continue
                value = match[1]
        else:
            continue
        if value:
            if kind == "doi":
                value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", unquote(value).lower())
            if value and value not in result.setdefault(kind, []):
                result[kind].append(value)
    return result


def inspect_jats(data: bytes) -> dict:
    """Return exact structural leads. No network, chemical inference, or acceptance."""
    if len(data) > MAX_BYTES:
        raise ValueError("JATS document exceeds source-preflight size limit")
    if re.search(br"<!ENTITY\s", data, re.I):
        raise ValueError("JATS source preflight rejects entity declarations")
    root = ET.fromstring(data)
    if _tag(root) != "article":
        raise NotArticleDocument("Not a JATS article document")
    front = _child(root, "front")
    meta = _child(front, "article-meta") if front is not None else None
    if meta is None:
        raise ValueError("JATS article has no front/article-meta identity")
    title_group = _child(meta, "title-group")
    title = _child(title_group, "article-title") if title_group is not None else None
    paths, parents = {}, {}

    def visit(element, path):
        paths[element] = path
        counts = Counter()
        for child in element:
            tag = _tag(child)
            counts[tag] += 1
            parents[child] = element
            visit(child, f"{path}/{tag}[{counts[tag]}]")

    visit(root, ".")
    ids = _ids(meta, "article-id")
    refs = {}
    for element in root.iter():
        if _tag(element) == "ref" and element.get("id"):
            rid = element.get("id")
            refs.setdefault(rid, []).append({
                "reference_id": rid, "locator": paths[element],
                "identifiers": _ids(element.iter(), "pub-id"), "citation": _text(element),
            })
    candidates, methods_sections, supplements = [], [], []
    method_elements = set()
    for element in root.iter():
        if _tag(element) == "sec":
            heading = _text(_child(element, "title"))
            if _METHOD_HEADING.search(f"{heading} {element.get('sec-type', '')}"):
                method_elements.add(element)
                methods_sections.append({
                    "locator": paths[element], "heading": heading,
                    "sec_type": element.get("sec-type"),
                    "selection_basis": "Methods/protocol section type or heading heuristic",
                })
        if _tag(element) == "supplementary-material":
            links = sorted({v for child in element.iter() for k, v in child.attrib.items()
                            if k.rsplit("}", 1)[-1] == "href"})
            supplements.append({
                "locator": paths[element], "element_id": element.get("id"),
                "description": _text(element), "hrefs": links,
                "role_status": "linked supplementary object; content and necessity not assessed",
            })
    for element in root.iter():
        if _tag(element) != "xref" or element.get("ref-type") != "bibr":
            continue
        ancestor, paragraph, section = element, None, None
        while ancestor in parents:
            ancestor = parents[ancestor]
            if paragraph is None and _tag(ancestor) == "p":
                paragraph = ancestor
            if section is None and ancestor in method_elements:
                section = ancestor
        if section is None:
            continue
        context = _text(paragraph if paragraph is not None else parents[element])
        for rid in element.get("rid", "").split():
            candidates.append({
                "reference_id": rid, "xref_locator": paths[element],
                "section_locator": paths[section],
                "context_locator": paths[paragraph] if paragraph is not None else None,
                "context_excerpt": context[:1000], "context_truncated": len(context) > 1000,
                "reference_records": refs.get(rid, []),
                "dependency_status": "candidate_requires_procedure_dependency_audit",
            })
    return {
        "identity": {"identifiers": ids, "title": _text(title)},
        "article_type": root.get("article-type"), "methods_sections": methods_sections,
        "supplements": supplements, "procedure_citation_candidates": candidates,
        "coverage": "JATS structural leads only; supplements, manuals, code and untagged "
                    "procedure transfers require source inspection",
    }
