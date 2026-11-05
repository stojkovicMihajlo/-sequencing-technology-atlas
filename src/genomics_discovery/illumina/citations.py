"""Locate JATS citations without treating citation occurrence as ancestry.

JATS rid is an IDREFS list, not a numeric interval. Some publishers link only
the endpoints of a printed citation range. Expand that range only through
unique, explicit bibliography labels, never by guessing from reference IDs.
"""
import re
import xml.etree.ElementTree as ET


# Only text-transparent JATS inline markup may connect numeric endpoints.
# Block, opaque and unknown elements delimit segments even if itertext() would
# make their contents appear adjacent. Their explicit xrefs remain discoverable.
_INLINE_ELEMENTS = {
    "abbrev", "bold", "ext-link", "fixed-case", "italic", "monospace",
    "named-content", "overline", "roman", "sans-serif", "sc", "strike",
    "styled-content", "sub", "sup", "underline", "x", "xref",
}


def local(element):
    return element.tag.rsplit("}", 1)[-1]


def _number(text):
    match = re.fullmatch(r"\s*\[?(\d+)\]?\.?\s*", text)
    return int(match[1]) if match else None


def _text_and_links(element):
    """Retain source text and xrefs, with segment barriers around non-inline nodes."""
    parts, links, position = [], [], 0
    segment = 0

    def append(text):
        nonlocal position
        parts.append(text or "")
        position += len(text or "")

    def visit(node, *, root=False):
        nonlocal segment
        boundary = not root and local(node) not in _INLINE_ELEMENTS
        if boundary:
            segment += 1
        start = position
        start_segment = segment
        append(node.text)
        for child in node:
            visit(child)
            append(child.tail)
        if local(node) == "xref":
            links.append((start, position, node,
                          start_segment if start_segment == segment else None))
        if boundary:
            segment += 1

    visit(element, root=True)
    return "".join(parts), sorted(links, key=lambda item: item[0])


def bibliography(raw):
    tree = ET.fromstring(raw)
    refs, labels = {}, {}
    for ref in (e for e in tree.iter() if local(e) == "ref"):
        rid = ref.attrib["id"]
        if rid in refs:
            raise ValueError("Duplicate bibliography reference ID")
        ids = {e.get("pub-id-type"): "".join(e.itertext()).strip()
               for e in ref.iter() if local(e) == "pub-id"}
        label = next(("".join(e.itertext()) for e in ref if local(e) == "label"), "")
        number = _number(label)
        if number is not None:
            labels.setdefault(number, []).append(rid)
        refs[rid] = {"reference_id": rid, "citation": " ".join("".join(ref.itertext()).split()),
                     "identifiers": ids, "contexts": [], "context_provenance": []}

    for index, paragraph in enumerate(e for e in tree.iter() if local(e) == "p"):
        raw_text, links = _text_and_links(paragraph)
        text = " ".join(raw_text.split())
        located = {rid: {"basis": "explicit_xref"} for _, _, node, _ in links
                   if node.get("ref-type") == "bibr" for rid in node.get("rid", "").split()
                   if rid in refs}
        # Only neighbouring, singly linked numeric endpoints separated by a
        # dash qualify. A comma, intervening prose, figure link, missing label,
        # duplicate label or nonnumeric citation never licenses an expansion.
        for (left_start, end, left, left_segment), (start, right_end, right, right_segment) in zip(links, links[1:]):
            if left_segment is None or left_segment != right_segment:
                continue
            if raw_text[end:start].strip() not in {"-", "–", "—"}:
                continue
            if left.get("ref-type") != "bibr" or right.get("ref-type") != "bibr":
                continue
            lo, hi = _number("".join(left.itertext())), _number("".join(right.itertext()))
            if lo is None or hi is None or not lo < hi or hi - lo >= len(refs):
                continue
            if any(len(labels.get(n, [])) != 1 for n in range(lo, hi + 1)):
                continue
            if left.get("rid", "").split() != labels[lo] or right.get("rid", "").split() != labels[hi]:
                continue
            for n in range(lo + 1, hi):
                located.setdefault(labels[n][0], {"basis": "printed_numeric_range",
                    "endpoint_reference_ids": [labels[lo][0], labels[hi][0]],
                    "printed_range": raw_text[left_start:right_end]})
        for rid, basis in located.items():
            refs[rid]["contexts"].append(text)
            refs[rid]["context_provenance"].append({**basis, "paragraph_index": index,
                "paragraph_id": paragraph.get("id"), "context_index": len(refs[rid]["contexts"]) - 1})
    return refs
