"""Host renditions of retrieved XML/DOCX; no model-authored source text."""

from io import BytesIO
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from ..cached_documents import _publish_rendition
from ..document_manifest import MAX_BYTES, inspect_jats
from ..extraction_batch.storage import atomic_bytes, sha

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
DOCX_LIMITS = ("DOCX body paragraphs and table rows/cells, visible run text, explicit sub/superscripts and hyperlink targets. "
               "Field instructions (including EndNote metadata) are omitted; this is not a Word layout rendering. "
               "Images, drawings, equations, numbering/styles, merged-cell geometry, headers/footers, notes, comments and "
               "embedded objects require original inspection. Tracked changes are marked, not resolved.")


def _xml(raw):
    if len(raw) > MAX_BYTES or re.search(br"<!ENTITY\s", raw, re.I):
        raise ValueError("Oversized XML or entity declarations in source rendition")
    return ET.fromstring(raw)


def select_jats(raw, matches):
    """Select exactly one direct matching article; preserve the wrapper separately."""
    root = _xml(raw)
    tag = root.tag.rsplit("}", 1)[-1]
    if tag == "article":
        identity = inspect_jats(raw)["identity"]
        matches(identity)
        return raw, identity, None
    if tag != "pmc-articleset":
        raise ValueError("Unsupported JATS container; expected article or pmc-articleset")
    articles = [child for child in root if child.tag.rsplit("}", 1)[-1] == "article"]
    selected = []
    for index, article in enumerate(articles):
        rendered = ET.tostring(article, encoding="utf-8", xml_declaration=True)
        try:
            identity = inspect_jats(rendered)["identity"]
            matches(identity)
        except ValueError:
            continue
        selected.append((index, rendered, identity))
    if len(selected) != 1:
        raise ValueError(f"JATS wrapper requires exactly one matching article; found {len(selected)}")
    index, rendered, identity = selected[0]
    return rendered, identity, {"container":tag, "direct_article_count":len(articles),
        "selected_article_index":index, "selected_identity":identity,
        "converter":"source_acquisition.select_jats/1",
        "limits":"Selected one identity-matching direct article; other wrapper content is excluded. XML serialization may normalize namespace/entity syntax and omit comments/processing instructions; original wrapper retained."}


def jats_rendition(original, output, matches):
    raw, identity, selection = select_jats(Path(original).read_bytes(), matches)
    path = Path(original)
    if selection:
        atomic_bytes(Path(output), raw)
        path = Path(output)
    receipt = {"original":str(original), "original_sha256":sha(original),
               "rendition":str(path), "rendition_sha256":sha(path),
               **(selection or {"converter":"workflow_extraction.sources.build_packet JATS reader"})}
    return path, identity, receipt


def docx_rendition(original: Path, output: Path):
    """Read visible WordprocessingML rather than ZIP/XML itertext metadata."""
    original = Path(original); original_hash = sha(original)
    try:
        with ZipFile(BytesIO(original.read_bytes())) as archive:
            infos = archive.infolist()
            names = [row.filename for row in infos]
            if len(names) != len(set(names)) or sum(row.file_size for row in infos) > MAX_BYTES:
                raise ValueError("Duplicate DOCX archive member or expanded DOCX exceeds source limit")
            if any(row.flag_bits & 1 for row in infos):
                raise ValueError("Encrypted DOCX is not a readable public source")
            root = _xml(archive.read("word/document.xml"))
            if root.tag != W + "document" or root.find(W + "body") is None:
                raise ValueError("Not a WordprocessingML document body")
            relationships = {}
            rel_path = "word/_rels/document.xml.rels"
            if rel_path in names:
                for row in _xml(archive.read(rel_path)):
                    if row.get("Type", "").endswith("/hyperlink"):
                        relationships[row.get("Id")] = row.get("Target", "")
    except (BadZipFile, KeyError) as exc:
        raise ValueError("Not a readable DOCX ZIP/document") from exc
    omitted_instructions = 0

    def inline(node):
        nonlocal omitted_instructions
        tag = node.tag
        if tag in {W+"t", W+"delText"}:
            return node.text or ""
        if tag == W+"instrText":
            omitted_instructions += 1
            match = re.match(r'\s*HYPERLINK\s+"([^"]+)"', node.text or "", re.I)
            return f" [link: {match[1]}] " if match else ""
        if tag in {W+"tab", W+"br", W+"cr"}:
            return "\t" if tag == W+"tab" else "\n"
        if tag == W+"noBreakHyphen":return "‑"
        if tag == W+"softHyphen":return "\u00ad"
        if tag in {W+"rPr", W+"pPr", W+"tblPr", W+"tcPr", W+"trPr"}:return ""
        if tag in {W+"drawing", W+"pict", W+"object"} or tag.rsplit("}",1)[-1] in {"oMath", "oMathPara"}:
            return " [Image/object/equation not rendered; inspect original] "
        text = "".join(inline(child) for child in node)
        if tag == W+"r":
            align = node.find(f"{W}rPr/{W}vertAlign")
            if align is not None and align.get(W+"val") in {"superscript", "subscript"}:
                return ("^(" if align.get(W+"val") == "superscript" else "_(") + text + ")"
        if tag == W+"hyperlink":
            target = relationships.get(node.get(R+"id")) or ("#"+node.get(W+"anchor") if node.get(W+"anchor") else None)
            return text + (f" [link: {target}]" if target else " [Unresolved hyperlink; inspect original]")
        if tag == W+"fldSimple":
            match = re.match(r'\s*HYPERLINK\s+"([^"]+)"', node.get(W+"instr", ""), re.I)
            return text + (f" [link: {match[1]}]" if match else "")
        if tag in {W+"ins", W+"del", W+"moveFrom", W+"moveTo"}:
            return f" [Tracked {tag.split('}')[1]}: {text}] "
        return text

    def blocks(parent):
        result = []
        for child in parent:
            if child.tag == W+"p":
                result.append(inline(child))
            elif child.tag == W+"tbl":
                result.append("[Table]")
                for row in child.findall(W+"tr"):
                    cells = [" / ".join(blocks(cell)) for cell in row.findall(W+"tc")]
                    result.append(" | ".join(cells))
                result.append("[/Table]")
            elif child.tag == W+"sdt":
                for content in child.findall(W+"sdtContent"):result.extend(blocks(content))
        return result
    text = "\n\n".join(blocks(root.find(W+"body")))
    if not text.strip():raise ValueError("DOCX has no visible paragraph/table text")
    _publish_rendition(output,text,original,original_hash,"source_acquisition.docx/1",DOCX_LIMITS)
    return {"original":str(original), "original_sha256":original_hash, "rendition":str(output),
            "rendition_sha256":sha(output), "converter":"source_acquisition.docx/1", "limits":DOCX_LIMITS,
            "omitted_field_instruction_elements":omitted_instructions}
