"""Reproducible renditions of cached, identified publications and supplements.

Rendition is not scientific review. Original bytes, conversion command, and lost
layout remain explicit. Unregistered adjacent files are never promoted to sources.
"""

from html import escape
from html.parser import HTMLParser
import errno
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from .run_state import sha


class ArticleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metadata, self.parts, self.skip = {}, [], 0
        self.math_depth, self.links = 0, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        local = tag.rsplit(":", 1)[-1]
        if not self.skip and (self.math_depth or local == "math"):
            self.parts.append(self.get_starttag_text())
            if local == "math":
                if not self.math_depth:
                    self.parts.insert(len(self.parts) - 1, "\n[MathML source; unrendered] ")
                self.math_depth += 1
            return
        if tag == "meta" and attrs.get("name", "").lower().startswith("citation_"):
            self.metadata.setdefault(attrs["name"].lower(), []).append(attrs.get("content", ""))
        if tag in {"script", "style", "nav", "footer", "header"}:
            self.skip += 1
        if not self.skip:
            if tag in {"p", "div", "section", "li", "tr", "h1", "h2", "h3", "h4", "h5", "br", "hr"}:
                self.parts.append("\n")
            elif tag in {"td", "th"}:
                self.parts.append(" | ")
            elif tag == "sup":
                self.parts.append("^(")
            elif tag == "sub":
                self.parts.append("_(")
            elif tag == "a":
                self.links.append(attrs.get("href"))
            elif tag == "img":
                self.parts.append(f" [Image not rendered: {attrs.get('alt') or attrs.get('src') or 'unlabelled image'}] ")

    def handle_startendtag(self, tag, attrs):
        if self.math_depth:
            self.parts.append(self.get_starttag_text())
        elif tag.rsplit(":", 1)[-1] == "math" and not self.skip:
            self.parts.append("\n[MathML source; unrendered] " + self.get_starttag_text() + "\n")
        else:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.math_depth:
            self.parts.append(f"</{tag}>")
            if tag.rsplit(":", 1)[-1] == "math":
                self.math_depth -= 1
                self.parts.append("\n")
            return
        if tag in {"script", "style", "nav", "footer", "header"}:
            self.skip = max(0, self.skip - 1)
        elif not self.skip:
            if tag in {"sup", "sub"}:
                self.parts.append(")")
            elif tag == "a" and self.links:
                link = self.links.pop()
                if link:
                    self.parts.append(f" [link: {link}]")
            if tag in {"p", "div", "section", "li", "tr", "h1", "h2", "h3", "h4", "h5"}:
                self.parts.append("\n")

    def handle_data(self, text):
        if not self.skip:
            self.parts.append(escape(text, quote=False) if self.math_depth else text)

    @property
    def text(self):
        return "\n\n".join(line for raw in "".join(self.parts).splitlines()
                           if (line := " ".join(raw.split()))) + "\n"


def _publish_rendition(output: Path, text: str, original: Path, original_sha256: str,
                       converter: str, limits: str) -> None:
    """Never overwrite a source or a previously registered rendition."""
    if sha(original) != original_sha256:
        raise ValueError("Original changed during rendition")
    output.parent.mkdir(parents=True, exist_ok=True)
    provenance = ("[Rendition provenance, not publication content]\n"
                  f"Original cached file: {original.resolve()}\nSHA256: {original_sha256}\n"
                  f"Converter: {converter}\nRendition limits: {limits}\n\n")
    with output.open("x", encoding="utf-8") as handle:
        handle.write(provenance + text)


def primary_html(folder: Path, pmid: str, output: Path) -> tuple[dict, dict]:
    original = (folder / "article.html").resolve()
    raw = original.read_bytes()
    original_sha256 = hashlib.sha256(raw).hexdigest()
    parser = ArticleHTML()
    parser.feed(raw.decode("utf-8"))
    parser.close()
    # Identical repeated tags do not make publication identity ambiguous.
    if {value.strip() for value in parser.metadata.get("citation_pmid", [])} != {pmid}:
        raise ValueError("Cached HTML has no unique matching citation_pmid")
    titles = {value.strip() for value in parser.metadata.get("citation_title", [])}
    if len(titles) != 1 or not next(iter(titles)):
        raise ValueError("Cached HTML has no unique publication title")
    title = next(iter(titles))
    text = parser.text
    if not text.strip():
        raise ValueError("No text in cached publication HTML")
    converter = "cached_documents.ArticleHTML/2"
    limits = ("Linear HTML text with table separators and explicit sub/sup scripts; "
              "MathML is retained as unrendered markup, images are marked but not rendered. "
              "CSS layout, image content and other equation renderings require original inspection. "
              "Hyperlink targets are retained; linked files are not implicitly registered.")
    _publish_rendition(output, text, original, original_sha256, converter, limits)
    receipt = {"original": str(original), "original_sha256": original_sha256,
               "rendition": str(output.resolve()), "rendition_sha256": sha(output),
               "converter": converter, "identity": parser.metadata, "limits": limits}
    source = {"source_id": f"pmid_{pmid}", "title": title,
              "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "role": "primary_paper",
              "path": str(output.resolve())}
    return source, receipt


def supplements(folder: Path, pmid: str, output: Path) -> tuple[list, list, list]:
    """Use the download register; retain failures/binary files as explicit leads.

    PDFs are re-rendered from their registered originals, never an unbound old txt.
    Unavailable converters or formats remain source tasks instead of being skipped.
    """
    register = folder / "supplements" / "acquisition.json"
    if not register.exists():
        return [], [], []
    document = json.loads(register.read_bytes())
    if not isinstance(document, dict) or document.get("pmid") != pmid:
        raise ValueError("Supplement register belongs to another publication or is invalid")
    rows = document.get("files", [])
    if not isinstance(rows, list):
        raise ValueError("Supplement register files must be a list")
    sources, receipts, leads, seen = [], [], [], set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path", ""), str):
            leads.append({"status": "invalid_registration", "record": row})
            continue
        path = Path(row.get("path", ""))
        original = (path if path.is_absolute() else register.parent / path).resolve()
        if row.get("status") != "cached" or not original.is_file():
            leads.append({"status": "unavailable", "record": row})
            continue
        if not original.is_relative_to((folder / "supplements").resolve()):
            leads.append({"status": "registration_outside_publication_folder", "record": row})
            continue
        if original in seen:
            continue
        original_sha256 = sha(original)
        if row.get("sha256") and original_sha256 != row["sha256"]:
            leads.append({"status": "source_hash_mismatch", "record": row})
            continue
        if original.suffix.lower() not in {".pdf", ".txt"}:
            leads.append({"status": "original_requires_format_specific_inspection", "record": row,
                          "original": str(original), "original_sha256": original_sha256})
            continue
        destination = output / f"supplement_{len(sources) + 1:03d}.txt"
        try:
            url = row["url"]
            if not isinstance(url, str) or not url.strip():
                raise ValueError("Registered supplement URL is missing")
            if original.suffix.lower() == ".pdf":
                with original.open("rb") as handle:
                    if handle.read(5) != b"%PDF-":
                        raise ValueError("Registered PDF has invalid signature")
                executable = shutil.which("pdftotext")
                if executable is None:
                    raise ValueError("pdftotext is not installed")
                # Failed conversion never leaves or overwrites a registered destination.
                with tempfile.TemporaryDirectory() as temporary:
                    converted = Path(temporary) / "converted.txt"
                    executed = [executable, "-layout", str(original), str(converted)]
                    subprocess.run(executed, check=True, capture_output=True, timeout=120)
                    text = converted.read_text(encoding="utf-8")
                command = executed
                limits = "PDF text rendition with layout hints; images, tables and formulas may be lost or reordered."
            else:
                command = ["utf8_text_copy"]
                text = original.read_text(encoding="utf-8")
                limits = "UTF-8 text rendition; original text does not establish missing image/table/formula content."
            if not text.strip():
                raise ValueError("Rendition is empty; original needs inspection/OCR")
            _publish_rendition(destination, text, original, original_sha256, command[0], limits)
            source_id = f"pmid_{pmid}_supp_{len(sources) + 1:03d}"
            sources.append({"source_id": source_id, "title": original.name,
                            "url": url, "role": "supplement", "path": str(destination.resolve())})
            receipts.append({"source_id": source_id, "original": str(original),
                             "original_sha256": original_sha256, "rendition": str(destination.resolve()),
                             "rendition_sha256": sha(destination), "command": command,
                             "register": str(register.resolve()), "register_sha256": sha(register),
                             "archive_member": row.get("archive_member"), "limits": limits})
            seen.add(original)
        except (OSError, ValueError, subprocess.SubprocessError, KeyError) as exc:
            if isinstance(exc, OSError) and exc.errno in {errno.ENOSPC, errno.EDQUOT}:
                raise
            leads.append({"status": "rendition_failed", "record": row, "error": str(exc)})
    return sources, receipts, leads
