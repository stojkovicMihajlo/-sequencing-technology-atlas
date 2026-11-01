"""Conservative source-text rendition, not a mathematical interpreter/renderer."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

LEGACY_RENDITION_VERSION = "structured_jats_text/1.0"
RENDITION_VERSION = "structured_jats_text/1.1"
SUPPORTED_RENDITIONS = {LEGACY_RENDITION_VERSION, RENDITION_VERSION}
_MATHML = "http://www.w3.org/1998/Math/MathML"


def tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _source_xml(element, *, robust: bool = False) -> str:
    """Deterministic XML serialization; original byte-exact source stays registered."""
    namespaces = sorted({name[1:].split("}", 1)[0] for node in element.iter()
                         for name in (node.tag, *node.attrib) if name.startswith("{")})
    prefixes = {uri: ("xml" if uri == "http://www.w3.org/XML/1998/namespace" else
                      "mml" if uri == _MATHML else f"ns{i}")
                for i, uri in enumerate(namespaces)}

    def qname(name):
        if name.startswith("{"):
            uri, local = name[1:].split("}", 1)
            return prefixes[uri] + ":" + local
        return name

    def encoded(value):
        return "".join(f"&#{ord(char)};" if robust and char.isspace() else char for char in value)

    def serialize(node, root=False):
        attrs = [(qname(key), value) for key, value in sorted(node.attrib.items())]
        if root:
            attrs = [("xmlns:" + prefixes[uri], uri) for uri in namespaces] + attrs
        opening = "<" + qname(node.tag) + "".join(
            f" {key}={encoded(quoteattr(value))}" for key, value in attrs) + ">"
        body = encoded(escape(node.text or "")) + "".join(
            serialize(child) + encoded(escape(child.tail or "")) for child in node)
        return opening + body + "</" + qname(node.tag) + ">"

    return serialize(element, root=True)


def _simple_script(element, *, robust: bool = False) -> str | None:
    """Only isolated script layout is previewed; no row/fraction semantics inferred."""
    kind, children = tag(element), list(element)
    if robust:
        if (element.tag != f"{{{_MATHML}}}{kind}" or kind == "mtext"
                or kind in {"mi", "mn"} and any(c.isspace() for c in element.text or "")):
            return None
        if kind == "math" and set(element.attrib) - {"id", "display"}:
            return None
        if kind not in {"mi", "mn"} and (
            (element.text or "").strip() or any((child.tail or "").strip() for child in children)
        ):
            return None
    if kind == "math" and len(children) == 1:
        return _simple_script(children[0], robust=robust)
    if kind in {"mi", "mn", "mtext"} and not children and not element.attrib:
        return element.text or ""
    if kind in {"msup", "msub"} and len(children) == 2 and not element.attrib:
        values = [_simple_script(child, robust=robust) for child in children]
        if all(value is not None for value in values):
            symbol = "^" if kind == "msup" else "_"
            return f"({values[0]}){symbol}({values[1]})"
    return None


def _formula(element, notes: list[str], *, robust: bool = False) -> str:
    """Select one source alternative; complex mathematics remains source XML/TeX."""
    if robust and tag(element) in {"disp-formula", "inline-formula"}:
        # Direct mixed content composes a formula; only <alternatives> selects one source.
        text = element.text or ""
        for child in element:
            kind = tag(child)
            if kind == "label":
                rendered = ""
            elif kind in {"math", "tex-math", "graphic", "inline-graphic", "alternatives"}:
                rendered = _formula(child, notes, robust=True)
            elif kind in {"caption", "alt-text"}:
                rendered = f"[Source {kind}: " + _inline(child, notes, robust=True) + "] "
            else:
                rendered = _inline(child, notes, robust=True)
            text += rendered + (child.tail or "")
        return text
    if tag(element) == "math" or robust and tag(element) in {
            "tex-math", "graphic", "inline-graphic"}:
        choices = [element]
    else:
        alternatives = next((child for child in element if tag(child) == "alternatives"), None)
        choices = list(alternatives) if alternatives is not None else list(element)
    selected = next((child for child in choices if tag(child) == "math"), None)
    if selected is not None:
        if robust and selected.tag != f"{{{_MATHML}}}math":
            notes.append("Unrecognized math namespace; source XML retained without interpretation.")
            return "[XML math source, unrendered] " + _source_xml(selected, robust=True)
        preview = _simple_script(selected, robust=robust)
        if preview is not None:
            notes.append("MathML preview preserves isolated script layout, not semantics.")
            return "[MathML script layout: " + preview + "]"
        notes.append("MathML source retained; mathematical text rendering unavailable.")
        return "[MathML source, unrendered] " + _source_xml(selected, robust=robust)
    selected = next((child for child in choices if tag(child) == "tex-math"), None)
    if selected is not None:
        notes.append("TeX source retained without execution or mathematical conversion.")
        return "[TeX source, unrendered] " + "".join(selected.itertext())
    graphics = [child for child in choices if tag(child) in {"graphic", "inline-graphic"}]
    if graphics:
        refs = [value for child in graphics for key, value in child.attrib.items()
                if key.rsplit("}", 1)[-1] == "href"]
        notes.append("Image-only formula is unreadable as text; no image interpretation performed.")
        alt = next((child for child in choices if tag(child) == "alt-text"), None)
        provided = ("; source alt-text: " + "".join(alt.itertext())) if alt is not None else ""
        return "[Formula image unreadable as text; resources: " + ", ".join(refs) + provided + "]"
    if robust:
        # Skip a separately rendered label's contents, but always retain its tail.
        return (element.text or "") + "".join(
            ("" if tag(child) == "label" else _inline(child, notes, robust=True))
            + (child.tail or "") for child in element)
    return (element.text or "") + "".join(
        _inline(child, notes, robust=False) + (child.tail or "")
        for child in element if tag(child) != "label")


def _inline(element, notes: list[str], *, robust: bool = False) -> str:
    kind = tag(element)
    if kind in {"sup", "sub"}:
        # Explicit wrappers distinguish script position from ordinary '-'/'_' text.
        return ("^" if kind == "sup" else "_") + "(" + _mixed(element, notes, robust=robust) + ")"
    if kind in {"math", "inline-formula", "disp-formula"}:
        label = next((child for child in element if tag(child) == "label"), None)
        prefix = (_mixed(label, notes, robust=robust) + " ") if label is not None else ""
        return prefix + _formula(element, notes, robust=robust)
    if kind == "alternatives" and any(tag(child) in {"math", "tex-math"} for child in element):
        return _formula(element, notes, robust=robust)
    if kind == "caption":
        return " ; ".join(_mixed(child, notes, robust=robust) for child in element
                          if tag(child) in {"label", "title", "p"})
    if kind in {"graphic", "inline-graphic"}:
        return ""
    return _mixed(element, notes, robust=robust)


def _mixed(element, notes: list[str], *, robust: bool = False) -> str:
    return (element.text or "") + "".join(
        _inline(child, notes, robust=robust) + (child.tail or "") for child in element)


def render_block(element, rendition_version: str = RENDITION_VERSION) -> tuple[str, list[str]]:
    """Preserve scripts, table/caption boundaries and explicit unrenderable formulas."""
    if not isinstance(rendition_version, str) or rendition_version not in SUPPORTED_RENDITIONS:
        raise ValueError("Unsupported JATS rendition version")
    robust = rendition_version == RENDITION_VERSION
    notes = []
    if tag(element) == "table-wrap":
        rows = [_mixed(child, notes, robust=robust) for child in element
                if tag(child) in {"label", "caption", "table-wrap-foot"}]
        for row in element.iter():
            if tag(row) == "tr":
                cells = [_mixed(cell, notes, robust=robust) for cell in row
                         if tag(cell) in {"td", "th"}]
                if cells:
                    rows.append(" | ".join(cells))
        text = " ; ".join(rows) if rows else _mixed(element, notes, robust=robust)
    elif tag(element) in {"fig", "caption"}:
        text = " ; ".join(_inline(child, notes, robust=robust) for child in element
                          if tag(child) in {"label", "title", "caption", "p"})
    elif tag(element) == "disp-formula":
        text = _inline(element, notes, robust=robust)
    else:
        text = _mixed(element, notes, robust=robust)
    return " ".join(text.split()), list(dict.fromkeys(notes))
